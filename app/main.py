from fastapi import FastAPI

from app.core.config import settings
from app.routers import analytics, auth, copilot, ingestion, marketing, report

app = FastAPI(
    title=settings.PROJECT_NAME,
    description=(
        "API du Copilote IA PME (Groupe 2, Master 1 IA Appliquée — Université de Ngaoundéré). "
        "Expose l'analyse ventes/trésorerie/stocks (sous-projet Maslaw), la segmentation RFM "
        "et les recommandations marketing (sous-projet Abdoulmadjid), et l'assistant "
        "conversationnel mutualisé. Toutes les routes /api/v1/* (hors /auth) exigent un "
        "jeton JWT (cf. POST /api/v1/auth/login) — utilisez le bouton \"Authorize\" ci-dessous."
    ),
    version="0.3.0",
)

app.include_router(auth.router, prefix=settings.API_V1_PREFIX)
app.include_router(analytics.router, prefix=settings.API_V1_PREFIX)
app.include_router(marketing.router, prefix=settings.API_V1_PREFIX)
app.include_router(copilot.router, prefix=settings.API_V1_PREFIX)
app.include_router(ingestion.router, prefix=settings.API_V1_PREFIX)
app.include_router(report.router, prefix=settings.API_V1_PREFIX)


@app.get("/", tags=["Santé"])
def root():
    return {
        "projet": settings.PROJECT_NAME,
        "statut": "ok",
        "docs": "/docs",
    }


@app.get("/health", tags=["Santé"])
def health():
    return {"status": "ok"}
