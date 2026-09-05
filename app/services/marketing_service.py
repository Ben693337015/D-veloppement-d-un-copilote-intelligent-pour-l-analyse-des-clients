"""Service métier — sous-projet Abdoulmadjid (segmentation RFM & marketing).

Cf. skill `pme-marketing-rfm` pour les règles détaillées (fenêtre d'analyse,
gestion de qcut, choix K-Means/DBSCAN/GMM, métriques de clustering).
Ce fichier expose la LECTURE/agrégation depuis PostgreSQL ; le calcul réel
du RFM et du clustering est fait par `app/services/rfm_clustering_service.py`
(déclenché via POST /api/v1/marketing/rfm/run), qui écrit dans les colonnes
clients.segment_rfm, clients.score_churn, clients.cluster_id.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Client

# Table de correspondance segment -> action marketing (skill pme-marketing-rfm §4)
SEGMENT_ACTIONS: dict[str, str] = {
    "Fidèle": "Programme de fidélité, offres premium",
    "À risque": "Campagne de relance ciblée, alerte automatique dans le tableau de bord",
    "Occasionnel": "Offres de découverte, incitation au réachat",
    "Nouveau": "Séquence d'onboarding",
    "Atypique (B2B/grossiste)": "Compte dédié, tarification négociée — à exclure des campagnes grand public",
}


def list_clients_rfm(db: Session, segment: str | None = None, limit: int = 200) -> list[Client]:
    query = db.query(Client).filter(Client.segment_rfm.is_not(None))
    if segment:
        query = query.filter(Client.segment_rfm == segment)
    return query.limit(limit).all()


def get_segment_summary(db: Session) -> list[dict]:
    """Agrégat par segment consommé par le dashboard (US-04)."""
    rows = (
        db.query(
            Client.segment_rfm,
            func.count(Client.client_id).label("nb_clients"),
            func.avg(Client.montant_total).label("montant_total_moyen"),
            func.avg(Client.score_churn).label("score_churn_moyen"),
        )
        .filter(Client.segment_rfm.is_not(None))
        .group_by(Client.segment_rfm)
        .all()
    )
    return [
        {
            "segment_rfm": r.segment_rfm,
            "nb_clients": r.nb_clients,
            "montant_total_moyen": float(r.montant_total_moyen or 0),
            "score_churn_moyen": float(r.score_churn_moyen) if r.score_churn_moyen is not None else None,
            "action_marketing_recommandee": SEGMENT_ACTIONS.get(
                r.segment_rfm, "Action à définir"
            ),
        }
        for r in rows
    ]
