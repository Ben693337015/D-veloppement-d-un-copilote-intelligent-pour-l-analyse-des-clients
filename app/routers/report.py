from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.schemas.report import RapportPDFRequest
from app.services import report_service

router = APIRouter(
    prefix="/report",
    tags=["Rapport — Export PDF"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/pdf")
def generate_pdf_report(payload: RapportPDFRequest, db: Session = Depends(get_db)):
    """Génère le rapport PDF (écran "Génération du Rapport PDF" fourni en
    exemple) : page de garde, KPIs, diagnostic K-Means (coude/silhouette),
    distribution des segments, recommandations, extrait des meilleurs
    clients. Réutilise les pipelines déjà validés, ne recalcule rien en
    double."""
    try:
        pdf_bytes = report_service.generate_pdf_report(db, payload)
    except report_service.RapportPDFIndisponible as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="rapport_copilote_pme.pdf"'},
    )
