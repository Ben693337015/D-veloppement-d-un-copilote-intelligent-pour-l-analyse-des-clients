from pydantic import BaseModel


class CopilotChatRequest(BaseModel):
    question: str
    client_id: str | None = None  # pour filtrer le contexte RAG si pertinent


class SourceExplicabilite(BaseModel):
    module: str  # "analytics" | "marketing"
    variable: str
    contribution: float  # valeur SHAP/LIME


class CopilotChatResponse(BaseModel):
    reponse: str
    sources: list[str] = []
    explicabilite: list[SourceExplicabilite] = []
