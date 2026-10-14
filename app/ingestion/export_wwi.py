"""Extraction de Wide World Importers (Microsoft) -> 3 fichiers prêts à importer.

Pourquoi ce jeu de données (cf. rapport, section « origine des données ») :
Online Retail II ne contient QUE des ventes. Wide World Importers (WWI) est
la base d'exemple officielle de Microsoft pour SQL Server : une PME fictive
de négoce (importateur-grossiste), avec dans UNE MÊME base cohérente les
factures clients, les niveaux de stock et les paiements clients/fournisseurs.
Elle alimente donc les trois onglets de l'écran « Données » et les deux
modules (segmentation RFM + pilotage ventes/stocks/trésorerie), avec des
identifiants produits communs entre ventes et stock.

  Source  : https://github.com/Microsoft/sql-server-samples/releases/tag/wide-world-importers-v1.0
  Doc     : https://learn.microsoft.com/sql/samples/wide-world-importers-oltp-database-catalog
  Licence : MIT (dépôt microsoft/sql-server-samples)
  Période : 01/01/2013 -> 31/05/2016 — données SIMULÉES (à dire au jury).

Fichiers produits (dans --sortie, par défaut data/wwi/) :
  ventes.csv      -> onglet « Ventes (transactions) »  (Sales.Invoices + InvoiceLines)
  stock.csv       -> onglet « Stock »                   (Warehouse.StockItems + StockItemHoldings)
  tresorerie.csv  -> onglet « Trésorerie »              (paiements de Sales.CustomerTransactions
                                                         et Purchasing.SupplierTransactions)
  LISEZMOI.md     -> provenance, volumes, correspondance des colonnes

Les noms de colonnes sont choisis pour que la détection automatique de
`generic_loader.detect_mapping_*` propose d'elle-même le bon mapping : il
suffit de vérifier puis de cliquer « Importer ». Le script le vérifie à la
fin (`verifier_mapping_auto`).

Pré-requis (une seule fois) :
  1. SQL Server dans Docker :
       docker compose -f scripts/wwi/docker-compose.yml up -d
  2. Pilote Python :  pip install pymssql

Usage :
  python -m app.ingestion.export_wwi --telecharger --mot-de-passe "Wwi_Export_2026!"
  python -m app.ingestion.export_wwi --depuis 2015-01-01      # fichier ventes plus léger

Ce module ne touche JAMAIS la base PostgreSQL de l'application : il produit
des fichiers, que l'on importe ensuite par l'interface (ou l'API /ingestion).
Les fonctions `transformer_*` sont pures (DataFrame -> DataFrame) et testées
sans SQL Server (tests/test_export_wwi.py).
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

URL_BAK = (
    "https://github.com/Microsoft/sql-server-samples/releases/download/"
    "wide-world-importers-v1.0/WideWorldImporters-Full.bak"
)
NOM_BAK = "WideWorldImporters-Full.bak"
NOM_BASE = "WideWorldImporters"
# Chemin du .bak VU DEPUIS LE CONTENEUR SQL Server (cf. volume dans
# scripts/wwi/docker-compose.yml : data/raw/wwi -> /var/opt/mssql/backup).
CHEMIN_BAK_CONTENEUR = f"/var/opt/mssql/backup/{NOM_BAK}"

LIMITE_UPLOAD_OCTETS = 50 * 1024 * 1024  # = app/routers/ingestion.py::_MAX_UPLOAD_BYTES

# ── Requêtes SQL (schéma OLTP WideWorldImporters) ───────────────────────────

SQL_VENTES = """
SELECT
    i.InvoiceID      AS invoice_id,
    i.InvoiceDate    AS invoice_date,
    i.IsCreditNote   AS is_credit_note,
    c.CustomerID     AS customer_id,
    c.CustomerName   AS customer_name,
    co.CountryName   AS country,
    il.StockItemID   AS stock_item_id,
    il.Description   AS description,
    il.Quantity      AS quantity,
    il.UnitPrice     AS unit_price
FROM Sales.InvoiceLines il
JOIN Sales.Invoices i               ON i.InvoiceID = il.InvoiceID
JOIN Sales.Customers c              ON c.CustomerID = i.CustomerID
JOIN Application.Cities ci          ON ci.CityID = c.DeliveryCityID
JOIN Application.StateProvinces sp  ON sp.StateProvinceID = ci.StateProvinceID
JOIN Application.Countries co       ON co.CountryID = sp.CountryID
WHERE i.InvoiceDate >= %s AND i.InvoiceDate <= %s
ORDER BY i.InvoiceDate, i.InvoiceID, il.InvoiceLineID
"""

SQL_STOCK = """
SELECT
    si.StockItemID      AS stock_item_id,
    si.StockItemName    AS stock_item_name,
    h.QuantityOnHand    AS quantity_on_hand,
    h.ReorderLevel      AS reorder_level,
    h.TargetStockLevel  AS target_stock_level,
    h.LastCostPrice     AS last_cost_price,
    si.LeadTimeDays     AS lead_time_days
