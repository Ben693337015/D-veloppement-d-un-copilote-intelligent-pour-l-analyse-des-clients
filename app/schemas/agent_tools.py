"""Tâche #8 de la roadmap de clôture — validation stricte des paramètres
que le LLM peut envoyer à chaque outil (cf. `copilot_service._executer_outil`),
AVANT tout appel à un service métier. Miroir des contraintes déjà
imposées par FastAPI/Query sur les endpoints REST équivalents
(`app/routers/analytics.py`, `app/routers/marketing.py`) — mêmes bornes,
pour que le comportement de l'agent reste cohérent avec l'API directe."""

from pydantic import BaseModel, Field


class GetKpisParams(BaseModel):
    pass


class GetSalesForecastParams(BaseModel):
    horizon_jours: int = Field(30, ge=1, le=90)
    modele: str = Field("prophet", pattern="^(prophet|arima|xgboost)$")


class GetCashflowForecastParams(BaseModel):
    horizon_jours: int = Field(45, ge=1, le=90)
    modele: str = Field("prophet", pattern="^(prophet|arima|xgboost)$")
    seuil_critique: float = 0.0


class GetRfmSegmentsParams(BaseModel):
    segment: str | None = None
    limit: int = Field(200, ge=1, le=1000)


class GetStockAlertsParams(BaseModel):
    pass