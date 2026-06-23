"""Phase 0 tests: issuer registry + trigger detection (scope §4 / §13.4)."""
from mgi.registry import load_registry, Issuer, Registry


def test_loads_all_issuers():
    reg = load_registry()
    # scope §4 lists 15 A-tier + 14 B-tier = 29 issuers
    assert len(reg.issuers) == 29
    tiers = {}
    for i in reg.issuers:
        tiers[i.tier] = tiers.get(i.tier, 0) + 1
    assert tiers.get("A") == 15
    assert tiers.get("B") == 14


def test_every_issuer_has_country_and_base_url():
    reg = load_registry()
    for i in reg.issuers:
        assert i.country, i.abbrev
        assert i.tier in ("A", "B")
        assert i.base_url.startswith("http")
        assert i.robots_respect is True


def test_lookup_by_abbrev_is_case_insensitive():
    reg = load_registry()
    nice = reg.by_abbrev("nice")
    assert nice is not None
    assert nice.country == "GB"
    assert "National Institute" in nice.name


def test_trigger_terms_present():
    reg = load_registry()
    low = [t.lower() for t in reg.triggers]
    for term in ("guideline", "clinical practice guideline", "position statement",
                 "consensus", "cochrane"):
        assert term in low


def test_match_triggers_detects_guideline_citation():
    reg = load_registry()
    assert reg.looks_like_guideline("NICE clinical practice guideline NG28")
    hits = reg.match_triggers("WHO guidelines on hypertension")
    assert "WHO" in hits
    assert any("guideline" in h.lower() for h in hits)


def test_non_guideline_citation_has_no_triggers():
    reg = load_registry()
    assert reg.match_triggers("Smith J, et al. A randomized trial. NEJM 2020.") == []


def test_abbrev_expansions_available():
    reg = load_registry()
    assert reg.abbrev_expansions.get("NICE", "").startswith("National Institute")
    assert reg.abbrev_expansions.get("WHO") == "World Health Organization"


def test_issuer_from_dict_coerces_none_lists():
    iss = Issuer.from_dict({"name": "X", "abbrev": "X", "index_pages": None,
                            "junk_field": 1})
    assert iss.index_pages == []
    assert iss.aliases == []
