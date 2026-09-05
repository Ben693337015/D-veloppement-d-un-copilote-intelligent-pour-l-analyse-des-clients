"""Test d'intégration bout-en-bout — jeu de données PME simulé.

Objectif : valider que LA PLATEFORME COMPLÈTE fonctionne pour une PME
fictive, en traversant la même chaîne qu'un utilisateur réel via l'API
HTTP (jamais d'appel direct aux fonctions internes) :

  1. Upload + import mappé de transactions (module RFM ET module ventes,
     table partagée) — noms de colonnes volontairement différents
     d'Online Retail II pour re-valider la généricité du pipeline
     d'ingestion (cf. tests/test_ingestion_router.py).
  2. Upload + import mappé du stock.
  3. Upload + import mappé de la trésorerie.
  4. Calcul RFM + clustering K-Means (module Abdoulmadjid, Phase 3).
  5. KPIs globaux, alertes de stock (module Maslaw).
  6. Prévision des ventes (ARIMA — Prophet volontairement exclu de la
     suite automatisée pour la vitesse, cf. test_forecasting_service.py).
  7. Assistant conversationnel (copilote) — vérifie que ses réponses
     reflètent fidèlement l'état réel de la base après tout le pipeline.
  8. Génération du rapport PDF final.

Scénario : "Quincaillerie Sahel", PME camerounaise fictive vendant des
matériaux de construction, sur ~250 jours d'historique avec 4 profils
clients volontairement contrastés (fidèles / à risque / occasionnels /
nouveaux) pour que la segmentation ait un signal réel à détecter.
"""

import io
import json
from datetime import datetime, timedelta

import pandas as pd

from app.models import Client, StockProduit, Transaction, Tresorerie

AUJOURD_HUI = datetime(2024, 9, 1)  # date de la transaction la plus récente du jeu de données


# ── GÉNÉRATION DU JEU DE DONNÉES SIMULÉ ──────────────────────────────────────

def _generer_transactions_csv() -> bytes:
    """Colonnes volontairement différentes d'Online Retail II (français,
    noms d'entreprise), pour re-valider la généricité du mapping."""
    lignes = []
    produits = [
        ("ART-CIM50", "Ciment 50kg", 4500),
        ("ART-FER12", "Fer à béton 12mm", 6200),
        ("ART-PEINT5", "Peinture 5L", 8900),
        ("ART-TUBE3", "Tube PVC 3m", 2100),
        ("ART-CLOU1", "Boîte de clous 1kg", 900),
    ]

    def _ajouter_client(prefixe, n_clients, n_factures_range, jours_range, montant_range):
        for i in range(n_clients):
            code_client = f"{prefixe}-{i:03d}"
            n_factures = __import__("random").randint(*n_factures_range)
            for f in range(n_factures):
                jour_offset = __import__("random").randint(*jours_range)
                produit_code, produit_nom, prix_base = __import__("random").choice(produits)
                lignes.append(
                    {
                        "Date Operation": (AUJOURD_HUI - timedelta(days=jour_offset)).strftime("%Y-%m-%d"),
                        "Reference Client": code_client,
                        "Nom Client": f"Client {code_client}",
                        "Email Client": f"{code_client.lower()}@example.cm",
                        "Pays": "Cameroun",
                        "Reference Vente": f"V-{code_client}-{f}",
                        "Code Article": produit_code,
                        "Libelle Article": produit_nom,
                        "Quantite Vendue": __import__("random").randint(1, 5),
                        "Prix Unitaire HT": round(
                            prix_base * __import__("random").uniform(*montant_range), 2
                        ),
                    }
                )

    __import__("random").seed(2024)
    # Fidèles : achats fréquents et récents (0-25 jours)
    _ajouter_client("FID", 20, (8, 14), (0, 25), (0.9, 1.1))
    # À risque : achats répétés mais anciens (200-250 jours -> > 182.5j, seuil "À risque")
    _ajouter_client("RISK", 20, (4, 7), (200, 250), (0.8, 1.0))
    # Occasionnels : peu d'achats, récents (0-60 jours)
    _ajouter_client("OCC", 25, (2, 3), (0, 60), (0.5, 0.8))
    # Nouveaux : un seul achat récent (0-15 jours) -> géré via n_factures fixé à 1
    _ajouter_client("NEW", 15, (1, 1), (0, 15), (0.6, 0.9))

    df = pd.DataFrame(lignes)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def _generer_stock_csv() -> bytes:
    lignes = [
        {"Code Article": "ART-CIM50", "Libelle Article": "Ciment 50kg", "Stock Disponible": 120,
         "Seuil Alerte": 20, "Seuil Reappro": 40, "Cout Achat Unitaire": 3800, "Delai Appro Jours": 5},
        {"Code Article": "ART-FER12", "Libelle Article": "Fer à béton 12mm", "Stock Disponible": 8,
         "Seuil Alerte": 15, "Seuil Reappro": 30, "Cout Achat Unitaire": 5200, "Delai Appro Jours": 10},
        {"Code Article": "ART-PEINT5", "Libelle Article": "Peinture 5L", "Stock Disponible": 3,
         "Seuil Alerte": 10, "Seuil Reappro": 20, "Cout Achat Unitaire": 7200, "Delai Appro Jours": 7},
        {"Code Article": "ART-TUBE3", "Libelle Article": "Tube PVC 3m", "Stock Disponible": 200,
         "Seuil Alerte": 30, "Seuil Reappro": 60, "Cout Achat Unitaire": 1700, "Delai Appro Jours": 4},
        {"Code Article": "ART-CLOU1", "Libelle Article": "Boîte de clous 1kg", "Stock Disponible": 75,
         "Seuil Alerte": 25, "Seuil Reappro": 50, "Cout Achat Unitaire": 700, "Delai Appro Jours": 3},
    ]
    df = pd.DataFrame(lignes)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def _generer_tresorerie_csv() -> bytes:
    import random

    random.seed(99)
    lignes = []
    for jour_offset in range(60, -1, -1):
        date = (AUJOURD_HUI - timedelta(days=jour_offset)).strftime("%Y-%m-%d")
        encaissement = round(random.uniform(20_000, 80_000), 2)
        lignes.append({"Date Mouvement": date, "Montant Net": encaissement, "Categorie Mouvement": "vente"})
        if random.random() < 0.4:
            decaissement = round(random.uniform(5_000, 30_000), 2)
            lignes.append(
                {"Date Mouvement": date, "Montant Net": -decaissement, "Categorie Mouvement": "fournisseur"}
            )
    df = pd.DataFrame(lignes)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


