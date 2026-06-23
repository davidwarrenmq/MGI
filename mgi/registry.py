"""Issuer registry: loads ``issuers.yaml`` into ``Issuer`` records.

The registry is the *single source of truth* for issuers (scope §4) and the
guideline-citation *trigger terms* (scope §13.4). Adding an issuer requires only
an edit to ``issuers.yaml`` -- no code change (scope §5).

YAML loading uses PyYAML when available, but falls back to a tiny built-in
parser for the restricted subset this file uses, so the core stays
dependency-free (scope §6 / system prompt §6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_YAML = Path(__file__).with_name("issuers.yaml")


@dataclass
class Issuer:
    """One guideline-issuing organisation (scope §4 / §5)."""

    name: str
    abbrev: str
    country: str = "international"      # ISO 3166 alpha-2 or "international"
    tier: str = "A"                     # "A" or "B"
    base_url: str = ""
    crawl_strategy: str = "generic"
    robots_respect: bool = True
    sitemap: Optional[str] = None
    index_pages: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Issuer":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in (data or {}).items() if k in known}
        for list_field in ("index_pages", "aliases"):
            if kwargs.get(list_field) is None:
                kwargs[list_field] = []
        return cls(**kwargs)


@dataclass
class Registry:
    """All issuers plus the shared trigger-term vocabulary (scope §13.4)."""

    issuers: List[Issuer] = field(default_factory=list)
    triggers: List[str] = field(default_factory=list)
    abbrev_expansions: Dict[str, str] = field(default_factory=dict)

    # ---- lookups -------------------------------------------------------
    def by_abbrev(self, abbrev: str) -> Optional[Issuer]:
        if not abbrev:
            return None
        a = abbrev.strip().upper()
        for iss in self.issuers:
            if iss.abbrev.upper() == a:
                return iss
        return None

    def match_triggers(self, text: str) -> List[str]:
        """Return trigger terms present in ``text`` (case-insensitive)."""
        if not text:
            return []
        low = text.lower()
        hits = [t for t in self.triggers if t.lower() in low]
        # issuer abbreviations & names also act as triggers
        for iss in self.issuers:
            if iss.abbrev and iss.abbrev.lower() in low:
                hits.append(iss.abbrev)
            elif iss.name and iss.name.lower() in low:
                hits.append(iss.abbrev or iss.name)
        # de-dupe, preserve order
        seen: set = set()
        out: List[str] = []
        for h in hits:
            if h.lower() not in seen:
                seen.add(h.lower())
                out.append(h)
        return out

    def looks_like_guideline(self, text: str) -> bool:
        return bool(self.match_triggers(text))


def load_registry(path: "str | Path | None" = None) -> Registry:
    """Load the issuer registry from ``issuers.yaml`` (scope §5)."""
    p = Path(path) if path else DEFAULT_YAML
    data = _load_yaml(p.read_text(encoding="utf-8"))
    issuers = [Issuer.from_dict(d) for d in (data.get("issuers") or [])]
    triggers = list(data.get("triggers") or [])
    expansions = dict(data.get("abbrev_expansions") or {})
    return Registry(issuers=issuers, triggers=triggers, abbrev_expansions=expansions)


# ---------------------------------------------------------------------------
# Minimal YAML loader (fallback). Uses PyYAML when installed; otherwise parses
# the restricted subset that issuers.yaml uses: top-level mapping keys, lists of
# scalars, and lists of inline-or-block mappings with scalar values.
# ---------------------------------------------------------------------------

def _load_yaml(text: str) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    except Exception:
        return _mini_yaml(text)


def _coerce_scalar(s: str) -> Any:
    s = s.strip()
    if s == "" or s in ("~", "null", "None"):
        return None
    if (s[0] == s[-1]) and s[0] in ("'", '"') and len(s) >= 2:
        return s[1:-1]
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _split_inline_list(s: str) -> List[Any]:
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    parts, buf, depth, q = [], "", 0, ""
    for ch in s:
        if q:
            buf += ch
            if ch == q:
                q = ""
        elif ch in ("'", '"'):
            q = ch
            buf += ch
        elif ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf)
    return [_coerce_scalar(p) for p in parts if p.strip() != ""]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _mini_yaml(text: str) -> Dict[str, Any]:
    # Strip comments and blank lines, keep indentation.
    lines = []
    for raw in text.splitlines():
        # drop full-line comments and trailing comments outside quotes
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        out, q = "", ""
        for ch in raw:
            if q:
                out += ch
                if ch == q:
                    q = ""
            elif ch in ("'", '"'):
                q = ch
                out += ch
            elif ch == "#":
                break
            else:
                out += ch
        if out.strip():
            lines.append(out.rstrip())

    root: Dict[str, Any] = {}
    i, n = 0, len(lines)

    def parse_block(start: int, indent: int):
        items_list: List[Any] = []
        mapping: Dict[str, Any] = {}
        j = start
        while j < n:
            line = lines[j]
            ind = _indent(line)
            if ind < indent:
                break
            content = line.strip()
            if content.startswith("- "):
                if ind > indent:
                    break
                item = content[2:].strip()
                if ":" in item and not item.startswith(("[", "'", '"')):
                    # inline first key of a list-of-mappings entry
                    submap: Dict[str, Any] = {}
                    k, _, v = item.partition(":")
                    submap[k.strip()] = _coerce_scalar(v) if v.strip() else None
                    j += 1
                    # consume deeper-indented keys belonging to this list item
                    while j < n and _indent(lines[j]) > ind:
                        kl = lines[j].strip()
                        kk, _, vv = kl.partition(":")
                        vv = vv.strip()
                        if vv.startswith("["):
                            submap[kk.strip()] = _split_inline_list(vv)
                        else:
                            submap[kk.strip()] = _coerce_scalar(vv) if vv else None
                        j += 1
                    items_list.append(submap)
                else:
                    items_list.append(_coerce_scalar(item))
                    j += 1
            else:
                k, _, v = content.partition(":")
                key, v = k.strip(), v.strip()
                if v == "":
                    sub, j = parse_block(j + 1, ind + 1) if False else _parse_child(j, ind)
                    mapping[key] = sub
                elif v.startswith("["):
                    mapping[key] = _split_inline_list(v)
                    j += 1
                else:
                    mapping[key] = _coerce_scalar(v)
                    j += 1
        return (items_list if items_list else mapping), j

    def _parse_child(parent_idx: int, parent_ind: int):
        # find the indentation of the child block
        k = parent_idx + 1
        if k >= n:
            return {}, parent_idx + 1
        child_ind = _indent(lines[k])
        if child_ind <= parent_ind:
            return None, parent_idx + 1
        return parse_block(k, child_ind)

    result, _ = parse_block(0, 0)
    return result if isinstance(result, dict) else {"_root": result}
