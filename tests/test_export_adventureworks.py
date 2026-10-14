"""Tests de app/ingestion/export_adventureworks.py et des corrections
d'import mises au jour par le jeu AdventureWorks réel."""

import io
import json

import pandas as pd
import pytest

from app.ingestion import export_adventureworks as aw
from app.models import Client, StockProduit, Transaction, Tresorerie


def _brut(table: str, lignes: list[dict]) -> pd.DataFrame:
    """DataFrame au format de lire_brut : toutes les colonnes, en texte."""
    df = pd.DataFrame(lignes, columns=aw.COLONNES_BRUTES[table]).fillna("")
    return df.astype(str)


def _jeu() -> dict[str, pd.DataFrame]:
    produits = _brut("DimProduct", [
        # Deux versions du même article (historique de prix) : seule la 2e compte.
        {"ProductKey": "10", "ProductAlternateKey": "BK-R93R-62", "EnglishProductName": "Road-150 Red, 62",
         "StandardCost": "2000", "SafetyStockLevel": "100", "ReorderPoint": "75", "DaysToManufacture": "4"},
        {"ProductKey": "11", "ProductAlternateKey": "BK-R93R-62", "EnglishProductName": "Road-150 Red, 62",
         "StandardCost": "2171.2942", "SafetyStockLevel": "100", "ReorderPoint": "75", "DaysToManufacture": "4"},
        {"ProductKey": "20", "ProductAlternateKey": "CA-1098", "EnglishProductName": "AWC Logo Cap",
         "StandardCost": "6.9223", "SafetyStockLevel": "4", "ReorderPoint": "3", "DaysToManufacture": "0"},
        {"ProductKey": "30", "ProductAlternateKey": "XX-0001", "EnglishProductName": "Jamais vendu",
         "StandardCost": "1", "SafetyStockLevel": "4", "ReorderPoint": "3", "DaysToManufacture": "0"},
    ])
    clients = _brut("DimCustomer", [
        {"CustomerKey": "1", "GeographyKey": "7", "CustomerAlternateKey": "AW00011000",
         "FirstName": "Jon", "LastName": "Yang", "EmailAddress": "jon24@adventure-works.com"},
        {"CustomerKey": "2", "GeographyKey": "7", "CustomerAlternateKey": "AW00011001",
         "FirstName": "Eugene", "LastName": "Huang", "EmailAddress": "eugene10@adventure-works.com"},
    ])
    geo = _brut("DimGeography", [{"GeographyKey": "7", "EnglishCountryRegionName": "Australia"}])

    def vente(cle, client, numero, date, montant):
        return {"ProductKey": cle, "CustomerKey": client, "SalesOrderNumber": numero,
                "OrderQuantity": "1", "SalesAmount": montant, "OrderDate": f"{date} 00:00:00.000"}

    ventes = _brut("FactInternetSales", [
        vente("10", "1", "SO43697", "2013-01-05", "3578.27"),
        vente("11", "1", "SO50000", "2013-06-01", "2443.35"),
        # Deux commandes distinctes, même client/jour/article/prix (cas réel SO73199/SO73201).
        vente("20", "2", "SO73199", "2013-12-16", "8.99"),
        vente("20", "2", "SO73201", "2013-12-16", "8.99"),
    ])
    inventaire = _brut("FactProductInventory", [
        {"ProductKey": "11", "MovementDate": "2013-12-01", "UnitsBalance": "90"},
        {"ProductKey": "11", "MovementDate": "2013-12-16", "UnitsBalance": "70"},
        {"ProductKey": "11", "MovementDate": "2014-06-30", "UnitsBalance": "999"},  # après la photo
        {"ProductKey": "20", "MovementDate": "2013-12-16", "UnitsBalance": "-2"},   # négatif source
    ])

    def ecriture(compte, montant, org="3", scenario="1", date="2013-01-29"):
        return {"ScenarioKey": scenario, "OrganizationKey": org, "AccountKey": compte,
                "Amount": montant, "Date": f"{date} 00:00:00.000"}

    finance = _brut("FactFinance", [
        ecriture("101", "10000"), ecriture("101", "5000", org="4"),   # ventes, 2 divisions USA
        ecriture("60", "3000"), ecriture("61", "500"),                 # salaires + charges
        ecriture("91", "-200"),                                        # moins-value -> sortie
        ecriture("80", "999"),                                         # amortissement : exclu
        ecriture("101", "777", org="11"),                              # France (EUR) : exclu
        ecriture("101", "888", scenario="2"),                          # budget : exclu
    ])
    return {"produits": produits, "clients": clients, "geo": geo, "ventes": ventes,
            "inventaire": inventaire, "finance": finance}