# ── TEST BOUT-EN-BOUT ─────────────────────────────────────────────────────

def test_full_pipeline_simulated_pme_dataset(client, db_session):
    # ── 1. Import des transactions (mapping explicite, colonnes non-Online Retail II) ──
    mapping_transactions = {
        "date_col": "Date Operation",
        "client_col": "Reference Client",
        "nom_client_col": "Nom Client",
        "email_client_col": "Email Client",
        "pays_col": "Pays",
        "numero_facture_col": "Reference Vente",
        "code_produit_col": "Code Article",
        "description_produit_col": "Libelle Article",
        "quantite_col": "Quantite Vendue",
        "prix_unitaire_col": "Prix Unitaire HT",
    }
    resp = client.post(
        "/api/v1/ingestion/import/transactions",
        files={"file": ("ventes.csv", io.BytesIO(_generer_transactions_csv()), "text/csv")},
        data={"mapping": json.dumps(mapping_transactions)},
    )
    assert resp.status_code == 200, resp.text
    import_transactions = resp.json()
    assert import_transactions["n_enregistrements_crees"] == 80  # 20+20+25+15 clients distincts
    assert import_transactions["n_lignes_importees"] > 0

    # ── 2. Import du stock ──────────────────────────────────────────────────
    mapping_stock = {
        "code_produit_col": "Code Article",
        "nom_produit_col": "Libelle Article",
        "quantite_disponible_col": "Stock Disponible",
        "seuil_alerte_col": "Seuil Alerte",
        "seuil_reappro_col": "Seuil Reappro",
        "cout_unitaire_col": "Cout Achat Unitaire",
        "delai_livraison_col": "Delai Appro Jours",
    }
    resp = client.post(
        "/api/v1/ingestion/import/stocks",
        files={"file": ("stock.csv", io.BytesIO(_generer_stock_csv()), "text/csv")},
        data={"mapping": json.dumps(mapping_stock)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["n_enregistrements_crees"] == 5

    # ── 3. Import de la trésorerie ──────────────────────────────────────────
    mapping_tresorerie = {
        "date_col": "Date Mouvement",
        "montant_col": "Montant Net",
        "categorie_col": "Categorie Mouvement",
    }
    resp = client.post(
        "/api/v1/ingestion/import/tresorerie",
        files={"file": ("treso.csv", io.BytesIO(_generer_tresorerie_csv()), "text/csv")},
        data={"mapping": json.dumps(mapping_tresorerie)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["n_enregistrements_crees"] > 0

    # Vérification directe en base : le socle commun est bien peuplé
    assert db_session.query(Client).count() == 80
    assert db_session.query(Transaction).count() > 0
    assert db_session.query(StockProduit).count() == 5
    assert db_session.query(Tresorerie).count() > 0

    # ── 4. Segmentation RFM + clustering K-Means (module Abdoulmadjid) ──────
    resp = client.post("/api/v1/marketing/rfm/run", json={"k_min": 2, "k_max": 6})
    assert resp.status_code == 200, resp.text
    rfm_resultat = resp.json()
    assert rfm_resultat["n_clients"] == 80
    assert rfm_resultat["k_choisi"] is not None
    assert rfm_resultat["silhouette_choisi"] > 0.3
    assert rfm_resultat["ecrit_en_base"] is True

    resp = client.get("/api/v1/marketing/segments/summary")
    assert resp.status_code == 200
    segments_summary = resp.json()
    total_segmente = sum(s["nb_clients"] for s in segments_summary)
    assert total_segmente == 80

    # Les 4 profils synthétiques doivent être détectés avec un minimum de
    # cohérence : au moins "Fidèle", "À risque" et "Nouveau" doivent exister
    # (Occasionnel peut fusionner avec un autre segment selon le k choisi).
    labels_presents = {s["segment_rfm"] for s in segments_summary}
    assert "Fidèle" in labels_presents
    assert "À risque" in labels_presents
    assert "Nouveau" in labels_presents

    # Vérification fine : les profils synthétiques FID-*/RISK-*/NEW-* doivent
    # être étiquetés cohéremment avec leur construction (cf. génération ci-dessus).
    fid_labels = {c.segment_rfm for c in db_session.query(Client).filter(Client.code_client_externe.like("FID-%"))}
    risk_labels = {c.segment_rfm for c in db_session.query(Client).filter(Client.code_client_externe.like("RISK-%"))}
    new_labels = {c.segment_rfm for c in db_session.query(Client).filter(Client.code_client_externe.like("NEW-%"))}
    assert fid_labels == {"Fidèle"}
    assert risk_labels == {"À risque"}
    assert new_labels == {"Nouveau"}

    # ── 5. KPIs globaux (module Maslaw) ──────────────────────────────────────
    resp = client.get("/api/v1/analytics/kpis")
    assert resp.status_code == 200
    kpis = resp.json()
    assert kpis["nb_clients"] == 80
    assert kpis["chiffre_affaires_total"] > 0
    assert kpis["nb_transactions"] > 0

    # ── 6. Alertes de stock — 2 produits volontairement sous seuil ──────────
    resp = client.get("/api/v1/analytics/stock/alertes")
    assert resp.status_code == 200
    alertes = resp.json()
    codes_en_alerte = {a["code_produit"] for a in alertes}
    assert "ART-FER12" in codes_en_alerte  # stock=8 < seuil_alerte=15
    assert "ART-PEINT5" in codes_en_alerte  # stock=3 < seuil_alerte=10
    assert "ART-CIM50" not in codes_en_alerte  # stock=120, largement au-dessus

    # ── 7. Prévision des ventes (ARIMA — rapide, adapté à la CI) ────────────
    resp = client.get("/api/v1/analytics/forecast/sales?horizon_jours=14&modele=arima")
    assert resp.status_code == 200
    prevision = resp.json()
    assert prevision["modele_utilise"] == "ARIMA(1,1,1)"
    assert len(prevision["points"]) == 14
    assert prevision["rmse_validation"] is not None  # historique de 250j -> largement suffisant
    assert all(p["valeur_prevue"] >= 0 for p in prevision["points"])

    # ── 8. Assistant conversationnel — doit refléter l'état réel de la base ──
    resp = client.post("/api/v1/copilot/chat", json={"question": "Comment se portent mes ventes et mes stocks ?"})
    assert resp.status_code == 200
    reponse_copilote = resp.json()["reponse"]
    assert "Clients: 80" in reponse_copilote
    assert "Alertes stock actives: 2" in reponse_copilote
    assert "Segment Fidèle" in reponse_copilote
    assert "Segment À risque" in reponse_copilote

    # ── 9. Rapport PDF final — doit s'assembler sans erreur sur ce jeu réel ──
    resp = client.post(
        "/api/v1/report/pdf",
        json={"entreprise": "Quincaillerie Sahel", "auteur": "Groupe 2 — Master IA Appliquée"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"
    assert len(resp.content) > 5000  # un vrai rapport multi-sections, pas une page vide
