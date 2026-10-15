import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.routers import analytics, auth, copilot, ingestion, marketing, report

_log = logging.getLogger("uvicorn.error")


def _creer_tables_ajoutees() -> None:
    """Crée les tables ajoutées après la création initiale de la base.

    `scripts/init_db.sql` n'est exécuté par PostgreSQL qu'à la PREMIÈRE
    création du volume Docker : une base existante n'aurait jamais la table
    `historique_segmentation` (ajout du 13/10/2026) sans `docker compose
    down -v` (qui efface les données). On la crée donc au démarrage si elle
    manque (CREATE TABLE IF NOT EXISTS, sans effet sinon), et on donne le
    droit de lecture au rôle du copilote s'il existe. Jamais bloquant : si
    la base est injoignable, l'API démarre quand même (erreur 503 ensuite)."""
    from sqlalchemy import text

    from app.core.database import engine
    from app.models import HistoriqueSegmentation

    try:
        HistoriqueSegmentation.__table__.create(bind=engine, checkfirst=True)
        if engine.dialect.name == "postgresql":
            with engine.begin() as conn:
                # Colonnes ajoutées ensuite (comparaison des algorithmes, 13/10/2026).
                conn.execute(text(
                    "ALTER TABLE historique_segmentation ADD COLUMN IF NOT EXISTS comparaison TEXT, "
                    "ADD COLUMN IF NOT EXISTS synthese TEXT"
                ))
                if conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'copilote_lecture'")).first():
                    conn.execute(text("GRANT SELECT ON historique_segmentation TO copilote_lecture"))
    except Exception as exc:  # noqa: BLE001 — démarrage jamais bloqué
        logging.getLogger("app").warning("Tables ajoutées non vérifiées au démarrage : %s", exc)


def _journaliser_fournisseur_llm() -> None:
    """Affiche dans les logs (docker compose logs api) le fournisseur LLM
    retenu après lecture du .env, et les éventuels avertissements."""
    from app.services import llm_client

    d = llm_client.diagnostic_public()
    if d["configure"]:
        _log.info("Copilote : fournisseur LLM %s, modèle %s (clé lue dans %s)", d["fournisseur"], d["modele"], d["source"])
    else:
        _log.info("Copilote : aucune clé API LLM — synthèse factuelle locale")
    for avertissement in d["avertissements"]:
        _log.warning("Copilote : %s", avertissement)


def _prechauffer_previsions() -> None:
    """Temps de réponse du copilote : les prévisions (3 modèles) sont gardées
    en mémoire, donc perdues à chaque redémarrage de l'API — la première
    question au copilote (ou la page Ventes) attendait alors leur calcul
    complet. On le lance en arrière-plan dès le démarrage. Jamais bloquant :
    en cas d'échec (base vide ou injoignable), le calcul sera fait à la demande."""
    import threading

    from app.core.database import engine
    from app.services import comparaison_previsions

    threading.Thread(
        target=comparaison_previsions.precalculer_en_arriere_plan, args=(engine,), daemon=True,
        name="prechauffage-previsions",
    ).start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _creer_tables_ajoutees()
    _journaliser_fournisseur_llm()
    _prechauffer_previsions()
    yield


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
    lifespan=lifespan,
)

@app.exception_handler(OperationalError)
async def base_de_donnees_injoignable(request: Request, exc: OperationalError):
    """Base PostgreSQL injoignable ou identifiants refusés : avant, l'utilisateur
    voyait un « Internal Server Error » brut (ex. à l'inscription) sans aucune
    piste. On renvoie 503 avec la cause probable ; le détail reste dans les logs."""
    logging.getLogger("app").error("Base de données injoignable : %s", exc.orig)
    cause = str(exc.orig).lower()
    if "password authentication failed" in cause or "does not exist" in cause:
        detail = (
            "Connexion à la base de données refusée : l'utilisateur ou le mot de passe PostgreSQL "
            "ne correspond pas à la base existante. Cf. README §7, « Dépannage rapide »."
        )
    else:
        detail = "Base de données injoignable. Vérifiez que le service « db » est démarré."
    return JSONResponse(status_code=503, content={"detail": detail})


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
