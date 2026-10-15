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


# ── Lecture « dirigeant » (sans jargon) ─────────────────────────────────────
#
# Public de la plateforme : dirigeants, responsables et employés de PME, pas
# des data scientists. Les noms de modèles (Prophet, ARIMA, XGBoost) et les
# métriques (RMSE, MAE) restent disponibles pour le « mode expert » de
# l'interface et pour le rapport, mais la vue par défaut ne montre que des
# notions métier : combien je vais vendre, est-ce plus ou moins que d'habitude,
# à quel point je peux m'y fier, et quoi faire. Calcul DÉTERMINISTE (pas de
# LLM), comme `_interpreter` : reproductible et testable.

SEUIL_EVOLUTION = 0.05  # ±5 % vs le rythme récent : en dessous, activité « stable »


def _dispersion(valides: list[dict]) -> float | None:
    """Écart relatif entre la prévision totale la plus haute et la plus basse
    des modèles validés (même définition que dans `_synthese`)."""
    totaux = [m["indicateurs"]["total_prevu"] for m in valides]
    if len(totaux) < 2 or np.mean(totaux) <= 0:
        return None
    return (max(totaux) - min(totaux)) / float(np.mean(totaux))


def _scenarios(meilleur: dict) -> tuple[float, float] | None:
    """Scénario bas / scénario haut sur l'horizon, pour le modèle retenu.

    Avec intervalle de confiance (Prophet, ARIMA) : somme des bornes
    journalières. Sans (XGBoost) : prévision ± écart moyen de validation (MAE)
    par jour. Dans les deux cas, c'est une fourchette PRUDENTE (les écarts
    journaliers ne s'additionnent pas tous dans le même sens en pratique) —
    d'où le vocabulaire « scénario », pas « intervalle à 80 % »."""
    points = meilleur.get("points") or []
    if not points:
        return None
    if all(p.get("borne_basse") is not None and p.get("borne_haute") is not None for p in points):
        return (
            max(0.0, float(sum(p["borne_basse"] for p in points))),
            float(sum(p["borne_haute"] for p in points)),
        )
    mae = meilleur.get("mae_validation")
    if mae is None:
        return None
    total = float(sum(p["valeur_prevue"] for p in points))
    return max(0.0, total - mae * len(points)), total + mae * len(points)


