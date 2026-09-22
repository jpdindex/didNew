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
        "/api/v1/match-kpis/build",
        "/api/v1/match-ratings/build",
            "/api/v1/match-ratings/read",
            "/api/v1/legacy-import",
            "/api/v1/legacy-import/jobs/{job_id}",
                "/api/v1/match-input/matches",
                "/api/v1/match-input/matches/{gm_id}/squads",
                "/api/v1/match-input/matches/{gm_id}/squads/refresh",
                "/api/v1/match-input/drafts/{gm_id}/{side}",
            "/api/v1/match-input/drafts/{gm_id}/{side}/restore-raw",
            "/api/v1/match-input/drafts/{gm_id}/{side}/promote-h1",
            "/api/v1/match-input/drafts/{gm_id}/{side}/finalize",
            "/api/v1/match-input/approvals",
            "/api/v1/match-input/approvals/{gm_id}/{side}/promote",
        }
    assert schema["components"]["securitySchemes"]["SwaggerKey"]["name"] == "X-Swagger-Key"
    assert schema["paths"]["/api/v1/match-kpis/build"]["post"]["security"] == [{"SwaggerKey": []}]
    assert schema["paths"]["/api/v1/match-ratings/build"]["post"]["security"] == [{"SwaggerKey": []}]
    rating_operation = schema["paths"]["/api/v1/match-ratings/build"]["post"]
    assert "requestBody" in rating_operation
    assert "parameters" not in rating_operation
    assert "application/x-www-form-urlencoded" in rating_operation["requestBody"]["content"]
    rating_schema = rating_operation["requestBody"]["content"]["application/x-www-form-urlencoded"]["schema"]
    rating_properties = schema["components"]["schemas"][rating_schema["$ref"].split("/")[-1]]["properties"]
    assert rating_properties["force"]["default"] is False
    kpi_operation = schema["paths"]["/api/v1/match-kpis/build"]["post"]
    kpi_schema = kpi_operation["requestBody"]["content"]["application/x-www-form-urlencoded"]["schema"]
    kpi_properties = schema["components"]["schemas"][kpi_schema["$ref"].split("/")[-1]]["properties"]
    assert kpi_properties["force"]["default"] is False
    input_operation = schema["paths"]["/api/v1/match-input/drafts/{gm_id}/{side}"]["put"]
    assert "application/json" in input_operation["requestBody"]["content"]
    import_operation = schema["paths"]["/api/v1/legacy-import"]["post"]
    assert import_operation["security"] == [{"SwaggerKey": []}]
    assert "multipart/form-data" in import_operation["requestBody"]["content"]


def test_kpi_build_rejects_missing_swagger_key() -> None:
    with TestClient(app) as client:
        response = client.post("/api/v1/match-kpis/build")

    assert response.status_code == 401
