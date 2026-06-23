"""Medical Guideline Index (MGI).

A standalone, importable library that indexes reputable medical guideline
documents and resolves guideline citations to a DIRECT document link.

Primary entry points (scope §10):

* :func:`resolve` -- citation string -> 7-key resolver dict (scope §13.3).
* :func:`build_index` -- (re)build the SQLite/FTS5 store from the crawl/seed.
* :class:`GuidelineRecord` -- the per-document data model (scope §6).

Works both as a library (``from mgi import resolve``) and behind the FastAPI app
(``mgi.service:app``). Heavier/optional deps (``sqlite3`` store, crawler, app,
pandas) are imported LAZILY so the core import never fails in a minimal
environment (system prompt §6).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .guideline_record import GuidelineRecord, SCHEMA_VERSION, canonical_id
from .registry import Issuer, Registry, load_registry

if TYPE_CHECKING:  # for type-checkers only; not imported at runtime
    from .store import Store, open_store  # noqa: F401

__all__ = [
    "GuidelineRecord", "SCHEMA_VERSION", "canonical_id",
    "Issuer", "Registry", "load_registry",
    "Store", "open_store", "resolve", "build_index",
    "GuidelineIndexBackend", "resolve_citation",
]

__version__ = "0.1.0"

# Lazily-exported names -> (module, attribute). Imported on first access so a
# missing optional dependency (e.g. sqlite3) never breaks ``import mgi``.
_LAZY = {
    "Store": (".store", "Store"),
    "open_store": (".store", "open_store"),
    "GuidelineIndexBackend": (".integration", "GuidelineIndexBackend"),
    "resolve_citation": (".integration", "resolve_citation"),
}


def __getattr__(name: str) -> Any:  # PEP 562 module-level lazy attributes
    if name in _LAZY:
        import importlib
        mod_name, attr = _LAZY[name]
        mod = importlib.import_module(mod_name, __name__)
        return getattr(mod, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def resolve(citation: str, *, min_confidence: float = 0.45) -> dict:
    """Resolve a guideline citation to the §13.3 dict. Never raises.

    Thin re-export of :func:`mgi.resolve.resolve` (defined in Phase 1).
    """
    from .resolve import resolve as _resolve
    return _resolve(citation, min_confidence=min_confidence)


def build_index(*args: Any, **kwargs: Any) -> Any:
    """(Re)build the MGI store. Re-export of :func:`mgi.build.build_index`."""
    from .build import build_index as _build
    return _build(*args, **kwargs)
