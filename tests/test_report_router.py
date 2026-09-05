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
