from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.schemas.copilot import BriefingResponse, CopilotChatRequest, CopilotChatResponse
from app.services import copilot_service, copilote_briefing, llm_client

router = APIRouter(
    prefix="/copilot",
    tags=["Copilote — Assistant conversationnel & XAI"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/chat", response_model=CopilotChatResponse)
def chat(payload: CopilotChatRequest, db: Session = Depends(get_db)):
    """Interrogation en langage naturel du copilote (US-05, parcours utilisateur Tâche #8)."""
    historique = [m.model_dump() for m in payload.historique]
    return copilot_service.answer_question(db, payload.question, historique)


@router.get("/briefing", response_model=BriefingResponse)
def briefing(db: Session = Depends(get_db)):
    """Points qui demandent l'attention du dirigeant (stocks, trésorerie,
    ventes, clients), calculés sans LLM — affichés à l'ouverture du copilote."""
    return {"points": copilote_briefing.points_d_attention(db)}


@router.get("/fournisseur")
def fournisseur():
    """Fournisseur LLM et modèle effectivement utilisés, d'après le .env
    chargé au démarrage (jamais la clé), avec les avertissements de
    configuration : permet de vérifier un changement de clé API."""
    return llm_client.diagnostic_public()
