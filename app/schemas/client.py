import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ClientBase(BaseModel):
    code_client_externe: str | None = None
    nom: str | None = None
    email: str | None = None
    pays: str | None = None


class ClientCreate(ClientBase):
    pass


class ClientRFM(BaseModel):
    """Vue exposée par /api/v1/marketing/ — sortie du module Abdoulmadjid."""

    model_config = ConfigDict(from_attributes=True)

    client_id: uuid.UUID
    code_client_externe: str | None
    nom: str | None
    recence_jours: int | None
    frequence_achats: int | None
    montant_total: float | None
    score_r: int | None
    score_f: int | None
    score_m: int | None
    segment_rfm: str | None
    cluster_id: int | None
    score_churn: float | None
    date_maj_segmentation: datetime | None


class SegmentSummary(BaseModel):
    """Agrégat par segment, consommé par le dashboard unifié."""

    segment_rfm: str
    nb_clients: int
    montant_total_moyen: float
    score_churn_moyen: float | None
    action_marketing_recommandee: str
