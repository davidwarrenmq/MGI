#!/usr/bin/env python3
"""CLI: crawl/seed and populate the MGI SQLite+FTS5 store (scope §5, §9).

Usage:
    python scripts/build_index.py                 # seed-only build -> mgi.db
    python scripts/build_index.py --reset         # full rebuild
    python scripts/build_index.py --crawl         # seed + polite crawl (Phase 2)
    python scripts/build_index.py --crawl --issuer NICE --issuer WHO

This thin wrapper ensures the project root is importable, then delegates to
``mgi.build.main`` so ``mgi.db`` rebuilds end-to-end from one command (scope §10).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mgi.build import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
