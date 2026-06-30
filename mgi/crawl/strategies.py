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
    index_from_listing_meta: bool = False      
    
    # Dynamic Paging Support Parameters
    paging_param: str = ""                     # URL parameter string to inject, e.g., "page"
    stop_pattern: str = ""                     # Regex layout pattern indicating an empty page/no results


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
        # 1. Provide the direct absolute sub-sitemaps bypassing the top shell index completely
        sitemap_urls=[
            "https://www.who.int/SiteMaps/sitemap_static1.xml",
            "https://www.who.int/SiteMaps/sitemap_static2.xml",
            "https://www.who.int/SiteMaps/sitemap_static3.xml",
        ],
        listing_urls=[],
        # 2. Broaden pattern constraint to catch absolute and protocol variations cleanly
        doc_pattern=r"who\.int/publications/i/item/",
        identifier_pattern=r"/publications/i/item/([A-Za-z0-9.-]+)",
        max_docs=200, 
        index_from_listing_meta=False,
    ),
    "NHMRC": Strategy(
        abbrev="NHMRC",
        listing_urls=["https://www.nhmrc.gov.au/health-advice/all-guidelines"],
        doc_pattern=r"nhmrc\.gov\.au/(about-us/publications|health-advice|file)/.+",
    ),
    "CDC": Strategy(
        abbrev="CDC",
        listing_urls=["https://www.cdc.gov/mmwr/rr_archives.html"],
        # Simplified: Removes leading forward slash restrictions to support normalized absolute matching
        doc_pattern=r"mmwr/(preview/mmwrhtml/rr|volumes/\d+/rr/|.+guideline)",
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
    "Cochrane": Strategy(
        abbrev="Cochrane",
        listing_urls=["https://www.cochranelibrary.com/api/rss/reviews/en/CRG-HEART.xml"],
        doc_pattern=r"cochranelibrary\.com/cdsr/doi/",
        identifier_pattern=r"doi/(10\.1002/[A-Za-z0-9.]+)",
    ),
    "SIGN": Strategy(
        abbrev="SIGN",
        sitemap_urls=["https://www.sign.ac.uk/sitemap.xml"],
        listing_urls=["https://www.sign.ac.uk/our-guidelines/"],
        doc_pattern=r"sign\.ac\.uk/(our-guidelines|assets)/[A-Za-z0-9-]+",
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
        doc_pattern=r"escardio\.org/Guidelines/",
        index_from_listing_meta=True, # Collects overview anchors directly to bypass dynamic elements
    ),
    "AHA/ACC": Strategy(
        abbrev="AHA/ACC",
        listing_urls=[
            "https://www.acc.org/Guidelines",
            "https://professional.heart.org/en/guidelines-and-statements",
        ],
        # Removed root front slashes for clean absolute URL searching
        doc_pattern=r"(guidelines/|ahajournals\.org/doi/|jacc\.org/doi/)",
        identifier_pattern=r"doi/(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)",
    ),
    "ACP": Strategy(
        abbrev="ACP",
        listing_urls=["https://www.acponline.org/clinical-information/guidelines"],
        doc_pattern=r"(acponline\.org/clinical-information/guidelines/|acpjournals\.org/doi/10\.7326/)",
        identifier_pattern=r"/doi/(10\.7326/[A-Za-z0-9.-]+)",
        index_from_listing_meta=True,
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

from urllib.parse import urljoin

def discover_doc_urls(strat: Strategy, crawler: PoliteCrawler, 
                      diagnostics: Optional[Dict[str, Any]] = None) -> List[str]:
    """Find candidate document URLs for an issuer (sitemap index expansion first, then listings)."""
    found: List[str] = []
    seen = set()
    rx = re.compile(strat.doc_pattern, re.IGNORECASE) if strat.doc_pattern else None
    stop_rx = re.compile(strat.stop_pattern, re.IGNORECASE) if strat.stop_pattern else None

    def keep(u: str) -> None:
        if u and u not in seen and (rx is None or rx.search(u)):
            seen.add(u)
            found.append(u)

    # 1. Ingest Sitemaps via Recursive Sitemap Index expansion
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
                for sub_url in extracted_urls:
                    if sub_url.endswith(".xml") or "sitemap" in sub_url.lower():
                        sitemap_queue.append(sub_url)
            else:
                before_count = len(found)
                for loc in extracted_urls:
                    keep(loc)
                sm_diag["guidelines_located"] = len(found) - before_count
                
        if diagnostics is not None:
            if "sitemaps" not in diagnostics:
                diagnostics["sitemaps"] = {}
            diagnostics["sitemaps"][sm_url] = sm_diag

    # 2. Fall back to index pages if sitemaps did not satisfy capacity requirements
    if len(found) < strat.max_docs:
        for base_listing in strat.listing_urls:
            target_url = base_listing
            page_count = 1
            
            while target_url and len(found) < strat.max_docs:
                res = crawler.fetch(target_url)
                list_diag = {
                    "status_code": res.status,
                    "found": res.ok,
                    "error": res.error,
                    "robot_check_failed": (res.error == "disallowed by robots.txt"),
                    "links_found": 0,
                    "page_index": page_count
                }
                
                if not res.ok or (stop_rx and stop_rx.search(res.text)):
                    if diagnostics is not None and "listings" in diagnostics:
                        diagnostics["listings"][target_url] = list_diag
                    break

                before_count = len(found)

                # Parse RSS/Atom variants vs traditional standard HTML anchors
                if "rss" in res.text[:200].lower() or "<rss" in res.text or "<feed" in res.text:
                    rss_links = re.findall(r"<link>\s*([^<\s]+)\s*</link>", res.text)
                    rss_links.extend(re.findall(r'<link[^>]+href=["\']([^"\']+)["\']', res.text))
                    list_diag["links_found"] = len(rss_links)
                    for r_link in rss_links:
                        keep(r_link.strip())
                    next_page_url = None
                else:
                    extracted_links = extract_links(res.text, base_url=target_url, pattern=strat.doc_pattern)
                    list_diag["links_found"] = len(extracted_links)
                    for link in extracted_links:
                        keep(link["href"])
                    
                    # Target query parameter elements inside layout anchors
                    all_anchors = extract_links(res.text, base_url=target_url)
                    next_page_url = None
                    target_param_match = f"page={page_count + 1}"
                    
                    for anchor in all_anchors:
                        href_lower = anchor["href"].lower()
                        if target_param_match in href_lower or (strat.paging_param and f"{strat.paging_param}={page_count + 1}" in href_lower):
                            # Fix: Ensure relative paths are forced absolute against current host domain base URL
                            next_page_url = urljoin(target_url, anchor["href"])
                            break
                        
                list_diag["guidelines_located"] = len(found) - before_count
                
                if diagnostics is not None:
                    if "listings" not in diagnostics:
                        diagnostics["listings"] = {}
                    diagnostics["listings"][target_url] = list_diag

                if len(found) == before_count or not next_page_url:
                    break

                target_url = next_page_url
                page_count += 1
                
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
    crawler.respect_robots = issuer.robots_respect
    
    ts = crawl_ts if crawl_ts is not None else int(time.time())
    records: List[GuidelineRecord] = []
    stop_rx = re.compile(strat.stop_pattern, re.IGNORECASE) if strat.stop_pattern else None
    
    # Shortcut Option: Index directly using layout pagination variables without secondary fetches
    if getattr(strat, "index_from_listing_meta", False):
        for base_listing in strat.listing_urls:
            target_url = base_listing
            page_count = 1
            
            while target_url and len(records) < strat.max_docs:
                res = crawler.fetch(target_url)
                if not res.ok or (stop_rx and stop_rx.search(res.text)):
                    break

                anchors = extract_links(res.text, base_url=target_url, pattern=strat.doc_pattern)
                if not anchors:
                    break

                count_before_page = len(records)

                if diagnostics is not None:
                    diagnostics["documents"]["total_discovered"] += len(anchors)

                for a in anchors:
                    meta = {"title": a["text"], "year": None, "doi": None}
                    rec_url = a["href"]
                    if rec_url not in [r.url for r in records]:
                        records.append(_build_record(rec_url, meta, issuer, strat, ts))
                        if diagnostics is not None:
                            diagnostics["documents"]["successfully_fetched"] += 1
                            diagnostics["documents"]["successfully_parsed"] += 1
                    if len(records) >= strat.max_docs:
                        break
                
                # Scan ahead to locate pagination links safely
                all_anchors = extract_links(res.text, base_url=target_url)
                next_page_url = None
                target_param_match = f"page={page_count + 1}"
                
                for anchor in all_anchors:
                    href_lower = anchor["href"].lower()
                    if target_param_match in href_lower or (strat.paging_param and f"{strat.paging_param}={page_count + 1}" in href_lower):
                        # Fix: Forces normalization back to absolute form
                        next_page_url = urljoin(target_url, anchor["href"])
                        break
                        
                if len(records) == count_before_page or not next_page_url:
                    break

                target_url = next_page_url
                page_count += 1
                
        return records[: strat.max_docs]
    
    # Standard Workflow: Discover URLs then request specific child metadata details
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
