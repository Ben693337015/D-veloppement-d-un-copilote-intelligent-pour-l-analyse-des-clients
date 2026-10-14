"""Colonnes comparaison et synthese de historique_segmentation (mode « auto »).

Revision ID: c4e8a2f17b93
Revises: b7d3e9a41c20
Create Date: 2026-10-13
"""

from alembic import op

revision = "c4e8a2f17b93"
down_revision = "b7d3e9a41c20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE historique_segmentation ADD COLUMN IF NOT EXISTS comparaison TEXT")
    op.execute("ALTER TABLE historique_segmentation ADD COLUMN IF NOT EXISTS synthese TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE historique_segmentation DROP COLUMN IF EXISTS synthese")
    op.execute("ALTER TABLE historique_segmentation DROP COLUMN IF EXISTS comparaison")
