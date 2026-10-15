"""Prévision par les 3 modèles + interprétation (comparaison_previsions)."""

import threading
from datetime import datetime, timedelta

import pandas as pd
import pytest

from app.models import Transaction
from app.services import comparaison_previsions as cp
from app.services import forecasting_service


def _ventes(db, n_jours=60, base=100.0, pente=2.0, debut=datetime(2024, 1, 1)):
    for i in range(n_jours):
        jour = debut + timedelta(days=i)
        db.add(Transaction(numero_facture=f"F{i}", quantite=1, prix_unitaire=base + pente * i
                           + (15 if jour.weekday() >= 5 else 0), date_transaction=jour))
    db.commit()


@pytest.fixture
def sans_prophet(monkeypatch):
    """Prophet (CmdStan) est lent en CI : on le remplace par ARIMA pour les
    tests de structure ; le chemin Prophet réel est couvert par
    test_prophet_reel (marqué lent)."""
    vrai = forecasting_service._generer_prevision

    def faux(quotidien, horizon, modele, *a, **k):
        return vrai(quotidien, horizon, "arima" if modele == "prophet" else modele, *a, **k)

    monkeypatch.setattr(forecasting_service, "_generer_prevision", faux)


def test_les_trois_modeles_sont_calcules_et_interpretes(client, db_session, sans_prophet):
    _ventes(db_session)
    r = client.get("/api/v1/analytics/forecast/sales/comparaison?horizon_jours=14")
    assert r.status_code == 200
    corps = r.json()
    assert [m["modele"] for m in corps["modeles"]] == ["prophet", "arima", "xgboost"]
    assert len(corps["historique"]) == 60
    for m in corps["modeles"]:
        assert m["statut"] == "ok"
        assert len(m["points"]) == 14
        assert m["interpretation"][0].startswith("CA prévu sur 14 jours")
        assert m["indicateurs"]["sens"] in {"hausse", "baisse", "stable"}
    assert corps["synthese"]["meilleur_modele"] in {"prophet", "arima", "xgboost"}
    assert corps["synthese"]["texte"][0].startswith("Modèle le plus fiable")


def test_cache_tant_que_les_donnees_ne_changent_pas(client, db_session, sans_prophet, monkeypatch):
    _ventes(db_session)
    appels = []
    vrai = cp._calculer
    monkeypatch.setattr(cp, "_calculer", lambda db, h: appels.append(h) or vrai(db, h))

    url = "/api/v1/analytics/forecast/sales/comparaison?horizon_jours=14"
    assert client.get(url).json()["depuis_cache"] is False
    assert client.get(url).json()["depuis_cache"] is True
    assert appels == [14]

    # nouvelles ventes -> nouvelle signature -> recalcul
    db_session.add(Transaction(numero_facture="NEW", quantite=1, prix_unitaire=999,
                               date_transaction=datetime(2024, 3, 15)))
    db_session.commit()
    assert client.get(url).json()["depuis_cache"] is False
    assert appels == [14, 14]


def test_deux_demandes_simultanees_un_seul_calcul(db_session, monkeypatch):
    """La 2e demande attend le calcul en cours au lieu d'en lancer un second."""
    _ventes(db_session, n_jours=20)
    debut, fin = threading.Event(), threading.Event()
    appels = []

    def lent(db, h):
        appels.append(h)
        debut.set()
        fin.wait(5)
        return {"horizon_jours": h, "historique": [], "modeles": [], "synthese": {}, "calcule_le": datetime.utcnow()}

    monkeypatch.setattr(cp, "_calculer", lent)
    sig = cp._signature(db_session)
    monkeypatch.setattr(cp, "_signature", lambda db: sig)
    resultats = []
    t1 = threading.Thread(target=lambda: resultats.append(cp.comparer_modeles(db_session, 30)))
    t1.start()
    debut.wait(5)
    t2 = threading.Thread(target=lambda: resultats.append(cp.comparer_modeles(db_session, 30)))
    t2.start()
    fin.set()
    t1.join(5)
    t2.join(5)
    assert appels == [30]
    assert sorted(r["depuis_cache"] for r in resultats) == [False, True]


