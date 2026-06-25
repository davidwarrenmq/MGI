"""Per-issuer crawl strategies (registry-driven) -> GuidelineRecords (scope §8/§9).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

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
    doc_pattern: str = ""                      
    identifier_pattern: str = ""               
    default_doc_type: str = "guideline"
    topics: List[str] = field(default_factory=list)
    max_docs: int = 200


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
    "CDC": Strategy(
        abbrev="CDC",
        listing_urls=["https://www.cdc.gov/mmwr/rr_archives.html"],
        # Captures both old relative preview slugs and modernized server-relative volume structures
        doc_pattern=r"(/mmwr/preview/mmwrhtml/rr|/mmwr/volumes/\d+/rr/|mmwr.+guideline)",
    ),
    "SIGN": Strategy(
        abbrev="SIGN",
        sitemap_urls=["https://www.sign.ac.uk/sitemap.xml"],
        listing_urls=["https://www.sign.ac.uk/our-guidelines/"],
        # Broadened to capture direct static download keys and lowercase/uppercase folder routes
        doc_pattern=r"sign\.ac\.uk/(our-guidelines|assets)/[A-Za-z0-9-]+",
    ),
    "Cochrane": Strategy(
        abbrev="Cochrane",
        # Pivot to using their clean review feed aggregator for high static volume output
        listing_urls=["https://www.cochranelibrary.com/api/rss/reviews/en/CRG-HEART.xml"],
        doc_pattern=r"cochranelibrary\.com/cdsr/doi/",
        identifier_pattern=r"doi/(10\.1002/[A-Za-z0-9.]+)",
    ),
    "NHMRC": Strategy(
        abbrev="NHMRC",
        listing_urls=["https://www.nhmrc.gov.au/health-advice/all-guidelines"],
        # Accommodates paths tracking dynamic redirect items or deep PDF attachments
        doc_pattern=r"nhmrc\.gov\.au/(about-us/publications|health-advice│file)/.+",
    ),
    "AHA/ACC": Strategy(
        abbrev="AHA/ACC",
        listing_urls=[
            "https://www.acc.org/Guidelines",
            "https://professional.heart.org/en/guidelines-and-statements",
        ],
        # Expands search pattern to catch absolute and relative link components across both bodies
        doc_pattern=r"(/guidelines/|ahajournals\.org/doi/|jacc\.org/doi/)",
        identifier_pattern=r"doi/(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)",
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
    "ACP": Strategy(
        abbrev="ACP",
        listing_urls=["https://www.acponline.org/clinical-information/guidelines"],
        doc_pattern=r"(acponline\.org/clinical-information/guidelines/|acpjournals\.org/doi/10\.7326/)",
        identifier_pattern=r"/doi/(10\.7326/[A-Za-z0-9.-]+)",
    ),
    "IDSA": Strategy(
        abbrev="IDSA",
        listing_urls=["https://www.idsociety.org/practice-guideline/practice-guidelines/"],
        doc_pattern=r"idsociety\.org/(practice-guideline|academic/idsa)/[a-z0-9-]+",
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


def discover_doc_urls(strat: Strategy, crawler: PoliteCrawler, 
                      diagnostics: Optional[Dict[str, Any]] = None) -> List[str]:
    """Find candidate document URLs for an issuer (sitemap index expansion first, then listings)."""
    found: List[str] = []
    seen = set()
    rx = re.compile(strat.doc_pattern, re.IGNORECASE) if strat.doc_pattern else None

    def keep(u: str) -> None:
        if u and u not in seen and (rx is None or rx.search(u)):
            seen.add(u)
            found.append(u)

    # Change 1: Queue processing tracking for deep recursive sitemap loops
    sitemap_queue = list(strat.sitemap_urls)
    processed_sitemaps = set()

    while sitemap_queue and len(found) < strat.max_docs:
        sm_url = sitemap_queue.pop(0)
        if sm_url in processed_sitemaps:
            continue
        processed_sitemaps.add(sm_url)

        res = crawler.fetch(sm_url)
        sm_diag = {
            "status_code": res.status,
            "found": res.ok,
            "error": res.error,
            "robot_check_failed": (res.error == "disallowed by robots.txt"),
            "is_index_file": False,
            "urls_extracted": 0
        }
        
        if res.ok:
            is_index = "<sitemapindex" in res.text or ("<sitemap" in res.text and not "<url" in res.text)
            sm_diag["is_index_file"] = is_index
            extracted_urls = parse_sitemap(res.text)
            sm_diag["urls_extracted"] = len(extracted_urls)
            
            if is_index:
                # Enqueue sub-sitemaps for parsing inside next loops
                for sub_url in extracted_urls:
                    if sub_url.endswith(".xml") or "sitemap" in sub_url.lower():
                        sitemap_queue.append(sub_url)
            else:
                # Leaf urlset node; apply regular matching
                before_count = len(found)
                for loc in extracted_urls:
                    keep(loc)
                sm_diag["guidelines_located"] = len(found) - before_count
                
        if diagnostics is not None:
            if "sitemaps" not in diagnostics:
                diagnostics["sitemaps"] = {}
            diagnostics["sitemaps"][sm_url] = sm_diag

    # Fall back to index pages if sitemaps did not satisfy capacity requirements
    if len(found) < strat.max_docs:
        for listing in strat.listing_urls:
            res = crawler.fetch(listing)
            list_diag = {
                "status_code": res.status,
                "found": res.ok,
                "error": res.error,
                "robot_check_failed": (res.error == "disallowed by robots.txt"),
                "links_found": 0
            }
            
            if res.ok:
                extracted_links = extract_links(res.text, base_url=listing, pattern=strat.doc_pattern)
                list_diag["links_found"] = len(extracted_links)
                before_count = len(found)
                for link in extracted_links:
                    keep(link["href"])
                list_diag["guidelines_located"] = len(found) - before_count
                
            if diagnostics is not None:
                if "listings" not in diagnostics:
                    diagnostics["listings"] = {}
                diagnostics["listings"][listing] = list_diag

            if len(found) >= strat.max_docs:
                break
                
    return found[: strat.max_docs]


def crawl_issuer(abbrev: str, *, crawler: Optional[PoliteCrawler] = None,
                 registry: Optional[Registry] = None,
                 crawl_ts: Optional[int] = None,
                 diagnostics: Optional[Dict[str, Any]] = None) -> List[GuidelineRecord]:
    """Crawl one issuer into GuidelineRecords. Never raises; returns [] on trouble."""
    registry = registry or load_registry()
    strat = get_strategy(abbrev)
    issuer = registry.by_abbrev(abbrev)
    
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update({
            "issuer_abbrev": abbrev,
            "strategy_found": strat is not None,
            "issuer_registered": issuer is not None,
            "sitemaps": {},
            "listings": {},
            "documents": {
                "total_discovered": 0,
                "successfully_fetched": 0,
                "successfully_parsed": 0,
                "failed_fetches": [],
                "failed_parses": []
            }
        })

    if strat is None or issuer is None:
        return []
        
    crawler = crawler or PoliteCrawler()

    if issuer is not None:
        crawler.respect_robots = issuer.robots_respect
        
    ts = crawl_ts if crawl_ts is not None else int(time.time())
    records: List[GuidelineRecord] = []
    
    try:
        urls = discover_doc_urls(strat, crawler, diagnostics=diagnostics)
    except Exception as exc:
        if diagnostics is not None:
            diagnostics["discovery_exception"] = f"{type(exc).__name__}: {exc}"
        return []
        
    if diagnostics is not None:
        diagnostics["documents"]["total_discovered"] = len(urls)

    for url in urls:
        try:
            res = crawler.fetch(url)
            if not res.ok:
                if diagnostics is not None:
                    diagnostics["documents"]["failed_fetches"].append({
                        "url": url,
                        "status_code": res.status,
                        "error": res.error,
                        "robot_check_failed": (res.error == "disallowed by robots.txt")
                    })
                continue
                
            if diagnostics is not None:
                diagnostics["documents"]["successfully_fetched"] += 1
                
            # Change 2: Pass down URL trace reference to populate fallbacks for binaries/PDFs
            meta = extract_page_meta(res.text, url=url)
            if not meta.get("title"):
                if diagnostics is not None:
                    diagnostics["documents"]["failed_parses"].append({
                        "url": url,
                        "reason": "Missing or empty HTML title attribute tag"
                    })
                continue
                
            records.append(_build_record(url, meta, issuer, strat, ts))
            if diagnostics is not None:
                diagnostics["documents"]["successfully_parsed"] += 1
                
        except Exception as exc:
            if diagnostics is not None:
                diagnostics["documents"]["failed_parses"].append({
                    "url": url,
                    "reason": f"Unhandled parsing Exception: {type(exc).__name__}: {exc}"
                })
            continue
            
    return records


def crawl_issuers(abbrevs: Optional[List[str]] = None, *,
                  crawler: Optional[PoliteCrawler] = None,
                  registry: Optional[Registry] = None,
                  global_diagnostics: Optional[Dict[str, Any]] = None) -> List[GuidelineRecord]:
    """Crawl several issuers (default: all that have a Strategy)."""
    registry = registry or load_registry()
    crawler = crawler or PoliteCrawler()
    
    if abbrevs:
        targets = [a.upper() for a in abbrevs]
    else:
        targets = list(STRATEGIES.keys())
        
    if global_diagnostics is not None:
        global_diagnostics.clear()
        global_diagnostics["issuers_tracked"] = {}
        
    out: List[GuidelineRecord] = []
    for ab in targets:
        issuer_diag = {}
        records = crawl_issuer(ab, crawler=crawler, registry=registry, diagnostics=issuer_diag)
        out.extend(records)
        
        if global_diagnostics is not None:
            global_diagnostics["issuers_tracked"][ab] = issuer_diag
            
    return out
