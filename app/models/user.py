import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    """Table `users` — authentification (Phase 4, cf. README §6).

    Périmètre volontairement "simple" (décision produit) : un seul rôle
    implicite (tout utilisateur authentifié a accès à toute l'API), pas de
    RBAC granulaire par module. Protège l'accès (login/mot de passe requis)
    sans complexifier avec une table de rôles/permissions — cohérent avec un
    outil interne à une seule petite équipe PME plutôt qu'une plateforme
    multi-tenant avec des profils Paul/Marc/Sophie aux droits différenciés
    (cf. Cadrage §Personas — hors périmètre choisi pour cette itération).
    """

    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    nom: Mapped[str | None] = mapped_column(String(150))
    mot_de_passe_hache: Mapped[str] = mapped_column(String(255), nullable=False)
    actif: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    date_creation: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
