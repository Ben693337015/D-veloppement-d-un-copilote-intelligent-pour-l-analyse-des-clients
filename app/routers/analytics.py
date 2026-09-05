from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.schemas.analytics import AlerteStock, KPIsGlobaux, PrevisionVentes
from app.services import analytics_service, forecasting_service

router = APIRouter(
    prefix="/analytics",
    tags=["Analytics — Ventes, Trésorerie & Stocks"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/kpis", response_model=KPIsGlobaux)
def get_kpis(db: Session = Depends(get_db)):
    """KPIs globaux affichés sur le tableau de bord synthétique (Persona Paul)."""
    return analytics_service.get_kpis_globaux(db)


@router.get("/forecast/sales", response_model=PrevisionVentes)
def get_sales_forecast(
    horizon_jours: int = Query(30, ge=1, le=90, description="Horizon de prévision en jours"),
    modele: str = Query(
        "prophet",
        pattern="^(prophet|arima|xgboost)$",
        description="Modèle de prévision : 'prophet' (défaut), 'arima' (baseline) ou 'xgboost' (variables exogènes calendaires).",
    ),
    db: Session = Depends(get_db),
):
    """Prévision des ventes à 1-90 jours (US-02), entraînée EN LIGNE sur le CA
    journalier réel (cf. forecasting_service pour la méthodologie : découpage
    train/test, RMSE/MAE, ré-entraînement final sur tout l'historique).
    """
    try:
        return forecasting_service.get_previsions_ventes(db, horizon_jours, modele)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/stock/alertes", response_model=list[AlerteStock])
def get_stock_alertes(db: Session = Depends(get_db)):
    """Alertes de réapprovisionnement (US-03)."""
    return analytics_service.get_stock_alertes(db)
