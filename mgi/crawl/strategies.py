"""Per-issuer crawl strategies (registry-driven) -> GuidelineRecords (scope §8/§9).

Each strategy declares, for one issuer abbrev:

* ``listing_urls`` -- index/sitemap pages to discover document links from,
* ``doc_pattern``  -- regex selecting *document* links (not nav/marketing),
* ``build`` (optional) -- override to refine title/identifier/topics.

The registry (``issuers.yaml``) remains the single source of truth for issuer
identity (name/country/tier/base_url); strategies add only the parse rules, so
adding an issuer is data + one small strategy entry (scope §5, §8).

This module performs NO network on import. ``crawl_issuers`` accepts an injected
crawler/store for testability, and degrades gracefully when networking or an
issuer page is unavailable (system prompt §7).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..guideline_record import GuidelineRecord
from ..registry import Issuer, Registry, load_registry
from .base import PoliteCrawler
from .sitemap import parse_sitemap, extract_links, extract_page_meta


@dataclass
class Strategy:
    """Parse rules for one issuer (keyed by abbrev)."""

    abbrev: str
    listing_urls: List[str] = field(default_factory=list)
    sitemap_urls: List[str] = field(default_factory=list)
    doc_pattern: str = ""                      # regex: which links are documents
    identifier_pattern: str = ""               # regex: extract issuer code from URL
    default_doc_type: str = "guideline"
    topics: List[str] = field(default_factory=list)
    max_docs: int = 200


# --- the seed set of strategies (NICE, WHO, NHMRC, CDC) -----------------
STRATEGIES: Dict[str, Strategy] = {
    "NICE": Strategy(
        abbrev="NICE",
        sitemap_urls=["https://www.nice.org.uk/sitemap.xml"],
        listing_urls=["https://www.nice.org.uk/guidance/published?type=ng,cg"],
        doc_pattern=r"nice\.org\.uk/guidance/(ng|cg|ta)\d+",
        identifier_pattern=r"/guidance/((?:NG|CG|TA)\d+)",
    ),
    "WHO": Strategy(
        abbrev="WHO",
        sitemap_urls=["https://www.who.int/sitemap.xml"],
        listing_urls=["https://www.who.int/publications/who-guidelines"],
        doc_pattern=r"who\.int/publications/i/item/",
    ),
    "NHMRC": Strategy(
        abbrev="NHMRC",
        listing_urls=["https://www.nhmrc.gov.au/health-advice/all-guidelines"],
        doc_pattern=r"nhmrc\.gov\.au/.+guidelines?",
    ),
    "CDC": Strategy(
        abbrev="CDC",
        listing_urls=["https://www.cdc.gov/mmwr/rr_archives.html"],
        doc_pattern=r"cdc\.gov/(mmwr|.+/guidelines?)",
    ),

    # --- remaining A-tier issuers ---------------------------------------
    "USPSTF": Strategy(
        abbrev="USPSTF",
        listing_urls=[
            "https://www.uspreventiveservicestaskforce.org/uspstf/topic_search_results?topic_status=P",
        ],
        doc_pattern=r"uspreventiveservicestaskforce\.org/uspstf/recommendation/",
    ),
    "NCCN": Strategy(
        abbrev="NCCN",
        listing_urls=["https://www.nccn.org/guidelines/category_1"],
        doc_pattern=r"nccn\.org/guidelines/guidelines-detail",
        identifier_pattern=r"[?&]id=(\d+)",
    ),
    "Cochrane": Strategy(
        abbrev="Cochrane",
        listing_urls=["https://www.cochranelibrary.com/cdsr/reviews/topics"],
        doc_pattern=r"cochranelibrary\.com/cdsr/doi/10\.1002/",
        identifier_pattern=r"(10\.1002/14651858\.[A-Za-z0-9.]+)",
    ),
    "SIGN": Strategy(
        abbrev="SIGN",
        sitemap_urls=["https://www.sign.ac.uk/sitemap.xml"],
        listing_urls=["https://www.sign.ac.uk/our-guidelines/"],
        doc_pattern=r"sign\.ac\.uk/our-guidelines/[a-z0-9-]+/?$",
    ),
    "eTG": Strategy(
        abbrev="eTG",
        listing_urls=["https://www.tg.org.au/the-guidelines/"],
        doc_pattern=r"tg\.org\.au/(the-guidelines|.+guidelines?)",
    ),
    "ESC": Strategy(
        abbrev="ESC",
        listing_urls=[
            "https://www.escardio.org/Guidelines/Clinical-Practice-Guidelines",
        ],
        doc_pattern=r"escardio\.org/Guidelines/Clinical-Practice-Guidelines/",
    ),
    "AHA/ACC": Strategy(
        abbrev="AHA/ACC",
        listing_urls=[
            "https://www.acc.org/Guidelines",
            "https://professional.heart.org/en/guidelines-and-statements",
        ],
        doc_pattern=r"(acc\.org/guidelines/|ahajournals\.org/doi/10\.1161/)",
        identifier_pattern=r"(10\.1161/[A-Za-z0-9.]+)",
    ),
    "ACP": Strategy(
        abbrev="ACP",
        listing_urls=["https://www.acponline.org/clinical-information/guidelines"],
        doc_pattern=r"(acponline\.org/clinical-information/guidelines/|acpjournals\.org/doi/10\.7326/)",
        identifier_pattern=r"(10\.7326/[A-Za-z0-9.-]+)",
    ),
    "IDSA": Strategy(
        abbrev="IDSA",
        listing_urls=["https://www.idsociety.org/practice-guideline/practice-guidelines/"],
        doc_pattern=r"idsociety\.org/practice-guideline/[a-z0-9-]+/?$",
    ),
    "CTFPHC": Strategy(
        abbrev="CTFPHC",
        listing_urls=["https://canadiantaskforce.ca/guidelines/published-guidelines/"],
        doc_pattern=r"canadiantaskforce\.ca/guidelines/published-guidelines/[a-z0-9-]+",
    ),
    "GIN": Strategy(
        abbrev="GIN",
        listing_urls=["https://g-i-n.net/international-guidelines-library/"],
        doc_pattern=r"g-i-n\.net/.+(guideline|library)",
    ),
}


def get_strategy(abbrev: str) -> Optional[Strategy]:
    return STRATEGIES.get((abbrev or "").upper())


def _identifier_from_url(url: str, strat: Strategy) -> Optional[str]:
    if not strat.identifier_pattern:
        return None
    m = re.search(strat.identifier_pattern, url, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _build_record(url: str, meta: dict, issuer: Issuer, strat: Strategy,
                  crawl_ts: int) -> GuidelineRecord:
    """Assemble a GuidelineRecord from a fetched document page (scope §13.5)."""
    title = (meta.get("title") or "").strip()
    year = None
    if meta.get("year"):
        try:
            year = int(str(meta["year"])[:4])
        except (TypeError, ValueError):
            year = None
    return GuidelineRecord(
        title=title,
        issuer=issuer.name,
        issuer_abbrev=issuer.abbrev,
        country=issuer.country or "international",
        tier=issuer.tier,
        url=url,
        year=year,
        doc_type=strat.default_doc_type,
        doi=meta.get("doi"),
        identifier=_identifier_from_url(url, strat),
        topics=list(strat.topics),
        status="active",
        source_crawl_ts=crawl_ts,
        raw_meta={"discovered_via": "crawl"},
    )


def discover_doc_urls(strat: Strategy, crawler: PoliteCrawler) -> List[str]:
    """Find candidate document URLs for an issuer (sitemap first, then listings)."""
    found: List[str] = []
    seen = set()
    rx = re.compile(strat.doc_pattern, re.IGNORECASE) if strat.doc_pattern else None

    def keep(u: str) -> None:
        if u and u not in seen and (rx is None or rx.search(u)):
            seen.add(u)
            found.append(u)

    for sm_url in strat.sitemap_urls:
        res = crawler.fetch(sm_url)
        if res.ok:
            for loc in parse_sitemap(res.text):
                keep(loc)
        if len(found) >= strat.max_docs:
            return found[: strat.max_docs]

    for listing in strat.listing_urls:
        res = crawler.fetch(listing)
        if res.ok:
            for link in extract_links(res.text, base_url=listing,
                                      pattern=strat.doc_pattern):
                keep(link["href"])
        if len(found) >= strat.max_docs:
            break
    return found[: strat.max_docs]


def crawl_issuer(abbrev: str, *, crawler: Optional[PoliteCrawler] = None,
                 registry: Optional[Registry] = None,
                 crawl_ts: Optional[int] = None) -> List[GuidelineRecord]:
    """Crawl one issuer into GuidelineRecords. Never raises; returns [] on trouble."""
    import time

    registry = registry or load_registry()
    strat = get_strategy(abbrev)
    issuer = registry.by_abbrev(abbrev)
    if strat is None or issuer is None:
        return []
    crawler = crawler or PoliteCrawler()
    ts = crawl_ts if crawl_ts is not None else int(time.time())

    records: List[GuidelineRecord] = []
    try:
        urls = discover_doc_urls(strat, crawler)
    except Exception:
        return []
    for url in urls:
        try:
            res = crawler.fetch(url)
            if not res.ok:
                continue
            meta = extract_page_meta(res.text)
            if not meta.get("title"):
                continue
            records.append(_build_record(url, meta, issuer, strat, ts))
        except Exception:
            continue
    return records


def crawl_issuers(abbrevs: Optional[List[str]] = None, *,
                  crawler: Optional[PoliteCrawler] = None,
                  registry: Optional[Registry] = None) -> List[GuidelineRecord]:
    """Crawl several issuers (default: all that have a Strategy)."""
    registry = registry or load_registry()
    crawler = crawler or PoliteCrawler()
    if abbrevs:
        targets = [a.upper() for a in abbrevs]
    else:
        targets = list(STRATEGIES.keys())
    out: List[GuidelineRecord] = []
    for ab in targets:
        out.extend(crawl_issuer(ab, crawler=crawler, registry=registry))
    return out
