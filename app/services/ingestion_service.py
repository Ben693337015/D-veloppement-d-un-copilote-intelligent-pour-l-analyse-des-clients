"""Service métier — Import universel de données PME (Module A, mutualisé).

Couche d'orchestration entre le router `/api/v1/ingestion/` et les scripts
bas niveau de `app/ingestion/`. Respecte le principe "un routeur ne contient
jamais de logique métier" (cf. README §2) : ce fichier fait le lien entre
la requête HTTP (fichier uploadé + mapping choisi) et les fonctions pures
de app/ingestion/generic_*.py.

Rafraîchissement automatique post-import (cf. `_rafraichir_segmentation_rfm`) :
un import de transactions écrit les lignes brutes (`clients`, `transactions`)
mais, avant cet ajout, laissait les colonnes DÉRIVÉES de `clients`
(segment_rfm, score_churn, cluster_id) inchangées tant que personne
n'appelait explicitement `POST /marketing/rfm/run` — un nouveau client
importé restait donc avec une segmentation NULL, et le copilote (qui lit
ces mêmes colonnes, cf. `copilot_service.build_context_snapshot`) répondait
avec un état obsolète juste après un import. On déclenche donc maintenant
`rfm_clustering_service.run_full_pipeline` (K-Means, paramètres par défaut)
à la fin de tout import ayant réellement ajouté des lignes.
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
from app.services import rfm_clustering_service


def preview_file(file_bytes: bytes, filename: str) -> dict:
    df = load_any(file_bytes, filename)
    return build_preview(df)


def _rafraichir_segmentation_rfm(db: Session, resultat: ImportResult) -> ImportResult:
    """Recalcule automatiquement la segmentation RFM (K-Means, paramètres
    par défaut de `run_full_pipeline`) après un import de transactions ayant
    effectivement ajouté des lignes exploitables — pour que `clients.segment_rfm`
    /`score_churn`/`cluster_id` ne restent jamais périmés en base entre deux
    imports, sans que l'utilisateur ait à rappeler `POST /marketing/rfm/run`
    lui-même.

    Reste VOLONTAIREMENT sur l'algorithme par défaut (kmeans) plutôt que de
    deviner le dernier algorithme choisi (non persisté ailleurs en base) :
    un choix explicite de `dbscan`/`gmm` via `POST /marketing/rfm/run` reste
    possible et disponible à tout moment après cet import automatique — ce
    rafraîchissement garantit juste qu'un état existe TOUJOURS, jamais qu'il
    est optimal pour le cas d'usage métier précis de la PME.

    N'échoue JAMAIS l'import lui-même : si le recalcul automatique échoue
    (ex. façon dont sklearn réagit à un jeu de données dégénéré), l'import
    des lignes brutes reste acquis et un avertissement explicite est ajouté
    plutôt que de renvoyer une erreur HTTP sur un import par ailleurs réussi.
    """
    try:
        diagnostic = rfm_clustering_service.run_full_pipeline(db, dry_run=False)
    except Exception as exc:  # noqa: BLE001 — ne jamais faire échouer un import déjà commité
        resultat.avertissements.append(
            f"Segmentation RFM non recalculée automatiquement après cet import ({exc}) "
            "— relancer manuellement POST /marketing/rfm/run."
        )
        return resultat

    if diagnostic.n_clients == 0:
        return resultat

    if diagnostic.k_choisi is not None:
        sil = f", silhouette={diagnostic.silhouette_choisi:.3f}" if diagnostic.silhouette_choisi is not None else ""
        resultat.avertissements.append(
            f"Segmentation RFM recalculée automatiquement (K-Means, k={diagnostic.k_choisi}{sil}) "
            f"sur {diagnostic.n_clients} client(s) — POST /marketing/rfm/run reste disponible pour "
            "choisir un autre algorithme (dbscan, gmm) ou une autre fenêtre d'analyse."
        )
    resultat.avertissements.extend(diagnostic.avertissements)
    return resultat


def import_transactions(
    db: Session, file_bytes: bytes, filename: str, mapping: TransactionColumnMapping
) -> ImportResult:
    df: pd.DataFrame = load_any(file_bytes, filename)
    resultat = import_transactions_generic(db, df, mapping)
    if resultat.n_lignes_importees > 0:
        resultat = _rafraichir_segmentation_rfm(db, resultat)
    return resultat


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