FROM Warehouse.StockItems si
JOIN Warehouse.StockItemHoldings h ON h.StockItemID = si.StockItemID
ORDER BY si.StockItemID
"""

# Seuls les mouvements d'ARGENT (paiements, remboursements) vont en
# trésorerie — jamais les factures, qui sont des créances/dettes, pas du cash.
SQL_TRESORERIE = """
SELECT ct.TransactionDate AS transaction_date, 'client' AS tiers,
       tt.TransactionTypeName AS transaction_type, ct.TransactionAmount AS amount
FROM Sales.CustomerTransactions ct
JOIN Application.TransactionTypes tt ON tt.TransactionTypeID = ct.TransactionTypeID
WHERE (tt.TransactionTypeName LIKE '%%Payment%%' OR tt.TransactionTypeName LIKE '%%Refund%%')
  AND ct.TransactionDate >= %s AND ct.TransactionDate <= %s
UNION ALL
SELECT st.TransactionDate, 'fournisseur',
       tt.TransactionTypeName, st.TransactionAmount
FROM Purchasing.SupplierTransactions st
JOIN Application.TransactionTypes tt ON tt.TransactionTypeID = st.TransactionTypeID
WHERE (tt.TransactionTypeName LIKE '%%Payment%%' OR tt.TransactionTypeName LIKE '%%Refund%%')
  AND st.TransactionDate >= %s AND st.TransactionDate <= %s
