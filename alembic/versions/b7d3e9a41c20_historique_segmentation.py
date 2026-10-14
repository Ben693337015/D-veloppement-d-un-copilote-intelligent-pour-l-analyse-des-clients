"""Ajout de la table historique_segmentation (métriques de chaque exécution RFM).

Même DDL que `scripts/init_db.sql` (source d'autorité), idempotent.
L'API la crée aussi au démarrage si elle manque (cf. app/main.py).

Revision ID: b7d3e9a41c20
Revises: 724552ea2cc8
Create Date: 2026-10-13
"""

from alembic import op

revision = "b7d3e9a41c20"
down_revision = "724552ea2cc8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS historique_segmentation (
            execution_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            date_execution          TIMESTAMP NOT NULL DEFAULT now(),
            algorithme              VARCHAR(20) NOT NULL,
            n_clients               INTEGER NOT NULL,
            n_clients_hors_fenetre  INTEGER DEFAULT 0,
            fenetre_jours           INTEGER NOT NULL,
            date_reference          TIMESTAMP,
            k_choisi                INTEGER,
            eps_choisi              DOUBLE PRECISION,
            silhouette              DOUBLE PRECISION,
            davies_bouldin          DOUBLE PRECISION,
            calinski_harabasz       DOUBLE PRECISION,
            repartition_segments    TEXT,
            diagnostics             TEXT,
            avertissements          TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_historique_segmentation_date
            ON historique_segmentation (date_execution);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS historique_segmentation")