def test_un_modele_en_echec_n_empeche_pas_les_autres(client, db_session, sans_prophet, monkeypatch):
    _ventes(db_session)
    vrai = forecasting_service._generer_prevision

    def casse_xgboost(quotidien, horizon, modele, *a, **k):
        if modele == "xgboost":
            raise RuntimeError("panne simulée")
        return vrai(quotidien, horizon, modele, *a, **k)

    monkeypatch.setattr(forecasting_service, "_generer_prevision", casse_xgboost)
    corps = client.get("/api/v1/analytics/forecast/sales/comparaison?horizon_jours=7").json()
    statuts = {m["modele"]: m["statut"] for m in corps["modeles"]}
    assert statuts == {"prophet": "ok", "arima": "ok", "xgboost": "erreur"}
    assert corps["synthese"]["meilleur_modele"] in {"prophet", "arima"}


def test_base_vide(client, db_session):
    corps = client.get("/api/v1/analytics/forecast/sales/comparaison").json()
    assert corps["modeles"] == [] and "Aucune vente" in corps["synthese"]["texte"][0]


def test_historique_court_signale_la_moyenne_naive(client, db_session):
    _ventes(db_session, n_jours=5)
    corps = client.get("/api/v1/analytics/forecast/sales/comparaison?horizon_jours=7").json()
    assert all("trop court" in m["interpretation"][0] for m in corps["modeles"])
    assert corps["synthese"]["meilleur_modele"] is None


# ── Interprétation : règles chiffrées ───────────────────────────────────────


def _res(valeurs, rmse=None, bornes=None, nom="ARIMA(1,1,1)"):
    pts = [{"date_prevision": f"2024-01-{i + 1:02d}", "valeur_prevue": v,
            "borne_basse": bornes[0][i] if bornes else None, "borne_haute": bornes[1][i] if bornes else None}
           for i, v in enumerate(valeurs)]
    return {"modele_utilise": nom, "points": pts, "rmse_validation": rmse}


def test_interpretation_tendance_niveau_et_fiabilite():
    phrases, ind = cp._interpreter(_res([100] * 7 + [100] * 7 + [130] * 7, rmse=20), 100, 100)
    assert ind["sens"] == "hausse" and ind["tendance"] == pytest.approx(0.3)
    assert ind["ecart_niveau"] == pytest.approx(0.1)
    assert ind["fiabilite"] == "bonne"  # 20 % du CA moyen de validation
    assert any("Tendance à la hausse" in p for p in phrases)

    _, ind = cp._interpreter(_res([100] * 21, rmse=40), 100, 100)
    assert ind["sens"] == "stable" and ind["fiabilite"] == "moyenne"
    _, ind = cp._interpreter(_res([100] * 21, rmse=80), 100, 100)
    assert ind["fiabilite"] == "faible"


def test_erreur_relative_rapportee_a_la_periode_de_validation():
    """Le RMSE est rapporté au CA moyen de la période où il a été mesuré,
    pas au niveau récent (qui peut être en rupture)."""
    _, ind = cp._interpreter(_res([10] * 7, rmse=30), reference_jour=10, moyenne_validation=100)
    assert ind["erreur_relative"] == pytest.approx(0.3)


def test_intervalle_de_confiance_decrit():
    phrases, _ = cp._interpreter(_res([100] * 7, rmse=10, bornes=([90] * 7, [110] * 7)), 100, 100)
    assert any("±10" in p for p in phrases)
    phrases, _ = cp._interpreter(_res([100] * 7, rmse=10), 100, 100)
    assert any("Pas d'intervalle" in p for p in phrases)


def _m(modele, rmse, total, sens="stable"):
    return {"modele": modele, "nom": cp.NOMS[modele], "statut": "ok", "rmse_validation": rmse,
            "indicateurs": {"total_prevu": total, "sens": sens}}


