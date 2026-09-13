"""Import générique -> table `stocks_produits`.

Permet à une PME d'uploader son propre export de stock (tableur, ERP) au
lieu de dépendre uniquement de `simulate_tresorerie_stocks.py` (simulation
Faker/NumPy utilisée en l'absence de données réelles, cf. README §1).

Comportement upsert : si `code_produit` existe déjà, la ligne est mise à
jour (quantité, seuils...) plutôt que dupliquée — un ré-import du même
export (ex. export hebdomadaire) doit rafraîchir l'état du stock, pas créer
de doublons.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from app.models import StockProduit
from app.schemas.ingestion import ImportResult, StockColumnMapping


def import_stock_generic(db: Session, df: pd.DataFrame, mapping: StockColumnMapping) -> ImportResult:
    n_lignes_lues = len(df)
    avertissements: list[str] = []

    for col in (mapping.code_produit_col, mapping.quantite_disponible_col):
        if col not in df.columns:
            raise ValueError(f"Colonne mappée introuvable dans le fichier : '{col}'")

    df = df.copy()
    df["_code"] = df[mapping.code_produit_col].astype(str).str.strip()
    df["_qte"] = pd.to_numeric(df[mapping.quantite_disponible_col], errors="coerce")

    mask_valide = df["_code"].notna() & (df["_code"] != "") & df["_qte"].notna()
    n_rejetees = int((~mask_valide).sum())
    df = df[mask_valide].copy()
    if n_rejetees:
        avertissements.append(f"{n_rejetees} ligne(s) rejetée(s) : code produit ou quantité manquant/invalide.")

    n_avant_dedup = len(df)
    df = df.drop_duplicates(subset=["_code"], keep="last")
    n_doublons = n_avant_dedup - len(df)
    if n_doublons:
        avertissements.append(
            f"{n_doublons} code(s) produit dupliqué(s) dans le fichier : seule la dernière ligne a été conservée."
        )

    n_crees, n_maj = 0, 0
    for _, r in df.iterrows():
        stock = db.query(StockProduit).filter(StockProduit.code_produit == r["_code"]).first()
        if stock is None:
            stock = StockProduit(code_produit=r["_code"], quantite_disponible=int(r["_qte"]))
            db.add(stock)
            n_crees += 1
        else:
            stock.quantite_disponible = int(r["_qte"])
            n_maj += 1

        if mapping.nom_produit_col and pd.notna(r.get(mapping.nom_produit_col)):
            stock.nom_produit = str(r[mapping.nom_produit_col])
        if mapping.seuil_alerte_col and pd.notna(r.get(mapping.seuil_alerte_col)):
            stock.seuil_alerte = int(r[mapping.seuil_alerte_col])
        if mapping.seuil_reappro_col and pd.notna(r.get(mapping.seuil_reappro_col)):
            stock.seuil_reapprovisionnement = int(r[mapping.seuil_reappro_col])
        if mapping.cout_unitaire_col and pd.notna(r.get(mapping.cout_unitaire_col)):
            stock.cout_unitaire = float(r[mapping.cout_unitaire_col])
        if mapping.delai_livraison_col and pd.notna(r.get(mapping.delai_livraison_col)):
            stock.delai_livraison_jours = int(r[mapping.delai_livraison_col])

    db.commit()

    return ImportResult(
        cible="stocks",
        n_lignes_lues=n_lignes_lues,
        n_lignes_importees=len(df),
        n_lignes_rejetees=n_rejetees,
        n_doublons_supprimes=n_doublons,
        n_enregistrements_crees=n_crees,
        n_enregistrements_maj=n_maj,
        avertissements=avertissements,
    )
