"""resolve(citation) -> the exact 7-key Citation Checker resolver dict (scope §13.3).

This is MGI's single most important deliverable. The function:

* NEVER raises -- on any internal error it returns the error dict form (§13.3).
* Returns exactly the seven keys ``status, confidence, backend, resolved_url,
  resolved_doi, resolved_pmid, details`` (plus an MGI-only ``record`` extra the
  Citation Checker is free to ignore).
* ``backend`` is always ``"mgi"``; ``resolved_pmid`` is always ``None``.
* DOIs are returned bare + lower-cased (no ``https://doi.org/``; no trailing
  punctuation).
* Status thresholds mirror the Citation Checker EXACTLY (scope §7):
  ``score >= 0.78`` -> ``verified``; ``score >= 0.45`` -> ``likely_exists``;
  otherwise ``no_match``.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .search import Searcher, Candidate

BACKEND = "mgi"

# DOI normalisation (scope §13.3): strip URL/prefix forms + trailing punctuation,
# then lower-case, leaving the bare form e.g. "10.1001/jama.2023.12345".
_DOI_PREFIX = re.compile(r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi:)\s*", re.IGNORECASE)
_DOI_TRAIL = re.compile(r"[.,);\]]+$")


def normalise_doi(doi: Optional[str]) -> Optional[str]:
    """Return the bare, lower-cased DOI, or ``None`` (scope §13.3)."""
    if not doi:
        return None
    d = _DOI_PREFIX.sub("", str(doi).strip())
    d = _DOI_TRAIL.sub("", d).strip()
    return d.lower() or None


# Confidence formulas (scope §7 step 4 / §13.3).
VERIFIED_THRESHOLD = 0.78
LIKELY_THRESHOLD = 0.45


def _confidence_for(score: float) -> "tuple[str, float]":
    if score >= VERIFIED_THRESHOLD:
        return "verified", min(1.0, 0.6 + 0.4 * score)
    if score >= LIKELY_THRESHOLD:
        return "likely_exists", min(0.9, 0.3 + 0.6 * score)
    return "no_match", 0.0


def _error(reason: str) -> Dict[str, Any]:
    return {
        "status": "error",
        "confidence": 0.0,
        "backend": BACKEND,
        "resolved_url": None,
        "resolved_doi": None,
        "resolved_pmid": None,
        "details": reason,
    }


def _no_match(details: str) -> Dict[str, Any]:
    return {
        "status": "no_match",
        "confidence": 0.0,
        "backend": BACKEND,
        "resolved_url": None,
        "resolved_doi": None,
        "resolved_pmid": None,
        "details": details,
        "record": None,
    }


# Module-level cached searcher so repeated resolve() calls don't reopen the DB.
_DEFAULT_SEARCHER: Optional[Searcher] = None


def _get_default_searcher() -> Searcher:
    """Open the default store (mgi.db) and build a Searcher, cached.

    If ``mgi.db`` does not exist or is empty, fall back to the bundled seed
    records loaded into an in-memory store so ``resolve`` works out-of-the-box.
    """
    global _DEFAULT_SEARCHER
    if _DEFAULT_SEARCHER is not None:
        return _DEFAULT_SEARCHER

    from pathlib import Path
    from .store import Store
    from .registry import load_registry

    db_path = Path("mgi.db")
    store = Store(db_path)
    if store.count() == 0:
        # Seed the in-process DB from the bundled seed file (idempotent).
        seed = Path(__file__).with_name("seed_guidelines.jsonl")
        if seed.exists():
            from .guideline_record import GuidelineRecord
            store.upsert_many(GuidelineRecord.load_jsonl(seed))
    _DEFAULT_SEARCHER = Searcher(store, load_registry())
    return _DEFAULT_SEARCHER


def _build_result(cand: Candidate, min_confidence: float) -> Dict[str, Any]:
    score = cand.score
    rec = cand.record

    # Guard against title-only false positives: a candidate with NO corroborating
    # signal (no issuer / year / identifier agreement) must clear a higher base
    # similarity bar, since shared generic words ("guidelines", "management")
    # inflate SequenceMatcher. Specific matches (with boosts) are trusted as-is.
    BARE_MATCH_FLOOR = 0.62
    if not cand.boosts and cand.base_similarity < BARE_MATCH_FLOOR:
        return _no_match(
            f"best candidate '{rec.title[:60]}' had only weak title overlap "
            f"(sim={cand.base_similarity:.2f}) and no issuer/year/identifier match"
        )

    status, confidence = _confidence_for(score)

    # Demote sub-threshold matches to no_match (respect caller's min_confidence).
    if status == "no_match" or confidence < min_confidence:
        return _no_match(
            f"best candidate '{rec.title[:60]}' scored {score:.2f} "
            f"(< min_confidence {min_confidence:.2f})"
        )

    note = ""
    if rec.status and rec.status != "active":
        note = f" NOTE: this document is {rec.status}"
        if rec.superseded_by:
            note += f" (superseded_by={rec.superseded_by})"

    boost_txt = ", ".join(f"{k}+{v:.2f}" for k, v in cand.boosts.items()) or "none"
    details = (
        f"matched {rec.issuer_abbrev or rec.issuer} "
        f"{('[' + rec.identifier + '] ') if rec.identifier else ''}"
        f"'{rec.title[:70]}' (sim={cand.base_similarity:.2f}, boosts={boost_txt}, "
        f"score={score:.2f}).{note}"
    )

    return {
        "status": status,
        "confidence": round(confidence, 4),
        "backend": BACKEND,
        "resolved_url": rec.url or None,
        "resolved_doi": normalise_doi(rec.doi),
        "resolved_pmid": None,
        "details": details,
        "record": rec.to_dict(),
    }


def resolve(citation: str, *, min_confidence: float = 0.45,
            searcher: Optional[Searcher] = None) -> Dict[str, Any]:
    """Resolve a guideline citation to the §13.3 dict. NEVER raises.

    Args:
        citation: raw citation text (markdown tolerated; normalised internally).
        min_confidence: minimum confidence to report a (non ``no_match``) status.
        searcher: optional pre-built Searcher (for tests / custom stores);
            defaults to the bundled/``mgi.db`` index.

    Returns:
        A plain dict with exactly the keys in scope §13.3 (plus ``record``).
    """
    try:
        if not isinstance(citation, str) or not citation.strip():
            return _no_match("empty or non-string citation")

        sr = searcher if searcher is not None else _get_default_searcher()
        best = sr.best(citation)
        if best is None:
            return _no_match("no candidate guidelines found in the index")
        return _build_result(best, min_confidence)
    except Exception as exc:  # never raise out of resolve() (scope §13.3)
        return _error(f"{type(exc).__name__}: {exc}")
