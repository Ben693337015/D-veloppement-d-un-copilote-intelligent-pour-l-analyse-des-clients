import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Transaction(Base):
    """Table `transactions` — point d'ancrage commun (Cadrage §4).

    Exploitée par Maslaw pour le CA/BFR et par Abdoulmadjid pour R/F/M.
    montant_total_ligne est une colonne générée côté PostgreSQL
    (quantite * prix_unitaire) — ne pas la recalculer côté Python à l'insert.
    """

    __tablename__ = "transactions"

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.client_id", ondelete="SET NULL")
    )
    numero_facture: Mapped[str | None] = mapped_column(String(64))
    code_produit: Mapped[str | None] = mapped_column(String(64))
    description_produit: Mapped[str | None] = mapped_column(Text)
    quantite: Mapped[int]
    prix_unitaire: Mapped[float] = mapped_column(Numeric(12, 2))
    date_transaction: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    pays: Mapped[str | None] = mapped_column(String(100))
    est_retour: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="transactions")
