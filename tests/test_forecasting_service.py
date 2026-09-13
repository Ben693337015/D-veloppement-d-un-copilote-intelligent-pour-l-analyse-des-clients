"""Tests du service de prévision (Phase 3, module Maslaw).

NOTE : ces tests automatisés utilisent modele="arima" (statsmodels, rapide,
aucune dépendance binaire externe) plutôt que "prophet" (cmdstan, ~2-3s par
entraînement et une première invocation qui peut nécessiter une compilation
du backend Stan) — plus adapté à une suite pytest/CI rapide. Le chemin
Prophet a été vérifié manuellement (fit + predict fonctionnels) et reste le
modèle par défaut de l'API en usage réel (cf. forecasting_service.py).
"""

from datetime import datetime, timedelta

from app.models import Transaction
from app.services import forecasting_service


def _seed_daily_sales(db_session, n_jours: int = 25, ca_base: float = 100.0):
    """Insère n_jours de transactions avec une tendance croissante + un peu
    de saisonnalité hebdomadaire simple, pour donner du signal à ARIMA."""
    debut = datetime(2024, 1, 1)
    for i in range(n_jours):
        jour = debut + timedelta(days=i)
        montant = ca_base + i * 2.0 + (10.0 if jour.weekday() in (5, 6) else 0.0)
        db_session.add(
            Transaction(
                client_id=None,
                numero_facture=f"INV-{i:04d}",
                code_produit="PRD-01",
                quantite=1,
                prix_unitaire=montant,
                date_transaction=jour,
                est_retour=False,
            )
        )
    db_session.commit()


def test_insufficient_history_returns_naive_baseline(db_session):
    _seed_daily_sales(db_session, n_jours=3)
    resultat = forecasting_service.get_previsions_ventes(db_session, horizon_jours=5, modele="arima")

    assert "insuffisant" in resultat["modele_utilise"].lower()
    assert resultat["rmse_validation"] is None
    assert resultat["mae_validation"] is None
    assert len(resultat["points"]) == 5


def test_single_outlier_day_does_not_wreck_the_forecast(db_session):
    """PRÉTRAITEMENT (winsorizing) : un unique jour de vente aberrant (ex.
    quantité saisie en trop par erreur) ne doit plus faire exploser le RMSE
    ni produire des prévisions délirantes — verrouille le bug corrigé
    (avant prétraitement : RMSE ~13 380 sur une baseline ~100/jour)."""
    _seed_daily_sales(db_session, n_jours=25, ca_base=100.0)
    debut = datetime(2024, 1, 1)
    db_session.add(
        Transaction(
            client_id=None,
            numero_facture="INV-ERREUR",
            code_produit="PRD-01",
            quantite=5000,  # faute de frappe probable (quantité au lieu de 1-2)
            prix_unitaire=100.0,
            date_transaction=debut + timedelta(days=10),
            est_retour=False,
        )
    )
    db_session.commit()

    resultat = forecasting_service.get_previsions_ventes(db_session, horizon_jours=5, modele="arima")

    assert resultat["rmse_validation"] is not None
    assert resultat["rmse_validation"] < 50  # avant correctif : > 13000
    assert any("plafonné" in a for a in resultat["avertissements"])
    for point in resultat["points"]:
        assert point["valeur_prevue"] < 1000  # avant correctif : ~9900


def test_arima_forecast_with_sufficient_history(db_session):
    _seed_daily_sales(db_session, n_jours=25)
    resultat = forecasting_service.get_previsions_ventes(db_session, horizon_jours=5, modele="arima")

    assert resultat["modele_utilise"] == "ARIMA(1,1,1)"
    assert len(resultat["points"]) == 5
    # Le CA simulé est strictement croissant -> la prévision doit rester positive
    assert all(p["valeur_prevue"] >= 0 for p in resultat["points"])
    # Validation train/test doit avoir tourné (historique suffisant)
    assert resultat["rmse_validation"] is not None
    assert resultat["mae_validation"] is not None
    assert resultat["rmse_validation"] >= 0
    assert resultat["mae_validation"] >= 0


