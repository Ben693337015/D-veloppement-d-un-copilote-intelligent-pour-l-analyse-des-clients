from datetime import datetime, timedelta

from app.models import Client, Transaction


def _seed_donnees(db_session):
    today = datetime(2024, 6, 1)
    for i in range(6):
        c = Client(code_client_externe=f"C{i}")
        db_session.add(c)
        db_session.flush()
        for j in range(4):
            db_session.add(
                Transaction(
                    client_id=c.client_id,
                    numero_facture=f"F{i}-{j}",
                    quantite=1,
                    prix_unitaire=50.0 + i * 10,
                    date_transaction=today - timedelta(days=j * 5),
                )
            )
    db_session.commit()


def test_generate_pdf_report_returns_valid_pdf(client, db_session):
    _seed_donnees(db_session)
    # Génère les segments avant le rapport, comme dans un vrai flux (rfm/run -> report/pdf)
    resp_rfm = client.post("/api/v1/marketing/rfm/run", json={"k_min": 2, "k_max": 3})
    assert resp_rfm.status_code == 200

    resp = client.post(
        "/api/v1/report/pdf",
        json={"entreprise": "Boulangerie Test", "auteur": "Groupe 2"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"
    assert len(resp.content) > 1000


def test_generate_pdf_report_respects_sections_filter(client, db_session):
    _seed_donnees(db_session)
    resp = client.post(
        "/api/v1/report/pdf",
        json={"sections": ["page_garde", "kpis_globaux"]},
    )
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_generate_pdf_report_empty_database_does_not_crash(client):
    resp = client.post("/api/v1/report/pdf", json={})
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_generate_pdf_report_ignores_unknown_sections(client, db_session):
    _seed_donnees(db_session)
    resp = client.post(
        "/api/v1/report/pdf",
        json={"sections": ["page_garde", "section_qui_n_existe_pas"]},
    )
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_sans_weasyprint_l_api_demarre_et_le_pdf_renvoie_503(client, monkeypatch):
    """Installation locale sous Windows sans GTK : avant, `import weasyprint`
    au chargement du module faisait tomber toute l'API. Désormais l'import
    est paresseux et seul le PDF répond 503 avec la marche à suivre."""
    import builtins

    vrai_import = builtins.__import__

    def import_sans_gtk(nom, *args, **kwargs):
        if nom == "weasyprint":
            raise OSError("cannot load library 'gobject-2.0-0'")
        return vrai_import(nom, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_sans_gtk)
    assert client.get("/health").status_code == 200
    r = client.post("/api/v1/report/pdf", json={})
    assert r.status_code == 503
    assert "GTK3 Runtime" in r.json()["detail"]


# ── Contenu du rapport (refonte du 13/10/2026) ──────────────────────────────


def _jeu_complet(client, db_session):
    from datetime import datetime, timedelta

    from app.models import Client, StockProduit, Transaction, Tresorerie

    base = datetime(2024, 1, 1)
    for i in range(30):
        c = Client(code_client_externe=f"C{i}", nom=f"Client {i}")
        db_session.add(c)
        db_session.flush()
        for j in range(1 + i % 4):
            db_session.add(Transaction(client_id=c.client_id, numero_facture=f"F{i}-{j}", quantite=1 + j,
                                       prix_unitaire=20 + i * 5, date_transaction=base + timedelta(days=(i * 3 + j * 11) % 90)))
    for i in range(60):
        db_session.add(Tresorerie(date_mouvement=(base + timedelta(days=i)).date(), montant=1000 + i * 10,
                                  type_mouvement="encaissement" if i % 3 else "decaissement"))
    db_session.add(StockProduit(code_produit="P1", nom_produit="Vélo", quantite_disponible=1, seuil_alerte=5,
                                seuil_reapprovisionnement=10))
    db_session.commit()
    assert client.post("/api/v1/marketing/rfm/run", json={"k_min": 2, "k_max": 4}).status_code == 200


def test_rapport_contient_toutes_les_analyses_et_leurs_interpretations(client, db_session):
    from app.schemas.report import RapportPDFRequest
    from app.services import report_service

    _jeu_complet(client, db_session)
    import html as html_mod

    html = html_mod.unescape(report_service.construire_html(db_session, RapportPDFRequest()))
    for titre in ("Résumé exécutif", "Indicateurs clés", "Segmentation RFM — choix de l'algorithme",
                  "Segments et actions recommandées", "Clients — concentration et meilleurs clients",
                  "Prévision des ventes à 30 jours — 3 modèles", "Trésorerie — solde et prévision",
                  "Stocks — alertes de réapprovisionnement"):
        assert titre in html, titre
    assert "Section indisponible" not in html
    # interprétations reprises de l'application
    assert "Algorithme recommandé" in html          # synthèse de la segmentation
    assert "Interprétation détaillée" in html and "Kaufman" in html
    assert "Modèle le plus fiable" in html            # synthèse des prévisions
    assert "Interprétation par modèle" in html
    assert "Niveau d'alerte" in html
    assert "critique" in html                           # alerte de stock
    assert html.count("data:image/png;base64") >= 4    # segmentation, segments, prévisions, trésorerie


def test_resume_executif_reprend_chaque_module(client, db_session):
    from app.services import report_service

    _jeu_complet(client, db_session)
    themes = [t for t, _ in report_service._phrases_resume(report_service._Donnees(db_session))]
    for theme in ("Activité", "Clients", "Segmentation", "Segments", "Ventes", "Trésorerie", "Stocks"):
        assert theme in themes, theme


def test_anciens_identifiants_de_section_acceptes():
    from app.schemas.report import RapportPDFRequest

    r = RapportPDFRequest(sections=["top_clients", "coude_silhouette", "segments_summary", "distribution_segments"])
    assert r.sections_valides() == ["segmentation", "segments", "clients"]


def test_section_en_echec_n_empeche_pas_le_rapport(client, db_session, monkeypatch):
    from app.schemas.report import RapportPDFRequest
    from app.services import comparaison_previsions, report_service

    def panne(*_a, **_k):
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(comparaison_previsions, "comparer_modeles", panne)
    html = report_service.construire_html(db_session, RapportPDFRequest(sections=["kpis_globaux", "previsions_ventes"]))
    assert "Indicateurs clés" in html and "Section indisponible (panne simulée)" in html


def test_rapport_sans_segmentation_explique_quoi_faire(db_session):
    from app.schemas.report import RapportPDFRequest
    from app.services import report_service

    html = report_service.construire_html(db_session, RapportPDFRequest(sections=["segmentation"]))
    assert "lancer le pipeline RFM" in html
