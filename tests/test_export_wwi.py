"""Tests de l'export Wide World Importers (app/ingestion/export_wwi.py).

SQL Server n'est pas nécessaire : on fabrique des DataFrames ayant EXACTEMENT
la forme renvoyée par les requêtes SQL du module (mêmes alias de colonnes),
puis on vérifie la chaîne complète que suivra l'utilisateur :
  transformation -> CSV -> détection automatique du mapping (écran « Données »)
  -> import réel par l'API -> données correctes en base.
"""

import io
import json
import random
from datetime import date, timedelta

import pandas as pd
import pytest

from app.ingestion import export_wwi as wwi
from app.models import Client, StockProduit, Transaction, Tresorerie


def _brut_ventes() -> pd.DataFrame:
    random.seed(7)
    lignes, invoice_id = [], 0
    debut = date(2015, 1, 1)
    for customer_id in range(1, 31):
        for _ in range(random.randint(1, 8)):
            invoice_id += 1
            jour = debut + timedelta(days=random.randint(0, 500))
            for item in random.sample(range(1, 21), k=random.randint(1, 3)):
                lignes.append({
                    "invoice_id": invoice_id, "invoice_date": jour, "is_credit_note": False,
                    # Virgules et guillemets : pièges classiques d'un CSV.
                    "customer_id": customer_id,
                    "customer_name": f'Tailspin Toys (Store {customer_id}, "FL")',
                    "country": "United States", "stock_item_id": item,
                    "description": f"Article {item}, taille L", "quantity": random.randint(1, 50),
                    "unit_price": round(random.uniform(1, 300), 2),
                })
    # Deux lignes d'avoir (IsCreditNote = 1, quantité positive dans WWI).
    for k in range(2):
        invoice_id += 1
        lignes.append({
            "invoice_id": invoice_id, "invoice_date": date(2016, 3, 1), "is_credit_note": True,
            "customer_id": 1, "customer_name": "Tailspin Toys (Store 1)", "country": "United States",
            "stock_item_id": 5 + k, "description": "Article retourné", "quantity": 2, "unit_price": 10.0,
        })
    return pd.DataFrame(lignes)


def _brut_stock() -> pd.DataFrame:
    return pd.DataFrame([
        {"stock_item_id": i, "stock_item_name": f"Article {i}, taille L",
         "quantity_on_hand": 5 if i == 1 else 500, "reorder_level": 10,
         "target_stock_level": 4 if i == 2 else 100,  # i=2 : cible < seuil -> doit être relevée
         "last_cost_price": 3.456, "lead_time_days": 14}
        for i in range(1, 21)
    ])


def _brut_treso() -> pd.DataFrame:
    # Convention WWI : facture > 0, paiement < 0, dans les deux tables.
    return pd.DataFrame([
        {"transaction_date": date(2016, 1, 4), "tiers": "client",
         "transaction_type": "Customer Payment Received", "amount": -1000.0},
        {"transaction_date": date(2016, 1, 4), "tiers": "client",
         "transaction_type": "Customer Payment Received", "amount": -500.0},
        {"transaction_date": date(2016, 1, 5), "tiers": "fournisseur",
         "transaction_type": "Supplier Payment Issued", "amount": -300.0},
        {"transaction_date": date(2016, 1, 6), "tiers": "client",
         "transaction_type": "Customer Refund", "amount": 50.0},
    ])


# ── Transformations ─────────────────────────────────────────────────────────

def test_ventes_codes_montant_ht_et_avoirs_negatifs():
    brut = _brut_ventes()
    ventes = wwi.transformer_ventes(brut)

    assert list(ventes.columns) == wwi.COLONNES_VENTES
    assert ventes["code_client"].iloc[0] == "CLI-0001"
    assert ventes["numero_facture"].iloc[0] == "FAC-000001"
    assert ventes["code_produit"].str.match(r"^SKU-\d{4}$").all()
    attendu = (brut["quantity"] * brut["unit_price"]).round(2)
    normales = ~brut["is_credit_note"]
    assert (ventes.loc[normales, "montant"] == attendu[normales]).all()
    avoirs = ventes[brut["is_credit_note"]]
    assert (avoirs["quantite"] == -2).all() and (avoirs["montant"] == -20.0).all()


def test_stock_seuil_reappro_jamais_inferieur_au_seuil_alerte():
    stock = wwi.transformer_stock(_brut_stock()).set_index("code_produit")
    assert stock.loc["SKU-0001", "seuil_alerte"] == 10
    assert stock.loc["SKU-0001", "seuil_reapprovisionnement"] == 100
    assert stock.loc["SKU-0002", "seuil_reapprovisionnement"] == 10
    assert stock.loc["SKU-0001", "cout_unitaire"] == pytest.approx(3.46)


