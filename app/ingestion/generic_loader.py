"""Chargement universel de fichiers PME + détection automatique des colonnes.

Objectif : permettre à N'IMPORTE QUELLE PME d'importer ses propres fichiers
(CSV/Excel export de sa caisse, de son ERP, d'un tableur comptable...) sans
être limitée aux noms de colonnes exacts du dataset Online Retail II.

Inspiré du pattern de détection/preview de l'application desktop
"RFM Analytics Pro" (infrastructure/data_loader.py) : on détecte une
suggestion de mapping, mais on ne l'applique JAMAIS aveuglément — l'appelant
(endpoint /api/v1/ingestion/preview) doit toujours laisser l'utilisateur
vérifier/corriger avant de lancer l'import (cf. capture d'écran fournie par
l'utilisateur : "Vérifiez et corrigez les associations avant de lancer
l'analyse").

Ce module ne touche jamais la base de données : il ne fait que lire et
inspecter un fichier. L'import réel est fait par generic_transactions.py /
generic_stock.py / generic_tresorerie.py.
"""

from __future__ import annotations

import io
from typing import Any

import pandas as pd

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
MAX_PREVIEW_ROWS = 10


class UnsupportedFileError(ValueError):
    """Levée quand le format de fichier n'est pas supporté."""


class EmptyDatasetError(ValueError):
    """Levée quand le fichier ne contient aucune colonne exploitable."""


