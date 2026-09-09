"""Service métier — Prévision des ventes (Phase 3, module Maslaw).

Remplace `analytics_service.get_previsions_ventes_placeholder` (qui renvoyait
des zéros en attendant l'entraînement) par un entraînement RÉEL sur le CA
journalier agrégé depuis `transactions`.

Modèles disponibles (cf. README §5 "Choix des modèles & benchmarks") :
  - "prophet" (par défaut) : robuste à la saisonnalité, rapide à intégrer.
  - "arima"                : baseline de référence (statsmodels ARIMA(1,1,1)),
    utile pour comparer sur des séries courtes (cf. Cadrage Tâche #10,
    traçabilité méthodologique — comparer au moins deux modèles).
  - "xgboost"              : régression tabulaire sur variables exogènes
    dérivées du calendrier (jour de semaine, mois, week-end, jour férié
    camerounais via le package `holidays`) + variables autorégressives
    (lag J-1, lag J-7, moyenne mobile 7 jours). Ce n'est PAS le schéma de
    "promotions" évoqué initialement comme prérequis (ce champ n'existe
    dans aucune donnée PME réelle qu'on nous fournirait à l'avance) — les
    features de calendrier, elles, sont déductibles de n'importe quelle
    date sans dépendre d'une colonne supplémentaire, ce qui débloque
    XGBoost sans modifier le schéma. Contrepartie : pas d'intervalle de
    confiance natif (contrairement à Prophet/ARIMA) et prévision multi-jours
    par récursion (chaque jour prédit alimente les lags du suivant, donc
    l'incertitude s'accumule avec l'horizon — signalé en avertissement
    au-delà de 14 jours).

Validation (jamais de fuite de données futures) :
  - Découpage temporel train/test : les derniers jours de l'historique
    servent de test (jamais un split aléatoire, qui mélangerait passé et
    futur — piège classique en série temporelle).
  - RMSE et MAE calculés sur cette période de test.
  - Le modèle FINAL livré en prévision est ensuite ré-entraîné sur
    l'intégralité de l'historique (le split train/test ne sert QU'À mesurer
    RMSE/MAE, jamais à produire la prévision elle-même — sinon on prive le
    modèle final des jours les plus récents, les plus informatifs).

Si l'historique est trop court (< 7 jours de ventes distinctes, ou < 14
jours pour XGBoost qui a besoin de lags J-7 exploitables), aucun modèle
n'est entraîné : une baseline naïve (moyenne journalière) est renvoyée avec
un `modele_utilise` explicite, pour ne jamais présenter un modèle réputé
fiable alors qu'il n'aurait pas assez de signal pour être validé.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.preprocessing import winsoriser
from app.models import Transaction, Tresorerie

MODELES_DISPONIBLES = {"prophet", "arima", "xgboost"}
HISTORIQUE_MIN_JOURS = 7
# XGBoost a besoin d'assez de lignes NON-NaN après création du lag J-7
# (les 7 premiers jours de la série n'ont pas de lag_7 valide) pour qu'un
# découpage train/test garde un minimum de points d'entraînement exploitable.
HISTORIQUE_MIN_JOURS_XGBOOST = 14
HORIZON_AVERTISSEMENT_XGBOOST = 14
WINSOR_PERCENTILE = 99.0


def _serie_ca_journalier(db: Session) -> tuple[pd.DataFrame, int]:
    """CA journalier agrégé depuis `transactions` (retours exclus), avec
    les jours sans vente complétés à 0 — Prophet/ARIMA exigent une série
    régulière, sans "trous" de calendrier.

    PRÉTRAITEMENT : la série est ensuite plafonnée (winsorizing, cf.
    `app.core.preprocessing.winsoriser`) avant d'être renvoyée — un SEUL
    jour de vente aberrant (ex. quantité saisie en trop, faute de frappe sur
    un prix) suffit à faire exploser le RMSE et à produire des prévisions
    délirantes (vérifié par test : RMSE multiplié par ~100, intervalles de
    confiance à 6 chiffres). Retourne (série, nombre de jours plafonnés)
    pour que l'appelant puisse le signaler dans la réponse de l'API.
    """
    lignes = (
        db.query(Transaction.date_transaction, Transaction.quantite, Transaction.prix_unitaire)
        .filter(~Transaction.est_retour)
        .all()
    )
    if not lignes:
        return pd.DataFrame(columns=["ds", "y"]), 0

    df = pd.DataFrame(lignes, columns=["date_transaction", "quantite", "prix_unitaire"])
    df["jour"] = pd.to_datetime(df["date_transaction"]).dt.normalize()
    df["ca"] = df["quantite"].astype(float) * df["prix_unitaire"].astype(float)
    quotidien = df.groupby("jour", as_index=False)["ca"].sum().rename(columns={"jour": "ds", "ca": "y"})
    quotidien = quotidien.sort_values("ds").reset_index(drop=True)

    calendrier_complet = pd.date_range(quotidien["ds"].min(), quotidien["ds"].max(), freq="D")
    quotidien = (
        quotidien.set_index("ds")
        .reindex(calendrier_complet, fill_value=0.0)
        .rename_axis("ds")
        .reset_index()
    )

    # Le plafonnement porte sur les jours à CA > 0 uniquement : les jours
    # sans vente (0, ajoutés ci-dessus pour compléter le calendrier) ne
    # doivent jamais entrer dans le calcul du plafond, sous peine de le
    # tirer vers le bas de façon artificielle.
    jours_avec_ventes = quotidien["y"] > 0
    serie_plafonnee, n_plafonnes = winsoriser(quotidien.loc[jours_avec_ventes, "y"], WINSOR_PERCENTILE)
    quotidien.loc[jours_avec_ventes, "y"] = serie_plafonnee
    return quotidien, n_plafonnes


def _decoupe_train_test(quotidien: pd.DataFrame, ratio_test: float = 0.2, min_jours_test: int = 3):
    n = len(quotidien)
    n_test = max(min_jours_test, int(n * ratio_test))
    n_test = min(n_test, n - 3)  # garder au moins 3 points d'entraînement
    if n_test <= 0:
        return quotidien, quotidien.iloc[0:0]
    return quotidien.iloc[:-n_test].copy(), quotidien.iloc[-n_test:].copy()


def _rmse_mae(y_vrai, y_predit) -> tuple[float, float]:
    y_vrai = np.asarray(y_vrai, dtype=float)
    y_predit = np.asarray(y_predit, dtype=float)
    rmse = float(np.sqrt(np.mean((y_vrai - y_predit) ** 2)))
    mae = float(np.mean(np.abs(y_vrai - y_predit)))
    return rmse, mae


def _previsions_prophet(quotidien: pd.DataFrame, horizon_jours: int, clamp_non_negatif: bool = True):
    from prophet import Prophet

    logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

    train, test = _decoupe_train_test(quotidien)
    rmse = mae = None
    if len(test) > 0:
        modele_validation = Prophet(
            daily_seasonality=False,
            weekly_seasonality=len(train) >= 14,
            yearly_seasonality=False,
        )
        modele_validation.fit(train[["ds", "y"]])
        prediction_test = modele_validation.predict(test[["ds"]])
        rmse, mae = _rmse_mae(test["y"].values, prediction_test["yhat"].values)

    # Ré-entraînement sur TOUT l'historique pour la prévision finale livrée
    # (cf. docstring module : le split train/test ne sert qu'à valider).
    modele_final = Prophet(
        daily_seasonality=False,
        weekly_seasonality=len(quotidien) >= 14,
        yearly_seasonality=False,
    )
    modele_final.fit(quotidien[["ds", "y"]])
    futur = modele_final.make_future_dataframe(periods=horizon_jours)
    prevision = modele_final.predict(futur).tail(horizon_jours)

    plancher = 0.0 if clamp_non_negatif else float("-inf")
    points = [
        {
            "date_prevision": ligne.ds.date(),
            "valeur_prevue": max(plancher, float(ligne.yhat)),
            "borne_basse": max(plancher, float(ligne.yhat_lower)),
            "borne_haute": float(ligne.yhat_upper),
        }
        for ligne in prevision.itertuples(index=False)
    ]
    return points, rmse, mae


def _previsions_arima(quotidien: pd.DataFrame, horizon_jours: int, clamp_non_negatif: bool = True):
    from statsmodels.tsa.arima.model import ARIMA

    train, test = _decoupe_train_test(quotidien)
    rmse = mae = None
    if len(test) > 0:
        modele_validation = ARIMA(train["y"].to_numpy(), order=(1, 1, 1)).fit()
        prediction_test = modele_validation.forecast(steps=len(test))
        rmse, mae = _rmse_mae(test["y"].values, prediction_test)

    modele_final = ARIMA(quotidien["y"].to_numpy(), order=(1, 1, 1)).fit()
    resultat = modele_final.get_forecast(steps=horizon_jours)
    moyenne = resultat.predicted_mean
    intervalle = resultat.conf_int(alpha=0.2)  # intervalle de confiance à 80%

    plancher = 0.0 if clamp_non_negatif else float("-inf")
    dernier_jour = quotidien["ds"].max()
    points = []
    for i in range(horizon_jours):
        date_prevision = (dernier_jour + pd.Timedelta(days=i + 1)).date()
        points.append(
            {
                "date_prevision": date_prevision,
                "valeur_prevue": max(plancher, float(moyenne[i])),
                "borne_basse": max(plancher, float(intervalle[i][0])),
                "borne_haute": float(intervalle[i][1]),
            }
        )
    return points, rmse, mae


def _calendrier_feries(annee_min: int, annee_max: int):
    """Jours fériés camerounais (fixes + mobiles : Pâques, Ascension, Aïd
    el-Fitr, Aïd el-Adha, Mawlid) sur toutes les années couvertes par
    l'historique ET l'horizon de prévision — sinon les jours fériés futurs
    (hors plage) seraient silencieusement traités comme des jours normaux."""
    import holidays

    return holidays.Cameroon(years=range(annee_min, annee_max + 1))


def _construire_features_calendrier(quotidien: pd.DataFrame, cal_feries) -> pd.DataFrame:
    """Variables exogènes dérivées de la seule date (jour de semaine, mois,
    week-end, jour férié) + variables autorégressives (lag J-1, lag J-7,
    moyenne mobile 7j calculée sur les jours PRÉCÉDENTS uniquement — jamais
    le jour courant, sous peine de fuite de données)."""
    df = quotidien.copy()
    df["jour_semaine"] = df["ds"].dt.dayofweek
    df["mois"] = df["ds"].dt.month
    df["est_weekend"] = df["jour_semaine"].isin([5, 6]).astype(int)
    df["est_ferie"] = df["ds"].dt.date.map(lambda d: int(d in cal_feries))
    df["lag_1"] = df["y"].shift(1)
    df["lag_7"] = df["y"].shift(7)
    df["moyenne_mobile_7"] = df["y"].shift(1).rolling(window=7, min_periods=1).mean()
    return df


FEATURES_XGBOOST = [
    "jour_semaine",
    "mois",
    "est_weekend",
    "est_ferie",
    "lag_1",
    "lag_7",
    "moyenne_mobile_7",
]

NOMS_LISIBLES_FEATURES = {
    "jour_semaine": "Jour de la semaine",
    "mois": "Mois",
    "est_weekend": "Week-end",
    "est_ferie": "Jour férié",
    "lag_1": "Valeur de la veille (J-1)",
    "lag_7": "Valeur à J-7 (même jour, semaine précédente)",
    "moyenne_mobile_7": "Moyenne mobile 7 jours",
}


def _calculer_explicabilite_xgboost(modele_final, features: pd.DataFrame, top_n: int = 3) -> list[dict]:
    """Tâche #3 : SHAP TreeExplainer sur XGBoost déjà entraîné (aucun coût
    d'entraînement supplémentaire). Importance = moyenne de |valeur SHAP|
    par variable — lecture standard indépendante du signe."""
    import shap

    explainer = shap.TreeExplainer(modele_final)
    valeurs_shap = explainer.shap_values(features[FEATURES_XGBOOST])
    importance_moyenne = np.abs(valeurs_shap).mean(axis=0)

    classement = sorted(
        zip(FEATURES_XGBOOST, importance_moyenne), key=lambda item: item[1], reverse=True
    )[:top_n]
    return [
        {"variable": NOMS_LISIBLES_FEATURES.get(nom, nom), "importance": float(valeur)}
        for nom, valeur in classement
    ]


def _previsions_xgboost(quotidien: pd.DataFrame, horizon_jours: int, clamp_non_negatif: bool = True):
    from xgboost import XGBRegressor

    cal_feries = _calendrier_feries(
        quotidien["ds"].min().year, quotidien["ds"].max().year + (horizon_jours // 365) + 1
    )
    features = _construire_features_calendrier(quotidien, cal_feries)
    # Les 7 premiers jours n'ont pas de lag_7 valide (pas assez d'historique
    # en amont) -> exclus de l'entraînement plutôt que remplis par une
    # valeur arbitraire qui biaiserait le modèle.
    features = features.dropna(subset=["lag_1", "lag_7"]).reset_index(drop=True)

    train, test = _decoupe_train_test(features)
    rmse = mae = None
    if len(test) > 0 and len(train) >= 3:
        modele_validation = XGBRegressor(
            n_estimators=200, max_depth=4, learning_rate=0.05, random_state=42
        )
        modele_validation.fit(train[FEATURES_XGBOOST], train["y"])
        prediction_test = modele_validation.predict(test[FEATURES_XGBOOST])
        rmse, mae = _rmse_mae(test["y"].values, prediction_test)

    modele_final = XGBRegressor(n_estimators=200, max_depth=4, learning_rate=0.05, random_state=42)
    modele_final.fit(features[FEATURES_XGBOOST], features["y"])
    explicabilite = _calculer_explicabilite_xgboost(modele_final, features)

    # Prévision multi-jours par récursion : XGBoost n'a pas d'équivalent
    # natif du forecast multi-step de Prophet/ARIMA. Chaque jour prédit
    # alimente les lags du jour suivant — donc l'erreur peut s'accumuler
    # avec l'horizon (signalé côté appelant au-delà de 14 jours).
    historique_y = list(quotidien["y"].to_numpy())
    dernier_jour = quotidien["ds"].max()
    points = []
    for i in range(horizon_jours):
        date_prevision = dernier_jour + pd.Timedelta(days=i + 1)
        ligne = pd.DataFrame(
            [
                {
                    "jour_semaine": date_prevision.dayofweek,
                    "mois": date_prevision.month,
                    "est_weekend": int(date_prevision.dayofweek in (5, 6)),
                    "est_ferie": int(date_prevision.date() in cal_feries),
                    "lag_1": historique_y[-1],
                    "lag_7": historique_y[-7] if len(historique_y) >= 7 else historique_y[0],
                    "moyenne_mobile_7": float(np.mean(historique_y[-7:])),
                }
            ]
        )
        valeur_brute = float(modele_final.predict(ligne[FEATURES_XGBOOST])[0])
        valeur_prevue = max(0.0, valeur_brute) if clamp_non_negatif else valeur_brute
        historique_y.append(valeur_prevue)
        points.append(
            {
                "date_prevision": date_prevision.date(),
                "valeur_prevue": valeur_prevue,
                # Pas d'intervalle de confiance natif pour une régression
                # ponctuelle XGBoost (contrairement à Prophet/ARIMA) —
                # mieux vaut ne rien afficher que fabriquer une fausse
                # marge d'incertitude.
                "borne_basse": None,
                "borne_haute": None,
            }
        )
    return points, rmse, mae, explicabilite


def _generer_prevision(
    quotidien: pd.DataFrame,
    horizon_jours: int,
    modele: str,
    n_jours_plafonnes: int = 0,
    clamp_non_negatif: bool = True,
) -> dict:
    """Cœur commun de prévision, indépendant du domaine métier (ventes OU
    trésorerie) : reçoit une série quotidienne déjà construite (colonnes
    ds, y), applique le modèle demandé et gère le repli baseline.

    Extrait de `get_previsions_ventes` en tâche #1 de la roadmap de
    clôture, pour être réutilisé tel quel par `get_previsions_tresorerie`
    plutôt que de dupliquer la logique de validation/dispatch.

    `clamp_non_negatif` : True pour le CA (une vente ne peut pas être
    négative), False pour un solde de trésorerie (un solde négatif est un
    signal réel — l'alerte de risque de déficit, cf. tâche #2, en dépend
    directement : le plafonner à 0 masquerait justement ce qu'on veut
    détecter).
    """
    if modele not in MODELES_DISPONIBLES:
        raise ValueError(f"Modèle inconnu : '{modele}' (attendu : {sorted(MODELES_DISPONIBLES)})")

    n_jours_historique = len(quotidien)
    avertissements: list[str] = []
    explicabilite: list[dict] = []
    if n_jours_plafonnes:
        avertissements.append(
            f"{n_jours_plafonnes} jour(s) plafonné(s) (prétraitement anti-valeurs-extrêmes) "
            "avant l'ajustement du modèle — évite qu'une seule valeur isolée aberrante ne "
            "fausse toute la prévision."
        )

    seuil_historique = HISTORIQUE_MIN_JOURS_XGBOOST if modele == "xgboost" else HISTORIQUE_MIN_JOURS
    if n_jours_historique < seuil_historique:
        moyenne = float(quotidien["y"].mean()) if n_jours_historique else 0.0
        dernier_jour = quotidien["ds"].max() if n_jours_historique else pd.Timestamp.today().normalize()
        points = [
            {
                "date_prevision": (dernier_jour + pd.Timedelta(days=i + 1)).date(),
                "valeur_prevue": moyenne,
                "borne_basse": None,
                "borne_haute": None,
            }
            for i in range(horizon_jours)
        ]
        return {
            "modele_utilise": f"Moyenne naïve (historique insuffisant : {n_jours_historique}j < {seuil_historique}j requis pour '{modele}')",
            "horizon_jours": horizon_jours,
            "points": points,
            "rmse_validation": None,
            "mae_validation": None,
            "avertissements": avertissements,
            "explicabilite": [],
        }

    if modele == "arima":
        points, rmse, mae = _previsions_arima(quotidien, horizon_jours, clamp_non_negatif)
        nom_modele = "ARIMA(1,1,1)"
    elif modele == "xgboost":
        points, rmse, mae, explicabilite = _previsions_xgboost(quotidien, horizon_jours, clamp_non_negatif)
        nom_modele = "XGBoost"
        avertissements.append(
            "XGBoost ne fournit pas d'intervalle de confiance natif (borne_basse/borne_haute "
            "non renseignées) — contrairement à Prophet/ARIMA."
        )
        if horizon_jours > HORIZON_AVERTISSEMENT_XGBOOST:
            avertissements.append(
                f"Prévision au-delà de {HORIZON_AVERTISSEMENT_XGBOOST} jours obtenue par récursion "
                "(chaque jour prédit alimente les lags du suivant) : l'erreur peut s'accumuler avec "
                "l'horizon. Prophet ou ARIMA restent préférables pour des horizons longs."
            )
    else:
        points, rmse, mae = _previsions_prophet(quotidien, horizon_jours, clamp_non_negatif)
        nom_modele = "Prophet"

    return {
        "modele_utilise": nom_modele,
        "horizon_jours": horizon_jours,
        "points": points,
        "rmse_validation": rmse,
        "mae_validation": mae,
        "avertissements": avertissements,
        "explicabilite": explicabilite,
    }


def get_previsions_ventes(db: Session, horizon_jours: int = 30, modele: str = "prophet") -> dict:
    quotidien, n_jours_plafonnes = _serie_ca_journalier(db)
    return _generer_prevision(quotidien, horizon_jours, modele, n_jours_plafonnes, clamp_non_negatif=True)


def _serie_solde_journalier(db: Session) -> tuple[pd.DataFrame, int]:
    """Solde de trésorerie cumulé, un point par jour — même contrat que
    `_serie_ca_journalier` (colonnes ds, y) pour réutiliser telle quelle la
    mécanique de prévision de `_generer_prevision`.

    Différence clé avec le CA : un jour SANS mouvement ne vaut pas 0 (le
    solde ne se remet pas à zéro chaque jour, contrairement au CA
    journalier) — il doit reporter le solde de la veille. On calcule donc
    le mouvement NET (encaissements - décaissements) par jour, complété à
    0 sur le calendrier, puis on prend la somme cumulée : un jour sans
    mouvement a un delta nul, donc le cumul reporte naturellement le solde
    précédent.

    Pas de winsorizing ici (contrairement au CA) : un solde qui varie
    fortement d'un jour à l'autre (gros encaissement client, paiement
    fournisseur important) est un signal réel de trésorerie, pas une
    erreur de saisie à plafonner.
    """
    lignes = db.query(Tresorerie.date_mouvement, Tresorerie.type_mouvement, Tresorerie.montant).all()
    if not lignes:
        return pd.DataFrame(columns=["ds", "y"]), 0

    df = pd.DataFrame(lignes, columns=["date_mouvement", "type_mouvement", "montant"])
    df["ds"] = pd.to_datetime(df["date_mouvement"]).dt.normalize()
    df["delta"] = np.where(
        df["type_mouvement"] == "encaissement",
        df["montant"].astype(float),
        -df["montant"].astype(float),
    )
    quotidien_delta = df.groupby("ds", as_index=False)["delta"].sum().sort_values("ds").reset_index(drop=True)

    calendrier_complet = pd.date_range(quotidien_delta["ds"].min(), quotidien_delta["ds"].max(), freq="D")
    quotidien_delta = (
        quotidien_delta.set_index("ds")
        .reindex(calendrier_complet, fill_value=0.0)
        .rename_axis("ds")
        .reset_index()
    )
    quotidien_delta["y"] = quotidien_delta["delta"].cumsum()
    return quotidien_delta[["ds", "y"]], 0


def get_previsions_tresorerie(db: Session, horizon_jours: int = 30, modele: str = "prophet") -> dict:
    """Tâche #1 de la roadmap de clôture — tient l'engagement "trésorerie
    prévisionnelle" du cahier des charges (§3.2), jusqu'ici réduit à un
    solde instantané (`analytics_service.get_kpis_globaux`).

    `clamp_non_negatif=False` : contrairement au CA, un solde prévu
    négatif est un résultat légitime et même le signal recherché par la
    tâche #2 (alerte de risque de déficit)."""
    quotidien, n_jours_plafonnes = _serie_solde_journalier(db)
    return _generer_prevision(quotidien, horizon_jours, modele, n_jours_plafonnes, clamp_non_negatif=False)
