"""Prévision des ventes par les 3 modèles à la fois, avec interprétation.

Remplace, côté interface, le choix manuel « modèle + bouton Générer » :
  - les 3 modèles (Prophet, ARIMA, XGBoost) sont entraînés EN PARALLÈLE
    sur la même série (`_serie_ca_journalier`), avec le même protocole de
    validation que `forecasting_service` (découpage temporel, RMSE/MAE,
    ré-entraînement sur tout l'historique) ;
  - chaque modèle reçoit une interprétation en français (niveau prévu,
    tendance, fiabilité, incertitude) calculée de façon DÉTERMINISTE à
    partir des chiffres — pas de LLM, donc reproductible et testable ;
  - une synthèse désigne le modèle le plus fiable (RMSE de validation le
    plus bas) et signale si les modèles divergent.

Arrière-plan et cache : le résultat est mis en cache (mémoire du processus
API) sous une « signature » des données (nombre de ventes, dernière date,
CA total). Il est recalculé seulement si les données changent. Le calcul
est lancé automatiquement après chaque import de ventes
(`precalculer_en_arriere_plan`), si bien qu'en ouvrant la page Ventes &
Stocks le résultat est en général déjà prêt. Deux demandes simultanées pour
les mêmes données attendent le même calcul au lieu de le lancer deux fois.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime

import numpy as np
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Transaction
from app.services import forecasting_service

log = logging.getLogger(__name__)

MODELES = ("prophet", "arima", "xgboost")
NOMS = {"prophet": "Prophet", "arima": "ARIMA", "xgboost": "XGBoost"}
HISTORIQUE_AFFICHE_JOURS = 90  # jours réels montrés avant la prévision
FENETRE_REFERENCE_JOURS = 30  # « niveau récent » auquel on compare la prévision
SEUIL_TENDANCE = 0.05  # ±5 % : en dessous, la tendance est dite stable
SEUIL_DIVERGENCE = 0.20  # écart entre modèles au-delà duquel on alerte
SEUIL_RUPTURE = 0.5  # variation du niveau récent (vs 90 j précédents) jugée anormale

_cache: dict[tuple, dict] = {}
_en_cours: dict[tuple, Future] = {}
_verrou = threading.Lock()
_executeur = ThreadPoolExecutor(max_workers=3, thread_name_prefix="prevision")


# ── Signature des données (clé de cache) ────────────────────────────────────


def _signature(db: Session) -> tuple:
    n, derniere, total = (
        db.query(
            func.count(Transaction.transaction_id),
            func.max(Transaction.date_transaction),
            func.sum(Transaction.quantite * Transaction.prix_unitaire),
        )
        .filter(~Transaction.est_retour)
        .one()
    )
    return (int(n or 0), str(derniere), round(float(total or 0), 2))


# ── Interprétation ──────────────────────────────────────────────────────────


def _fmt(x: float) -> str:
    """12345.6 -> « 12 346 » (espaces fines insécables, sans décimales)."""
    return f"{x:,.0f}".replace(",", " ")


def _pct(x: float) -> str:
    return f"{x * 100:+.0f} %".replace("+-", "−").replace("-", "−")


def _interpreter(res: dict, reference_jour: float, moyenne_validation: float) -> tuple[list[str], dict]:
    """Phrases d'interprétation + indicateurs chiffrés pour un modèle.

    `reference_jour` : CA journalier moyen des 30 derniers jours réels (le
    niveau auquel on compare la prévision). `moyenne_validation` : CA
    journalier moyen de la période de validation, à laquelle on rapporte le
    RMSE (erreur relative = RMSE / moyenne réelle de la période où l'erreur a
    été mesurée — la rapporter à une autre période fausserait le pourcentage)."""
    valeurs = np.array([p["valeur_prevue"] for p in res["points"]], dtype=float)
    horizon = len(valeurs)
    total = float(valeurs.sum())
    moyenne = float(valeurs.mean()) if horizon else 0.0
    ecart_niveau = (moyenne - reference_jour) / reference_jour if reference_jour > 0 else None

    k = max(1, min(7, horizon // 3))
    debut, fin = float(valeurs[:k].mean()), float(valeurs[-k:].mean())
    tendance = (fin - debut) / debut if debut > 0 else 0.0
    sens = "hausse" if tendance > SEUIL_TENDANCE else "baisse" if tendance < -SEUIL_TENDANCE else "stable"

    rmse = res.get("rmse_validation")
    erreur_relative = rmse / moyenne_validation if rmse is not None and moyenne_validation > 0 else None
    fiabilite = (
        None if erreur_relative is None
        else "bonne" if erreur_relative < 0.25
        else "moyenne" if erreur_relative < 0.5
        else "faible"
    )

    largeurs = [
        p["borne_haute"] - p["borne_basse"]
        for p in res["points"]
        if p.get("borne_haute") is not None and p.get("borne_basse") is not None
    ]
    incertitude = float(np.mean(largeurs)) / moyenne if largeurs and moyenne > 0 else None

    phrases = []
    naif = res["modele_utilise"].startswith("Moyenne naïve")
    if naif:
        phrases.append(
            "Historique trop court pour entraîner ce modèle : la prévision est une simple "
            "moyenne journalière, à ne pas utiliser pour décider."
        )
    niveau = f"CA prévu sur {horizon} jours : {_fmt(total)}, soit {_fmt(moyenne)} par jour en moyenne"
    if ecart_niveau is not None:
        niveau += (
            f" ({_pct(ecart_niveau)} par rapport aux {FENETRE_REFERENCE_JOURS} derniers jours, "
            f"{_fmt(reference_jour)} par jour)"
        )
    phrases.append(niveau + ".")
    if not naif:
        phrases.append(
            "Tendance stable sur l'horizon."
            if sens == "stable"
            else f"Tendance à la {sens} : {_pct(tendance)} entre la première et la dernière semaine prévues."
        )
    if fiabilite is not None:
        phrases.append(
            f"Fiabilité {fiabilite} : sur la période de validation, l'erreur type (RMSE) est de "
            f"{_fmt(rmse)}, soit {erreur_relative * 100:.0f} % du CA journalier moyen de cette période."
        )
    if incertitude is not None:
        phrases.append(
            f"Intervalle de confiance moyen : ±{incertitude * 50:.0f} % autour de la prévision"
            + (" — large : à lire comme un ordre de grandeur." if incertitude > 1 else ".")
        )
    elif not naif and res["points"]:
        phrases.append("Pas d'intervalle de confiance pour ce modèle (prévision ponctuelle).")

    indicateurs = {
        "total_prevu": total,
        "moyenne_jour": moyenne,
        "ecart_niveau": ecart_niveau,
        "tendance": tendance,
        "sens": sens,
        "erreur_relative": erreur_relative,
        "fiabilite": fiabilite,
    }
    return phrases, indicateurs


def _synthese(modeles: list[dict], rupture: float | None = None) -> dict:
    valides = [m for m in modeles if m["statut"] == "ok" and m["rmse_validation"] is not None]
    if not valides:
        return {
            "meilleur_modele": None,
            "texte": ["Aucun modèle n'a pu être validé (historique insuffisant) : prévisions indicatives."],
        }
    classes = sorted(valides, key=lambda m: m["rmse_validation"])
    meilleur = classes[0]
    texte = []
    phrase = f"Modèle le plus fiable sur vos données : {meilleur['nom']} (RMSE {_fmt(meilleur['rmse_validation'])}"
    if len(classes) > 1:
        gain = 1 - meilleur["rmse_validation"] / classes[1]["rmse_validation"]
        phrase += f", {gain * 100:.0f} % d'erreur en moins que {classes[1]['nom']}"
    texte.append(phrase + ").")
    horizon = len(meilleur.get("points") or [])
    texte.append(
        f"CA prévu sur {horizon} jours — "
        + " · ".join(f"{m['nom']} : {_fmt(m['indicateurs']['total_prevu'])}" for m in valides)
        + "."
    )

    totaux = [m["indicateurs"]["total_prevu"] for m in valides]
    if len(totaux) > 1 and np.mean(totaux) > 0:
        dispersion = (max(totaux) - min(totaux)) / float(np.mean(totaux))
        if dispersion > SEUIL_DIVERGENCE:
            texte.append(
                f"Les modèles divergent ({dispersion * 100:.0f} % d'écart entre le plus haut et le "
                f"plus bas) : privilégier {meilleur['nom']} et traiter les autres comme des scénarios."
            )
        else:
            texte.append(f"Les modèles concordent (écart de {dispersion * 100:.0f} %) : prévision robuste.")
    sens = {m["indicateurs"]["sens"] for m in valides}
    if len(sens) == 1:
        texte.append(f"Les {len(valides)} modèles s'accordent sur une tendance {sens.pop()}.")
    if rupture is not None and abs(rupture) > SEUIL_RUPTURE:
        texte.append(
            f"Rupture récente : le CA des {FENETRE_REFERENCE_JOURS} derniers jours est {_pct(rupture)} "
            "par rapport aux 90 jours précédents. Vérifier que les dernières ventes sont complètes. "
            "Les modèles y réagissent différemment : ARIMA et XGBoost suivent surtout le niveau récent, "
            "Prophet la tendance de long terme — d'où leurs écarts."
        )
    return {"meilleur_modele": meilleur["modele"], "texte": texte}


# ── Calcul ──────────────────────────────────────────────────────────────────


def _un_modele(quotidien, horizon: int, modele: str, n_plafonnes: int, reference: float,
               moyenne_validation: float) -> dict:
    try:
        res = forecasting_service._generer_prevision(quotidien.copy(), horizon, modele, n_plafonnes)
    except Exception as exc:  # un modèle en échec ne doit pas masquer les deux autres
        log.exception("Prévision %s en échec", modele)
        return {"modele": modele, "nom": NOMS[modele], "statut": "erreur", "erreur": str(exc),
                "modele_utilise": NOMS[modele], "horizon_jours": horizon, "points": [], "rmse_validation": None,
                "mae_validation": None, "avertissements": [], "interpretation": [], "indicateurs": {}}
    phrases, indicateurs = _interpreter(res, reference, moyenne_validation)
    return {
        "modele": modele,
        "nom": NOMS[modele],
        "statut": "ok",
        "erreur": None,
        **res,
        "interpretation": phrases,
        "indicateurs": indicateurs,
    }


def _calculer(db: Session, horizon: int) -> dict:
    quotidien, n_plafonnes = forecasting_service._serie_ca_journalier(db)
    if quotidien.empty:
        return {"horizon_jours": horizon, "historique": [], "modeles": [],
                "synthese": {"meilleur_modele": None, "texte": ["Aucune vente importée : rien à prévoir."]},
                "calcule_le": datetime.utcnow()}
    y = quotidien["y"]
    reference = float(y.tail(FENETRE_REFERENCE_JOURS).mean())
    avant = y.iloc[-(FENETRE_REFERENCE_JOURS + 90):-FENETRE_REFERENCE_JOURS]
    rupture = (reference - float(avant.mean())) / float(avant.mean()) if len(avant) and avant.mean() > 0 else None
    _, test = forecasting_service._decoupe_train_test(quotidien)
    moyenne_validation = float(test["y"].mean()) if len(test) else reference
    futurs = [
        _executeur.submit(_un_modele, quotidien, horizon, m, n_plafonnes, reference, moyenne_validation)
        for m in MODELES
    ]
    modeles = [f.result() for f in futurs]
    historique = [
        {"date": ligne.ds.date(), "valeur": float(ligne.y)}
        for ligne in quotidien.tail(HISTORIQUE_AFFICHE_JOURS).itertuples(index=False)
    ]
    return {
        "horizon_jours": horizon,
        "historique": historique,
        "modeles": modeles,
        "synthese": _synthese(modeles, rupture),
        "calcule_le": datetime.utcnow(),
    }


def comparer_modeles(db: Session, horizon: int = 30) -> dict:
    """Résultat des 3 modèles pour les données actuelles (depuis le cache si
    elles n'ont pas changé ; sinon calculé, ou attendu s'il est en cours)."""
    cle = (_signature(db), horizon)
    with _verrou:
        if cle in _cache:
            return {**_cache[cle], "depuis_cache": True}
        futur = _en_cours.get(cle)
        proprietaire = futur is None
        if proprietaire:
            futur = Future()
            _en_cours[cle] = futur
    if not proprietaire:
        return {**futur.result(), "depuis_cache": True}
    try:
        resultat = _calculer(db, horizon)
        with _verrou:
            # Une seule version des données gardée (mémoire bornée) ; les autres
            # horizons calculés sur ces mêmes données restent en cache.
            for ancienne in [k for k in _cache if k[0] != cle[0]]:
                del _cache[ancienne]
            _cache[cle] = resultat
        futur.set_result(resultat)
        return {**resultat, "depuis_cache": False}
    except BaseException as exc:
        futur.set_exception(exc)
        raise
    finally:
        with _verrou:
            _en_cours.pop(cle, None)


def precalculer_en_arriere_plan(moteur, horizon: int = 30) -> None:
    """Tâche d'arrière-plan lancée après un import de ventes : calcule les 3
    prévisions avec sa propre session (sur le même moteur que la requête),
    pour que la page soit prête quand l'utilisateur l'ouvre."""
    from sqlalchemy.orm import sessionmaker

    db = sessionmaker(bind=moteur)()
    try:
        comparer_modeles(db, horizon)
    except Exception:  # jamais d'erreur visible : la page recalculera à la demande
        log.exception("Pré-calcul des prévisions en échec")
    finally:
        db.close()
