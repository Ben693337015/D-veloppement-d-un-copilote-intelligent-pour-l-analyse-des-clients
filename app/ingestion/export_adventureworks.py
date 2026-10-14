"""AdventureWorks (Microsoft) -> 3 fichiers prêts à importer, SANS SQL Server.

Microsoft publie l'entrepôt de données d'exemple AdventureWorksDW sous forme
de fichiers CSV bruts (séparateur « | », sans en-tête) dans son dépôt GitHub
officiel. Ce script les télécharge, les joint et produit :

  ventes.csv      -> onglet « Ventes »      (FactInternetSales + DimCustomer + DimGeography + DimProduct)
  stock.csv       -> onglet « Stock »       (FactProductInventory + DimProduct)
  tresorerie.csv  -> onglet « Trésorerie »  (FactFinance, scénario « Actual », divisions USA)
  LISEZMOI.md     -> provenance et choix de transformation (section « origine des données »)

  Source  : https://github.com/microsoft/sql-server-samples/tree/master/samples/databases/adventure-works/data-warehouse-install-script
  Licence : MIT (dépôt microsoft/sql-server-samples)
  Entreprise FICTIVE : Adventure Works Cycles, fabricant-distributeur de vélos
  et d'accessoires. Données simulées par Microsoft — à présenter comme telles.

Usage :
  python -m app.ingestion.export_adventureworks                  # télécharge + construit
  python -m app.ingestion.export_adventureworks --brut data/raw/aw --sortie data/adventureworks

Les fonctions `construire_*` sont pures (DataFrames bruts -> DataFrame de
sortie) et testées dans tests/test_export_adventureworks.py.
"""

from __future__ import annotations

import argparse
import csv
import urllib.request
from pathlib import Path

import pandas as pd

URL_BASE = (
    "https://raw.githubusercontent.com/microsoft/sql-server-samples/master/"
    "samples/databases/adventure-works/data-warehouse-install-script"
)

# Colonnes des fichiers bruts, dans l'ordre du script officiel instawdbdw.sql
# (les CSV Microsoft n'ont pas de ligne d'en-tête).
COLONNES_BRUTES = {
    "FactInternetSales": (
        "ProductKey OrderDateKey DueDateKey ShipDateKey CustomerKey PromotionKey CurrencyKey "
        "SalesTerritoryKey SalesOrderNumber SalesOrderLineNumber RevisionNumber OrderQuantity "
        "UnitPrice ExtendedAmount UnitPriceDiscountPct DiscountAmount ProductStandardCost "
        "TotalProductCost SalesAmount TaxAmt Freight CarrierTrackingNumber CustomerPONumber "
        "OrderDate DueDate ShipDate"
    ).split(),
    "DimCustomer": (
        "CustomerKey GeographyKey CustomerAlternateKey Title FirstName MiddleName LastName "
        "NameStyle BirthDate MaritalStatus Suffix Gender EmailAddress YearlyIncome TotalChildren "
        "NumberChildrenAtHome EnglishEducation SpanishEducation FrenchEducation EnglishOccupation "
        "SpanishOccupation FrenchOccupation HouseOwnerFlag NumberCarsOwned AddressLine1 "
        "AddressLine2 Phone DateFirstPurchase CommuteDistance"
    ).split(),
    "DimGeography": (
        "GeographyKey City StateProvinceCode StateProvinceName CountryRegionCode "
        "EnglishCountryRegionName SpanishCountryRegionName FrenchCountryRegionName PostalCode "
        "SalesTerritoryKey IpAddressLocator"
    ).split(),
    "DimProduct": (
        "ProductKey ProductAlternateKey ProductSubcategoryKey WeightUnitMeasureCode "
        "SizeUnitMeasureCode EnglishProductName SpanishProductName FrenchProductName StandardCost "
        "FinishedGoodsFlag Color SafetyStockLevel ReorderPoint ListPrice Size SizeRange Weight "
        "DaysToManufacture ProductLine DealerPrice Class Style ModelName LargePhoto "
        "EnglishDescription FrenchDescription ChineseDescription ArabicDescription "
        "HebrewDescription ThaiDescription GermanDescription JapaneseDescription "
        "TurkishDescription StartDate EndDate Status"
    ).split(),
    "FactProductInventory": "ProductKey DateKey MovementDate UnitCost UnitsIn UnitsOut UnitsBalance".split(),
    "FactFinance": (
        "FinanceKey DateKey OrganizationKey DepartmentGroupKey ScenarioKey AccountKey Amount Date"
    ).split(),
}

