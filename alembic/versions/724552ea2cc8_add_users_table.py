"""Ajout de la table users — authentification JWT (Phase 4).

Le DDL de `users` a aussi été ajouté à `scripts/init_db.sql` (source
d'autorité unique partagée avec la migration initiale, cf. docstring
`272544c7b5ae_schema_initial.py`), donc une base créée AUJOURD'HUI pour la
première fois obtient déjà `users` via `alembic upgrade head` (qui rejoue
tout `init_db.sql`, désormais à jour) ou via le premier démarrage Docker
(qui exécute `init_db.sql` directement). Cette migration incrémentale reste
nécessaire pour toute base ayant DÉJÀ appliqué la migration initiale AVANT
l'ajout de `users` — `CREATE TABLE IF NOT EXISTS` la rend idempotente dans
les deux cas (aucune erreur si la table existe déjà).

Revision ID: 724552ea2cc8
Revises: 272544c7b5ae
Create Date: 2026-08-24
"""

from alembic import op

revision = "724552ea2cc8"
down_revision = "272544c7b5ae"
branch_labels = None
depends_on = None

_UPGRADE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email                    VARCHAR(255) NOT NULL UNIQUE,
    nom                       VARCHAR(150),
    mot_de_passe_hache         VARCHAR(255) NOT NULL,
    actif                       BOOLEAN NOT NULL DEFAULT TRUE,
    date_creation                TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
"""

_DOWNGRADE_SQL = "DROP TABLE IF EXISTS users;"


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)