def _lecture_dirigeant(
    modeles: list[dict], horizon: int, moyenne_validation: float, rupture: float | None
) -> dict:
    """Traduction de la meilleure prévision en langage métier (vue par
    défaut de l'interface et base des réponses du copilote)."""
    titre = f"Ventes attendues sur les {horizon} prochains jours"
    valides = [m for m in modeles if m["statut"] == "ok" and m["rmse_validation"] is not None]
    if not valides:
        return {
            "disponible": False,
            "titre": titre,
            "resume": "Pas encore assez d'historique de ventes pour une prévision fiable.",
            "conseils": ["Importez au moins quelques semaines de ventes pour obtenir une prévision."],
        }

    meilleur = min(valides, key=lambda m: m["rmse_validation"])
    ind = meilleur["indicateurs"]
    total, moyenne_jour = ind["total_prevu"], ind["moyenne_jour"]
    evolution = ind["ecart_niveau"]

    if evolution is None:
        sens_evolution, evolution_texte = None, None
    elif evolution > SEUIL_EVOLUTION:
        sens_evolution = "hausse"
        evolution_texte = f"{_pct(evolution)} par rapport à votre rythme des {FENETRE_REFERENCE_JOURS} derniers jours"
    elif evolution < -SEUIL_EVOLUTION:
        sens_evolution = "baisse"
        evolution_texte = f"{_pct(evolution)} par rapport à votre rythme des {FENETRE_REFERENCE_JOURS} derniers jours"
    else:
        sens_evolution = "stable"
        evolution_texte = f"proche de votre rythme des {FENETRE_REFERENCE_JOURS} derniers jours ({_pct(evolution)})"

    mae = meilleur.get("mae_validation")
    ecart_jour = float(mae) if mae is not None else None
    ecart_relatif = ecart_jour / moyenne_validation if ecart_jour is not None and moyenne_validation > 0 else None
    fiabilite = ind["fiabilite"]
    fiabilite_texte = (
        f"Sur les semaines passées, la prévision d'une journée s'est écartée en moyenne de "
        f"±{_fmt(ecart_jour)} des ventes réelles"
        + (f" (environ {ecart_relatif * 100:.0f} % des ventes d'une journée)" if ecart_relatif is not None else "")
        + "."
        if ecart_jour is not None
        else None
    )

    dispersion = _dispersion(valides)
    concordance = None if dispersion is None else ("incertaine" if dispersion > SEUIL_DIVERGENCE else "solide")
    scenarios = _scenarios(meilleur)

    conseils: list[str] = []
    if sens_evolution == "hausse":
        conseils.append("Ventes en hausse attendue : vérifiez que vos stocks suivront (alertes de stock ci-dessus).")
    elif sens_evolution == "baisse":
        conseils.append(
            "Ventes en baisse attendue : surveillez votre trésorerie et envisagez une relance de vos clients."
        )
    elif sens_evolution == "stable":
        conseils.append("Activité stable attendue : pas d'ajustement particulier nécessaire.")
    if fiabilite == "faible" or concordance == "incertaine":
        conseils.append(
            "Prévision peu sûre : restez prudent sur les engagements importants (grosses commandes, embauches)."
        )
    if rupture is not None and abs(rupture) > SEUIL_RUPTURE:
        conseils.append(
            f"Vos ventes récentes ont fortement changé ({_pct(rupture)} sur {FENETRE_REFERENCE_JOURS} jours) : "
            "vérifiez que toutes les dernières ventes ont bien été importées."
        )

    resume = f"Environ {_fmt(total)} de ventes attendues sur {horizon} jours"
    if evolution_texte:
        resume += f", {evolution_texte}"
    resume += "."

    return {
        "disponible": True,
        "titre": titre,
        "resume": resume,
        "total_prevu": total,
        "moyenne_jour": moyenne_jour,
        "evolution": evolution,
        "sens_evolution": sens_evolution,
        "evolution_texte": evolution_texte,
        "fiabilite": fiabilite,
        "fiabilite_texte": fiabilite_texte,
        "ecart_jour": ecart_jour,
        "concordance": concordance,
        "scenario_bas": scenarios[0] if scenarios else None,
        "scenario_haut": scenarios[1] if scenarios else None,
        "conseils": conseils,
        # Pour le mode expert uniquement : quel modèle a produit ces chiffres.
        "modele_retenu": meilleur["modele"],
    }


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
                "lecture_dirigeant": {
                    "disponible": False,
                    "titre": f"Ventes attendues sur les {horizon} prochains jours",
                    "resume": "Aucune vente importée : rien à prévoir pour l'instant.",
                    "conseils": ["Importez vos ventes (page Données) pour obtenir une prévision."],
                },
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
        "lecture_dirigeant": _lecture_dirigeant(modeles, horizon, moyenne_validation, rupture),
        "calcule_le": datetime.utcnow(),
    }


def est_en_cache(db: Session, horizon: int) -> bool:
    with _verrou:
        return (_signature(db), horizon) in _cache


def calculer_un_modele(db: Session, horizon: int, modele: str) -> dict:
    """Comme `comparer_modeles`, mais pour UN seul modèle (déjà désigné comme
    le plus fiable) — utilisé par le copilote pour un horizon non encore
    calculé (cf. `copilote_outils._comparaison_rapide`). Non mis en cache."""
    quotidien, n_plafonnes = forecasting_service._serie_ca_journalier(db)
    if quotidien.empty:
        return _calculer(db, horizon)
    y = quotidien["y"]
    reference = float(y.tail(FENETRE_REFERENCE_JOURS).mean())
    avant = y.iloc[-(FENETRE_REFERENCE_JOURS + 90):-FENETRE_REFERENCE_JOURS]
    rupture = (reference - float(avant.mean())) / float(avant.mean()) if len(avant) and avant.mean() > 0 else None
    _, test = forecasting_service._decoupe_train_test(quotidien)
    moyenne_validation = float(test["y"].mean()) if len(test) else reference
    modeles = [_un_modele(quotidien, horizon, modele, n_plafonnes, reference, moyenne_validation)]
    return {
        "horizon_jours": horizon,
        "historique": [],
        "modeles": modeles,
        "synthese": _synthese(modeles, rupture),
        "lecture_dirigeant": _lecture_dirigeant(modeles, horizon, moyenne_validation, rupture),
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
