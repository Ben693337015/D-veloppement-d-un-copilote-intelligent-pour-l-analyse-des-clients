"""Outils de calcul du copilote (sous-projet Maslaw : copilote, ventes, trésorerie, stocks).

Pourquoi des outils en plus de l'agent SQL (`sql_agent`) : l'agent SQL ne
fait que LIRE la base. Un copilote d'aide à la décision doit aussi CALCULER
à la demande — prévoir sur un horizon choisi, simuler un scénario « et si »,
recommander une quantité à commander. Ces calculs appellent les services
métier existants (prévisions, trésorerie, stocks) ; aucune écriture en base.

Protocole indépendant du fournisseur LLM (même principe que l'agent SQL,
cf. sa docstring) : pas de « function calling » natif, dont le format diffère
entre Anthropic, OpenAI, Groq, Google et OpenRouter. Le LLM écrit, dans sa
réponse de planification, un bloc :

    ```outil
    {"nom": "prevision_ventes", "parametres": {"horizon_jours": 60}}
    ```

L'application lit ce bloc (`extraire_appels`), VALIDE les paramètres avec
Pydantic (bornes, valeurs autorisées : un LLM peut envoyer n'importe quoi),
exécute l'outil (`executer`) et renvoie un résultat déjà formulé en langage
métier, que le LLM reformule pour l'utilisateur.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import StockProduit, Transaction
from app.services import analytics_service, comparaison_previsions, forecasting_service

APPELS_MAX = 3  # outils exécutés au plus par question
FENETRE_VENTES_JOURS = 30  # rythme de vente récent utilisé pour le réapprovisionnement
DELAI_LIVRAISON_DEFAUT = 7
PRODUITS_MAX = 15  # produits listés au plus par l'outil de réapprovisionnement

_BLOC_OUTIL = re.compile(r"```outil\s*(.*?)```", re.IGNORECASE | re.DOTALL)


class OutilInvalide(ValueError):
    """Nom d'outil inconnu ou paramètres refusés — renvoyé au LLM en texte."""


# ── Paramètres (validés avant tout calcul) ──────────────────────────────────


class ParamsPrevisionVentes(BaseModel):
    horizon_jours: int = Field(30, ge=7, le=90)
    methode: Literal["auto", "prophet", "arima", "xgboost"] = "auto"


class ParamsTresorerie(BaseModel):
    horizon_jours: int = Field(30, ge=7, le=90)
    seuil_critique: float = 0.0


class ParamsSimulation(BaseModel):
    variation_pct: float = Field(ge=-90, le=200)
    horizon_jours: int = Field(30, ge=7, le=90)
    seuil_critique: float = 0.0


class ParamsReapprovisionnement(BaseModel):
    code_produit: str | None = Field(None, max_length=64)
    jours_couverture: int = Field(30, ge=7, le=120)


# ── Outils ──────────────────────────────────────────────────────────────────


def _totaux_hebdomadaires(points: list[dict]) -> list[dict]:
    """Regroupe une prévision journalière par semaine (lisible pour un dirigeant)."""
    semaines = []
    for i in range(0, len(points), 7):
        bloc = points[i : i + 7]
        semaines.append(
            {
                "du": str(bloc[0]["date_prevision"]),
                "au": str(bloc[-1]["date_prevision"]),
                "ventes_prevues": round(sum(p["valeur_prevue"] for p in bloc), 2),
            }
        )
    return semaines


def _comparaison_rapide(db: Session, horizon: int) -> dict:
    """Résultat au format de `comparaison_previsions.comparer_modeles`.

    Temps de réponse : un horizon déjà calculé (30 jours, préchauffé au
    démarrage) est lu dans le cache. Pour un AUTRE horizon, au lieu
    d'entraîner les 3 modèles (le plus lent, Prophet, compris), on reprend le
    modèle le plus fiable sur ces données (connu grâce au calcul à 30 jours)
    et on n'entraîne que lui : ~3 fois plus rapide, même méthode de choix.
    La concordance entre méthodes n'est alors pas évaluée (une seule)."""
    if horizon == 30 or comparaison_previsions.est_en_cache(db, horizon):
        return comparaison_previsions.comparer_modeles(db, horizon)
    reference = comparaison_previsions.comparer_modeles(db, 30)
    meilleur = (reference.get("lecture_dirigeant") or {}).get("modele_retenu")
    if not meilleur:
        return comparaison_previsions.comparer_modeles(db, horizon)
    return comparaison_previsions.calculer_un_modele(db, horizon, meilleur)


