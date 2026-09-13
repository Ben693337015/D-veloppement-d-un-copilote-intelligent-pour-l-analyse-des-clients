import uuid
from datetime import datetime

from sqlalchemy import DateTime, Numeric, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Client(Base):
    """Table `clients` — cf. Cadrage §4 (contrat d'interface).

    Colonnes segment_rfm / score_churn / cluster_id sont écrites par le
    sous-projet Abdoulmadjid (module RFM) et lues par le sous-projet Maslaw
    (dashboard, copilote). Ne jamais renommer sans coordination (cf. skill
    pme-marketing-rfm §5 "Cohérence avec le reste du projet").
    """

    __tablename__ = "clients"

    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code_client_externe: Mapped[str | None] = mapped_column(String(64), unique=True)
    nom: Mapped[str | None] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(150))
    pays: Mapped[str | None] = mapped_column(String(100))
    date_creation: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Écrit par le module RFM (Abdoulmadjid)
    recence_jours: Mapped[int | None]
    frequence_achats: Mapped[int | None]
    montant_total: Mapped[float | None] = mapped_column(Numeric(14, 2))
    score_r: Mapped[int | None] = mapped_column(SmallInteger)
    score_f: Mapped[int | None] = mapped_column(SmallInteger)
    score_m: Mapped[int | None] = mapped_column(SmallInteger)
    segment_rfm: Mapped[str | None] = mapped_column(String(50))
    cluster_id: Mapped[int | None] = mapped_column(SmallInteger)
    score_churn: Mapped[float | None] = mapped_column(Numeric(5, 4))
    date_maj_segmentation: Mapped[datetime | None]

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    transactions = relationship("Transaction", back_populates="client")