COLONNES_VENTES = [
    "date_facture", "numero_facture", "code_client", "nom_client", "email_client", "pays",
    "code_produit", "description", "quantite", "montant",
]
COLONNES_STOCK = [
    "code_produit", "nom_produit", "quantite_disponible", "seuil_alerte",
    "seuil_reapprovisionnement", "cout_unitaire", "delai_livraison_jours",
]
COLONNES_TRESORERIE = ["date_mouvement", "type_mouvement", "montant", "categorie"]

MAPPING_ATTENDU = {
    "transactions": {
        "date_col": "date_facture", "client_col": "code_client", "montant_col": "montant",
        "quantite_col": "quantite", "code_produit_col": "code_produit",
        "description_produit_col": "description", "numero_facture_col": "numero_facture",
        "pays_col": "pays", "nom_client_col": "nom_client", "email_client_col": "email_client",
    },
    "stocks": {
        "code_produit_col": "code_produit", "nom_produit_col": "nom_produit",
        "quantite_disponible_col": "quantite_disponible", "seuil_alerte_col": "seuil_alerte",
        "seuil_reappro_col": "seuil_reapprovisionnement", "cout_unitaire_col": "cout_unitaire",
        "delai_livraison_col": "delai_livraison_jours",
    },
    "tresorerie": {
        "date_col": "date_mouvement", "type_mouvement_col": "type_mouvement",
        "montant_col": "montant", "categorie_col": "categorie",
    },
}

# ── Trésorerie : comptes du résultat retenus comme flux de caisse ───────────
# FactFinance est un grand livre mensuel (compte de résultat + bilan), pas un
# relevé bancaire. On reconstitue les flux par la méthode directe simplifiée :
# produits encaissés et charges décaissées, en EXCLUANT les écritures sans
# mouvement d'argent (amortissements, ventes intra-groupe, écarts de change)
# et tout le bilan. Clé = AccountKey de DimAccount.
COMPTES_TRESORERIE = {
    101: ("encaissement", "Ventes encaissées"),           # Trade Sales
    89: ("encaissement", "Produits financiers"),          # Interest Income
    92: ("encaissement", "Autres produits"),              # Other Income
    91: ("encaissement", "Cessions d'actifs"),            # Gain/Loss on Sales of Asset
    53: ("decaissement", "Retours et remises clients"),   # Returns and Adjustments
    54: ("decaissement", "Retours et remises clients"),   # Discounts
    56: ("decaissement", "Achats et coût des ventes"),    # Standard Cost of Sales
    57: ("decaissement", "Achats et coût des ventes"),    # Variances
    60: ("decaissement", "Salaires et charges sociales"),  # Salaries
    61: ("decaissement", "Salaires et charges sociales"),  # Payroll Taxes
    62: ("decaissement", "Salaires et charges sociales"),  # Employee Benefits
    63: ("decaissement", "Commissions"),
    65: ("decaissement", "Déplacements"), 66: ("decaissement", "Déplacements"),
    67: ("decaissement", "Déplacements"), 68: ("decaissement", "Déplacements"),
    69: ("decaissement", "Déplacements"),
    71: ("decaissement", "Marketing"), 72: ("decaissement", "Marketing"),
    73: ("decaissement", "Fournitures de bureau"),
    74: ("decaissement", "Honoraires"),
    76: ("decaissement", "Téléphone et énergie"), 77: ("decaissement", "Téléphone et énergie"),
    78: ("decaissement", "Autres charges"),
    87: ("decaissement", "Loyers"),
    90: ("decaissement", "Intérêts d'emprunt"),
    94: ("decaissement", "Impôts"),
}
SCENARIO_REEL = 1                       # DimScenario : 1 = Actual (2 = Budget, 3 = Forecast)
ORGANISATIONS_USA = {3, 4, 5, 6, 7}     # Divisions de « USA Operations », toutes en USD


