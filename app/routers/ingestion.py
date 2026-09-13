"""Router — Import universel de données PME (Module A, mutualisé Maslaw/Abdoulmadjid).

Flux prévu côté Dashboard (cf. capture d'écran fournie, écran "Chargement
des données") :

  1. L'utilisateur dépose un fichier -> POST /preview -> l'API renvoie les
     colonnes détectées + un aperçu + une suggestion de mapping pour
     chacune des 3 cibles (transactions, stocks, tresorerie).
  2. L'utilisateur vérifie/corrige le mapping dans l'écran "Configuration
     des colonnes requise" (jamais appliqué sans validation explicite).
  3. L'utilisateur valide -> POST /import/{cible} avec le même fichier et
     le mapping choisi -> import réel + rapport de qualité.

Ce même écran de mapping sert donc AUSSI BIEN au module RFM/marketing
(Abdoulmadjid, cible "transactions") qu'au module ventes/trésorerie/stocks
(Maslaw, cibles "transactions", "stocks", "tresorerie") — une seule
plateforme d'ingestion pour les deux sous-projets, utilisable par
n'importe quelle PME.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.ingestion.generic_loader import EmptyDatasetError, UnsupportedFileError
from app.schemas.ingestion import (
    DatasetPreview,
    ImportResult,
    StockColumnMapping,
    TransactionColumnMapping,
    TresorerieColumnMapping,
)
from app.services import ingestion_service

router = APIRouter(
    prefix="/ingestion",
    tags=["Ingestion — Import universel PME"],
    dependencies=[Depends(get_current_user)],
)

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 Mo, raisonnable pour un prototype PME


async def _read_upload(file: UploadFile) -> bytes:
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux (limite : 50 Mo).")
    if not content:
        raise HTTPException(status_code=400, detail="Fichier vide.")
    return content


def _parse_mapping(mapping_json: str, schema_cls):
    try:
        raw = json.loads(mapping_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Mapping JSON invalide : {exc}") from exc
    try:
        return schema_cls(**raw)
    except Exception as exc:  # pydantic ValidationError
        raise HTTPException(status_code=422, detail=f"Mapping invalide : {exc}") from exc


@router.post("/preview", response_model=DatasetPreview)
async def preview_dataset(file: UploadFile = File(...)):
    """Charge le fichier et suggère un mapping de colonnes pour les 3 cibles
    possibles (transactions, stocks, tresorerie), sans jamais importer."""
    content = await _read_upload(file)
    try:
        return ingestion_service.preview_file(content, file.filename or "fichier")
    except (UnsupportedFileError, EmptyDatasetError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/import/transactions", response_model=ImportResult)
async def import_transactions(
    file: UploadFile = File(...),
    mapping: str = Form(
        ...,
        description=(
            "Mapping JSON validé par l'utilisateur, ex. "
            '{"date_col": "Order Date", "client_col": "Client ID", "montant_col": "Total"}'
        ),
    ),
    db: Session = Depends(get_db),
):
    """Import mappé -> `clients` + `transactions`. Alimente à la fois le
    module RFM (Abdoulmadjid) et le module ventes/BFR (Maslaw), puisque
    `transactions` est la table partagée (cf. Cadrage §4)."""
    content = await _read_upload(file)
    parsed_mapping = _parse_mapping(mapping, TransactionColumnMapping)
    try:
        return ingestion_service.import_transactions(db, content, file.filename or "fichier", parsed_mapping)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/import/stocks", response_model=ImportResult)
async def import_stocks(
    file: UploadFile = File(...),
    mapping: str = Form(...),
    db: Session = Depends(get_db),
):
    """Import mappé -> `stocks_produits` (module Maslaw)."""
    content = await _read_upload(file)
    parsed_mapping = _parse_mapping(mapping, StockColumnMapping)
    try:
        return ingestion_service.import_stock(db, content, file.filename or "fichier", parsed_mapping)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/import/tresorerie", response_model=ImportResult)
async def import_tresorerie(
    file: UploadFile = File(...),
    mapping: str = Form(...),
    db: Session = Depends(get_db),
):
    """Import mappé -> `tresorerie` (module Maslaw)."""
    content = await _read_upload(file)
    parsed_mapping = _parse_mapping(mapping, TresorerieColumnMapping)
    try:
        return ingestion_service.import_tresorerie(db, content, file.filename or "fichier", parsed_mapping)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