def load_any(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """Charge un fichier CSV/Excel en DataFrame, quel que soit l'encodage/séparateur.

    CORRECTION (cf. bug connu documenté dans les mémoires du projet :
    des exports PME utilisent souvent ';' comme séparateur — un export
    français d'Excel/Sage/Odoo par exemple — et un encodage latin-1/cp1252
    plutôt qu'utf-8). On essaie plusieurs combinaisons avant d'abandonner,
    au lieu de supposer un unique format comme le faisait
    `load_online_retail.py` (`encoding="ISO-8859-1"` en dur).
    """
    ext = _extension(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileError(
            f"Format non supporté : '{ext}'. Formats acceptés : {sorted(SUPPORTED_EXTENSIONS)}"
        )

    if ext == ".csv":
        last_error: Exception | None = None
        for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252", "ISO-8859-1"):
            for sep in (",", ";", "\t"):
                try:
                    df = pd.read_csv(
                        io.BytesIO(file_bytes), encoding=encoding, sep=sep, engine="python"
                    )
                    if df.shape[1] > 1:  # un vrai découpage en colonnes a eu lieu
                        return _clean_column_names(df)
                except Exception as exc:  # noqa: BLE001 — on essaie la combinaison suivante
                    last_error = exc
                    continue
        raise UnsupportedFileError(
            "Impossible de lire le CSV : encodage ou séparateur non reconnu. "
            f"Dernière erreur : {last_error}"
        )

    # .xlsx / .xls — nécessite openpyxl (.xlsx) et xlrd (.xls), cf. requirements.txt.
    try:
        df = pd.read_excel(io.BytesIO(file_bytes))
    except (ImportError, ValueError) as exc:
        # ImportError : moteur (openpyxl/xlrd) manquant côté serveur.
        # ValueError : fichier corrompu, vide, ou pas un vrai fichier Excel
        # malgré son extension (piège fréquent : un CSV renommé en .xlsx).
        raise UnsupportedFileError(
            f"Lecture de '{ext}' impossible : {exc}"
        ) from exc
    return _clean_column_names(df)


def _clean_column_names(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if df.shape[1] == 0:
        raise EmptyDatasetError("Le fichier ne contient aucune colonne exploitable.")
    return df


def _extension(filename: str) -> str:
    name = filename.lower()
    for ext in SUPPORTED_EXTENSIONS:
        if name.endswith(ext):
            return ext
    # fallback : dernier "." trouvé
    return "." + name.rsplit(".", 1)[-1] if "." in name else ""


# ── DÉTECTION AUTOMATIQUE DE MAPPING PAR CIBLE ───────────────────────────────
# Chaque cible correspond à une table du socle commun (cf. Cadrage §4).
# Les dictionnaires de mots-clés couvrent le français et l'anglais, car une
# PME camerounaise peut exporter dans l'une ou l'autre langue.

_DATE_HINTS = {"date", "jour", "day", "time", "timestamp", "orderdate", "invoicedate", "date_transaction", "date_vente", "date_facture"}
_CLIENT_HINTS = {"client", "customer", "customerid", "client_id", "code_client", "user", "userid", "acheteur"}
_MONTANT_HINTS = {"montant", "amount", "total", "prix", "price", "unitprice", "prix_unitaire", "ca", "revenue", "sales"}
_QUANTITE_HINTS = {"quantite", "quantity", "qty", "qte", "nombre"}
_PRODUIT_CODE_HINTS = {"code_produit", "stockcode", "product_code", "productcode", "prod_code", "item_code", "produit_id", "sku", "reference", "code_article"}
_PRODUIT_NOM_HINTS = {"description", "nom_produit", "product_name", "productname", "item_name", "designation", "libelle", "produit"}
_FACTURE_HINTS = {"invoiceno", "numero_facture", "facture", "invoice", "order_id", "commande"}
_PAYS_HINTS = {"pays", "country", "region"}
_NOM_CLIENT_HINTS = {"nom", "name", "nom_client", "client_name"}
_EMAIL_HINTS = {"email", "mail", "e-mail", "courriel"}

_STOCK_CODE_HINTS = _PRODUIT_CODE_HINTS
_STOCK_QTE_HINTS = {"quantite_disponible", "stock", "quantite", "qty_available", "quantity_on_hand"}
_STOCK_SEUIL_ALERTE_HINTS = {"seuil_alerte", "alert_threshold", "seuil_min", "reorder_point"}
_STOCK_SEUIL_REAPPRO_HINTS = {"seuil_reapprovisionnement", "reorder_qty", "seuil_max"}
_STOCK_COUT_HINTS = {"cout_unitaire", "unit_cost", "cout", "cost"}
_STOCK_DELAI_HINTS = {"delai_livraison", "lead_time", "delai"}

_TRESO_DATE_HINTS = _DATE_HINTS
_TRESO_TYPE_HINTS = {"type_mouvement", "type", "sens", "direction"}
_TRESO_MONTANT_HINTS = _MONTANT_HINTS
_TRESO_CATEGORIE_HINTS = {"categorie", "category", "libelle", "poste"}


def _find_column(df: pd.DataFrame, hints: set[str], numeric_only: bool = False,
                  datetime_check: bool = False, exclude: set[str] | None = None) -> str | None:
    exclude = exclude or set()
    for col in df.columns:
        if col in exclude:
            continue
        normalized = col.lower().strip().replace(" ", "_").replace("-", "_")
        if normalized in hints or any(h in normalized for h in hints):
            if numeric_only and not pd.api.types.is_numeric_dtype(df[col]):
                continue
            if datetime_check:
                try:
                    parsed = pd.to_datetime(df[col], errors="coerce")
                    if parsed.notna().mean() < 0.5:  # moins de 50% de dates valides -> pas la bonne colonne
                        continue
                except Exception:  # noqa: BLE001
                    continue
            return col
    return None


def detect_mapping_transactions(df: pd.DataFrame) -> dict[str, str | None]:
    """Suggestion de mapping pour la table `transactions` (+ `clients`).

    Reflète l'écran "Configuration des colonnes requise" fourni en exemple :
    Colonne Date / Colonne Client / Colonne Montant, complété par les
    colonnes optionnelles nécessaires au schéma réel (quantité, produit...).

    Chaque colonne déjà assignée à un champ est EXCLUE de la recherche des
    champs suivants (évite qu'une colonne comme "Client Name" soit reprise
    à la fois comme `client_col` et `nom_client_col`, ou que "Product Code"
    et "Product Name" se fassent voler leur match l'une par l'autre).
    """
    used: set[str] = set()

    def _assign(hints: set[str], **kwargs) -> str | None:
        col = _find_column(df, hints, exclude=used, **kwargs)
        if col:
            used.add(col)
        return col

    date_col = _assign(_DATE_HINTS, datetime_check=True)
    client_col = _assign(_CLIENT_HINTS)
    montant_col = _assign(_MONTANT_HINTS, numeric_only=True)
    quantite_col = _assign(_QUANTITE_HINTS, numeric_only=True)
    code_produit_col = _assign(_PRODUIT_CODE_HINTS)
    description_produit_col = _assign(_PRODUIT_NOM_HINTS)
    numero_facture_col = _assign(_FACTURE_HINTS)
    pays_col = _assign(_PAYS_HINTS)
    nom_client_col = _assign(_NOM_CLIENT_HINTS)
    email_client_col = _assign(_EMAIL_HINTS)

    return {
        "date_col": date_col,
        "client_col": client_col,
        "montant_col": montant_col,
        "quantite_col": quantite_col,
        "code_produit_col": code_produit_col,
        "description_produit_col": description_produit_col,
        "numero_facture_col": numero_facture_col,
        "pays_col": pays_col,
        "nom_client_col": nom_client_col,
        "email_client_col": email_client_col,
    }


def detect_mapping_stock(df: pd.DataFrame) -> dict[str, str | None]:
    used: set[str] = set()

    def _assign(hints: set[str], **kwargs) -> str | None:
        col = _find_column(df, hints, exclude=used, **kwargs)
        if col:
            used.add(col)
        return col

    return {
        "code_produit_col": _assign(_STOCK_CODE_HINTS),
        "nom_produit_col": _assign(_PRODUIT_NOM_HINTS),
        "quantite_disponible_col": _assign(_STOCK_QTE_HINTS, numeric_only=True),
        "seuil_alerte_col": _assign(_STOCK_SEUIL_ALERTE_HINTS, numeric_only=True),
        "seuil_reappro_col": _assign(_STOCK_SEUIL_REAPPRO_HINTS, numeric_only=True),
        "cout_unitaire_col": _assign(_STOCK_COUT_HINTS, numeric_only=True),
        "delai_livraison_col": _assign(_STOCK_DELAI_HINTS, numeric_only=True),
    }


def detect_mapping_tresorerie(df: pd.DataFrame) -> dict[str, str | None]:
    used: set[str] = set()

    def _assign(hints: set[str], **kwargs) -> str | None:
        col = _find_column(df, hints, exclude=used, **kwargs)
        if col:
            used.add(col)
        return col

    return {
        "date_col": _assign(_TRESO_DATE_HINTS, datetime_check=True),
        "type_mouvement_col": _assign(_TRESO_TYPE_HINTS),
        "montant_col": _assign(_TRESO_MONTANT_HINTS, numeric_only=True),
        "categorie_col": _assign(_TRESO_CATEGORIE_HINTS),
    }


def build_preview(df: pd.DataFrame) -> dict[str, Any]:
    """Construit la charge utile de GET /api/v1/ingestion/preview.

    Renvoie les colonnes détectées, un échantillon de lignes, et une
    suggestion de mapping pour les 3 cibles possibles — à valider/corriger
    côté utilisateur avant l'import réel (jamais d'auto-application).
    """
    sample = df.head(MAX_PREVIEW_ROWS).copy()
    # JSON-safe : NaN -> None, Timestamp -> isoformat
    sample = sample.astype(object).where(pd.notnull(sample), None)
    for col in sample.columns:
        sample[col] = sample[col].apply(
            lambda v: v.isoformat() if hasattr(v, "isoformat") else v
        )

    columns_info = [
        {"nom": col, "type_detecte": str(df[col].dtype)} for col in df.columns
    ]

    return {
        "nom_fichier_colonnes": list(df.columns),
        "n_lignes": int(len(df)),
        "n_colonnes": int(df.shape[1]),
        "colonnes": columns_info,
        "apercu_lignes": sample.to_dict(orient="records"),
        "mapping_suggere": {
            "transactions": detect_mapping_transactions(df),
            "stocks": detect_mapping_stock(df),
            "tresorerie": detect_mapping_tresorerie(df),
        },
    }