def code_client(alternate_key: str) -> str:
    # "AW00011000" -> "CLI-11000" : évite tout code commençant par A + chiffre.
    return f"CLI-{int(str(alternate_key)[2:])}"


# ── Lecture des CSV Microsoft ──────────────────────────────────────────────

def lire_brut(chemin: Path, table: str) -> pd.DataFrame:
    return pd.read_csv(
        chemin, sep="|", header=None, names=COLONNES_BRUTES[table], quoting=csv.QUOTE_NONE,
        dtype=str, keep_default_na=False, encoding="utf-8",
    )


def telecharger(dossier: Path) -> dict[str, Path]:
    dossier.mkdir(parents=True, exist_ok=True)
    chemins = {}
    for table in COLONNES_BRUTES:
        chemin = dossier / f"{table}.csv"
        if not chemin.exists():
            print(f"Téléchargement de {table}.csv…")
            urllib.request.urlretrieve(f"{URL_BASE}/{table}.csv", chemin)
        chemins[table] = chemin
    return chemins


# ── Transformations pures ──────────────────────────────────────────────────

def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.replace("", None))


def produits_courants(produits: pd.DataFrame) -> pd.DataFrame:
    """DimProduct garde plusieurs versions d'un même article (historique des
    prix). Un article = ProductAlternateKey ; on garde sa version la plus récente."""
    p = produits.assign(ProductKey=_num(produits["ProductKey"]).astype(int))
    return p.sort_values("ProductKey").groupby("ProductAlternateKey", as_index=False).tail(1)


def construire_ventes(ventes: pd.DataFrame, clients: pd.DataFrame, geo: pd.DataFrame,
                      produits: pd.DataFrame) -> pd.DataFrame:
    """Une ligne = une ligne de commande web. Montant = SalesAmount (HT, hors
    taxe et frais de port), en USD."""
    c = clients.merge(geo[["GeographyKey", "EnglishCountryRegionName"]], on="GeographyKey", how="left")
    c = c.assign(nom=(c["FirstName"].str.strip() + " " + c["LastName"].str.strip()).str.strip())
    p = produits[["ProductKey", "ProductAlternateKey", "EnglishProductName"]]
    df = ventes.merge(c, on="CustomerKey", how="left").merge(p, on="ProductKey", how="left")
    sortie = pd.DataFrame({
        "date_facture": df["OrderDate"].str[:10],
        "numero_facture": df["SalesOrderNumber"],
        "code_client": df["CustomerAlternateKey"].map(code_client),
        "nom_client": df["nom"],
        "email_client": df["EmailAddress"],
        "pays": df["EnglishCountryRegionName"],
        "code_produit": df["ProductAlternateKey"],
        "description": df["EnglishProductName"],
        "quantite": _num(df["OrderQuantity"]).astype(int),
        "montant": _num(df["SalesAmount"]).round(2),
    })
    return sortie.sort_values(["date_facture", "numero_facture"])[COLONNES_VENTES].reset_index(drop=True)


def construire_stock(inventaire: pd.DataFrame, produits: pd.DataFrame, codes_vendus: set[str],
                     date_photo: str) -> tuple[pd.DataFrame, int]:
    """État du stock des articles vendus en ligne, à `date_photo` (dernière
    date de vente : ventes et stock décrivent alors le même instant).

    Seuils officiels AdventureWorks (DimProduct), dans le sens attendu par
    analytics_service.get_stock_alertes (rupture <= seuil_alerte <
    seuil_reapprovisionnement) :
      seuil_alerte              = ReorderPoint      (point de commande)
      seuil_reapprovisionnement = SafetyStockLevel  (stock de sécurité, toujours >= ReorderPoint)
    Retourne aussi le nombre de stocks négatifs ramenés à 0 (incohérence de la source).
    """
    courants = produits_courants(produits)
    courants = courants[courants["ProductAlternateKey"].isin(codes_vendus)]
    inv = inventaire.assign(ProductKey=_num(inventaire["ProductKey"]).astype(int))
    inv = inv[inv["MovementDate"].str[:10] <= date_photo]
    dernier = inv.sort_values("MovementDate").groupby("ProductKey", as_index=False).tail(1)
    df = courants.merge(dernier[["ProductKey", "UnitsBalance"]], on="ProductKey", how="left")

    quantite = _num(df["UnitsBalance"]).fillna(0).astype(int)
    n_negatifs = int((quantite < 0).sum())
    alerte = _num(df["ReorderPoint"]).fillna(0).astype(int)
    securite = _num(df["SafetyStockLevel"]).fillna(0).astype(int)
    sortie = pd.DataFrame({
        "code_produit": df["ProductAlternateKey"],
        "nom_produit": df["EnglishProductName"],
        "quantite_disponible": quantite.clip(lower=0),
        "seuil_alerte": alerte,
        "seuil_reapprovisionnement": securite.where(securite >= alerte, alerte),
        "cout_unitaire": _num(df["StandardCost"]).round(2),
        "delai_livraison_jours": _num(df["DaysToManufacture"]).fillna(0).astype(int),
    })
    return sortie.sort_values("code_produit")[COLONNES_STOCK].reset_index(drop=True), n_negatifs


