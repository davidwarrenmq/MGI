"""FastAPI app exposing /resolve and /search (scope §5, §9 Phase 3).

Run with:  uvicorn mgi.service:app --reload

FastAPI/uvicorn are OPTIONAL (declared under the ``app`` extra in pyproject).
Importing this module never hard-fails: if FastAPI is missing, ``app`` is None
and ``create_app`` raises a clear, actionable error only when actually called
(system prompt §6). The resolver itself has zero web dependencies.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from fastapi import FastAPI, HTTPException, Query
    from pydantic import BaseModel
    _HAVE_FASTAPI = True
except ImportError:  # pragma: no cover - depends on env
    FastAPI = None  # type: ignore
    HTTPException = Exception  # type: ignore
    BaseModel = object  # type: ignore
    def Query(default=None, **k):  # type: ignore
        return default
    _HAVE_FASTAPI = False


_INSTALL_HINT = (
    "FastAPI is not installed. Install the app extra, e.g.\n"
    "    pip install 'medical-guideline-index[app]'\n"
    "or:  pip install fastapi uvicorn"
)


if _HAVE_FASTAPI:

    class ResolveResponse(BaseModel):
        status: str
        confidence: float
        backend: str
        resolved_url: Optional[str] = None
        resolved_doi: Optional[str] = None
        resolved_pmid: Optional[str] = None
        details: str
        record: Optional[Dict[str, Any]] = None

    class SearchHit(BaseModel):
        score: float
        base_similarity: float
        boosts: Dict[str, float]
        record: Dict[str, Any]

    class SearchResponse(BaseModel):
        query: str
        count: int
        hits: List[SearchHit]


def create_app(db_path: str = "mgi.db"):
    """Build the FastAPI app. Raises a clear error if FastAPI is absent."""
    if not _HAVE_FASTAPI:
        raise RuntimeError(_INSTALL_HINT)

    from .resolve import resolve as _resolve
    from .search import Searcher
    from .registry import load_registry

    api = FastAPI(
        title="Medical Guideline Index",
        version="0.1.0",
        description="Resolve guideline citations to a direct, verifiable "
                    "document link. Complements the Citation Checker.",
    )

    # Lazily build a shared Searcher bound to the store (opened on first use).
    state: Dict[str, Any] = {"searcher": None}

    def _searcher() -> Searcher:
        if state["searcher"] is None:
            from .store import Store
            from pathlib import Path
            from .guideline_record import GuidelineRecord
            store = Store(db_path)
            if store.count() == 0:
                seed = Path(__file__).with_name("seed_guidelines.jsonl")
                if seed.exists():
                    store.upsert_many(GuidelineRecord.load_jsonl(seed))
            state["searcher"] = Searcher(store, load_registry())
        return state["searcher"]

    @api.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok", "service": "mgi", "version": "0.1.0"}

    @api.get("/resolve", response_model=ResolveResponse)
    def resolve_endpoint(
        citation: str = Query(..., description="Citation text to resolve"),
        min_confidence: float = Query(0.45, ge=0.0, le=1.0),
    ) -> Dict[str, Any]:
        # resolve() never raises; pass the shared searcher for speed.
        return _resolve(citation, min_confidence=min_confidence,
                        searcher=_searcher())

    @api.get("/search", response_model=SearchResponse)
    def search_endpoint(
        q: str = Query(..., description="Free-text guideline query"),
        limit: int = Query(10, ge=1, le=100),
    ) -> Dict[str, Any]:
        cands = _searcher().search(q, limit=limit)
        hits = [
            {
                "score": round(c.score, 4),
                "base_similarity": round(c.base_similarity, 4),
                "boosts": c.boosts,
                "record": c.record.to_dict(),
            }
            for c in cands
        ]
        return {"query": q, "count": len(hits), "hits": hits}

    return api


# Module-level ASGI app for ``uvicorn mgi.service:app`` (None if FastAPI absent).
app = create_app() if _HAVE_FASTAPI else None
