"""Schémas Pydantic — Module d'import universel (cf. Cadrage §3.1, Module A).

Ces schémas matérialisent le contrat entre le Dashboard (frontend, à venir
en Phase 3+) et l'API pour l'écran "Configuration des colonnes requise"
(cf. capture d'écran fournie : Colonne Date / Colonne Client / Colonne
Montant + bouton "Compris — je vérifie la configuration").

Aucun champ de mapping n'est deviné côté serveur sans validation explicite :
le endpoint /preview ne fait QUE suggérer, l'utilisateur doit soumettre le
mapping choisi à /import/*.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ColumnInfo(BaseModel):
    nom: str
    type_detecte: str


class DatasetPreview(BaseModel):
    """Sortie de POST /api/v1/ingestion/preview."""

    nom_fichier_colonnes: list[str]
    n_lignes: int
    n_colonnes: int
    colonnes: list[ColumnInfo]
    apercu_lignes: list[dict]
    mapping_suggere: dict[str, dict[str, str | None]]


class TransactionColumnMapping(BaseModel):
    """Mapping pour l'import dans `clients` + `transactions`.

    Trois façons de fournir le montant/la quantité d'une ligne, au choix
    de la PME (cf. `app.ingestion.generic_transactions._resolve_montant`
    pour le détail des calculs) :
      - `montant_col` seul (montant total de la ligne déjà calculé,
        quantité implicite = 1) ;
      - `quantite_col` + `prix_unitaire_col` (le montant est recalculé) ;
      - `montant_col` + `quantite_col` SANS prix unitaire (le prix
        unitaire implicite est recalculé = montant / quantité, ce qui
        préserve la vraie quantité vendue au lieu de l'écraser à 1).
    `montant_col` seul, ou `quantite_col` + `prix_unitaire_col`, est
    obligatoire (cf. validateur) ; `quantite_col` sans l'un des deux ne
    suffit pas à déterminer un montant.
    """

    date_col: str
    client_col: str
    montant_col: str | None = None
    quantite_col: str | None = None
    prix_unitaire_col: str | None = None
    code_produit_col: str | None = None
    description_produit_col: str | None = None
    numero_facture_col: str | None = None
    pays_col: str | None = None
    nom_client_col: str | None = None
    email_client_col: str | None = None

    @model_validator(mode="after")
    def _check_montant_ou_quantite_prix(self) -> TransactionColumnMapping:
        has_montant = self.montant_col is not None
        has_qte_prix = self.quantite_col is not None and self.prix_unitaire_col is not None
        if not has_montant and not has_qte_prix:
            raise ValueError(
                "Fournir soit 'montant_col' (montant total par ligne), "
                "soit 'quantite_col' ET 'prix_unitaire_col' (le montant sera calculé)."
            )
        return self


class StockColumnMapping(BaseModel):
    code_produit_col: str
    quantite_disponible_col: str
    nom_produit_col: str | None = None
    seuil_alerte_col: str | None = None
    seuil_reappro_col: str | None = None
    cout_unitaire_col: str | None = None
    delai_livraison_col: str | None = None


class TresorerieColumnMapping(BaseModel):
    date_col: str
    montant_col: str
    type_mouvement_col: str | None = Field(
        default=None,
        description=(
            "Colonne indiquant 'encaissement'/'decaissement' (ou équivalent). "
            "Si absente, le signe du montant détermine le type "
            "(positif = encaissement, négatif = décaissement)."
        ),
    )
    categorie_col: str | None = None


class ImportResult(BaseModel):
    """Sortie commune des 3 endpoints d'import — pensée pour être affichée
    directement dans le Dashboard (rapport de qualité façon `prepare_df`
    de BEST_RFM_v2)."""

    cible: str  # "transactions" | "stocks" | "tresorerie"
    n_lignes_lues: int
    n_lignes_importees: int
    n_lignes_rejetees: int
    n_doublons_supprimes: int
    n_enregistrements_crees: int = 0
    n_enregistrements_maj: int = 0
    avertissements: list[str] = []