def test_synthese_meilleur_modele_divergence_et_rupture():
    s = cp._synthese([_m("prophet", 30, 1000), _m("arima", 20, 1000), _m("xgboost", 10, 1050)])
    assert s["meilleur_modele"] == "xgboost"
    assert "50" in s["texte"][0]  # 50 % d'erreur en moins que ARIMA
    assert any("concordent" in t for t in s["texte"])
    assert any("tendance stable" in t for t in s["texte"])

    s = cp._synthese([_m("prophet", 30, 3000), _m("arima", 20, 1000)], rupture=-0.9)
    assert any("divergent" in t for t in s["texte"])
    assert any("Rupture récente" in t for t in s["texte"])


def test_rupture_detectee_sur_une_chute_du_dernier_mois(db_session, sans_prophet):
    _ventes(db_session, n_jours=150, pente=0)
    for t in db_session.query(Transaction).filter(Transaction.date_transaction >= datetime(2024, 5, 1)):
        t.prix_unitaire = 5
    db_session.commit()
    res = cp.comparer_modeles(db_session, 14)
    assert any("Rupture récente" in t for t in res["synthese"]["texte"])


def test_precalcul_en_arriere_plan_remplit_le_cache(db_session, sans_prophet):
    _ventes(db_session)
    cp.precalculer_en_arriere_plan(db_session.get_bind(), horizon=14)
    assert (cp._signature(db_session), 14) in cp._cache


