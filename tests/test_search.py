"""Phase 1 tests: citation normalisation, signal extraction, re-ranking (scope §7/§13.4)."""
from pathlib import Path

import pytest

from mgi.guideline_record import GuidelineRecord
from mgi.registry import load_registry
from mgi.search import (
    Searcher, normalise_citation, extract_signals, _title_similarity,
)

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
def searcher():
    return Searcher(_StubStore(GuidelineRecord.load_jsonl(SEED)), load_registry())


def test_normalise_strips_markdown_and_collapses_ws():
    out = normalise_citation("**NICE**  _NG28_  `guideline`")
    assert "*" not in out and "_" not in out and "`" not in out
    assert "  " not in out
    assert out == "nice ng28 guideline"


def test_normalise_expands_known_abbrev():
    reg = load_registry()
    out = normalise_citation("WHO hypertension guideline", reg)
    assert "world health organization" in out


def test_extract_year():
    assert extract_signals("ESC AF guidelines 2020").year == 2020
    assert extract_signals("no year here").year is None


def test_extract_quoted_title():
    s = extract_signals('the report titled "Standards of Medical Care in Diabetes" 2024')
    assert s.quoted_title == "Standards of Medical Care in Diabetes"


def test_extract_identifier():
    assert extract_signals("NICE NG28 diabetes").identifier == "NG28"
    assert extract_signals("hypertension CG127").identifier == "CG127"


def test_similarity_is_sequencematcher_ratio():
    from difflib import SequenceMatcher
    a, b = "Type 2 Diabetes", "type 2 diabetes in adults"
    assert _title_similarity(a, b) == SequenceMatcher(
        None, a.strip().lower(), b.strip().lower()).ratio()


def test_identifier_boost_lifts_correct_record(searcher):
    best = searcher.best("NICE NG28 type 2 diabetes in adults")
    assert best.record.identifier == "NG28"
    assert "identifier" in best.boosts


def test_ranking_prefers_matching_year(searcher):
    best = searcher.best("ESC Guidelines for the management of atrial fibrillation 2020")
    assert best.record.year == 2020
    assert best.record.issuer_abbrev == "ESC"
