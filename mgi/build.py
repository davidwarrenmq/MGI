"""build_index(): (re)build the MGI store from seed records and/or a crawl.

Phase 1 wires the SEED path (bundled ``seed_guidelines.jsonl``). The crawler
(Phase 2) plugs in via the ``crawl`` argument without changing this signature.

Importing this module does not require any optional dependency until
``build_index`` is actually called (the store import is local), so ``import mgi``
stays light (system prompt §6).
"""
from __future__ import annotations

import json
import sys
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
        ``{"db_path", "added", "total", "sources", "diagnostics"}``.
    """
    from .store import Store  # local import: optional sqlite3 dependency

    path = Path(db_path)
    if reset and path.exists():
        path.unlink()

    sources: List[str] = []
    records: List[GuidelineRecord] = []
    global_diagnostics: dict = {}

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
        crawled = _run_crawl(issuers, global_diagnostics)
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
        "diagnostics": global_diagnostics
    }


def _render_progress_bar(current: int, total: int, prefix: str = "", bar_length: int = 30) -> None:
    """Standard-library safe carriage-return text progress bar renderer."""
    percent = float(current) * 100 / total
    arrow = "-" * int(percent / 100 * bar_length - 1) + ">"
    spaces = " " * (bar_length - len(arrow))
    
    # \r resets the cursor position to rewrite the line cleanly
    sys.stdout.write(f"\r{prefix} [{arrow}{spaces}] {percent:.1f}% ({current}/{total})")
    sys.stdout.flush()


def _run_crawl(issuers: Optional[List[str]], global_diagnostics: dict) -> List[GuidelineRecord]:
    """Run the Phase 2 crawler with a live real-time progress indicator bar."""
    try:
        from .crawl.strategies import crawl_issuer, STRATEGIES
        from .crawl.base import PoliteCrawler
        from .registry import load_registry
    except Exception as exc:
        global_diagnostics["critical_error"] = f"Failed to import crawler elements: {exc}"
        return []

    # Identify targets based on CLI input filters or strategies dictionary keys
    if issuers:
        targets = [a.upper() for a in issuers]
    else:
        targets = list(STRATEGIES.keys())

    global_diagnostics["issuers_tracked"] = {}
    out: List[GuidelineRecord] = []
    
    total_targets = len(targets)
    if total_targets == 0:
        return []

    print(f"\nInitializing Polite Crawl Loop across {total_targets} organizations...")
    crawler = PoliteCrawler()
    registry = load_registry()

    # Initial empty bar baseline state
    _render_progress_bar(0, total_targets, prefix="Crawling Pipeline Progress:")

    for idx, abbrev in enumerate(targets, start=1):
        issuer_diag = {}
        try:
            records = crawl_issuer(abbrev, crawler=crawler, registry=registry, diagnostics=issuer_diag)
            out.extend(records)
        except Exception as exc:
            issuer_diag["unhandled_loop_exception"] = str(exc)
            
        global_diagnostics["issuers_tracked"][abbrev] = issuer_diag
        
        # Update progress bar status layout with active organization context info
        _render_progress_bar(
            idx, total_targets, 
            prefix=f"Crawling Pipeline Progress (Processing {abbrev:8}):"
        )

    # Clear trailing carriage characters on complete execution line output
    print("\nCrawl loop execution finalized successfully.\n")
    return out


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
    ap.add_argument("--verbose-diag", action="store_true", help="Print full JSON diagnostic dump")
    args = ap.parse_args(argv)

    summary = build_index(
        args.db, seed=not args.no_seed, reset=args.reset,
        crawl=args.crawl, issuers=args.issuers,
    )
    
    print(
        f"Built {summary['db_path']}: added={summary['added']} "
        f"total={summary['total']} sources={summary['sources']}"
    )

    if args.crawl and summary.get("diagnostics"):
        print("\n" + "="*50 + "\nCRAWLER DIAGNOSTIC METRICS REPORT\n" + "="*50)
        diag_data = summary["diagnostics"].get("issuers_tracked", {})
        
        for abbrev, logs in diag_data.items():
            discovered = logs.get("documents", {}).get("total_discovered", 0)
            parsed = logs.get("documents", {}).get("successfully_parsed", 0)
            failed_fetch = len(logs.get("documents", {}).get("failed_fetches", []))
            
            robot_blocked = any(
                s.get("robot_check_failed") for s in logs.get("sitemaps", {}).values()
            ) or any(
                l.get("robot_check_failed") for l in logs.get("listings", {}).values()
            )

            status_flag = "🟢 SUCCESS" if parsed > 0 else "🔴 ZERO YIELD"
            if robot_blocked:
                status_flag = "🛑 ROBOTS.TXT DISALLOWED"

            print(f"[{abbrev}] {status_flag}")
            print(f"  - Discovered Links : {discovered}")
            print(f"  - Records Indexed  : {parsed}")
            if failed_fetch > 0:
                print(f"  - Network Failures : {failed_fetch}")
            print("-" * 30)

        if args.verbose_diag:
            print("\nVERBOSE DIAGNOSTIC JSON DUMP:")
            print(json.dumps(summary["diagnostics"], indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
