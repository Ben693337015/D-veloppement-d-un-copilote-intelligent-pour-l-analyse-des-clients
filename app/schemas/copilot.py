from typing import Literal

from pydantic import BaseModel, Field


class MessageHistorique(BaseModel):
    """Échange précédent de la conversation (mémoire courte du copilote)."""

    role: Literal["user", "assistant"]
    contenu: str = Field(max_length=8000)


class CopilotChatRequest(BaseModel):
    # Question vide ou démesurée refusée (422) : avant, "" était accepté et
    # une question de plusieurs Mo partait telle quelle dans le prompt du LLM.
    question: str = Field(min_length=1, max_length=2000)
    client_id: str | None = None  # pour filtrer le contexte RAG si pertinent
    # Derniers échanges, pour comprendre « et pour 90 jours ? » (au plus 12,
    # seuls les 6 derniers sont utilisés, tronqués : cf. copilot_service).
    historique: list[MessageHistorique] = Field(default_factory=list, max_length=12)


class SourceExplicabilite(BaseModel):
    module: str  # "analytics" | "marketing"
    variable: str
    contribution: float  # valeur SHAP/LIME


class OutilUtilise(BaseModel):
    """Calcul effectué par le copilote pour répondre (cf. copilote_outils)."""

    nom: str
    libelle: str
    parametres: dict = {}
    statut: Literal["ok", "erreur"] = "ok"
    erreur: str | None = None


class CopilotChatResponse(BaseModel):
    reponse: str
    sources: list[str] = []
    explicabilite: list[SourceExplicabilite] = []
    # Origine de la réponse, affichée par le frontend : "llm" (générée par le
    # fournisseur configuré), "local" (aucun LLM configuré) ou "secours"
    # (LLM configuré mais en échec -> synthèse factuelle).
    mode: Literal["llm", "local", "secours"] = "local"
    fournisseur: str | None = None
    modele: str | None = None
    # Accès à la base (mode "llm") : requête SQL réellement exécutée en
    # lecture seule pour répondre, et nombre de lignes renvoyées.
    requete_sql: str | None = None  # première requête (compatibilité)
    requetes_sql: list[str] = []  # toutes les requêtes exécutées (jusqu'à 3)
    lignes_base: int | None = None
    # Outils de calcul exécutés (prévision, simulation, réapprovisionnement…).
    outils_utilises: list[OutilUtilise] = []


class PointBriefing(BaseModel):
    """Point d'attention du briefing proactif (cf. copilote_briefing)."""

    niveau: Literal["urgent", "attention", "info"]
    domaine: Literal["stocks", "tresorerie", "ventes", "clients"]
    titre: str
    detail: str
    question: str  # question à poser au copilote pour approfondir


class BriefingResponse(BaseModel):
    points: list[PointBriefing] = []
