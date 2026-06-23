"""Phase 3 tests: FastAPI service module (scope §9 Phase 3).

These verify graceful behaviour without requiring FastAPI to be installed
(system prompt §6). When FastAPI IS present, a TestClient smoke test runs;
otherwise it is skipped.
"""
import importlib.util

import pytest

import mgi.service as SV

_HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None
_HAS_SQLITE = importlib.util.find_spec("sqlite3") is not None


def test_module_imports_without_fastapi():
    # Import must never hard-fail; app is None when FastAPI is absent.
    assert hasattr(SV, "app")
    assert hasattr(SV, "create_app")


def test_create_app_raises_clear_error_when_fastapi_missing():
    if SV._HAVE_FASTAPI:
        pytest.skip("FastAPI is installed; degradation path not exercised")
    with pytest.raises(RuntimeError):
        SV.create_app()


@pytest.mark.skipif(not (_HAS_FASTAPI and _HAS_SQLITE),
                    reason="needs fastapi + sqlite3")
def test_endpoints_smoke(tmp_path):
    from fastapi.testclient import TestClient

    api = SV.create_app(db_path=str(tmp_path / "mgi.db"))
    client = TestClient(api)

    assert client.get("/health").json()["status"] == "ok"

    r = client.get("/resolve", params={"citation": "NICE NG28 type 2 diabetes in adults"})
    body = r.json()
    assert set(["status", "confidence", "backend", "resolved_url",
                "resolved_doi", "resolved_pmid", "details"]) <= set(body)
    assert body["backend"] == "mgi"

    s = client.get("/search", params={"q": "hypertension", "limit": 5})
    assert s.json()["count"] >= 0
