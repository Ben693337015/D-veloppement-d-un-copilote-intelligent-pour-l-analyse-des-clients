"""Import générique -> table `tresorerie`.

Alternative réelle à `simulate_tresorerie_stocks.simulate_tresorerie()`
(simulation Faker/NumPy) quand la PME dispose d'un export bancaire ou d'un
livre de caisse. Le solde cumulé est recalculé chronologiquement après
import, pour rester cohérent même si le fichier source n'est pas trié par
date (piège fréquent avec des exports Excel triés par catégorie).
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from app.models import Tresorerie
from app.schemas.ingestion import ImportResult, TresorerieColumnMapping

_ENCAISSEMENT_HINTS = {"encaissement", "credit", "in", "entree", "recette"}


def _normaliser_type_mouvement(valeur, montant: float) -> str:
    if valeur is not None:
        v = str(valeur).strip().lower()
        if v in _ENCAISSEMENT_HINTS or "encaiss" in v or "crédit" in v or "credit" in v:
            return "encaissement"
        return "decaissement"
    # Pas de colonne type fournie : le signe du montant fait foi.
    return "encaissement" if montant >= 0 else "decaissement"


def import_tresorerie_generic(
    db: Session, df: pd.DataFrame, mapping: TresorerieColumnMapping
) -> ImportResult:
    n_lignes_lues = len(df)
    avertissements: list[str] = []

    for col in (mapping.date_col, mapping.montant_col):
        if col not in df.columns:
            raise ValueError(f"Colonne mappée introuvable dans le fichier : '{col}'")

    df = df.copy()
    df["_date"] = pd.to_datetime(df[mapping.date_col], errors="coerce")
    df["_montant"] = pd.to_numeric(df[mapping.montant_col], errors="coerce")

    mask_valide = df["_date"].notna() & df["_montant"].notna()
    n_rejetees = int((~mask_valide).sum())
    df = df[mask_valide].copy()
    if n_rejetees:
        avertissements.append(f"{n_rejetees} ligne(s) rejetée(s) : date ou montant manquant/invalide.")

    # Tri chronologique obligatoire pour un solde cumulé correct.
    df = df.sort_values("_date").reset_index(drop=True)

    solde = (
        db.query(Tresorerie)
        .order_by(Tresorerie.date_mouvement.desc(), Tresorerie.created_at.desc())
        .first()
    )
    solde_courant = float(solde.solde_apres_mouvement) if solde and solde.solde_apres_mouvement is not None else 0.0

    n_crees = 0
    for _, r in df.iterrows():
        montant_brut = float(r["_montant"])
        type_col_val = r.get(mapping.type_mouvement_col) if mapping.type_mouvement_col else None
        type_mouvement = _normaliser_type_mouvement(type_col_val, montant_brut)
        montant_abs = abs(montant_brut)

        solde_courant += montant_abs if type_mouvement == "encaissement" else -montant_abs

        db.add(
            Tresorerie(
                date_mouvement=r["_date"].date(),
                type_mouvement=type_mouvement,
                categorie=(
                    str(r[mapping.categorie_col])
                    if mapping.categorie_col and pd.notna(r.get(mapping.categorie_col))
                    else None
                ),
                montant=montant_abs,
                solde_apres_mouvement=solde_courant,
                commentaire="Import fichier PME (mapping utilisateur)",
            )
        )
        n_crees += 1

    db.commit()

    return ImportResult(
        cible="tresorerie",
        n_lignes_lues=n_lignes_lues,
        n_lignes_importees=len(df),
        n_lignes_rejetees=n_rejetees,
        n_doublons_supprimes=0,
        n_enregistrements_crees=n_crees,
        n_enregistrements_maj=0,
        avertissements=avertissements,
    )
