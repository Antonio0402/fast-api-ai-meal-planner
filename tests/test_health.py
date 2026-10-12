def test_internal_health_check(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_exposes_exactly_seventeen_product_endpoints(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200

    paths = response.json()["paths"]
    product_operations = {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/api/v1/")
        for method in operations
    }
    assert len(product_operations) == 17
    assert "/health" in paths