def test_ventes_codes_et_montants():
    j = _jeu()
    v = aw.construire_ventes(j["ventes"], j["clients"], j["geo"], j["produits"])
    assert list(v.columns) == aw.COLONNES_VENTES
    assert set(v["code_client"]) == {"CLI-11000", "CLI-11001"}
    assert v.loc[0, "nom_client"] == "Jon Yang" and v.loc[0, "pays"] == "Australia"
    # Les deux versions de prix d'un article partagent la référence commerciale.
    assert (v.loc[v["code_client"] == "CLI-11000", "code_produit"] == "BK-R93R-62").all()
    assert v["montant"].sum() == pytest.approx(3578.27 + 2443.35 + 2 * 8.99)


def test_stock_photo_a_la_date_des_ventes_seuils_et_negatifs():
    j = _jeu()
    stock, n_negatifs = aw.construire_stock(j["inventaire"], j["produits"], {"BK-R93R-62", "CA-1098"},
                                            "2014-01-28")
    s = stock.set_index("code_produit")
    assert list(s.index) == ["BK-R93R-62", "CA-1098"]  # article jamais vendu exclu
    assert s.loc["BK-R93R-62", "quantite_disponible"] == 70  # pas la valeur postérieure (999)
    assert (s.loc["BK-R93R-62", "seuil_alerte"], s.loc["BK-R93R-62", "seuil_reapprovisionnement"]) == (75, 100)
    assert s.loc["BK-R93R-62", "cout_unitaire"] == pytest.approx(2171.29)
    assert s.loc["CA-1098", "quantite_disponible"] == 0 and n_negatifs == 1


def test_tresorerie_comptes_retenus_et_sens():
    t = aw.construire_tresorerie(_jeu()["finance"]).set_index("categorie")
    assert t.loc["Ventes encaissées", "montant"] == 15000  # 2 divisions USA, ni EUR ni budget
    assert t.loc["Ventes encaissées", "type_mouvement"] == "encaissement"
    assert t.loc["Salaires et charges sociales", "montant"] == 3500
    assert t.loc["Cessions d'actifs", "type_mouvement"] == "decaissement"
    assert "Amortissements" not in t.index and len(t) == 3


def test_chaine_complete_fichiers_mapping_et_import(client, db_session, tmp_path):
    j = _jeu()
    ventes = aw.construire_ventes(j["ventes"], j["clients"], j["geo"], j["produits"])
    stock, _ = aw.construire_stock(j["inventaire"], j["produits"], set(ventes["code_produit"]), "2014-01-28")
    treso = aw.construire_tresorerie(j["finance"])
    for cible, nom, df in (("transactions", "ventes.csv", ventes), ("stocks", "stock.csv", stock),
                           ("tresorerie", "tresorerie.csv", treso)):
        chemin = tmp_path / nom
        df.to_csv(chemin, index=False)
        assert aw.verifier_mapping_auto(chemin, cible) == [], cible
        resp = client.post(
            f"/api/v1/ingestion/import/{cible}",
            files={"file": (nom, io.BytesIO(chemin.read_bytes()), "text/csv")},
            data={"mapping": json.dumps(aw.MAPPING_ATTENDU[cible])},
        )
        assert resp.status_code == 200, resp.text

    assert db_session.query(Transaction).count() == 4  # les 2 commandes « jumelles » sont gardées
    assert db_session.query(Client).filter_by(code_client_externe="CLI-11000").one().email == \
        "jon24@adventure-works.com"
    assert db_session.query(StockProduit).count() == 2
    assert db_session.query(Tresorerie).count() == 3
    kpis = client.get("/api/v1/analytics/kpis").json()
    assert kpis["solde_tresorerie_estime"] == pytest.approx(15000 - 3500 - 200)


# ── Corrections de l'import générique ───────────────────────────────────────

