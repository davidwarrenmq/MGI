"""Lexical recall + fuzzy re-ranking of guideline candidates (scope §7, §13.4).

Pipeline:

1. ``normalise_citation`` -- strip markdown ``* _ `` characters, collapse
   whitespace, lower-case; also expand known issuer abbreviations.
2. ``extract_signals`` -- pull a 4-digit year and any
   quoted title (``"([^"]{6,120})"``) -- the strongest disambiguators (§13.4).
3. ``Searcher.search`` -- FTS5/LIKE recall from the store, then re-rank by
   ``difflib.SequenceMatcher(None, a, b).ratio()`` (the EXACT similarity
   primitive the Citation Checker uses, §7 step 3) plus small boosts for
   issuer / year / identifier matches.

No network and no heavy deps -- standard library + the MGI store/registry only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from .guideline_record import GuidelineRecord
from .registry import Registry, load_registry

# Markdown characters stripped before matching (scope §13.4).
_MD_CHARS = re.compile(r"[*_`]+")
_WS = re.compile(r"\s+")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_QUOTED = re.compile(r'"([^"]{6,120})"')
# An issuer-style code, e.g. NICE "NG28", "CG127", "SIGN 147", "JNC7".
_IDENT = re.compile(r"\b([A-Z]{1,5})\s?-?\s?(\d{1,4})\b")


def normalise_citation(text: str, registry: Optional[Registry] = None) -> str:
    """Lower-cased, markdown-stripped, whitespace-collapsed citation.

    Known issuer abbreviations are expanded (appended) so lexical recall hits
    both the abbreviation and the full issuer name (scope §7 step 1).
    """
    if not text:
        return ""
    s = _MD_CHARS.sub(" ", text)
    s = _WS.sub(" ", s).strip().lower()
    if registry is not None:
        extra: List[str] = []
        for abbrev, full in registry.abbrev_expansions.items():
            if re.search(rf"\b{re.escape(abbrev.lower())}\b", s):
                extra.append(full.lower())
        if extra:
            s = s + " " + " ".join(extra)
    return s


@dataclass
class CitationSignals:
    """Structured cues extracted from a raw citation (scope §13.4)."""

    year: Optional[int] = None
    quoted_title: Optional[str] = None
    identifier: Optional[str] = None


def extract_signals(text: str) -> CitationSignals:
    if not text:
        return CitationSignals()
    year_m = _YEAR.search(text)
    year = int(year_m.group(0)) if year_m else None
    q = _QUOTED.search(text)
    quoted = q.group(1).strip() if q else None
    ident = None
    im = _IDENT.search(text)
    if im:
        ident = f"{im.group(1).upper()}{im.group(2)}"
    return CitationSignals(year=year, quoted_title=quoted, identifier=ident)


def _title_similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio on lower-cased, stripped strings (scope §7 step 3)."""
    return SequenceMatcher(None, (a or "").strip().lower(), (b or "").strip().lower()).ratio()


@dataclass
class Candidate:
    """A scored search candidate."""

    record: GuidelineRecord
    score: float
    base_similarity: float
    boosts: dict


class Searcher:
    """Recall candidates from a Store and re-rank them (scope §7)."""

    # Re-rank weighting (small additive boosts on top of title similarity).
    ISSUER_BOOST = 0.12
    YEAR_BOOST = 0.06
    IDENTIFIER_BOOST = 0.18
    QUOTED_TITLE_WEIGHT = 0.5   # blend quoted-title sim with full-citation sim

    def __init__(self, store, registry: Optional[Registry] = None) -> None:
        self.store = store
        self.registry = registry or load_registry()

    def _recall(self, citation_norm: str, signals: CitationSignals, limit: int) -> List[GuidelineRecord]:
        # Query the store with the normalised text; also try the identifier and
        # quoted title for extra recall, then de-dupe by record id.
        seen = {}
        for q in (citation_norm, signals.quoted_title, signals.identifier):
            if not q:
                continue
            for rec in self.store.search(q, limit=limit):
                seen[rec.id] = rec
        return list(seen.values())

    def _score(self, rec: GuidelineRecord, citation_norm: str, signals: CitationSignals) -> Candidate:
        # Base similarity: best of (full citation vs title) and, when present,
        # (quoted title vs title) blended in -- quoted titles are strong cues.
        sim_full = _title_similarity(citation_norm, rec.title)
        sim = sim_full
        if signals.quoted_title:
            sim_q = _title_similarity(signals.quoted_title, rec.title)
            sim = max(sim_full, self.QUOTED_TITLE_WEIGHT * sim_full + (1 - self.QUOTED_TITLE_WEIGHT) * sim_q, sim_q)

        boosts: dict = {}
        score = sim

        # Issuer boost: abbreviation or full issuer name present in the citation.
        iss_abbrev = (rec.issuer_abbrev or "").lower()
        iss_name = (rec.issuer or "").lower()
        if (iss_abbrev and re.search(rf"\b{re.escape(iss_abbrev)}\b", citation_norm)) or \
           (iss_name and iss_name in citation_norm):
            score += self.ISSUER_BOOST
            boosts["issuer"] = self.ISSUER_BOOST

        # Year boost.
        if signals.year and rec.year and signals.year == rec.year:
            score += self.YEAR_BOOST
            boosts["year"] = self.YEAR_BOOST

        # Identifier boost (e.g. "NG28", "CG127") -- a very strong signal.
        if signals.identifier and rec.identifier:
            ci = re.sub(r"[^a-z0-9]", "", signals.identifier.lower())
            ri = re.sub(r"[^a-z0-9]", "", rec.identifier.lower())
            if ci and ci == ri:
                score += self.IDENTIFIER_BOOST
                boosts["identifier"] = self.IDENTIFIER_BOOST

        return Candidate(record=rec, score=min(1.0, score), base_similarity=sim, boosts=boosts)

    def search(self, citation: str, *, limit: int = 25) -> List[Candidate]:
        """Return scored candidates, best first. Pure/deterministic."""
        norm = normalise_citation(citation, self.registry)
        signals = extract_signals(citation)
        recalled = self._recall(norm, signals, limit)
        scored = [self._score(r, norm, signals) for r in recalled]
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored

    def best(self, citation: str) -> Optional[Candidate]:
        cands = self.search(citation, limit=25)
        return cands[0] if cands else None
