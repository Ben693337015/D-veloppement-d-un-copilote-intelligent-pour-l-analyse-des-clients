from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.schemas.copilot import CopilotChatRequest, CopilotChatResponse
from app.services import copilot_service

router = APIRouter(
    prefix="/copilot",
    tags=["Copilote — Assistant conversationnel & XAI"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/chat", response_model=CopilotChatResponse)
def chat(payload: CopilotChatRequest, db: Session = Depends(get_db)):
    """Interrogation en langage naturel du copilote (US-05, parcours utilisateur Tâche #8)."""
    return copilot_service.answer_question(db, payload.question)
