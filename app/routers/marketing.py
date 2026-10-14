from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.schemas.client import ClientRFM, SegmentSummary
from app.schemas.rfm_pipeline import RFMPipelineRequest, RFMPipelineResultOut
from app.services import marketing_service, rfm_clustering_service

router = APIRouter(
    prefix="/marketing",
    tags=["Marketing — Segmentation RFM & Recommandations"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/clients", response_model=list[ClientRFM])
def get_clients_rfm(
    segment: str | None = Query(None, description="Filtrer par segment_rfm"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Liste des clients avec leur segmentation RFM (US-04)."""
    return marketing_service.list_clients_rfm(db, segment=segment, limit=limit)


@router.get("/segments/summary", response_model=list[SegmentSummary])
def get_segments_summary(db: Session = Depends(get_db)):
    """Répartition des clients par segment + action marketing recommandée.

    Consommé par le dashboard unifié et par le module copilote/XAI.
    """
    return marketing_service.get_segment_summary(db)


@router.post("/rfm/run", response_model=RFMPipelineResultOut)
def run_rfm_pipeline(payload: RFMPipelineRequest, db: Session = Depends(get_db)):
    """Calcule le RFM, le diagnostic coude/silhouette (K-Means, DBSCAN ou GMM
    selon `algorithme`), choisit k (ou epsilon pour DBSCAN), segmente les
    clients et écrit en base (sauf `dry_run=True`).

    Reproduit le badge "Pipeline RFM · N clients · k=... · Sil.=..." et
    l'écran "Coude K-Means" (inertie + silhouette par k) fournis en exemple.
    `dry_run=True` permet d'explorer une fenêtre d'analyse différente sans
    modifier les segments déjà validés en base.
    """
    try:
        return rfm_clustering_service.run_full_pipeline(
            db,
            k_min=payload.k_min,
            k_max=payload.k_max,
            fenetre_jours=payload.fenetre_jours,
            winsor_percentile=payload.winsor_percentile,
            dry_run=payload.dry_run,
            algorithme=payload.algorithme,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
