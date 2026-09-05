from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class UserCreate(BaseModel):
    email: str
    mot_de_passe: str
    nom: str | None = None

    @field_validator("email")
    @classmethod
    def _email_plausible(cls, v: str) -> str:
        # Validation volontairement minimale (pas de dépendance
        # `email-validator` supplémentaire pour un besoin "simple") —
        # rejette au moins les saisies manifestement invalides.
        if "@" not in v or "." not in v.split("@")[-1] or len(v) < 5:
            raise ValueError("Adresse email invalide.")
        return v.strip().lower()

    @field_validator("mot_de_passe")
    @classmethod
    def _mot_de_passe_assez_long(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères.")
        return v


class UserOut(BaseModel):
    user_id: uuid.UUID
    email: str
    nom: str | None
    actif: bool
    date_creation: datetime

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