def construire_tresorerie(finance: pd.DataFrame) -> pd.DataFrame:
    """Flux mensuels encaissés/décaissés des divisions USA, scénario réel.

    Le sens vient de la nature du compte ; un montant négatif sur un compte
    (ex. moins-value de cession) inverse le sens. Les comptes sont regroupés
    en catégories lisibles, un mouvement par mois et par catégorie.
    """
    f = finance.assign(
        ScenarioKey=_num(finance["ScenarioKey"]).astype(int),
        OrganizationKey=_num(finance["OrganizationKey"]).astype(int),
        AccountKey=_num(finance["AccountKey"]).astype(int),
        Amount=_num(finance["Amount"]).astype(float),
    )
    f = f[(f["ScenarioKey"] == SCENARIO_REEL) & f["OrganizationKey"].isin(ORGANISATIONS_USA)
          & f["AccountKey"].isin(COMPTES_TRESORERIE)]
    sens = f["AccountKey"].map(lambda k: COMPTES_TRESORERIE[k][0])
    flux = f["Amount"].where(sens == "encaissement", -f["Amount"])
    df = pd.DataFrame({
        "date_mouvement": f["Date"].str[:10],
        "categorie": f["AccountKey"].map(lambda k: COMPTES_TRESORERIE[k][1]),
        "flux": flux,
    })
    df = df.groupby(["date_mouvement", "categorie"], as_index=False)["flux"].sum()
    df = df[df["flux"].round(2) != 0]
    df["type_mouvement"] = df["flux"].map(lambda v: "encaissement" if v > 0 else "decaissement")
    df["montant"] = df["flux"].abs().round(2)
    return df.sort_values(["date_mouvement", "categorie"])[COLONNES_TRESORERIE].reset_index(drop=True)


def verifier_mapping_auto(chemin: Path, cible: str) -> list[str]:
    """Relit le fichier avec le VRAI chargeur de l'application et vérifie que
    l'écran « Données » proposera le bon mapping (liste vide = OK)."""
    from app.ingestion.generic_loader import build_preview, load_any

    df = load_any(chemin.read_bytes(), chemin.name)
    suggestion = build_preview(df.head(200))["mapping_suggere"][cible]
    return [
        f"{champ}: proposé {suggestion.get(champ)!r}, attendu {attendu!r}"
        for champ, attendu in MAPPING_ATTENDU[cible].items()
        if suggestion.get(champ) != attendu
    ]


# ── Programme principal ─────────────────────────────────────────────────────

