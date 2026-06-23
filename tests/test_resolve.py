"""Phase 1 tests: the resolve() contract (scope §7 / §13.3).

These run WITHOUT sqlite3 by injecting a tiny in-memory stub store into a real
Searcher, so the resolver logic (normalisation, thresholds, DOI cleaning,
never-raises) is exercised everywhere.
"""
from pathlib import Path

import pytest

from mgi.guideline_record import GuidelineRecord
from mgi.search import Searcher
from mgi.registry import load_registry
import mgi.resolve as R

SEED = Path(__file__).resolve().parents[1] / "mgi" / "seed_guidelines.jsonl"

REQUIRED_KEYS = {
    "status", "confidence", "backend", "resolved_url",
    "resolved_doi", "resolved_pmid", "details",
}


class _StubStore:
    """Substring token-OR recall over title/issuer/topics/identifier."""

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
def searcher():
    recs = GuidelineRecord.load_jsonl(SEED)
    return Searcher(_StubStore(recs), load_registry())


def _resolve(searcher, q, **kw):
    return R.resolve(q, searcher=searcher, **kw)


# --- shape / contract -------------------------------------------------
def test_returns_all_seven_keys(searcher):
    res = _resolve(searcher, "NICE NG28 type 2 diabetes in adults management")
    assert REQUIRED_KEYS <= set(res)


def test_backend_is_always_mgi(searcher):
    for q in ["NICE NG28 diabetes", "nonsense xyzzy", ""]:
        assert _resolve(searcher, q)["backend"] == "mgi"


def test_resolved_pmid_always_none(searcher):
    for q in ["WHO hypertension guideline 2021", "fabricated zorblax 2099", ""]:
        assert _resolve(searcher, q)["resolved_pmid"] is None


# --- thresholds (scope §7) -------------------------------------------
def test_known_guideline_is_verified_with_direct_url(searcher):
    res = _resolve(searcher, "NICE NG28, type 2 diabetes in adults")
    assert res["status"] == "verified"
    assert res["confidence"] >= 0.78
    assert res["resolved_url"] == "https://www.nice.org.uk/guidance/ng28"


def test_confidence_formula_verified_band(searcher):
    res = _resolve(searcher, "WHO guideline on the pharmacological treatment of "
                             "hypertension in adults 2021")
    assert res["status"] == "verified"
    # confidence = min(1.0, 0.6 + 0.4*score) -> at least 0.6
    assert 0.6 <= res["confidence"] <= 1.0


def test_fabricated_guideline_is_no_match(searcher):
    res = _resolve(searcher, "Totally fabricated nonexistent guideline on "
                             "zorblax syndrome 2099")
    assert res["status"] == "no_match"
    assert res["confidence"] == 0.0
    assert res["resolved_url"] is None


def test_min_confidence_can_demote_to_no_match(searcher):
    # Force an impossibly high bar -> even a real match is reported as no_match.
    res = _resolve(searcher, "IDSA guideline candidiasis", min_confidence=0.99)
    assert res["status"] == "no_match"


# --- DOI normalisation (scope §13.3) ---------------------------------
def test_doi_is_bare_and_lowercased():
    assert R.normalise_doi("https://doi.org/10.1001/JAMA.2021.6238.") == "10.1001/jama.2021.6238"
    assert R.normalise_doi("doi:10.1093/EURHEARTJ/ehad191") == "10.1093/eurheartj/ehad191"
    assert R.normalise_doi("DOI: 10.7326/M20-7533);") == "10.7326/m20-7533"
    assert R.normalise_doi(None) is None
    assert R.normalise_doi("") is None


def test_resolved_doi_present_when_record_has_one(searcher):
    res = _resolve(searcher, "2017 ACC/AHA Guideline for the Management of High "
                             "Blood Pressure in Adults")
    assert res["status"] in ("verified", "likely_exists")
    assert res["resolved_doi"] == "10.1161/hyp.0000000000000065"


# --- zombie / currency (scope §11 / §13.5) ---------------------------
def test_superseded_guideline_exposes_superseded_by(searcher):
    res = _resolve(searcher, "NICE CG127 Hypertension clinical management of "
                             "primary hypertension in adults")
    rec = res.get("record")
    assert rec is not None
    assert rec["status"] == "superseded"
    assert rec["superseded_by"]  # points at the newer doc's id
    assert "superseded" in res["details"]


# --- never raises (scope §13.3) --------------------------------------
@pytest.mark.parametrize("bad", [None, "", "   ", 12345, [], {"x": 1}])
def test_never_raises_on_bad_input(searcher, bad):
    res = _resolve(searcher, bad)
    assert res["status"] in ("no_match", "error")
    assert REQUIRED_KEYS <= set(res)
    assert res["backend"] == "mgi"


def test_internal_error_returns_error_dict():
    class Boom:
        def best(self, *a, **k):
            raise RuntimeError("kaboom")
    res = R.resolve("anything", searcher=Boom())
    assert res["status"] == "error"
    assert res["confidence"] == 0.0
    assert res["backend"] == "mgi"
    assert "kaboom" in res["details"]
