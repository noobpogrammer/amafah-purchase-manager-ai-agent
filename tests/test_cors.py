from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def _preflight(origin: str):
    return client.options(
        "/rfq/create",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )


def test_cors_preflight_allows_local_vite():
    r = _preflight("http://localhost:5173")
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_preflight_allows_railway_frontend_host():
    origin = "https://amafha-frontend-production.up.railway.app"
    r = _preflight(origin)
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == origin
