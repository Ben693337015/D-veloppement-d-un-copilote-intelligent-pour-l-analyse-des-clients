from app.models import StockProduit


def test_kpis_empty_db_returns_zeros(client):
    resp = client.get("/api/v1/analytics/kpis")
    assert resp.status_code == 200
    body = resp.json()
    assert body["nb_clients"] == 0
    assert body["chiffre_affaires_total"] == 0.0


def test_sales_forecast_horizon(client):
    resp = client.get("/api/v1/analytics/forecast/sales?horizon_jours=7")
    assert resp.status_code == 200
    body = resp.json()
    assert body["horizon_jours"] == 7
    assert len(body["points"]) == 7


def test_stock_alertes_flags_low_stock(client, db_session):
    db_session.add(
        StockProduit(
            code_produit="P001",
            nom_produit="Produit test",
            quantite_disponible=2,
            seuil_alerte=10,
            seuil_reapprovisionnement=20,
        )
    )
    db_session.commit()

    resp = client.get("/api/v1/analytics/stock/alertes")
    assert resp.status_code == 200
    alertes = resp.json()
    assert len(alertes) == 1
    assert alertes[0]["niveau"] == "critique"
