"""Service métier — Import universel de données PME (Module A, mutualisé).

Couche d'orchestration entre le router `/api/v1/ingestion/` et les scripts
bas niveau de `app/ingestion/`. Respecte le principe "un routeur ne contient
jamais de logique métier" (cf. README §2) : ce fichier fait le lien entre
la requête HTTP (fichier uploadé + mapping choisi) et les fonctions pures
de app/ingestion/generic_*.py.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from app.ingestion.generic_loader import build_preview, load_any
from app.ingestion.generic_stock import import_stock_generic
from app.ingestion.generic_transactions import import_transactions_generic
from app.ingestion.generic_tresorerie import import_tresorerie_generic
from app.schemas.ingestion import (
    ImportResult,
    StockColumnMapping,
    TransactionColumnMapping,
    TresorerieColumnMapping,
)


def preview_file(file_bytes: bytes, filename: str) -> dict:
    df = load_any(file_bytes, filename)
    return build_preview(df)


def import_transactions(
    db: Session, file_bytes: bytes, filename: str, mapping: TransactionColumnMapping
) -> ImportResult:
    df: pd.DataFrame = load_any(file_bytes, filename)
    return import_transactions_generic(db, df, mapping)


def import_stock(
    db: Session, file_bytes: bytes, filename: str, mapping: StockColumnMapping
) -> ImportResult:
    df: pd.DataFrame = load_any(file_bytes, filename)
    return import_stock_generic(db, df, mapping)


def import_tresorerie(
    db: Session, file_bytes: bytes, filename: str, mapping: TresorerieColumnMapping
) -> ImportResult:
    df: pd.DataFrame = load_any(file_bytes, filename)
    return import_tresorerie_generic(db, df, mapping)
