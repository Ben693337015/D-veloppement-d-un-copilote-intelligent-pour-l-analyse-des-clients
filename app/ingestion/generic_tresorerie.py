"""Import générique -> table `tresorerie`.

Alternative réelle à `simulate_tresorerie_stocks.simulate_tresorerie()`
(simulation Faker/NumPy) quand la PME dispose d'un export bancaire ou d'un
livre de caisse. Le solde cumulé est recalculé chronologiquement SUR TOUTE
LA TABLE après import (cf. `_recalculer_soldes`), pas seulement pour les
nouvelles lignes — pour rester cohérent même si le fichier importé (ou un
import précédent) n'est pas trié par date, ou si les mouvements importés
sont antérieurs à des mouvements déjà en base (backfill d'un relevé
bancaire, import de plusieurs fichiers dans le désordre).
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


def _recalculer_soldes(db: Session, ids_deja_en_base: set) -> int:
    """Recalcule `solde_apres_mouvement` sur TOUTE la table `tresorerie`,
    dans l'ordre chronologique — jamais seulement pour les lignes du dernier
    import.

    BUG CORRIGÉ : la version précédente amorçait le solde cumulé sur le
    `solde_apres_mouvement` de la ligne la plus RÉCENTE déjà en base (tri
    par date décroissante), puis ajoutait les nouvelles lignes PAR-DESSUS,
    dans leur seul ordre interne. Correct uniquement si le nouvel import ne
    contient QUE des mouvements postérieurs à tout ce qui existe déjà. Un
    import a posteriori de mouvements plus anciens produisait alors un solde
    cumulé FAUX pour les nouvelles lignes ET laissait les lignes déjà en
    base (chronologiquement après les nouvelles) avec un solde qui ne tenait
    jamais compte de ces mouvements plus anciens — aucun mécanisme ne les
    recalculait après coup. Reconstruire le cumul sur l'intégralité de la
    table, dans l'ordre chronologique, à chaque import corrige les deux cas
    et reste correct quel que soit l'ordre dans lequel les fichiers sont
    importés au fil du temps.

    Retourne le nombre de lignes DÉJÀ EN BASE avant cet import dont le solde
    cumulé a réellement changé (distinct des lignes nouvellement créées,
    dont le solde est de toute façon renseigné pour la première fois) — sert
    à signaler explicitement, dans `ImportResult.avertissements`, qu'un
    import a rétroactivement corrigé des soldes déjà affichés au dashboard.
    """
    mouvements = db.query(Tresorerie).order_by(Tresorerie.date_mouvement, Tresorerie.created_at).all()
    solde = 0.0
    n_soldes_ajustes = 0
    for m in mouvements:
        solde += float(m.montant) if m.type_mouvement == "encaissement" else -float(m.montant)
        ancien = float(m.solde_apres_mouvement) if m.solde_apres_mouvement is not None else None
        if ancien is None or abs(ancien - solde) > 1e-9:
            m.solde_apres_mouvement = solde
            if m.mouvement_id in ids_deja_en_base:
                n_soldes_ajustes += 1
    return n_soldes_ajustes


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

    # Snapshot des lignes déjà en base AVANT cet import, pour distinguer
    # ensuite (dans `_recalculer_soldes`) les soldes rétroactivement ajustés
    # des soldes simplement renseignés pour la première fois.
    ids_deja_en_base = {m.mouvement_id for m in db.query(Tresorerie.mouvement_id).all()}

    n_crees = 0
    for _, r in df.iterrows():
        montant_brut = float(r["_montant"])
        type_col_val = r.get(mapping.type_mouvement_col) if mapping.type_mouvement_col else None
        type_mouvement = _normaliser_type_mouvement(type_col_val, montant_brut)
        montant_abs = abs(montant_brut)

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
                solde_apres_mouvement=None,  # renseigné par _recalculer_soldes ci-dessous
                commentaire="Import fichier PME (mapping utilisateur)",
            )
        )
        n_crees += 1

    db.flush()  # nécessaire pour que les nouvelles lignes aient un mouvement_id avant le recalcul
    n_soldes_ajustes = _recalculer_soldes(db, ids_deja_en_base)
    db.commit()

    if n_soldes_ajustes:
        avertissements.append(
            f"{n_soldes_ajustes} mouvement(s) déjà en base ont vu leur solde cumulé RÉTROACTIVEMENT "
            "ajusté suite à cet import (mouvements importés antérieurs à des mouvements existants)."
        )

    return ImportResult(
        cible="tresorerie",
        n_lignes_lues=n_lignes_lues,
        n_lignes_importees=len(df),
        n_lignes_rejetees=n_rejetees,
        n_doublons_supprimes=0,
        n_enregistrements_crees=n_crees,
        n_enregistrements_maj=n_soldes_ajustes,
        avertissements=avertissements,
    )