def prevision_ventes(db: Session, p: ParamsPrevisionVentes) -> dict:
    if p.methode == "auto":
        comparaison = _comparaison_rapide(db, p.horizon_jours)
        lecture = dict(comparaison.get("lecture_dirigeant") or {})
        retenu = next(
            (m for m in comparaison["modeles"] if m["modele"] == lecture.get("modele_retenu")), None
        )
        if retenu:
            lecture["detail_par_semaine"] = _totaux_hebdomadaires(retenu["points"])
        lecture.pop("modele_retenu", None)  # détail technique, inutile pour la réponse
        return lecture
    prevision = forecasting_service.get_previsions_ventes(db, p.horizon_jours, p.methode)
    points = prevision["points"]
    total = sum(pt["valeur_prevue"] for pt in points)
    return {
        "methode_demandee": p.methode,
        "horizon_jours": p.horizon_jours,
        "ventes_prevues_total": round(total, 2),
        "ventes_prevues_par_jour": round(total / len(points), 2) if points else None,
        "ecart_moyen_constate_par_jour": prevision["mae_validation"],
        "detail_par_semaine": _totaux_hebdomadaires(points),
        "avertissements": prevision["avertissements"],
    }


def _projection_tresorerie(db: Session, horizon: int) -> dict:
    """Projection du solde (ARIMA : rapide, comme le contexte du copilote)."""
    return forecasting_service.get_previsions_tresorerie(db, horizon, "arima")


def _resume_tresorerie(
    db: Session, prevision: dict, horizon: int, seuil: float, delta_ventes: float = 0.0
) -> dict:
    """Alerte sur une projection du solde. `delta_ventes` : écart de ventes
    simulé, réparti uniformément sur l'horizon et supposé encaissé
    (hypothèse affichée par l'outil de simulation)."""
    points = [dict(pt) for pt in prevision["points"]]
    if delta_ventes and points:
        for i, pt in enumerate(points, start=1):
            pt["valeur_prevue"] += delta_ventes * i / len(points)
    alerte = analytics_service.get_alerte_tresorerie({**prevision, "points": points}, horizon, seuil)
    solde_actuel = analytics_service.get_kpis_globaux(db)["solde_tresorerie_estime"]
    return {
        "solde_actuel": round(solde_actuel, 2),
        "solde_prevu_fin_periode": round(points[-1]["valeur_prevue"], 2) if points else None,
        "point_le_plus_bas": round(alerte["solde_projete"], 2),
        "date_du_point_le_plus_bas": str(alerte["date_alerte"]),
        "seuil_critique": seuil,
        "niveau_alerte": alerte["niveau"],  # ok | vigilance | risque_deficit
        "message": alerte["message"],
    }


def tresorerie_prevue(db: Session, p: ParamsTresorerie) -> dict:
    prevision = _projection_tresorerie(db, p.horizon_jours)
    return {"horizon_jours": p.horizon_jours, **_resume_tresorerie(db, prevision, p.horizon_jours, p.seuil_critique)}


def simulation_ventes(db: Session, p: ParamsSimulation) -> dict:
    base = prevision_ventes(db, ParamsPrevisionVentes(horizon_jours=p.horizon_jours))
    if not base.get("disponible"):
        return {"disponible": False, "message": base.get("resume", "Prévision des ventes indisponible.")}
    total_base = base["total_prevu"]
    delta = total_base * p.variation_pct / 100
    projection = _projection_tresorerie(db, p.horizon_jours)
    reference = _resume_tresorerie(db, projection, p.horizon_jours, p.seuil_critique)
    simule = _resume_tresorerie(db, projection, p.horizon_jours, p.seuil_critique, delta_ventes=delta)
    return {
        "variation_pct": p.variation_pct,
        "horizon_jours": p.horizon_jours,
        "ventes_prevues_sans_changement": round(total_base, 2),
        "ventes_dans_ce_scenario": round(total_base + delta, 2),
        "ecart_de_ventes": round(delta, 2),
        # Calculé ici : les petits LLM se trompent sur ce genre de division.
        "ecart_de_ventes_par_jour": round(delta / p.horizon_jours, 2),
        "tresorerie_sans_changement": {k: reference[k] for k in ("solde_prevu_fin_periode", "niveau_alerte")},
        "tresorerie_dans_ce_scenario": {
            k: simule[k] for k in ("solde_prevu_fin_periode", "point_le_plus_bas", "niveau_alerte", "message")
        },
        "hypothese": (
            "Simulation simplifiée : l'écart de ventes est supposé encaissé intégralement et réparti "
            "uniformément sur la période ; les dépenses ne changent pas."
        ),
    }