def test_unknown_model_raises_value_error(db_session):
    _seed_daily_sales(db_session, n_jours=10)
    try:
        forecasting_service.get_previsions_ventes(db_session, horizon_jours=5, modele="reseau-de-neurones")
        raise AssertionError("Devrait avoir levé ValueError pour un modèle inconnu")
    except ValueError as exc:
        assert "inconnu" in str(exc).lower()


def test_forecast_endpoint_accepts_modele_param(client, db_session):
    _seed_daily_sales(db_session, n_jours=15)
    resp = client.get("/api/v1/analytics/forecast/sales?horizon_jours=5&modele=arima")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["modele_utilise"] == "ARIMA(1,1,1)"
    assert len(body["points"]) == 5


def test_forecast_endpoint_rejects_unknown_modele(client, db_session):
    resp = client.get("/api/v1/analytics/forecast/sales?horizon_jours=5&modele=reseau-de-neurones")
    assert resp.status_code == 422


def test_xgboost_falls_back_to_naive_baseline_when_history_too_short(db_session):
    """XGBoost exige plus d'historique que Prophet/ARIMA à cause du lag J-7
    (les 7 premiers jours de la série ne fournissent pas de lag_7 valide,
    donc il faut > 14 jours pour garder assez de lignes entraînables après
    dropna). En dessous de ce seuil : baseline naïve, jamais un XGBoost mal
    entraîné faussement présenté comme fiable."""
    _seed_daily_sales(db_session, n_jours=10)
    resultat = forecasting_service.get_previsions_ventes(db_session, horizon_jours=5, modele="xgboost")

    assert "insuffisant" in resultat["modele_utilise"].lower()
    assert "xgboost" in resultat["modele_utilise"].lower()
    assert resultat["rmse_validation"] is None
    assert len(resultat["points"]) == 5


def test_xgboost_forecast_with_sufficient_history(db_session):
    _seed_daily_sales(db_session, n_jours=40)
    resultat = forecasting_service.get_previsions_ventes(db_session, horizon_jours=7, modele="xgboost")

    assert resultat["modele_utilise"] == "XGBoost"
    assert len(resultat["points"]) == 7
    assert all(p["valeur_prevue"] >= 0 for p in resultat["points"])
    # Validation train/test doit avoir tourné (historique suffisant)
    assert resultat["rmse_validation"] is not None
    assert resultat["mae_validation"] is not None
    assert resultat["rmse_validation"] >= 0
    # Pas d'intervalle de confiance natif pour XGBoost (cf. docstring service)
    assert all(p["borne_basse"] is None and p["borne_haute"] is None for p in resultat["points"])
    assert any("intervalle de confiance" in a for a in resultat["avertissements"])


def test_xgboost_long_horizon_warns_about_recursive_accumulation(db_session):
    _seed_daily_sales(db_session, n_jours=40)
    resultat = forecasting_service.get_previsions_ventes(db_session, horizon_jours=20, modele="xgboost")
    assert any("récursion" in a for a in resultat["avertissements"])


def test_xgboost_uses_calendar_features_deterministically(db_session):
    """Deux entraînements sur le même historique doivent produire EXACTEMENT
    la même prévision (random_state fixé) — sinon impossible à valider en
    soutenance ('pourquoi le chiffre change à chaque clic ?')."""
    _seed_daily_sales(db_session, n_jours=40)
    r1 = forecasting_service.get_previsions_ventes(db_session, horizon_jours=5, modele="xgboost")
    r2 = forecasting_service.get_previsions_ventes(db_session, horizon_jours=5, modele="xgboost")
    assert [p["valeur_prevue"] for p in r1["points"]] == [p["valeur_prevue"] for p in r2["points"]]


def test_forecast_endpoint_accepts_xgboost(client, db_session):
    _seed_daily_sales(db_session, n_jours=40)
    resp = client.get("/api/v1/analytics/forecast/sales?horizon_jours=5&modele=xgboost")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["modele_utilise"] == "XGBoost"
    assert len(body["points"]) == 5
