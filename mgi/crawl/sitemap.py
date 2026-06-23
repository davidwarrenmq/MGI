"""Discovery helpers: sitemap.xml parsing + HTML link/metadata extraction (scope §8).

Discovery priority (scope §8): issuer sitemap.xml -> guideline index/listing
pages -> (optional) DOI enrichment. This module covers the first two using only
the standard library (``xml.etree``, ``html.parser``); BeautifulSoup is used when
available for more robust HTML parsing but is NOT required.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Dict, List, Optional
from urllib.parse import urljoin

try:
    from bs4 import BeautifulSoup  # type: ignore
    _HAVE_BS4 = True
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore
    _HAVE_BS4 = False


def parse_sitemap(xml_text: str, *, limit: Optional[int] = None) -> List[str]:
    """Return all ``<loc>`` URLs from a sitemap or sitemap-index document."""
    urls: List[str] = []
    if not xml_text:
        return urls
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        # Fall back to a permissive regex if the XML is malformed.
        urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text)
        return urls[:limit] if limit else urls
    for loc in root.iter():
        tag = loc.tag.rsplit("}", 1)[-1]  # strip namespace
        if tag == "loc" and loc.text:
            urls.append(loc.text.strip())
            if limit and len(urls) >= limit:
                break
    return urls


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: List[Dict[str, str]] = []
        self._cur_href: Optional[str] = None
        self._cur_text: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._cur_href = href
                self._cur_text = []

    def handle_data(self, data):
        if self._cur_href is not None:
            self._cur_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._cur_href is not None:
            text = " ".join("".join(self._cur_text).split())
            self.links.append({"href": self._cur_href, "text": text})
            self._cur_href = None
            self._cur_text = []


def extract_links(html: str, base_url: str = "", *,
                  pattern: Optional[str] = None) -> List[Dict[str, str]]:
    """Extract ``{href, text}`` link dicts from HTML, optionally filtered.

    ``pattern`` (regex) is matched against the absolute href to keep only
    guideline-document links (per-issuer rules live in ``strategies.py``).
    """
    if not html:
        return []
    if _HAVE_BS4:
        soup = BeautifulSoup(html, "html.parser")
        raw = [{"href": a.get("href", ""), "text": a.get_text(" ", strip=True)}
               for a in soup.find_all("a", href=True)]
    else:
        p = _LinkExtractor()
        p.feed(html)
        raw = p.links

    out: List[Dict[str, str]] = []
    rx = re.compile(pattern) if pattern else None
    seen = set()
    for link in raw:
        href = urljoin(base_url, link["href"]) if base_url else link["href"]
        if not href or href in seen:
            continue
        if rx and not rx.search(href):
            continue
        seen.add(href)
        out.append({"href": href, "text": link.get("text", "")})
    return out


_TITLE_RX = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_DOI_RX = re.compile(r"\b10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)
_YEAR_RX = re.compile(r"\b(?:19|20)\d{2}\b")


def extract_page_meta(html: str) -> Dict[str, Optional[str]]:
    """Best-effort page metadata: title, doi, year (scope §8 / parse.py)."""
    meta: Dict[str, Optional[str]] = {"title": None, "doi": None, "year": None}
    if not html:
        return meta
    m = _TITLE_RX.search(html)
    if m:
        meta["title"] = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())
    d = _DOI_RX.search(html)
    if d:
        meta["doi"] = d.group(0).rstrip(".,);").lower()
    y = _YEAR_RX.search(html)
    if y:
        meta["year"] = y.group(0)
    return meta