def reapprovisionnement(db: Session, p: ParamsReapprovisionnement) -> dict:
    """Quantité à commander par produit : rythme de vente récent × (délai de
    livraison + couverture souhaitée) − stock disponible."""
    requete = db.query(StockProduit)
    if p.code_produit:
        requete = requete.filter(StockProduit.code_produit == p.code_produit)
    produits = requete.all()
    if not produits:
        return {"message": "Aucun produit en stock correspondant." if p.code_produit else "Aucun stock importé."}

    derniere = db.query(func.max(Transaction.date_transaction)).scalar()
    ventes: dict[str, float] = {}
    if derniere is not None:
        debut = derniere - timedelta(days=FENETRE_VENTES_JOURS)
        ventes = dict(
            db.query(Transaction.code_produit, func.sum(Transaction.quantite))
            .filter(~Transaction.est_retour, Transaction.date_transaction > debut)
            .group_by(Transaction.code_produit)
            .all()
        )

    lignes = []
    for prod in produits:
        rythme = float(ventes.get(prod.code_produit) or 0) / FENETRE_VENTES_JOURS
        delai = prod.delai_livraison_jours or DELAI_LIVRAISON_DEFAUT
        couverture = prod.quantite_disponible / rythme if rythme > 0 else None
        besoin = rythme * (delai + p.jours_couverture) - prod.quantite_disponible
        a_commander = max(0, math.ceil(besoin)) if rythme > 0 else 0
        if couverture is not None and couverture < delai:
            urgence = "urgent"  # rupture avant même l'arrivée d'une commande passée aujourd'hui
        elif a_commander > 0:
            urgence = "à prévoir"
        else:
            urgence = "suffisant"
        lignes.append(
            {
                "code_produit": prod.code_produit,
                "produit": prod.nom_produit,
                "stock_disponible": prod.quantite_disponible,
                "ventes_par_jour": round(rythme, 2),
                "jours_de_stock_restants": round(couverture, 1) if couverture is not None else None,
                "delai_livraison_jours": delai,
                "quantite_a_commander": a_commander,
                "cout_estime": round(a_commander * float(prod.cout_unitaire), 2) if prod.cout_unitaire else None,
                "urgence": urgence,
            }
        )
    ordre = {"urgent": 0, "à prévoir": 1, "suffisant": 2}
    lignes.sort(key=lambda lg: (ordre[lg["urgence"]], lg["jours_de_stock_restants"] or math.inf))
    a_traiter = [lg for lg in lignes if lg["urgence"] != "suffisant"]
    return {
        "couverture_souhaitee_jours": p.jours_couverture,
        "base_de_calcul": f"ventes des {FENETRE_VENTES_JOURS} derniers jours",
        "produits_urgents": sum(lg["urgence"] == "urgent" for lg in lignes),
        "produits_a_commander": len(a_traiter),
        # None (et non 0) si aucun coût unitaire n'est connu : « 0 » laisserait croire à une commande gratuite.
        "cout_total_estime": (
            round(sum(lg["cout_estime"] for lg in a_traiter if lg["cout_estime"] is not None), 2)
            if any(lg["cout_estime"] is not None for lg in a_traiter)
            else None
        ),
        "produits": (a_traiter or lignes)[:PRODUITS_MAX],
        "produits_omis": max(0, len(a_traiter or lignes) - PRODUITS_MAX),
    }


# ── Catalogue ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Outil:
    nom: str
    libelle: str  # affiché à l'utilisateur sous la réponse
    description: str  # lue par le LLM pour choisir
    parametres: type[BaseModel]
    fonction: Callable[[Session, BaseModel], dict]