def _importer(client, csv: bytes, mapping: dict):
    return client.post(
        "/api/v1/ingestion/import/transactions",
        files={"file": ("v.csv", io.BytesIO(csv), "text/csv")},
        data={"mapping": json.dumps(mapping)},
    ).json()


def test_dedup_garde_deux_commandes_distinctes_mais_retire_une_vraie_ligne_doublee(client, db_session):
    csv = (b"Date,Client,Montant,Facture,Produit\n"
           b"2024-01-05,CL-1,10,F-1,P1\n"
           b"2024-01-05,CL-1,10,F-2,P1\n"   # autre commande : gardée
           b"2024-01-05,CL-1,10,F-2,P1\n")  # même ligne exportée deux fois : retirée
    r = _importer(client, csv, {"date_col": "Date", "client_col": "Client", "montant_col": "Montant",
                                "numero_facture_col": "Facture", "code_produit_col": "Produit"})
    assert r["n_doublons_supprimes"] == 1
    assert db_session.query(Transaction).count() == 2


def test_rapport_import_ne_compte_pas_un_client_cree_comme_mis_a_jour(client):
    csv = b"Date,Client,Montant,Nom\n2024-01-05,CL-1,10,Awa\n2024-01-06,CL-2,20,Paul\n"
    r = _importer(client, csv, {"date_col": "Date", "client_col": "Client", "montant_col": "Montant",
                                "nom_client_col": "Nom"})
    assert (r["n_enregistrements_crees"], r["n_enregistrements_maj"]) == (2, 0)


# ── Détection automatique : prix unitaire ≠ montant total ───────────────────

CSV_ONLINE_RETAIL_II = b"""Invoice,StockCode,Description,Quantity,InvoiceDate,Price,Customer ID,Country
489434,85048,15CM CHRISTMAS GLASS BALL 20 LIGHTS,12,2009-12-01 07:45:00,6.95,13085,United Kingdom
489434,79323P,PINK CHERRY LIGHTS,12,2009-12-01 07:45:00,6.75,13085,United Kingdom
C489449,22087,PAPER BUNTING WHITE LACE,-12,2009-12-01 10:33:00,2.95,16321,Australia
"""


def test_online_retail_ii_price_detecte_comme_prix_unitaire_et_ca_exact(client, db_session):
    files = {"file": ("online_retail_II.csv", io.BytesIO(CSV_ONLINE_RETAIL_II), "text/csv")}
    suggestion = client.post("/api/v1/ingestion/preview", files=files).json()["mapping_suggere"]["transactions"]
    assert suggestion["prix_unitaire_col"] == "Price"
    assert suggestion["montant_col"] is None
    mapping = {k: v for k, v in suggestion.items() if v}
    resp = client.post(
        "/api/v1/ingestion/import/transactions",
        files={"file": ("online_retail_II.csv", io.BytesIO(CSV_ONLINE_RETAIL_II), "text/csv")},
        data={"mapping": json.dumps(mapping)},
    )
    assert resp.status_code == 200, resp.text
    ventes = [t for t in db_session.query(Transaction).all() if not t.est_retour]
    # CA = 12 x 6,95 + 12 x 6,75 (et non 6,95 + 6,75 comme avant la correction)
    assert sum(t.quantite * float(t.prix_unitaire) for t in ventes) == pytest.approx(164.4)
    assert db_session.query(Transaction).filter(Transaction.est_retour).count() == 1


def test_colonne_prix_seule_reste_un_montant():
    from app.ingestion.generic_loader import detect_mapping_transactions, load_any

    df = load_any(b"Date,Client,Prix\n2024-01-05,CL-1,1500\n2024-01-06,CL-2,800\n", "caisse.csv")
    m = detect_mapping_transactions(df)
    assert (m["montant_col"], m["prix_unitaire_col"]) == ("Prix", None)


def test_load_online_retail_accepte_les_deux_variantes_d_en_tetes(tmp_path, db_session, monkeypatch):
    from app.ingestion import load_online_retail as lor

    ancien = CSV_ONLINE_RETAIL_II.replace(b"Invoice,", b"InvoiceNo,", 1).replace(
        b",Price,Customer ID,", b",UnitPrice,CustomerID,")
    chemin = tmp_path / "ancien.csv"
    chemin.write_bytes(ancien)
    monkeypatch.setattr(lor, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    lor.load_online_retail(str(chemin))
    assert db_session.query(Transaction).count() == 3
