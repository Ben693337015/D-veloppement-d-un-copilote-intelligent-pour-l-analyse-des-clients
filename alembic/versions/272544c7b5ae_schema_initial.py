"""Schéma initial — clients, transactions, stocks_produits, tresorerie.

BUG CORRIGÉ : `alembic/versions/` était vide alors que `Makefile` expose une
cible `migrate` (`alembic upgrade head`) et que `README.md` la documente
comme méthode d'installation "sans Docker". Sans cette migration,
`alembic upgrade head` ne créait AUCUNE table — seul `scripts/init_db.sql`
(exécuté automatiquement par le conteneur PostgreSQL au premier démarrage,
cf. docker-compose.yml) créait réellement le schéma. Quiconque suivait le
chemin "sans Docker" du README se serait retrouvé avec une base vide.

Cette migration réutilise le DDL de `scripts/init_db.sql` tel quel (via
`op.execute`) plutôt que de le redéfinir avec les primitives Alembic/Core :
un seul et même DDL fait autorité pour les deux chemins de déploiement
(Docker et migration manuelle), au lieu de deux définitions de schéma
maintenues séparément et risquant de diverger silencieusement.

Revision ID: 272544c7b5ae
Revises:
Create Date: 2026-08-22
"""

from pathlib import Path

from alembic import op

revision = "272544c7b5ae"
down_revision = None
branch_labels = None
depends_on = None

_INIT_SQL_PATH = Path(__file__).resolve().parents[2] / "scripts" / "init_db.sql"

_DOWNGRADE_SQL = """
DROP VIEW IF EXISTS v_kpis_globaux;
DROP TABLE IF EXISTS tresorerie;
DROP TABLE IF EXISTS stocks_produits;
DROP TABLE IF EXISTS transactions;
DROP TABLE IF EXISTS clients;
"""


def upgrade() -> None:
    sql = _INIT_SQL_PATH.read_text(encoding="utf-8")
    op.execute(sql)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)