def test_tresorerie_sens_des_flux_et_cumul_journalier():
    treso = wwi.transformer_tresorerie(_brut_treso())
    lignes = {(r.date_mouvement, r.categorie): (r.type_mouvement, r.montant) for r in treso.itertuples()}
    assert lignes[("2016-01-04", "Encaissements clients")] == ("encaissement", 1500.0)
    assert lignes[("2016-01-05", "Paiements fournisseurs")] == ("decaissement", 300.0)
    assert lignes[("2016-01-06", "Remboursements clients")] == ("decaissement", 50.0)
    assert len(wwi.transformer_tresorerie(_brut_treso(), detail=True)) == 4


# ── Chaîne complète : CSV -> mapping automatique -> import API ───────────────

@pytest.fixture
def fichiers_wwi(tmp_path):
    fichiers = {
        "transactions": ("ventes.csv", wwi.transformer_ventes(_brut_ventes())),
        "stocks": ("stock.csv", wwi.transformer_stock(_brut_stock())),
        "tresorerie": ("tresorerie.csv", wwi.transformer_tresorerie(_brut_treso())),
    }
    chemins = {}
    for cible, (nom, df) in fichiers.items():
        chemin = tmp_path / nom
        df.to_csv(chemin, index=False, encoding="utf-8")
        chemins[cible] = (chemin, df)
    return chemins


def test_mapping_detecte_automatiquement_pour_les_trois_fichiers(fichiers_wwi):
    for cible, (chemin, _) in fichiers_wwi.items():
        assert wwi.verifier_mapping_auto(chemin, cible) == [], cible


def test_import_api_des_trois_fichiers(client, db_session, fichiers_wwi):
    for cible, (chemin, _) in fichiers_wwi.items():
        # Mapping tel que l'écran « Données » l'envoie : la suggestion de /preview.
        with chemin.open("rb") as f:
            apercu = client.post("/api/v1/ingestion/preview", files={"file": (chemin.name, f, "text/csv")})
        assert apercu.status_code == 200, apercu.text
        mapping = {k: v for k, v in apercu.json()["mapping_suggere"][cible].items() if v}
        with chemin.open("rb") as f:
            resp = client.post(
                f"/api/v1/ingestion/import/{cible}",
                files={"file": (chemin.name, io.BytesIO(f.read()), "text/csv")},
                data={"mapping": json.dumps(mapping)},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["n_lignes_rejetees"] == 0, resp.json()

    ventes = fichiers_wwi["transactions"][1]
    assert db_session.query(Client).count() == ventes["code_client"].nunique()
    transactions = db_session.query(Transaction).all()
    assert len(transactions) == len(ventes)
    # Seuls les 2 avoirs sont des retours : le préfixe "FAC-" / "CLI-" ne doit
    # jamais déclencher la règle « numéro d'annulation ».
    assert sum(t.est_retour for t in transactions) == 2
    # Quantités réelles conservées (pas écrasées à 1) et CA HT exact.
    ca_base = sum(t.quantite * float(t.prix_unitaire) for t in transactions if not t.est_retour)
    assert ca_base == pytest.approx(ventes.loc[ventes["montant"] > 0, "montant"].sum(), rel=1e-6)
    assert {t.code_produit for t in transactions} <= {s.code_produit for s in db_session.query(StockProduit)}

    sku1 = db_session.query(StockProduit).filter_by(code_produit="SKU-0001").one()
    assert (sku1.quantite_disponible, sku1.seuil_alerte, sku1.delai_livraison_jours) == (5, 10, 14)

    mouvements = db_session.query(Tresorerie).order_by(Tresorerie.date_mouvement).all()
    assert float(mouvements[-1].solde_apres_mouvement) == pytest.approx(1500 - 300 - 50)


# ── Bug corrigé : détection des retours par numéro de facture ───────────────

@pytest.mark.parametrize(
    ("numero", "est_retour"),
    [
        ("CMD-2024-001", False),  # bon de commande : était compté comme retour
        ("ACH-17", False),
        ("ART-9", False),
        ("C536379", True),        # annulation Online Retail II
        ("A563185", True),        # ajustement Online Retail II
        ("AV-2024-001", True),    # avoir ERP français
        ("AVOIR 12", True),
    ],
)
def test_prefixe_facture_retour(client, db_session, numero, est_retour):
    csv = f"Date,Client,Montant,Facture\n2024-01-05,CL-1,1000,{numero}\n".encode()
    mapping = {"date_col": "Date", "client_col": "Client", "montant_col": "Montant",
               "numero_facture_col": "Facture"}
    resp = client.post(
        "/api/v1/ingestion/import/transactions",
        files={"file": ("f.csv", io.BytesIO(csv), "text/csv")},
        data={"mapping": json.dumps(mapping)},
    )
    assert resp.status_code == 200, resp.text
    assert db_session.query(Transaction).one().est_retour is est_retour
