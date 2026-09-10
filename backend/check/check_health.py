from fastapi.testclient import TestClient
from unittest.mock import patch

from backend.app import app

# This check must not depend on a live Firestore request.


def test_health_and_browser_default_routes_are_valid() -> None:
    with (
        patch("backend.app.firebase_is_configured", return_value=False),
        patch("backend.api.api_health.firebase_is_configured", return_value=False),
        patch("backend.api.api_health.probe_firestore", return_value=False),
        TestClient(app) as client,
    ):
        response = client.get("/health")
        root_response = client.get("/")
        favicon_response = client.get("/favicon.ico")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "firebaseConfigured" in payload
    assert "firebaseInitialized" in payload
    assert "firestoreReady" in payload
    assert root_response.status_code == 200
    assert root_response.json()["status"] == "ok"
    assert favicon_response.status_code == 204


def test_openapi_lists_health_and_protected_kpi_runs() -> None:
    schema = app.openapi()

    assert "/health" in schema["paths"]
    assert set(schema["paths"]) == {
        "/health",
        "/api/v1/match-kpis/raw-change/all",
        "/api/v1/match-kpis/raw-change/round",
        "/api/v1/match-kpis/logic-change/all",
        "/api/v1/match-kpis/logic-change/round",
    }
    assert schema["components"]["securitySchemes"]["SwaggerKey"]["name"] == "X-Swagger-Key"
    assert schema["paths"]["/api/v1/match-kpis/raw-change/all"]["post"]["security"] == [{"SwaggerKey": []}]


def test_kpi_build_rejects_missing_swagger_key() -> None:
    with TestClient(app) as client:
        response = client.post("/api/v1/match-kpis/raw-change/all")

    assert response.status_code == 401