def ecrire_lisezmoi(sortie: Path, ventes: pd.DataFrame, stock: pd.DataFrame,
                    treso: pd.DataFrame, n_negatifs: int, date_photo: str) -> None:
    solde = treso["montant"].where(treso["type_mouvement"] == "encaissement", -treso["montant"]).sum()
    texte = f"""# Jeu de données AdventureWorks — export pour Copilote IA PME

Source : entrepôt d'exemple officiel Microsoft **AdventureWorksDW**, fichiers CSV du dépôt
`microsoft/sql-server-samples` (licence MIT) :
{URL_BASE.replace("raw.githubusercontent.com", "github.com").replace("/master/", "/tree/master/")}

Entreprise **fictive** (Adventure Works Cycles, vélos et accessoires) : données simulées par
Microsoft, à présenter comme telles au jury. Montants en dollars US (USD).

| Fichier | Onglet | Lignes | Période | Tables d'origine |
|---|---|---|---|---|
| ventes.csv | Ventes | {len(ventes)} | {ventes['date_facture'].min()} -> {ventes['date_facture'].max()} | FactInternetSales, DimCustomer, DimGeography, DimProduct |
| stock.csv | Stock | {len(stock)} | état au {date_photo} | FactProductInventory, DimProduct |
| tresorerie.csv | Trésorerie | {len(treso)} | {treso['date_mouvement'].min()} -> {treso['date_mouvement'].max()} | FactFinance, DimAccount |

Clients : {ventes['code_client'].nunique()} — commandes : {ventes['numero_facture'].nunique()} —
articles vendus : {ventes['code_produit'].nunique()} — pays : {ventes['pays'].nunique()}.

## Choix de transformation (à reprendre dans le rapport)

**Ventes** — ventes web aux particuliers. `montant` = SalesAmount, hors taxes et frais de port.
Une commande sur plusieurs lignes garde le même `numero_facture`. Codes : `CLI-11000` = client
AW00011000 ; `code_produit` = référence article Microsoft (ex. `BK-R93R-62`), commune au stock.

**Stock** — les {len(stock)} articles vendus en ligne, état au {date_photo} (dernière date de vente).
`seuil_alerte` = ReorderPoint (point de commande), `seuil_reapprovisionnement` = SafetyStockLevel
(stock de sécurité), `cout_unitaire` = StandardCost, `delai_livraison_jours` = DaysToManufacture
(délai de fabrication, 0 à 4 jours). {n_negatifs} stock(s) négatif(s) dans la source, ramené(s) à 0.

**Trésorerie** — reconstituée par la méthode directe simplifiée depuis le grand livre FactFinance
(scénario réel, divisions USA pour rester en une seule devise) : produits = encaissements,
charges = décaissements, un mouvement par mois et par catégorie. Exclus car sans mouvement
d'argent : amortissements, ventes intra-groupe, écarts de change, comptes de bilan.
Limites : c'est une approximation par le résultat (pas de décalage de paiement clients et
fournisseurs). Le solde part de 0 (pas de solde bancaire d'ouverture). Solde cumulé final :
{solde:,.0f} USD. Ce grand livre couvre l'ensemble de l'entreprise (revendeurs compris), pas
seulement les ventes web du fichier ventes.csv.

Ordre d'import conseillé : ventes.csv, stock.csv, tresorerie.csv.
"""
    (sortie / "LISEZMOI.md").write_text(texte, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Construit ventes/stock/trésorerie depuis AdventureWorks.")
    p.add_argument("--brut", default="data/raw/adventureworks", help="Dossier des CSV Microsoft.")
    p.add_argument("--sortie", default="data/adventureworks")
    args = p.parse_args(argv)

    chemins = telecharger(Path(args.brut))
    brut = {table: lire_brut(chemin, table) for table, chemin in chemins.items()}

    ventes = construire_ventes(brut["FactInternetSales"], brut["DimCustomer"],
                               brut["DimGeography"], brut["DimProduct"])
    date_photo = ventes["date_facture"].max()
    stock, n_negatifs = construire_stock(brut["FactProductInventory"], brut["DimProduct"],
                                         set(ventes["code_produit"]), date_photo)
    treso = construire_tresorerie(brut["FactFinance"])

    sortie = Path(args.sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    code_retour = 0
    for cible, nom, df in (("transactions", "ventes.csv", ventes), ("stocks", "stock.csv", stock),
                           ("tresorerie", "tresorerie.csv", treso)):
        chemin = sortie / nom
        df.to_csv(chemin, index=False, encoding="utf-8")
        print(f"{chemin} : {len(df)} lignes, {chemin.stat().st_size / 1e6:.1f} Mo")
        ecarts = verifier_mapping_auto(chemin, cible)
        for e in ecarts:
            print(f"  ATTENTION mapping : {e}")
        code_retour |= bool(ecarts)
    ecrire_lisezmoi(sortie, ventes, stock, treso, n_negatifs, date_photo)
    print(f"Terminé : importer {sortie}/ventes.csv, puis stock.csv, puis tresorerie.csv.")
    return int(code_retour)


if __name__ == "__main__":
    raise SystemExit(main())