OUTILS: dict[str, Outil] = {
    o.nom: o
    for o in (
        Outil(
            "prevision_ventes",
            "Prévision des ventes",
            "Prévoit les ventes sur un horizon choisi (méthode la plus fiable choisie automatiquement), "
            "avec évolution, fiabilité, fourchette, détail par semaine et conseils. "
            "Paramètres : horizon_jours (7 à 90, défaut 30), methode (auto|prophet|arima|xgboost, défaut auto "
            "— ne la changer que si l'utilisateur nomme une méthode).",
            ParamsPrevisionVentes,
            prevision_ventes,
        ),
        Outil(
            "tresorerie_prevue",
            "Prévision de trésorerie",
            "Projette le solde de trésorerie et signale un risque de passer sous un seuil. "
            "Paramètres : horizon_jours (7 à 90, défaut 30), seuil_critique (montant, défaut 0).",
            ParamsTresorerie,
            tresorerie_prevue,
        ),
        Outil(
            "simulation_ventes",
            "Simulation « et si »",
            "Simule l'effet d'une hausse ou d'une baisse des ventes (ex. « et si les ventes baissent de 10 % ? ») "
            "sur les ventes et la trésorerie. Paramètres : variation_pct (−90 à +200, OBLIGATOIRE, ex. -10), "
            "horizon_jours (7 à 90, défaut 30), seuil_critique (défaut 0).",
            ParamsSimulation,
            simulation_ventes,
        ),
        Outil(
            "reapprovisionnement",
            "Recommandation de réapprovisionnement",
            "Calcule, produit par produit, les jours de stock restants au rythme de vente actuel, l'urgence "
            "et la quantité à commander (délai de livraison + couverture souhaitée). Paramètres : "
            "code_produit (facultatif, un seul produit), jours_couverture (7 à 120, défaut 30).",
            ParamsReapprovisionnement,
            reapprovisionnement,
        ),
    )
}


def catalogue_texte() -> str:
    return "\n".join(f"- {o.nom} : {o.description}" for o in OUTILS.values())


@dataclass
class AppelOutil:
    nom: str
    parametres: dict
    libelle: str = ""
    resultat: dict | None = None
    erreur: str | None = None


def extraire_appels(reponse_llm: str) -> tuple[list[AppelOutil], list[str]]:
    """Blocs ```outil``` de la réponse de planification : (appels lisibles,
    erreurs de format). Au plus APPELS_MAX appels."""
    appels, erreurs = [], []
    for bloc in _BLOC_OUTIL.findall(reponse_llm or ""):
        try:
            donnees = json.loads(bloc.strip())
            nom = str(donnees["nom"])
            parametres = donnees.get("parametres") or {}
            if not isinstance(parametres, dict):
                raise TypeError("« parametres » doit être un objet JSON")
        except (ValueError, KeyError, TypeError) as exc:
            erreurs.append(f"bloc outil illisible ({exc}) : {bloc.strip()[:200]}")
            continue
        appels.append(AppelOutil(nom=nom, parametres=parametres))
    return appels[:APPELS_MAX], erreurs


def executer(db: Session, appel: AppelOutil) -> AppelOutil:
    """Valide puis exécute un appel ; ne lève jamais (l'erreur est renvoyée au LLM)."""
    outil = OUTILS.get(appel.nom)
    if outil is None:
        appel.erreur = f"outil inconnu : {appel.nom} (disponibles : {', '.join(OUTILS)})"
        return appel
    appel.libelle = outil.libelle
    try:
        params = outil.parametres.model_validate(appel.parametres)
    except ValidationError as exc:
        e = exc.errors()[0]
        champ = ".".join(str(x) for x in e["loc"]) or "paramètres"
        appel.erreur = f"paramètre invalide pour {appel.nom} — {champ} : {e['msg']}"
        return appel
    appel.parametres = params.model_dump()
    try:
        appel.resultat = outil.fonction(db, params)
    except Exception as exc:  # noqa: BLE001 — un calcul en échec ne doit pas casser la réponse
        appel.erreur = f"calcul impossible ({exc})"
    return appel


def formater_pour_prompt(appel: AppelOutil) -> str:
    entete = f"Outil {appel.nom} {json.dumps(appel.parametres, ensure_ascii=False)}"
    if appel.erreur:
        return f"{entete} — ÉCHEC : {appel.erreur}"
    return f"{entete} — résultat :\n{json.dumps(appel.resultat, ensure_ascii=False, default=str)}"