"""

# Colonnes de sortie, dans l'ordre : l'ordre compte pour la détection
# automatique (ex. `code_client` doit précéder `nom_client`, car le mot-clé
# "client" est cherché par sous-chaîne et la première colonne trouvée gagne).
COLONNES_VENTES = [
    "date_facture", "numero_facture", "code_client", "nom_client", "pays",
    "code_produit", "description", "quantite", "montant",
]
COLONNES_STOCK = [
    "code_produit", "nom_produit", "quantite_disponible", "seuil_alerte",
    "seuil_reapprovisionnement", "cout_unitaire", "delai_livraison_jours",
]
COLONNES_TRESORERIE = ["date_mouvement", "type_mouvement", "montant", "categorie"]

# Mapping que l'écran « Données » doit proposer tout seul pour chaque fichier.
MAPPING_ATTENDU = {
    "transactions": {
        "date_col": "date_facture", "client_col": "code_client", "montant_col": "montant",
        "quantite_col": "quantite", "code_produit_col": "code_produit",
        "description_produit_col": "description", "numero_facture_col": "numero_facture",
        "pays_col": "pays", "nom_client_col": "nom_client",
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


# ── Identifiants communs ventes <-> stock ───────────────────────────────────
# Préfixes volontairement sans lettre C/A suivie d'un chiffre, pour ne
# jamais déclencher la règle « numéro d'annulation » de l'import.

def code_produit(stock_item_id) -> str:
    return f"SKU-{int(stock_item_id):04d}"


def code_client(customer_id) -> str:
    return f"CLI-{int(customer_id):04d}"


def numero_facture(invoice_id) -> str:
    return f"FAC-{int(invoice_id):06d}"


# ── Transformations pures (testées sans SQL Server) ─────────────────────────

def transformer_ventes(brut: pd.DataFrame) -> pd.DataFrame:
    """Une ligne = une ligne de facture. Montant = quantité x prix unitaire HT.

    Le montant est HORS TAXES (WWI applique une TVA de 15 %) : c'est le
    chiffre d'affaires, base correcte du RFM et des prévisions. Les avoirs
    (IsCreditNote = 1) sont passés en quantité ET montant négatifs, ce que
    l'import reconnaît comme retours (`est_retour`) sans les supprimer.
    """
    df = brut.copy()
    signe = df["is_credit_note"].astype(bool).map({True: -1, False: 1})
    quantite = pd.to_numeric(df["quantity"]).astype(int).abs() * signe
    prix = pd.to_numeric(df["unit_price"]).astype(float)
    sortie = pd.DataFrame({
        "date_facture": pd.to_datetime(df["invoice_date"]).dt.strftime("%Y-%m-%d"),
        "numero_facture": df["invoice_id"].map(numero_facture),
        "code_client": df["customer_id"].map(code_client),
        "nom_client": df["customer_name"].astype(str).str.strip(),
        "pays": df["country"].astype(str).str.strip(),
        "code_produit": df["stock_item_id"].map(code_produit),
        "description": df["description"].astype(str).str.strip(),
        "quantite": quantite,
        "montant": (quantite * prix).round(2),
    })
    return sortie[COLONNES_VENTES]


def transformer_stock(brut: pd.DataFrame) -> pd.DataFrame:
    """Instantané du stock à la dernière date de la base (31/05/2016).

    Correspondance des seuils (sémantique de analytics_service.stock_alerts :
    rupture si quantité <= seuil_alerte, à surveiller si <= seuil_reappro) :
      seuil_alerte              = ReorderLevel      (point de commande WWI :
                                                     en dessous, il faut commander)
      seuil_reapprovisionnement = TargetStockLevel  (niveau cible WWI ; forcé
                                                     >= seuil_alerte)
    """
    df = brut.copy()
    seuil_alerte = pd.to_numeric(df["reorder_level"]).fillna(0).astype(int)
    seuil_reappro = pd.to_numeric(df["target_stock_level"]).fillna(0).astype(int)
    sortie = pd.DataFrame({
        "code_produit": df["stock_item_id"].map(code_produit),
        "nom_produit": df["stock_item_name"].astype(str).str.strip(),
        "quantite_disponible": pd.to_numeric(df["quantity_on_hand"]).fillna(0).astype(int),
        "seuil_alerte": seuil_alerte,
        "seuil_reapprovisionnement": seuil_reappro.where(seuil_reappro >= seuil_alerte, seuil_alerte),
        "cout_unitaire": pd.to_numeric(df["last_cost_price"]).astype(float).round(2),
        "delai_livraison_jours": pd.to_numeric(df["lead_time_days"]).fillna(0).astype(int),
    })
    return sortie[COLONNES_STOCK]


_CATEGORIES = {
    ("client", "encaissement"): "Encaissements clients",
    ("client", "decaissement"): "Remboursements clients",
    ("fournisseur", "decaissement"): "Paiements fournisseurs",
    ("fournisseur", "encaissement"): "Remboursements fournisseurs",
}


def transformer_tresorerie(brut: pd.DataFrame, detail: bool = False) -> pd.DataFrame:
    """Flux de caisse issus des paiements clients et fournisseurs.

    Convention de signe WWI : dans les deux tables de transactions, une
    facture augmente le solde dû (montant > 0) et un paiement le diminue
    (montant < 0). Donc :
      côté client      : flux de caisse = -TransactionAmount  (paiement reçu -> entrée)
      côté fournisseur : flux de caisse = +TransactionAmount  (paiement émis -> sortie)
    Le sens est déduit du SIGNE, pas du libellé, pour traiter aussi les
    remboursements. Par défaut, un mouvement par jour et par catégorie
    (format relevé bancaire, import rapide) ; `detail=True` garde chaque
    paiement.
    """
    df = brut.copy()
    montant = pd.to_numeric(df["amount"]).astype(float)
    flux = montant.where(df["tiers"] == "fournisseur", -montant)
    df = df.assign(
        date_mouvement=pd.to_datetime(df["transaction_date"]).dt.strftime("%Y-%m-%d"),
        type_mouvement=flux.map(lambda v: "encaissement" if v >= 0 else "decaissement"),
        montant=flux.abs(),
    )
    df = df[df["montant"] > 0].copy()
    df["categorie"] = [
        _CATEGORIES[(t, s)] for t, s in zip(df["tiers"], df["type_mouvement"], strict=True)
    ]
    if not detail:
        df = (
            df.groupby(["date_mouvement", "type_mouvement", "categorie"], as_index=False)["montant"]
            .sum()
        )
    df["montant"] = df["montant"].round(2)
    return df.sort_values(["date_mouvement", "categorie"])[COLONNES_TRESORERIE].reset_index(drop=True)


def verifier_mapping_auto(chemin: Path, cible: str) -> list[str]:
    """Relit le fichier produit avec le VRAI chargeur de l'application et
    vérifie que l'écran « Données » proposera le bon mapping. Retourne la
    liste des écarts (vide = OK)."""
    from app.ingestion.generic_loader import build_preview, load_any

    df = load_any(chemin.read_bytes(), chemin.name)
    suggestion = build_preview(df.head(200))["mapping_suggere"][cible]
    return [
        f"{champ}: proposé {suggestion.get(champ)!r}, attendu {attendu!r}"
        for champ, attendu in MAPPING_ATTENDU[cible].items()
        if suggestion.get(champ) != attendu
    ]


# ── Accès SQL Server ────────────────────────────────────────────────────────

def telecharger_bak(dossier: Path) -> Path:
    dossier.mkdir(parents=True, exist_ok=True)
    cible = dossier / NOM_BAK
    if cible.exists() and cible.stat().st_size > 50_000_000:
        print(f"Sauvegarde déjà présente : {cible}")
        return cible
    print(f"Téléchargement de {URL_BAK} (~120 Mo)…")
    partiel = cible.with_suffix(".part")
    urllib.request.urlretrieve(URL_BAK, partiel)
    partiel.replace(cible)
    return cible


def connecter(serveur: str, port: int, utilisateur: str, mot_de_passe: str, attente_s: int = 120):
    try:
        import pymssql
    except ImportError:
        sys.exit("Pilote manquant : pip install pymssql")
    debut = time.time()
    while True:
        try:
            return pymssql.connect(
                server=serveur, port=port, user=utilisateur, password=mot_de_passe,
                database="master", autocommit=True, login_timeout=10,
            )
        except pymssql.OperationalError as exc:
            if time.time() - debut > attente_s:
                sys.exit(f"SQL Server injoignable sur {serveur}:{port} ({exc}). "
                         "Le conteneur est-il démarré ? (docker compose -f scripts/wwi/docker-compose.yml up -d)")
            print("SQL Server pas encore prêt, nouvelle tentative dans 5 s…")
            time.sleep(5)


def restaurer_si_absente(conn) -> None:
    """RESTORE DATABASE avec MOVE construit dynamiquement depuis
    RESTORE FILELISTONLY (pas de noms de fichiers logiques codés en dur)."""
    cur = conn.cursor(as_dict=True)
    cur.execute("SELECT 1 AS ok FROM sys.databases WHERE name = %s", (NOM_BASE,))
    if cur.fetchone():
        print(f"Base {NOM_BASE} déjà restaurée.")
        return
    cur.execute(f"RESTORE FILELISTONLY FROM DISK = N'{CHEMIN_BAK_CONTENEUR}'")
    fichiers = cur.fetchall()
    moves = []
    for f in fichiers:
        nom_logique = f["LogicalName"]
        if f["Type"] == "S":  # filestream / in-memory : un DOSSIER, pas un fichier
            dest = f"/var/opt/mssql/data/{nom_logique}"
        else:
            ext = ".ldf" if f["Type"] == "L" else ".mdf"
            dest = f"/var/opt/mssql/data/{nom_logique}{ext}"
        moves.append(f"MOVE N'{nom_logique}' TO N'{dest}'")
    print(f"Restauration de {NOM_BASE} (1 à 3 minutes)…")
    cur.execute(
        f"RESTORE DATABASE [{NOM_BASE}] FROM DISK = N'{CHEMIN_BAK_CONTENEUR}' "
        f"WITH {', '.join(moves)}, REPLACE"
    )
    while cur.nextset():  # vider les messages de progression
        pass
    print("Restauration terminée.")


def lire(conn, sql: str, params: tuple) -> pd.DataFrame:
    cur = conn.cursor(as_dict=True)
    cur.execute(f"USE [{NOM_BASE}];")
    cur.execute(sql, params)
    return pd.DataFrame(cur.fetchall())


# ── Programme principal ─────────────────────────────────────────────────────

def ecrire_lisezmoi(sortie: Path, ventes: pd.DataFrame, stock: pd.DataFrame,
                    treso: pd.DataFrame, depuis: date, jusqua: date) -> None:
    lignes = [
        "# Jeu de données Wide World Importers — export pour Copilote IA PME",
        "",
        "Source : base d'exemple officielle Microsoft *WideWorldImporters* v1.0 (licence MIT)",
        f"<{URL_BAK}>",
        "",
        "Entreprise **fictive** (importateur-grossiste de produits fantaisie, San Francisco) :",
        "données simulées par Microsoft, à présenter comme telles.",
        "",
        f"Période extraite : {depuis} -> {jusqua}. Montants en USD.",
        "",
        "| Fichier | Onglet | Lignes | Tables WWI |",
        "|---|---|---|---|",
        f"| ventes.csv | Ventes | {len(ventes)} | Sales.Invoices, Sales.InvoiceLines, Sales.Customers |",
        f"| stock.csv | Stock | {len(stock)} | Warehouse.StockItems, Warehouse.StockItemHoldings |",
        f"| tresorerie.csv | Trésorerie | {len(treso)} | Sales.CustomerTransactions, Purchasing.SupplierTransactions |",
        "",
        f"Clients distincts : {ventes['code_client'].nunique()} — "
        f"factures : {ventes['numero_facture'].nunique()} — "
        f"produits : {ventes['code_produit'].nunique()} — "
        f"lignes d'avoir : {int((ventes['quantite'] < 0).sum())}.",
        "",
        "## Choix de transformation",
        "- `montant` des ventes = quantité x prix unitaire **HT** (chiffre d'affaires).",
        "- Avoirs (IsCreditNote) : quantité et montant négatifs -> marqués retours à l'import.",
        "- Stock : `seuil_alerte` = ReorderLevel, `seuil_reapprovisionnement` = TargetStockLevel.",
        "  Instantané à la dernière date de la base, pas un historique.",
        "- Trésorerie : uniquement les paiements et remboursements (jamais les factures),",
        "  montants **TTC**, agrégés par jour et par catégorie. Le solde part de 0 et",
        "  n'inclut ni salaires ni frais généraux (absents de WWI) : il surestime la trésorerie réelle.",
        "- Récence RFM : calculée depuis la dernière date des ventes (rfm_clustering_service._reference_date),",
        "  pas depuis la date du jour — aucun réglage nécessaire.",
        "",
        "Codes : `CLI-0001` (client WWI 1), `SKU-0001` (article WWI 1), `FAC-000001` (facture WWI 1).",
    ]
    (sortie / "LISEZMOI.md").write_text("\n".join(lignes) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Exporte Wide World Importers en 3 fichiers importables.")
    p.add_argument("--serveur", default="localhost")
    p.add_argument("--port", type=int, default=1433)
    p.add_argument("--utilisateur", default="sa")
    p.add_argument("--mot-de-passe", default="Wwi_Export_2026!",
                   help="Doit correspondre à MSSQL_SA_PASSWORD du docker-compose WWI.")
    p.add_argument("--telecharger", action="store_true",
                   help=f"Télécharge {NOM_BAK} dans --dossier-bak s'il est absent.")
    p.add_argument("--dossier-bak", default="data/raw/wwi")
    p.add_argument("--sortie", default="data/wwi")
    p.add_argument("--depuis", type=date.fromisoformat, default=date(2013, 1, 1))
    p.add_argument("--jusqua", type=date.fromisoformat, default=date(2016, 5, 31))
    p.add_argument("--tresorerie-detaillee", action="store_true",
                   help="Un mouvement par paiement au lieu d'un cumul journalier.")
    args = p.parse_args(argv)

    if args.telecharger:
        telecharger_bak(Path(args.dossier_bak))

    conn = connecter(args.serveur, args.port, args.utilisateur, args.mot_de_passe)
    restaurer_si_absente(conn)

    periode = (args.depuis, args.jusqua)
    print("Extraction des ventes…")
    ventes = transformer_ventes(lire(conn, SQL_VENTES, periode))
    print("Extraction du stock…")
    stock = transformer_stock(lire(conn, SQL_STOCK, ()))
    print("Extraction de la trésorerie…")
    treso = transformer_tresorerie(lire(conn, SQL_TRESORERIE, periode * 2), args.tresorerie_detaillee)
    conn.close()

    sortie = Path(args.sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    fichiers = {
        "transactions": (sortie / "ventes.csv", ventes),
        "stocks": (sortie / "stock.csv", stock),
        "tresorerie": (sortie / "tresorerie.csv", treso),
    }
    code_retour = 0
    for cible, (chemin, df) in fichiers.items():
        df.to_csv(chemin, index=False, encoding="utf-8")
        taille = chemin.stat().st_size
        print(f"  {chemin} : {len(df)} lignes, {taille / 1e6:.1f} Mo")
        if taille > LIMITE_UPLOAD_OCTETS:
            print("    ATTENTION : dépasse la limite d'upload de 50 Mo — relancer avec --depuis "
                  "(ex. --depuis 2015-01-01).")
            code_retour = 1
        ecarts = verifier_mapping_auto(chemin, cible)
        if ecarts:
            print("    ATTENTION : mapping automatique inattendu -> corriger à la main :")
            for e in ecarts:
                print(f"      - {e}")
            code_retour = 1
        else:
            print("    Mapping détecté automatiquement : OK")

    ecrire_lisezmoi(sortie, ventes, stock, treso, args.depuis, args.jusqua)
    print(f"Terminé. Importer dans l'ordre : ventes.csv, stock.csv, tresorerie.csv ({sortie}/).")
    return code_retour


if __name__ == "__main__":
    raise SystemExit(main())
