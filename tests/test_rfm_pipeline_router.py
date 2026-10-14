import pandas as pd

from app.models import Client, Transaction


def _seed_minimal_transactions(db_session):
    base_date = pd.Timestamp("2024-06-01")
    for i in range(6):
        c = Client(code_client_externe=f"C{i}")
        db_session.add(c)
        db_session.flush()
        for j in range(3):
            db_session.add(
                Transaction(
                    client_id=c.client_id,
                    numero_facture=f"F{i}-{j}",
                    quantite=2,
                    prix_unitaire=100.0 + i * 10,
                    date_transaction=base_date - pd.Timedelta(days=j * 10 + i),
                    est_retour=False,
                )
            )
    db_session.commit()


def test_run_rfm_pipeline_endpoint(client, db_session):
    _seed_minimal_transactions(db_session)

    resp = client.post("/api/v1/marketing/rfm/run", json={"k_min": 2, "k_max": 4})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["n_clients"] == 6
    assert body["ecrit_en_base"] is True
    assert body["k_choisi"] is not None
    assert len(body["diagnostics"]) == 3  # k=2,3,4
    for point in body["diagnostics"]:
        assert point["inertie"] >= 0

    # Les segments doivent maintenant être exploitables via l'endpoint existant.
    resp2 = client.get("/api/v1/marketing/clients")
    assert resp2.status_code == 200
    assert len(resp2.json()) == 6


def test_run_rfm_pipeline_dry_run_does_not_persist(client, db_session):
    _seed_minimal_transactions(db_session)

    resp = client.post("/api/v1/marketing/rfm/run", json={"dry_run": True})
    assert resp.status_code == 200
    assert resp.json()["ecrit_en_base"] is False

    resp2 = client.get("/api/v1/marketing/clients")
    assert resp2.json() == []  # segment_rfm toujours NULL -> filtré par le endpoint existant