def test_import_de_ventes_declenche_le_precalcul(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PREVISIONS_PRECALCUL", True)
    appels = []
    monkeypatch.setattr(cp, "precalculer_en_arriere_plan", lambda moteur, horizon=30: appels.append(moteur))
    csv = b"date,client,montant\n2024-01-01,C1,10\n2024-01-02,C2,20\n"
    mapping = '{"date_col": "date", "client_col": "client", "montant_col": "montant"}'
    r = client.post("/api/v1/ingestion/import/transactions", files={"file": ("v.csv", csv, "text/csv")},
                    data={"mapping": mapping})
    assert r.status_code == 200, r.text
    assert len(appels) == 1


@pytest.mark.slow
def test_prophet_reel(db_session):
    _ventes(db_session)
    q, n = forecasting_service._serie_ca_journalier(db_session)
    assert isinstance(q, pd.DataFrame)
    res = cp._un_modele(q, 7, "prophet", n, 100.0, 100.0)
    assert res["statut"] == "ok" and len(res["points"]) == 7


# ── Lecture « dirigeant » : la prévision sans jargon technique ──────────────

_JARGON = ("RMSE", "MAE", "Prophet", "ARIMA", "XGBoost", "intervalle de confiance", "modèle")


def _md(modele, rmse, total, *, mae=None, sens="hausse", ecart=0.10, fiabilite="bonne", bornes=True, h=10):
    points = [
        {"date_prevision": f"2024-03-{j + 1:02d}", "valeur_prevue": total / h,
         "borne_basse": total / h - 5 if bornes else None, "borne_haute": total / h + 5 if bornes else None}
        for j in range(h)
    ]
    return {"modele": modele, "nom": cp.NOMS[modele], "statut": "ok", "points": points,
            "rmse_validation": rmse, "mae_validation": mae if mae is not None else rmse / 2,
            "indicateurs": {"total_prevu": total, "moyenne_jour": total / h, "ecart_niveau": ecart,
                            "sens": sens, "fiabilite": fiabilite}}


def _sans_jargon(lecture: dict) -> None:
    textes = [lecture["titre"], lecture["resume"], lecture.get("evolution_texte") or "",
              lecture.get("fiabilite_texte") or "", *lecture["conseils"]]
    for texte in textes:
        for mot in _JARGON:
            assert mot.lower() not in texte.lower(), f"jargon « {mot} » dans : {texte}"


def test_lecture_dirigeant_retient_le_modele_le_plus_fiable_et_parle_metier():
    modeles = [_md("prophet", 30, 1000), _md("arima", 10, 1050, mae=4), _md("xgboost", 20, 1020)]
    lecture = cp._lecture_dirigeant(modeles, horizon=10, moyenne_validation=100.0, rupture=None)
    assert lecture["disponible"] is True
    assert lecture["modele_retenu"] == "arima"  # RMSE le plus bas
    assert lecture["total_prevu"] == 1050
    assert lecture["sens_evolution"] == "hausse"
    assert lecture["evolution_texte"].startswith("+10")
    assert lecture["ecart_jour"] == 4  # écart moyen constaté (MAE), pas le RMSE
    assert "4 % des ventes d'une journée" in lecture["fiabilite_texte"]
    assert lecture["concordance"] == "solide"  # 1000 / 1020 / 1050 : écart < 20 %
    # scénarios = somme des bornes journalières du modèle retenu (±5 par jour sur 10 jours)
    assert lecture["scenario_bas"] == pytest.approx(1000)
    assert lecture["scenario_haut"] == pytest.approx(1100)
    assert any("stocks" in c for c in lecture["conseils"])
    _sans_jargon(lecture)


def test_lecture_dirigeant_baisse_incertaine_et_rupture():
    modeles = [_md("prophet", 30, 600, sens="baisse", ecart=-0.3, fiabilite="faible"),
               _md("arima", 25, 900, sens="baisse", ecart=-0.3, fiabilite="faible")]
    lecture = cp._lecture_dirigeant(modeles, horizon=10, moyenne_validation=80.0, rupture=-0.6)
    assert lecture["sens_evolution"] == "baisse"
    assert lecture["concordance"] == "incertaine"  # 600 vs 900 : écart de 40 %
    conseils = " ".join(lecture["conseils"])
    assert "trésorerie" in conseils
    assert "prudent" in conseils
    assert "dernières ventes ont bien été importées" in conseils
    _sans_jargon(lecture)


def test_lecture_dirigeant_sans_intervalle_utilise_l_ecart_moyen():
    """Modèle retenu sans fourchette native (XGBoost) : scénarios = ± écart moyen par jour."""
    modeles = [_md("xgboost", 10, 1000, mae=8, bornes=False, ecart=0.02, sens="stable")]
    lecture = cp._lecture_dirigeant(modeles, horizon=10, moyenne_validation=100.0, rupture=None)
    assert lecture["sens_evolution"] == "stable"
    assert lecture["scenario_bas"] == pytest.approx(920)
    assert lecture["scenario_haut"] == pytest.approx(1080)
    assert lecture["concordance"] is None  # un seul modèle : rien à comparer
    assert lecture["conseils"] == ["Activité stable attendue : pas d'ajustement particulier nécessaire."]
    _sans_jargon(lecture)


def test_lecture_dirigeant_sans_modele_valide():
    lecture = cp._lecture_dirigeant([], horizon=30, moyenne_validation=0.0, rupture=None)
    assert lecture["disponible"] is False
    assert lecture["conseils"]
    _sans_jargon(lecture)


def test_lecture_dirigeant_exposee_par_l_api(client, db_session, sans_prophet):
    _ventes(db_session)
    corps = client.get("/api/v1/analytics/forecast/sales/comparaison?horizon_jours=14").json()
    lecture = corps["lecture_dirigeant"]
    assert lecture["disponible"] is True
    assert lecture["modele_retenu"] == corps["synthese"]["meilleur_modele"]
    assert lecture["scenario_bas"] <= lecture["total_prevu"] <= lecture["scenario_haut"]
    _sans_jargon(lecture)


def test_lecture_dirigeant_base_vide(client, db_session):
    lecture = client.get("/api/v1/analytics/forecast/sales/comparaison").json()["lecture_dirigeant"]
    assert lecture["disponible"] is False
    assert "Importez vos ventes" in lecture["conseils"][0]


def test_calculer_un_modele_reel(db_session, sans_prophet):
    """Chemin rapide du copilote : un seul modèle entraîné, même lecture métier."""
    _ventes(db_session)
    r = cp.calculer_un_modele(db_session, 21, "arima")
    assert [m["modele"] for m in r["modeles"]] == ["arima"] and len(r["modeles"][0]["points"]) == 21
    lecture = r["lecture_dirigeant"]
    assert lecture["disponible"] is True and lecture["modele_retenu"] == "arima"
    assert lecture["concordance"] is None  # une seule méthode : rien à comparer
    assert not cp.est_en_cache(db_session, 21)  # pas mis en cache
