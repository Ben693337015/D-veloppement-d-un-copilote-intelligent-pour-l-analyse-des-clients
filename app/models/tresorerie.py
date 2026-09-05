import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Tresorerie(Base):
    """Table `tresorerie` — consommée par le sous-projet Maslaw
    (suivi prédictif du BFR, alertes de liquidités, cf. Cadrage Tâche #6).
    """

    __tablename__ = "tresorerie"
    __table_args__ = (
        CheckConstraint(
            "type_mouvement IN ('encaissement', 'decaissement')",
            name="ck_tresorerie_type_mouvement",
        ),
    )

    mouvement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    date_mouvement: Mapped[date] = mapped_column(Date, nullable=False)
    type_mouvement: Mapped[str] = mapped_column(String(20), nullable=False)
    categorie: Mapped[str | None] = mapped_column(String(100))
    montant: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    solde_apres_mouvement: Mapped[float | None] = mapped_column(Numeric(14, 2))
    reference_transaction: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.transaction_id", ondelete="SET NULL")
    )
    commentaire: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
