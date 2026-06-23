"""SQLite + FTS5 store for GuidelineRecords (scope §6 / §177-183).

* Structured table ``guidelines`` holds the typed columns.
* FTS5 virtual table ``guidelines_fts`` indexes ``title, issuer, topics,
  identifier`` for fast lexical recall (scope §7 step 2).
* Ships as a single ``mgi.db`` file that can be committed or rebuilt from the
  crawl on demand.

Stdlib only (``sqlite3``). If the SQLite build lacks FTS5, the store falls back
to a plain table + LIKE search so the project still works everywhere; callers
can check :pyattr:`Store.fts_enabled`.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .guideline_record import GuidelineRecord

# Columns persisted in the structured table, in order.
COLUMNS = [
    "id", "title", "issuer", "issuer_abbrev", "country", "tier",
    "year", "version", "doc_type", "url", "doi", "identifier",
    "topics", "superseded_by", "status", "source_crawl_ts",
    "raw_meta", "notes", "schema_version",
]
# Columns stored as JSON text.
_JSON_COLS = {"topics", "raw_meta", "notes"}


def _fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


class Store:
    """Lightweight SQLite-backed index of GuidelineRecords."""

    def __init__(self, path: "str | Path" = "mgi.db") -> None:
        self.path = str(path)
        self.conn = self._connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.fts_enabled = _fts5_available(self.conn)
        self._create_schema()

    @staticmethod
    def _connect(path: str) -> "sqlite3.Connection":
        """Connect, transparently rebuilding a malformed on-disk DB file.

        A previously corrupted ``mgi.db`` (e.g. from an older schema) would
        otherwise raise ``sqlite3.DatabaseError: database disk image is
        malformed``. We detect that via a quick integrity check and, for a real
        file, recreate it from scratch (the index always rebuilds from the
        seed/crawl, so discarding a corrupt cache is safe).
        """
        conn = sqlite3.connect(path, check_same_thread=False)
        if path == ":memory:":
            return conn
        try:
            conn.execute("PRAGMA quick_check").fetchone()
            return conn
        except sqlite3.DatabaseError:
            conn.close()
            try:
                from pathlib import Path as _P
                _P(path).unlink(missing_ok=True)
            except OSError:
                pass
            return sqlite3.connect(path, check_same_thread=False)

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.conn.commit()
        self.close()

    # -- schema ----------------------------------------------------------
    def _create_schema(self) -> None:
        cols_sql = (
            "id TEXT PRIMARY KEY, title TEXT, issuer TEXT, issuer_abbrev TEXT, "
            "country TEXT NOT NULL, tier TEXT, year INTEGER, version TEXT, "
            "doc_type TEXT, url TEXT, doi TEXT, identifier TEXT, topics TEXT, "
            "superseded_by TEXT, status TEXT, source_crawl_ts INTEGER, "
            "raw_meta TEXT, notes TEXT, schema_version TEXT"
        )
        self.conn.execute(f"CREATE TABLE IF NOT EXISTS guidelines ({cols_sql})")
        if self.fts_enabled:
            # Standalone (NON external-content) FTS5 table keyed by the
            # record's stable text id. We deliberately avoid external-content /
            # content_rowid coupling: with a TEXT PRIMARY KEY the implicit rowid
            # is not stable across rebuilds, and manual writes to an
            # external-content table can corrupt the index ("database disk image
            # is malformed"). mgi_id is UNINDEXED so it is stored but not tokenised.
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS guidelines_fts USING fts5("
                "mgi_id UNINDEXED, title, issuer, topics, identifier)"
            )
        self.conn.commit()

    # -- serialisation helpers ------------------------------------------
    @staticmethod
    def _record_to_row(rec: GuidelineRecord) -> Dict[str, Any]:
        d = rec.to_dict()
        row: Dict[str, Any] = {}
        for c in COLUMNS:
            v = d.get(c)
            if c in _JSON_COLS:
                row[c] = json.dumps(v if v is not None else ([] if c != "raw_meta" else {}))
            else:
                row[c] = v
        if not row.get("country"):
            row["country"] = "international"   # NOT NULL invariant (scope §10)
        return row

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> GuidelineRecord:
        d: Dict[str, Any] = {}
        for c in COLUMNS:
            v = row[c]
            if c in _JSON_COLS and isinstance(v, str):
                try:
                    v = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    v = [] if c != "raw_meta" else {}
            d[c] = v
        return GuidelineRecord.from_dict(d)

    # -- writes ----------------------------------------------------------
    def upsert(self, rec: GuidelineRecord) -> None:
        row = self._record_to_row(rec)
        placeholders = ", ".join("?" for _ in COLUMNS)
        updates = ", ".join(f"{c}=excluded.{c}" for c in COLUMNS if c != "id")
        sql = (
            f"INSERT INTO guidelines ({', '.join(COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        self.conn.execute(sql, [row[c] for c in COLUMNS])
        if self.fts_enabled:
            try:
                self._reindex_fts(row["id"])
            except sqlite3.DatabaseError:
                # Never let FTS sync corrupt the primary write; degrade to LIKE.
                self.fts_enabled = False
        self.conn.commit()

    def upsert_many(self, recs: Iterable[GuidelineRecord]) -> int:
        n = 0
        for r in recs:
            self.upsert(r)
            n += 1
        return n

    def _reindex_fts(self, rec_id: str) -> None:
        cur = self.conn.execute("SELECT id, title, issuer, topics, identifier "
                                "FROM guidelines WHERE id=?", (rec_id,))
        r = cur.fetchone()
        if not r:
            return
        # Keep the FTS row in sync by stable text id (idempotent upsert).
        self.conn.execute("DELETE FROM guidelines_fts WHERE mgi_id=?", (rec_id,))
        self.conn.execute(
            "INSERT INTO guidelines_fts(mgi_id, title, issuer, topics, identifier) "
            "VALUES (?, ?, ?, ?, ?)",
            (r["id"], r["title"] or "", r["issuer"] or "",
             r["topics"] or "", r["identifier"] or ""),
        )

    # -- reads -----------------------------------------------------------
    def get(self, rec_id: str) -> Optional[GuidelineRecord]:
        cur = self.conn.execute("SELECT * FROM guidelines WHERE id=?", (rec_id,))
        row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM guidelines").fetchone()[0])

    def all_records(self) -> List[GuidelineRecord]:
        cur = self.conn.execute("SELECT * FROM guidelines")
        return [self._row_to_record(r) for r in cur.fetchall()]

    @staticmethod
    def _fts_query(text: str) -> str:
        # Build a safe OR query of quoted tokens for FTS5.
        toks = [t for t in "".join(c if c.isalnum() else " " for c in text).split() if t]
        return " OR ".join(f'"{t}"' for t in toks)

    def search(self, text: str, limit: int = 25) -> List[GuidelineRecord]:
        """Lexical recall over title/issuer/topics/identifier (scope §7)."""
        text = (text or "").strip()
        if not text:
            return []
        if self.fts_enabled:
            q = self._fts_query(text)
            if not q:
                return []
            try:
                cur = self.conn.execute(
                    "SELECT g.* FROM guidelines_fts f "
                    "JOIN guidelines g ON g.id = f.mgi_id "
                    "WHERE f MATCH ? "
                    "ORDER BY rank LIMIT ?", (q, limit),
                )
                rows = cur.fetchall()
                if rows:
                    return [self._row_to_record(r) for r in rows]
            except sqlite3.DatabaseError:
                # FTS unusable on this platform/index -> fall back to LIKE.
                self.fts_enabled = False
        # Fallback: LIKE over the indexed columns.
        like = f"%{text}%"
        cur = self.conn.execute(
            "SELECT * FROM guidelines WHERE title LIKE ? OR issuer LIKE ? "
            "OR topics LIKE ? OR identifier LIKE ? LIMIT ?",
            (like, like, like, like, limit),
        )
        return [self._row_to_record(r) for r in cur.fetchall()]


def open_store(path: "str | Path" = "mgi.db") -> Store:
    """Convenience opener mirroring the Citation Checker's factory style."""
    return Store(path)
