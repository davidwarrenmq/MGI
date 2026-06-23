"""Phase 2 tests: PoliteCrawler + sitemap/HTML parsing + strategies (scope §8).

All network is mocked (system prompt §7): no test makes a real HTTP request.
"""
import time

import pytest

from mgi.crawl.base import PoliteCrawler, FetchResult, USER_AGENT
from mgi.crawl.sitemap import parse_sitemap, extract_links, extract_page_meta
from mgi.crawl import strategies as ST
from mgi.registry import load_registry


# --- PoliteCrawler ----------------------------------------------------
def test_user_agent_identifies_project_with_contact():
    assert "medical-guideline-index" in USER_AGENT
    assert "+http" in USER_AGENT


def test_cache_roundtrip(tmp_path):
    pc = PoliteCrawler(cache_dir=tmp_path)
    pc._write_cache(FetchResult(url="https://e.org/a", status=200, text="hi",
                                content_type="text/html"))
    got = pc._read_cache("https://e.org/a")
    assert got is not None and got.from_cache and got.text == "hi"


def test_fetch_uses_cache_without_network(tmp_path):
    pc = PoliteCrawler(cache_dir=tmp_path)
    pc._write_cache(FetchResult(url="https://e.org/x", status=200, text="cached"))
    res = pc.fetch("https://e.org/x")
    assert res.from_cache and res.text == "cached"


def test_robots_disallow_blocks_fetch(tmp_path):
    pc = PoliteCrawler(cache_dir=tmp_path)

    class _R:
        def can_fetch(self, ua, url):
            return False

    pc._robots["blocked.example"] = _R()
    res = pc.fetch("https://blocked.example/secret", use_cache=False)
    assert res.status == 0 and "robots" in (res.error or "")


def test_respect_robots_false_bypasses(tmp_path):
    pc = PoliteCrawler(cache_dir=tmp_path, respect_robots=False)

    class _R:
        def can_fetch(self, ua, url):
            return False

    pc._robots["blocked.example"] = _R()
    assert pc.allowed("https://blocked.example/x") is True


def test_throttle_enforces_min_interval(tmp_path):
    pc = PoliteCrawler(cache_dir=tmp_path, min_interval=0.3, jitter=0.0)
    t0 = time.monotonic()
    pc._throttle("https://h.example/a")
    pc._throttle("https://h.example/b")
    assert time.monotonic() - t0 >= 0.29


# --- sitemap / HTML parsing ------------------------------------------
def test_parse_sitemap_with_namespace():
    xml = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           '<url><loc>https://x.org/a</loc></url>'
           '<url><loc>https://x.org/b</loc></url></urlset>')
    assert parse_sitemap(xml) == ["https://x.org/a", "https://x.org/b"]


def test_parse_sitemap_malformed_falls_back():
    assert parse_sitemap("<loc>https://x.org/a</loc>") == ["https://x.org/a"]


def test_extract_links_filters_by_pattern():
    html = ('<a href="/guidance/ng28">a</a><a href="/about">b</a>'
            '<a href="/guidance/cg127">c</a>')
    links = extract_links(html, base_url="https://www.nice.org.uk",
                          pattern=r"/guidance/(ng|cg)\d+")
    hrefs = [l["href"] for l in links]
    assert "https://www.nice.org.uk/guidance/ng28" in hrefs
    assert all("/about" not in h for h in hrefs)


def test_extract_page_meta():
    html = ('<html><head><title>NG28: Type 2 diabetes</title></head>'
            '<body>doi:10.1001/JAMA.2021.6238 in 2022</body></html>')
    meta = extract_page_meta(html)
    assert "Type 2 diabetes" in meta["title"]
    assert meta["doi"] == "10.1001/jama.2021.6238"


# --- strategies (mocked crawler) -------------------------------------
class _MockCrawler:
    def __init__(self, pages):
        self.pages = pages
        self.fetched = []

    def fetch(self, url, use_cache=True):
        self.fetched.append(url)
        if url in self.pages:
            return FetchResult(url=url, status=200, text=self.pages[url],
                               content_type="text/html")
        return FetchResult(url=url, status=404, error="not found")


def _nice_pages():
    return {
        "https://www.nice.org.uk/sitemap.xml":
            ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
             '<url><loc>https://www.nice.org.uk/guidance/ng28</loc></url>'
             '<url><loc>https://www.nice.org.uk/about-us</loc></url></urlset>'),
        "https://www.nice.org.uk/guidance/ng28":
            '<html><head><title>Type 2 diabetes in adults | NG28</title></head>'
            '<body>2022</body></html>',
        "https://www.nice.org.uk/guidance/published?type=ng,cg":
            '<html><body><a href="/guidance/ng28">x</a></body></html>',
    }


def test_crawl_issuer_builds_records_with_identity():
    reg = load_registry()
    recs = ST.crawl_issuer("NICE", crawler=_MockCrawler(_nice_pages()),
                           registry=reg, crawl_ts=1700000000)
    assert len(recs) == 1
    r = recs[0]
    assert r.issuer_abbrev == "NICE" and r.country == "GB" and r.tier == "A"
    assert r.identifier == "NG28"
    assert r.url == "https://www.nice.org.uk/guidance/ng28"
    assert r.source_crawl_ts == 1700000000


def test_crawl_filters_non_document_links():
    recs = ST.crawl_issuer("NICE", crawler=_MockCrawler(_nice_pages()),
                           registry=load_registry())
    assert all("/about-us" not in r.url for r in recs)


def test_crawl_unknown_issuer_returns_empty():
    assert ST.crawl_issuers(["ZZZ"], crawler=_MockCrawler({}),
                            registry=load_registry()) == []


def test_crawl_issuer_never_raises_on_bad_pages():
    # A crawler that returns only errors must yield [] without raising.
    recs = ST.crawl_issuer("WHO", crawler=_MockCrawler({}), registry=load_registry())
    assert recs == []
