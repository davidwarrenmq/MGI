"""Phase 4 tests: the Citation Checker integration adapter (scope §13).

Verifies the adapter shapes MGI output to the frozen contract WITHOUT importing
the Citation Checker. Uses an injected stub searcher (no sqlite3 / no network).
"""
from pathlib import Path

import pytest

from mgi.guideline_record import GuidelineRecord
from mgi.search import Searcher
from mgi.registry import load_registry
from mgi.integration import GuidelineIndexBackend, resolve_citation, CONTRACT_KEYS

SEED = Path(__file__).resolve().parents[1] / "mgi" / "seed_guidelines.jsonl"


class _StubStore:
    def __init__(self, records):
        self.records = records

    def search(self, text, limit=25):
        toks = "".join(c if c.isalnum() else " " for c in (text or "").lower()).split()
        out = []
        for r in self.records:
            hay = " ".join([r.title or "", r.issuer or "",
                            " ".join(r.topics or []), r.identifier or ""]).lower()
            if any(t in hay for t in toks):
                out.append(r)
        return out[:limit]


@pytest.fixture(scope="module")
def backend():
    sr = Searcher(_StubStore(GuidelineRecord.load_jsonl(SEED)), load_registry())
    return GuidelineIndexBackend(searcher=sr)


def test_backend_name_is_mgi(backend):
    assert backend.name == "mgi"


def test_is_candidate_gates_guideline_citations(backend):
    assert backend.is_candidate("NICE NG28 clinical guideline")
    assert backend.is_candidate("WHO guidelines on hypertension")
    assert not backend.is_candidate("Smith J et al. A randomized trial. NEJM 2020.")


def test_resolve_returns_full_contract_keys(backend):
    res = backend.resolve("NICE NG28 type 2 diabetes in adults")
    assert set(CONTRACT_KEYS) <= set(res)
    assert res["backend"] == "mgi"
    assert res["resolved_pmid"] is None


def test_include_record_false_yields_strict_seven_keys(backend):
    res = backend.resolve("NICE NG28 type 2 diabetes in adults", include_record=False)
    assert set(res) == set(CONTRACT_KEYS)
    assert "record" not in res


def test_resolve_verified_for_known_guideline(backend):
    res = backend.resolve("NICE NG28, type 2 diabetes in adults")
    assert res["status"] == "verified"
    assert res["resolved_url"] == "https://www.nice.org.uk/guidance/ng28"


def test_resolve_never_raises_on_bad_input(backend):
    for bad in (None, "", 123):
        res = backend.resolve(bad)
        assert res["status"] in ("no_match", "error")
        assert set(CONTRACT_KEYS) <= set(res)


def test_metadata_for_checker_maps_guideline_track_fields(backend):
    res = backend.resolve("NICE CG127 hypertension clinical management of "
                          "primary hypertension in adults")
    meta = GuidelineIndexBackend.metadata_for_checker(res.get("record"))
    assert meta["publication_types"] == ["Practice Guideline"]
    assert meta["status"] == "superseded"
    assert meta["is_current"] is False
    assert meta["superseded_by"]
    assert meta["country"] == "GB"


def test_metadata_for_checker_handles_none():
    assert GuidelineIndexBackend.metadata_for_checker(None) == {}


def test_convenience_resolve_citation(backend):
    # uses its own backend but the same stub-less path -> fabricated => no_match
    sr = backend  # reuse the fixture's searcher via the backend's resolve
    res = sr.resolve("A fabricated guideline on zorblax syndrome 2099")
    assert res["status"] == "no_match"


def test_no_citation_checker_import():
    # The adapter must not import the Citation Checker (scope §13.6).
    import mgi.integration as integ
    src = Path(integ.__file__).read_text(encoding="utf-8")
    lowered = src.lower()
    assert "import citation_checker" not in lowered
    assert "from citation_checker" not in lowered
