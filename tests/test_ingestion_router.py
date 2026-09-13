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


def test_import_tresorerie_backfill_retroactively_corrects_existing_balances(client, db_session):
    """BUG CORRIGÉ : un import de mouvements ANTÉRIEURS à des mouvements déjà
    en base amorçait auparavant le solde cumulé sur la dernière ligne
    existante (la plus récente) et ajoutait les nouvelles lignes par-dessus
    — donnant un solde faux aux nouvelles lignes ET laissant les lignes déjà
    en base (chronologiquement après) avec un solde qui ignorait ces
    mouvements plus anciens, jamais recalculé après coup. Cf.
    `generic_tresorerie._recalculer_soldes` : le solde cumulé est désormais
    reconstruit sur TOUTE la table à chaque import."""
    mapping = {"date_col": "Date", "montant_col": "Montant"}

    # 1er import : deux mouvements de février.
    csv1 = b"Date,Montant\n2024-02-10,50000\n2024-02-15,-12000\n"
    r1 = client.post(
        "/api/v1/ingestion/import/tresorerie",
        files={"file": ("t1.csv", io.BytesIO(csv1), "text/csv")},
        data={"mapping": json.dumps(mapping)},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["n_enregistrements_maj"] == 0

    # 2e import (backfill) : un mouvement de JANVIER, antérieur aux deux précédents.
    csv2 = b"Date,Montant\n2024-01-01,20000\n"
    r2 = client.post(
        "/api/v1/ingestion/import/tresorerie",
        files={"file": ("t2.csv", io.BytesIO(csv2), "text/csv")},
        data={"mapping": json.dumps(mapping)},
    )
    assert r2.status_code == 200, r2.text
    # Les 2 mouvements de février, déjà en base, doivent voir leur solde ajusté.
    assert r2.json()["n_enregistrements_maj"] == 2
    assert "RÉTROACTIVEMENT" in " ".join(r2.json()["avertissements"])

    mouvements = db_session.query(Tresorerie).order_by(Tresorerie.date_mouvement).all()
    soldes = [(m.date_mouvement.isoformat(), float(m.solde_apres_mouvement)) for m in mouvements]
    assert soldes == [
        ("2024-01-01", 20000.0),
        ("2024-02-10", 70000.0),  # 20000 (janvier) + 50000, pas seulement 50000
        ("2024-02-15", 58000.0),  # 70000 - 12000, pas 38000
    ]


def test_import_transactions_refreshes_rfm_segmentation_automatically(client, db_session):
    """NOUVEAU COMPORTEMENT : `clients.segment_rfm`/`score_churn`/`cluster_id`
    doivent être à jour juste après un import, SANS appel manuel à
    `POST /marketing/rfm/run` — sinon le copilote (et le dashboard) répondent
    avec un état périmé pour tout nouveau client importé. Jeu de données
    volontairement plus large que les autres tests de ce fichier (6 clients,
    plusieurs achats chacun) pour que `run_full_pipeline` produise un
    diagnostic k-means exploitable (k_choisi non None)."""
    lignes = ["Order Date,Client ID,Unit Price"]
    for i in range(6):
        for j in range(3):
            lignes.append(f"2024-0{1 + (i + j) % 6}-{10 + j:02d},CL-{i},{100 * (i + 1)}")
    csv_bytes = ("\n".join(lignes) + "\n").encode()

    mapping = {"date_col": "Order Date", "client_col": "Client ID", "montant_col": "Unit Price"}
    resp = client.post(
        "/api/v1/ingestion/import/transactions",
        files={"file": ("export.csv", io.BytesIO(csv_bytes), "text/csv")},
        data={"mapping": json.dumps(mapping)},
    )
    assert resp.status_code == 200, resp.text
    avertissements = " ".join(resp.json()["avertissements"])
    assert "Segmentation RFM recalculée automatiquement" in avertissements

    # Vérifiable directement en base (pas seulement dans le message) : aucun
    # client n'est resté avec une segmentation NULL après cet import.
    from app.models import Client

    clients = db_session.query(Client).all()
    assert len(clients) == 6
    assert all(c.segment_rfm is not None for c in clients)

    # Et via l'endpoint public consommé par le dashboard/copilote.
    resp_segments = client.get("/api/v1/marketing/segments/summary")
    assert resp_segments.status_code == 200
    assert sum(s["nb_clients"] for s in resp_segments.json()) == 6
