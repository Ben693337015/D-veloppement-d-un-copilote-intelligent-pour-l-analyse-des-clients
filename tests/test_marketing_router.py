from app.models import Client


def test_segments_summary_maps_action_marketing(client, db_session):
    db_session.add(
        Client(
            code_client_externe="C001",
            segment_rfm="Fidèle",
            montant_total=1200.0,
            score_churn=0.05,
        )
    )
    db_session.commit()

    resp = client.get("/api/v1/marketing/segments/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["segment_rfm"] == "Fidèle"
    assert "fidélité" in body[0]["action_marketing_recommandee"].lower()


def test_clients_rfm_filter_by_segment(client, db_session):
    db_session.add_all(
        [
            Client(code_client_externe="C001", segment_rfm="Fidèle"),
            Client(code_client_externe="C002", segment_rfm="À risque"),
        ]
    )
    db_session.commit()

    resp = client.get("/api/v1/marketing/clients?segment=À risque")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["code_client_externe"] == "C002"
