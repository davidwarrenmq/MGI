"""Phase 0 tests: GuidelineRecord serialisation + robustness (scope §6 / §13.2)."""
import json

from mgi.guideline_record import GuidelineRecord, SCHEMA_VERSION, canonical_id


def _sample():
    return GuidelineRecord(
        title="Type 2 diabetes in adults: management",
        issuer="National Institute for Health and Care Excellence",
        issuer_abbrev="NICE", country="GB", tier="A", year=2022,
        url="https://www.nice.org.uk/guidance/ng28", identifier="NG28",
        topics=["diabetes", "endocrinology"], doc_type="guideline", status="active",
    )


def test_stable_id_is_hash_of_issuer_and_url():
    r = _sample()
    assert r.id == canonical_id("NICE", "https://www.nice.org.uk/guidance/ng28")
    assert len(r.id) == 16


def test_schema_version_present():
    assert _sample().schema_version == SCHEMA_VERSION
    assert "to_dict" and SCHEMA_VERSION


def test_to_dict_from_dict_roundtrip():
    r = _sample()
    d = r.to_dict()
    assert d["country"] == "GB" and d["identifier"] == "NG28"
    r2 = GuidelineRecord.from_dict(d)
    assert r2.id == r.id
    assert r2.topics == ["diabetes", "endocrinology"]
    assert r2.year == 2022


def test_json_roundtrip():
    r = _sample()
    r2 = GuidelineRecord.from_json(r.to_json())
    assert r2.to_dict() == r.to_dict()


def test_from_dict_ignores_unknown_fields_as_note():
    d = _sample().to_dict()
    d["totally_unknown_field"] = "surprise"
    r = GuidelineRecord.from_dict(d)
    # unknown field must NOT raise and must be recorded as a note
    assert any("totally_unknown_field" in n for n in r.notes)


def test_from_dict_coerces_none_lists_to_empty():
    d = _sample().to_dict()
    d["topics"] = None
    r = GuidelineRecord.from_dict(d)
    assert r.topics == []


def test_year_coercion_from_string():
    r = GuidelineRecord(title="x", issuer_abbrev="NICE", url="https://x", year="2019")
    assert r.year == 2019


def test_collections_jsonl_roundtrip(tmp_path):
    recs = [_sample(), GuidelineRecord(title="Other", issuer_abbrev="WHO",
                                       url="https://who.int/x", country="international")]
    p = tmp_path / "recs.jsonl"
    GuidelineRecord.save_jsonl(recs, p)
    loaded = GuidelineRecord.load_jsonl(p)
    assert len(loaded) == 2
    assert {r.issuer_abbrev for r in loaded} == {"NICE", "WHO"}
