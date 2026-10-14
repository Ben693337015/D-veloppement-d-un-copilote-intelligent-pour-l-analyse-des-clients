import uuid
from datetime import datetime

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StockProduit(Base):
    """Table `stocks_produits` — consommée par le sous-projet Maslaw
    (seuils d'alerte et de réapprovisionnement, cf. ROADMAP.md Phase 3, M7).
    """

    __tablename__ = "stocks_produits"

    stock_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code_produit: Mapped[str] = mapped_column(String(64), nullable=False)
    nom_produit: Mapped[str | None] = mapped_column(String(200))
    quantite_disponible: Mapped[int] = mapped_column(default=0)
    seuil_alerte: Mapped[int] = mapped_column(default=10)
    seuil_reapprovisionnement: Mapped[int] = mapped_column(default=20)
    delai_livraison_jours: Mapped[int | None] = mapped_column(default=7)
    cout_unitaire: Mapped[float | None] = mapped_column(Numeric(12, 2))
    date_maj: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
