"""Briefing proactif du copilote (sous-projet Maslaw).

Un copilote n'attend pas qu'on lui pose la bonne question : à l'ouverture,
il signale ce qui demande l'attention du dirigeant. Calcul DÉTERMINISTE, sans
LLM (rapide, reproductible, disponible même sans clé API), à partir des
outils et services existants. Chaque point propose une question à poser au
copilote pour approfondir.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.services import copilote_outils as outils
from app.services import marketing_service

log = logging.getLogger(__name__)

_ORDRE = {"urgent": 0, "attention": 1, "info": 2}


def _fmt(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def _point_stocks(db: Session) -> dict | None:
    r = outils.reapprovisionnement(db, outils.ParamsReapprovisionnement())
    if not r.get("produits_a_commander"):
        return None
    urgents = [p for p in r["produits"] if p["urgence"] == "urgent"]
    exemples = ", ".join(p["produit"] or p["code_produit"] for p in (urgents or r["produits"])[:3])
    if urgents:
        return {
            "niveau": "urgent", "domaine": "stocks",
            "titre": f"{r['produits_urgents']} produit(s) bientôt en rupture",
            "detail": f"Stock épuisé avant l'arrivée d'une commande passée aujourd'hui : {exemples}.",
            "question": "Quels produits dois-je commander, et en quelle quantité ?",
        }
    return {
        "niveau": "attention", "domaine": "stocks",
        "titre": f"{r['produits_a_commander']} produit(s) à réapprovisionner",
        "detail": f"À commander pour couvrir les 30 prochains jours : {exemples}.",
        "question": "Quels produits dois-je commander, et en quelle quantité ?",
    }


def _point_tresorerie(db: Session) -> dict | None:
    r = outils.tresorerie_prevue(db, outils.ParamsTresorerie(horizon_jours=30))
    if r["niveau_alerte"] == "ok":
        return None
    return {
        "niveau": "urgent" if r["niveau_alerte"] == "risque_deficit" else "attention",
        "domaine": "tresorerie",
        "titre": "Trésorerie à surveiller" if r["niveau_alerte"] == "vigilance" else "Risque de déficit de trésorerie",
        "detail": r["message"],
        "question": "Comment va évoluer ma trésorerie sur les 30 prochains jours ?",
    }


def _point_ventes(db: Session) -> dict | None:
    # Jamais de calcul long à l'ouverture du copilote : si la prévision n'est
    # pas encore prête (juste après un démarrage, préchauffage en cours), on
    # passe ce point plutôt que de faire attendre l'utilisateur.
    if not outils.comparaison_previsions.est_en_cache(db, 30):
        return None
    lecture = outils.prevision_ventes(db, outils.ParamsPrevisionVentes(horizon_jours=30))
    if not lecture.get("disponible"):
        return None
    sens = lecture.get("sens_evolution")
    return {
        "niveau": "attention" if sens == "baisse" or lecture.get("fiabilite") == "faible" else "info",
        "domaine": "ventes",
        "titre": {"hausse": "Ventes en hausse attendue", "baisse": "Ventes en baisse attendue"}.get(
            sens, "Ventes stables attendues"
        ),
        "detail": lecture["resume"],
        "question": "Combien vais-je vendre dans les 30 prochains jours ?",
    }


def _point_clients(db: Session) -> dict | None:
    segments = {s["segment_rfm"]: s for s in marketing_service.get_segment_summary(db)}
    a_risque = segments.get("À risque")
    if not a_risque or not a_risque["nb_clients"]:
        return None
    return {
        "niveau": "attention", "domaine": "clients",
        "titre": f"{a_risque['nb_clients']} client(s) à risque de départ",
        "detail": (
            f"Clients habituels qui n'achètent plus (montant moyen dépensé : {_fmt(a_risque['montant_total_moyen'])}). "
            f"Action conseillée : {a_risque['action_marketing_recommandee']}."
        ),
        "question": "Qui sont mes clients à risque et que dois-je faire ?",
    }


def points_d_attention(db: Session) -> list[dict]:
    """Points triés par urgence ; une source en échec est ignorée (jamais d'erreur)."""
    points = []
    for source in (_point_stocks, _point_tresorerie, _point_ventes, _point_clients):
        try:
            point = source(db)
        except Exception:  # noqa: BLE001 — le briefing ne doit jamais empêcher d'ouvrir le copilote
            log.exception("Briefing : %s en échec", source.__name__)
            continue
        if point:
            points.append(point)
    return sorted(points, key=lambda p: _ORDRE[p["niveau"]])
