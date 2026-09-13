"""Ingestion du jeu de données Online Retail II -> tables clients / transactions.

REFACTOR : ce script était auparavant la SEULE façon d'importer des données
(logique de nettoyage entièrement dupliquée et figée sur les colonnes
`CustomerID`, `InvoiceDate`, `UnitPrice`...). Il s'appuie maintenant sur le
pipeline générique (app/ingestion/generic_transactions.py) via un mapping
explicite : Online Retail II est traité comme UN CAS PARTICULIER du import
générique, pas comme un chemin de code séparé. Toute correction de bug
(dates, doublons, retours...) profite désormais aux deux chemins à la fois.

Pour importer les données d'une AUTRE PME (colonnes différentes), utiliser
directement l'API :
    POST /api/v1/ingestion/preview              (upload + suggestion de mapping)
    POST /api/v1/ingestion/import/transactions   (upload + mapping validé)
Ce script CLI reste utile pour l'ingestion batch en une commande, hors API.

Source du dataset (à télécharger manuellement, licence CC BY 4.0) :
    UCI Machine Learning Repository :
        https://archive.ics.uci.edu/dataset/502/online+retail+ii
    Miroir Kaggle :
        https://www.kaggle.com/datasets/mashlyn/online-retail-ii-uci

Colonnes d'origine attendues (feuille Excel/CSV du dataset) :
    InvoiceNo, StockCode, Description, Quantity, InvoiceDate, UnitPrice,
    CustomerID, Country

Usage :
    python -m app.ingestion.load_online_retail --source data/raw/online_retail_II.csv
"""

import argparse

import pandas as pd

from app.core.database import SessionLocal
from app.ingestion.generic_transactions import import_transactions_generic
from app.schemas.ingestion import TransactionColumnMapping

# Mapping figé correspondant aux colonnes officielles d'Online Retail II.
# C'est la SEULE partie spécifique à ce dataset : tout le reste (nettoyage,
# détection des retours, dédoublonnage, get-or-create client) est mutualisé
# avec le pipeline générique utilisé par n'importe quelle autre PME.
ONLINE_RETAIL_MAPPING = TransactionColumnMapping(
    date_col="InvoiceDate",
    client_col="CustomerID",
    quantite_col="Quantity",
    prix_unitaire_col="UnitPrice",
    code_produit_col="StockCode",
    description_produit_col="Description",
    numero_facture_col="InvoiceNo",
    pays_col="Country",
)


def load_online_retail(source_path: str, batch_size: int = 5000) -> None:
    # Chargement direct via pandas : Online Retail II est fourni en CSV avec
    # un encodage latin-1 connu (pas besoin de la détection multi-encodage
    # générique de generic_loader.load_any, réservée aux fichiers PME
    # arbitraires dont on ignore l'encodage a priori).
    df = pd.read_csv(source_path, encoding="ISO-8859-1")

    db = SessionLocal()
    try:
        resultat = import_transactions_generic(db, df, ONLINE_RETAIL_MAPPING, batch_size=batch_size)
        print(
            f"Import terminé : {resultat.n_lignes_lues} lignes lues, "
            f"{resultat.n_lignes_importees} importées, "
            f"{resultat.n_enregistrements_crees} clients créés, "
            f"{resultat.n_lignes_rejetees} rejetées, "
            f"{resultat.n_doublons_supprimes} doublons supprimés."
        )
        for avertissement in resultat.avertissements:
            print(f"  - {avertissement}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="data/raw/online_retail_II.csv",
        help="Chemin local du CSV téléchargé depuis UCI/Kaggle (cf. lien en en-tête du fichier).",
    )
    args = parser.parse_args()
    load_online_retail(args.source)
