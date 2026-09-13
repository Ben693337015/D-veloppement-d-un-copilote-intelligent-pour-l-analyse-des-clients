def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["statut"] == "ok"


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_openapi_exposes_expected_routes(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"].keys()
    for expected in [
        "/api/v1/analytics/kpis",
        "/api/v1/analytics/forecast/sales",
        "/api/v1/analytics/stock/alertes",
        "/api/v1/marketing/clients",
        "/api/v1/marketing/segments/summary",
        "/api/v1/copilot/chat",
    ]:
        assert expected in paths
