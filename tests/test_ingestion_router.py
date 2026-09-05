import io
import json

from app.models import StockProduit, Transaction, Tresorerie

# Export fictif d'une PME camerounaise quelconque (boulangerie, quincaillerie...),
# avec des noms de colonnes totalement différents d'Online Retail II
# (Order Date / Client ID / Total Price / Qty) -> preuve de généricité.
CSV_PME_QUELCONQUE = b"""Order Date,Client ID,Product Code,Product Name,Qty,Unit Price,Invoice Ref,Country
2024-01-05,CL-001,PRD-01,Sac de riz 25kg,2,15000,INV-1001,Cameroun
2024-01-06,CL-002,PRD-02,Huile 5L,1,8000,INV-1002,Cameroun
2024-01-06,CL-001,PRD-01,Sac de riz 25kg,1,15000,INV-1003,Cameroun
2024-01-07,CL-003,PRD-03,Savon,-1,-1500,CINV-1004,Cameroun
"""


def _upload_csv(client, endpoint: str, mapping: dict | None = None):
    files = {"file": ("export_pme.csv", io.BytesIO(CSV_PME_QUELCONQUE), "text/csv")}
    data = {"mapping": json.dumps(mapping)} if mapping is not None else {}
    return client.post(endpoint, files=files, data=data)


def test_preview_detects_columns_for_unfamiliar_pme_export(client):
    resp = _upload_csv(client, "/api/v1/ingestion/preview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_lignes"] == 4
    suggestion = body["mapping_suggere"]["transactions"]
    # La suggestion doit repérer les bonnes colonnes malgré des noms
    # totalement différents d'Online Retail II.
    assert suggestion["date_col"] == "Order Date"
    assert suggestion["client_col"] == "Client ID"
    assert suggestion["code_produit_col"] == "Product Code"


def test_import_transactions_with_custom_mapping(client, db_session):
    mapping = {
        "date_col": "Order Date",
        "client_col": "Client ID",
        "quantite_col": "Qty",
        "prix_unitaire_col": "Unit Price",
        "code_produit_col": "Product Code",
        "description_produit_col": "Product Name",
        "numero_facture_col": "Invoice Ref",
        "pays_col": "Country",
    }
    resp = _upload_csv(client, "/api/v1/ingestion/import/transactions", mapping)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["n_lignes_lues"] == 4
    assert body["n_lignes_importees"] == 4
    assert body["n_enregistrements_crees"] == 3  # CL-001, CL-002, CL-003

    transactions = db_session.query(Transaction).all()
    assert len(transactions) == 4

    # La ligne CINV-1004 (préfixe "C" + quantité négative) doit être détectée
    # comme un retour, jamais supprimée.
    retour = next(t for t in transactions if t.numero_facture == "CINV-1004")
    assert retour.est_retour is True
    assert retour.quantite == 1  # valeur absolue
    assert retour.prix_unitaire == 1500.0


def test_import_transactions_montant_et_quantite_sans_prix_unitaire_preserve_la_quantite(
    client, db_session
):
    """BUG CORRIGÉ (régression) : un export PME fournissant à la fois un
    montant total ET une quantité réelle, mais SANS colonne de prix
    unitaire, doit conserver la vraie quantité (ex. 2, 1, 1, -1) au lieu de
    l'écraser silencieusement à 1 pour toutes les lignes — cf. docstring de
    `_resolve_montant` dans app/ingestion/generic_transactions.py."""
    mapping = {
        "date_col": "Order Date",
        "client_col": "Client ID",
        "montant_col": "Unit Price",  # ici utilisé comme "montant total ligne"
        "quantite_col": "Qty",
        "code_produit_col": "Product Code",
        "numero_facture_col": "Invoice Ref",
    }
    resp = _upload_csv(client, "/api/v1/ingestion/import/transactions", mapping)
    assert resp.status_code == 200, resp.text

    transactions = {t.numero_facture: t for t in db_session.query(Transaction).all()}

    # CL-001, 2024-01-05 : Qty=2, montant=15000 -> prix unitaire implicite 7500,
    # PAS quantite=1/prix=15000 (ancien comportement buggé).
    ligne_qte_2 = transactions["INV-1001"]
    assert ligne_qte_2.quantite == 2
    assert ligne_qte_2.prix_unitaire == 7500.0

    # Retour (quantité et montant négatifs) : quantité réelle préservée en
    # valeur absolue, pas réduite à 1.
    retour = transactions["CINV-1004"]
    assert retour.est_retour is True
    assert retour.quantite == 1  # Qty=-1 dans le CSV -> abs() = 1 (coïncidence du jeu de test)
    assert retour.prix_unitaire == 1500.0


def test_import_transactions_rejects_missing_column(client):
    resp = _upload_csv(
        client,
        "/api/v1/ingestion/import/transactions",
        {"date_col": "Colonne Inexistante", "client_col": "Client ID", "montant_col": "Unit Price"},
    )
    assert resp.status_code == 422


def test_import_stocks_with_custom_mapping(client, db_session):
    csv_stock = (
        b"SKU,Libelle,Quantite,Seuil Min\n"
        b"PRD-01,Sac de riz 25kg,40,10\n"
        b"PRD-02,Huile 5L,5,10\n"
    )
    mapping = {
        "code_produit_col": "SKU",
        "quantite_disponible_col": "Quantite",
        "nom_produit_col": "Libelle",
        "seuil_alerte_col": "Seuil Min",
    }
    files = {"file": ("stock.csv", io.BytesIO(csv_stock), "text/csv")}
    resp = client.post(
        "/api/v1/ingestion/import/stocks", files=files, data={"mapping": json.dumps(mapping)}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_enregistrements_crees"] == 2

    stocks = db_session.query(StockProduit).all()
    assert {s.code_produit for s in stocks} == {"PRD-01", "PRD-02"}


def test_import_tresorerie_computes_running_balance(client, db_session):
    csv_treso = (
        b"Date Mouvement,Montant,Categorie\n"
        b"2024-02-01,50000,vente\n"
        b"2024-02-02,-12000,fournisseur\n"
    )
    mapping = {"date_col": "Date Mouvement", "montant_col": "Montant", "categorie_col": "Categorie"}
    files = {"file": ("treso.csv", io.BytesIO(csv_treso), "text/csv")}
    resp = client.post(
        "/api/v1/ingestion/import/tresorerie", files=files, data={"mapping": json.dumps(mapping)}
    )
    assert resp.status_code == 200, resp.text

    mouvements = db_session.query(Tresorerie).order_by(Tresorerie.date_mouvement).all()
    assert len(mouvements) == 2
    assert mouvements[0].type_mouvement == "encaissement"
    assert mouvements[0].solde_apres_mouvement == 50000.0
    assert mouvements[1].type_mouvement == "decaissement"
    assert mouvements[1].solde_apres_mouvement == 38000.0
