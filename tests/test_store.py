"""Phase 0 tests: SQLite + FTS5 store (scope §6).

Skipped automatically where ``sqlite3`` is unavailable (e.g. some minimal
Pyodide builds); the store uses only stdlib sqlite3 + FTS5 and runs normally in
a standard CPython environment.
"""
import importlib.util

import pytest

from mgi.guideline_record import GuidelineRecord

sqlite_missing = importlib.util.find_spec("sqlite3") is None
pytestmark = pytest.mark.skipif(sqlite_missing, reason="sqlite3 not available")


def _records():
    return [
        GuidelineRecord(title="Type 2 diabetes in adults: management",
                        issuer="National Institute for Health and Care Excellence",
                        issuer_abbrev="NICE", country="GB", tier="A", year=2022,
                        url="https://www.nice.org.uk/guidance/ng28",
                        identifier="NG28", topics=["diabetes"]),
        GuidelineRecord(title="Guidelines for the management of arterial hypertension",
                        issuer="European Society of Cardiology", issuer_abbrev="ESC",
                        country="international", tier="A", year=2018,
                        url="https://academic.oup.com/eurheartj/esc-htn-2018",
                        topics=["cardiology", "hypertension"]),
    ]


def test_upsert_and_count(tmp_path):
    from mgi.store import Store
    with Store(tmp_path / "mgi.db") as st:
        st.upsert_many(_records())
        assert st.count() == 2


def test_get_roundtrip_preserves_lists(tmp_path):
    from mgi.store import Store
    recs = _records()
    with Store(tmp_path / "mgi.db") as st:
        st.upsert_many(recs)
        got = st.get(recs[0].id)
        assert got is not None
        assert got.topics == ["diabetes"]
        assert got.identifier == "NG28"


def test_country_never_null(tmp_path):
    from mgi.store import Store
    r = GuidelineRecord(title="x", issuer_abbrev="NICE", url="https://x")
    r.country = ""  # force empty -> must be coerced on write
    with Store(tmp_path / "mgi.db") as st:
        st.upsert(r)
        row = st.conn.execute("SELECT country FROM guidelines WHERE id=?",
                              (r.id,)).fetchone()
        assert row["country"]  # non-empty


def test_search_finds_by_title(tmp_path):
    from mgi.store import Store
    with Store(tmp_path / "mgi.db") as st:
        st.upsert_many(_records())
        hits = st.search("diabetes")
        assert any(h.identifier == "NG28" for h in hits)


def test_search_by_identifier(tmp_path):
    from mgi.store import Store
    with Store(tmp_path / "mgi.db") as st:
        st.upsert_many(_records())
        hits = st.search("NG28")
        assert any(h.identifier == "NG28" for h in hits)


def test_upsert_is_idempotent(tmp_path):
    from mgi.store import Store
    recs = _records()
    with Store(tmp_path / "mgi.db") as st:
        st.upsert_many(recs)
        st.upsert_many(recs)  # again
        assert st.count() == 2
