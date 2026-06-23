"""Drop-in adapter so the Citation Checker can use MGI as a fallback backend.

IMPORTANT (scope §2, §13.6 / system prompt §1): MGI is standalone. This module
does NOT import from, read files belonging to, or require runtime access to the
Citation Checker. It only *shapes* MGI's output to the frozen contract in
scope §13 so the eventual integration is trivial. It is wired into nothing.

Intended use on the Citation Checker side (illustrative only -- their code, not
shipped here):

    from mgi.integration import GuidelineIndexBackend
    backend = GuidelineIndexBackend(db_path="mgi.db")
    if backend.is_candidate(citation_text):
        result = backend.resolve(citation_text)   # exact §13.3 dict
        # merge `result` into the Checker's existence cascade by confidence

Everything here is pure-Python and never raises out of ``resolve``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

BACKEND_NAME = "mgi"

# The seven keys the Citation Checker's fallback resolver expects (scope §13.3).
CONTRACT_KEYS = (
    "status", "confidence", "backend", "resolved_url",
    "resolved_doi", "resolved_pmid", "details",
)


class GuidelineIndexBackend:
    """A resolver-backend wrapper around MGI, matching the Checker's seam.

    Args:
        db_path: path to the MGI SQLite store (default ``mgi.db``); the bundled
            seed index auto-loads if the store is empty.
        min_confidence: default floor passed through to ``resolve``.
        searcher: optional pre-built Searcher (e.g. for tests or a shared store).
    """

    name = BACKEND_NAME

    def __init__(self, db_path: str = "mgi.db", *, min_confidence: float = 0.45,
                 searcher: Optional[Any] = None) -> None:
        self.db_path = db_path
        self.min_confidence = min_confidence
        self._searcher = searcher
        self._registry = None  # lazily loaded for is_candidate()

    # -- gating ---------------------------------------------------------
    def _registry_obj(self):
        if self._registry is None:
            from .registry import load_registry
            self._registry = load_registry()
        return self._registry

    def is_candidate(self, citation: str) -> bool:
        """True if the citation looks institutional/guideline-like (§13.4).

        Mirrors the Citation Checker's gate: a guideline citation is one whose
        text contains issuer/guideline trigger terms. The Checker calls this to
        decide whether to invoke MGI at all.
        """
        try:
            return self._registry_obj().looks_like_guideline(citation or "")
        except Exception:
            return False


    # -- resolution -----------------------------------------------------
    def resolve(self, citation: str, *, min_confidence: Optional[float] = None,
                include_record: bool = True) -> Dict[str, Any]:
        """Resolve via MGI and return the §13.3 dict. Never raises.

        Args:
            citation: the citation text.
            min_confidence: override the instance default.
            include_record: keep MGI's extra ``record`` key (safe for the
                Checker to ignore). Set False for a strict 7-key dict.
        """
        from .resolve import resolve as _resolve

        mc = self.min_confidence if min_confidence is None else min_confidence
        try:
            result = _resolve(citation, min_confidence=mc, searcher=self._searcher)
        except Exception as exc:  # defensive; _resolve already never raises
            result = {
                "status": "error", "confidence": 0.0, "backend": BACKEND_NAME,
                "resolved_url": None, "resolved_doi": None, "resolved_pmid": None,
                "details": f"{type(exc).__name__}: {exc}",
            }

        # Guarantee all seven contract keys are present.
        for k in CONTRACT_KEYS:
            result.setdefault(k, None if k.startswith("resolved") else "")
        result["backend"] = BACKEND_NAME
        result["resolved_pmid"] = None

        if not include_record:
            result.pop("record", None)
        return result

    # -- metadata mapping (scope §13.5) ---------------------------------
    @staticmethod
    def metadata_for_checker(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Map a GuidelineRecord dict -> the Checker's guideline metadata dict.

        Provides the underlying values the Checker normalises for its
        relevance/evidence-grade stages (scope §13.5). For context only; MGI does
        not construct ``CitedDocument``.
        """
        if not record:
            return {}
        is_current = (record.get("status") or "active") == "active"
        return {
            "title": record.get("title"),
            "year": record.get("year"),
            "doi": record.get("doi"),
            "publisher": record.get("issuer"),       # issuer name is fine here
            "publication_types": ["Practice Guideline"],
            "status": record.get("status"),
            "superseded_by": record.get("superseded_by"),
            "is_current": is_current,                  # feeds the Currency signal
            "country": record.get("country"),
        }


def resolve_citation(citation: str, *, db_path: str = "mgi.db",
                     min_confidence: float = 0.45,
                     searcher: Optional[Any] = None) -> Dict[str, Any]:
    """One-shot convenience: build a backend and resolve a single citation."""
    backend = GuidelineIndexBackend(db_path=db_path, min_confidence=min_confidence,
                                    searcher=searcher)
    return backend.resolve(citation)
