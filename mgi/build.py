"""build_index(): (re)build the MGI store from seed records and/or a crawl.

Phase 1 wires the SEED path (bundled ``seed_guidelines.jsonl``). The crawler
(Phase 2) plugs in via the ``crawl`` argument without changing this signature.

Importing this module does not require any optional dependency until
``build_index`` is actually called (the store import is local), so ``import mgi``
stays light (system prompt §6).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, List, Optional

from .guideline_record import GuidelineRecord

DEFAULT_DB = "mgi.db"
SEED_FILE = Path(__file__).with_name("seed_guidelines.jsonl")


def load_seed_records(seed_path: "str | Path | None" = None) -> List[GuidelineRecord]:
    """Load the bundled (or supplied) seed guideline records."""
    p = Path(seed_path) if seed_path else SEED_FILE
    if not p.exists():
        return []
    return GuidelineRecord.load_jsonl(p)


def build_index(
    db_path: "str | Path" = DEFAULT_DB,
    *,
    seed: bool = True,
    seed_path: "str | Path | None" = None,
    extra_records: Optional[Iterable[GuidelineRecord]] = None,
    reset: bool = False,
    crawl: bool = False,
    issuers: Optional[List[str]] = None,
) -> dict:
    """Build / refresh the SQLite+FTS5 index and return a small summary.

    Args:
        db_path: target ``mgi.db`` path.
        seed: load the bundled seed records (default True).
        seed_path: override the seed file location.
        extra_records: additional GuidelineRecords to upsert.
        reset: delete any existing DB file first (full rebuild, scope §10).
        crawl: if True, run the polite crawler (Phase 2) for ``issuers``.
        issuers: optional list of issuer abbrevs to crawl (None = all in registry).

    Returns:
        ``{"db_path", "added", "total", "sources"}``.
    """
    from .store import Store  # local import: optional sqlite3 dependency

    path = Path(db_path)
    if reset and path.exists():
        path.unlink()

    sources: List[str] = []
    records: List[GuidelineRecord] = []

    if seed:
        seed_recs = load_seed_records(seed_path)
        records.extend(seed_recs)
        if seed_recs:
            sources.append(f"seed:{len(seed_recs)}")

    if extra_records:
        extra = list(extra_records)
        records.extend(extra)
        sources.append(f"extra:{len(extra)}")

    if crawl:
        crawled = _run_crawl(issuers)
        records.extend(crawled)
        sources.append(f"crawl:{len(crawled)}")

    with Store(path) as store:
        added = store.upsert_many(records)
        total = store.count()

    return {
        "db_path": str(path),
        "added": added,
        "total": total,
        "sources": sources,
    }


def _run_crawl(issuers: Optional[List[str]]) -> List[GuidelineRecord]:
    """Run the Phase 2 crawler. Imported lazily; returns [] if unavailable."""
    try:
        from .crawl.strategies import crawl_issuers
    except Exception:
        return []
    try:
        return list(crawl_issuers(issuers))
    except Exception:
        return []


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point used by scripts/build_index.py and the console script."""
    import argparse

    ap = argparse.ArgumentParser(description="Build the Medical Guideline Index store.")
    ap.add_argument("--db", default=DEFAULT_DB, help="output DB path (default mgi.db)")
    ap.add_argument("--no-seed", action="store_true", help="skip bundled seed records")
    ap.add_argument("--reset", action="store_true", help="delete existing DB first")
    ap.add_argument("--crawl", action="store_true", help="run the polite crawler (Phase 2)")
    ap.add_argument("--issuer", action="append", dest="issuers",
                    help="limit crawl to issuer abbrev (repeatable)")
    args = ap.parse_args(argv)

    summary = build_index(
        args.db, seed=not args.no_seed, reset=args.reset,
        crawl=args.crawl, issuers=args.issuers,
    )
    print(
        f"Built {summary['db_path']}: added={summary['added']} "
        f"total={summary['total']} sources={summary['sources']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
