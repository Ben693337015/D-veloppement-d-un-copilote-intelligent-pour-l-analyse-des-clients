import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HistoriqueSegmentation(Base):
    """Table `historique_segmentation` : une ligne par exécution du pipeline
    RFM écrite en base (POST /marketing/rfm/run sans dry_run).

    Avant, les métriques de la segmentation (k choisi, silhouette,
    Davies-Bouldin, diagnostic par k) n'étaient renvoyées qu'une fois à
    l'écran puis perdues : le copilote ne pouvait pas expliquer « pourquoi
    6 segments ? » ni comparer deux exécutions. Les colonnes JSON (texte)
    gardent le détail sans multiplier les tables.
    """

    __tablename__ = "historique_segmentation"

    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    date_execution: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    algorithme: Mapped[str] = mapped_column(String(20), nullable=False)
    n_clients: Mapped[int] = mapped_column(Integer, nullable=False)
    n_clients_hors_fenetre: Mapped[int] = mapped_column(Integer, default=0)
    fenetre_jours: Mapped[int] = mapped_column(Integer, nullable=False)
    date_reference: Mapped[datetime | None] = mapped_column(DateTime)
    k_choisi: Mapped[int | None] = mapped_column(Integer)
    eps_choisi: Mapped[float | None] = mapped_column(Float)
    silhouette: Mapped[float | None] = mapped_column(Float)
    davies_bouldin: Mapped[float | None] = mapped_column(Float)
    calinski_harabasz: Mapped[float | None] = mapped_column(Float)
    repartition_segments: Mapped[str | None] = mapped_column(Text)  # JSON {segment: nb clients}
    diagnostics: Mapped[str | None] = mapped_column(Text)  # JSON [{k, inertie, silhouette, ...}]
    avertissements: Mapped[str | None] = mapped_column(Text)  # JSON [texte]
    # Mode « auto » (ajout du 13/10/2026) : évaluation des 3 algorithmes et
    # synthèse, reprises telles quelles par le rapport PDF et le copilote.
    comparaison: Mapped[str | None] = mapped_column(Text)  # JSON [{algorithme, métriques, interprétation…}]
    synthese: Mapped[str | None] = mapped_column(Text)  # JSON [texte]
