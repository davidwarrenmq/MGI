"""GuidelineRecord: one row per guideline document.

Serialisation mirrors the Citation Checker's record conventions (scope doc
§6 / §13.2) so the two projects interoperate. In particular ``from_dict``:

* ignores unknown fields (records them as a note rather than raising), and
* coerces ``None`` list-fields to ``[]``,

so schema drift between MGI and the Citation Checker never crashes a load.

This module uses ONLY the standard library. Pandas interop imports pandas
lazily so the core remains dependency-free.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# Bump when the on-disk schema changes. Loading data with a different value is
# recorded as a note (drift), never an error -- same convention as the Checker.
SCHEMA_VERSION = "mgi-1.0"

# Allowed controlled vocabularies (validated softly; bad values become notes).
DOC_TYPES = {"guideline", "consensus", "technology_appraisal", "position_statement"}
STATUSES = {"active", "superseded", "withdrawn"}


def canonical_id(issuer_abbrev: str, url: str) -> str:
    """Stable id = short hash of ``issuer_abbrev + canonical_url`` (scope §6)."""
    key = f"{(issuer_abbrev or '').strip().upper()}|{(url or '').strip().lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


@dataclass
class GuidelineRecord:
    # --- identity / provenance ---
    title: str = ""
    issuer: str = ""
    issuer_abbrev: str = ""
    country: str = "international"          # ISO 3166 alpha-2 or "international"
    tier: str = "A"                          # "A" or "B"
    url: str = ""                            # DIRECT document URL, not the homepage

    # --- bibliographic ---
    year: Optional[int] = None
    version: Optional[str] = None
    doc_type: str = "guideline"              # see DOC_TYPES
    doi: Optional[str] = None
    identifier: Optional[str] = None         # issuer's own code, e.g. NICE "NG28"
    topics: List[str] = field(default_factory=list)

    # --- currency / lifecycle (feeds the Checker's "Currency" / Zombie case) ---
    superseded_by: Optional[str] = None      # id of newer doc, if known
    status: str = "active"                   # see STATUSES

    # --- audit ---
    source_crawl_ts: int = 0                 # unix ts of crawl
    raw_meta: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    # --- schema bookkeeping ---
    id: str = ""
    schema_version: str = SCHEMA_VERSION

    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        # Auto-derive a stable id when one was not supplied.
        if not self.id and (self.issuer_abbrev or self.url):
            self.id = canonical_id(self.issuer_abbrev, self.url)
        # Coerce year to int when possible.
        if self.year is not None and not isinstance(self.year, int):
            try:
                self.year = int(str(self.year)[:4])
            except (TypeError, ValueError):
                self.notes.append(f"unparseable_year: {self.year!r}")
                self.year = None
        # Soft-validate controlled vocabularies.
        if self.doc_type not in DOC_TYPES:
            self.notes.append(f"unknown_doc_type: {self.doc_type!r}")
        if self.status not in STATUSES:
            self.notes.append(f"unknown_status: {self.status!r}")

    # ----------------------- instance serialisation ------------------- #
    def to_dict(self) -> dict:
        """Dataclass -> plain dict (Enums would become .value; none used here)."""
        out: Dict[str, Any] = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if hasattr(val, "value"):           # Enum -> .value (future-proof)
                val = val.value
            out[f.name] = val
        return out

    def to_json(self, *, indent: Optional[int] = 2, ensure_ascii: bool = False) -> str:
        return json.dumps(self.to_dict(), indent=indent,
                          ensure_ascii=ensure_ascii, default=str)

    def save_json(self, path, *, indent: int = 2) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(indent=indent), encoding="utf-8")
        return p

    # ------------------------- class deserialisation ------------------ #
    @classmethod
    def from_dict(cls, data: dict) -> "GuidelineRecord":
        if not isinstance(data, dict):
            raise TypeError(f"from_dict expects a dict, got {type(data).__name__}")

        known_names = {f.name for f in fields(cls)}
        list_fields = {f.name for f in fields(cls)
                       if f.name in ("topics", "notes")}

        known: Dict[str, Any] = {}
        unknown: Dict[str, Any] = {}
        for k, v in data.items():
            (known if k in known_names else unknown)[k] = v

        # Robustness #2: coerce None list-fields to [].
        for lf in list_fields:
            if lf in known and known[lf] is None:
                known[lf] = []

        notes: List[str] = list(known.get("notes") or [])

        # Robustness #1: unknown fields are ignored (kept as a note, not fatal).
        if unknown:
            notes.append(f"unknown_fields_on_load: {sorted(unknown.keys())}")
            # stash the raw values for audit without crashing
            rm = dict(known.get("raw_meta") or {})
            rm.setdefault("_unknown_fields", unknown)
            known["raw_meta"] = rm

        # Schema drift is a note, not an error.
        loaded_sv = known.get("schema_version", SCHEMA_VERSION)
        if loaded_sv != SCHEMA_VERSION:
            notes.append(f"schema_version_drift: loaded={loaded_sv!r} current={SCHEMA_VERSION!r}")

        known["notes"] = notes
        return cls(**known)

    @classmethod
    def from_json(cls, s: str) -> "GuidelineRecord":
        return cls.from_dict(json.loads(s))

    @classmethod
    def load_json(cls, path) -> "GuidelineRecord":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    # --------------------------- collections -------------------------- #
    @staticmethod
    def save_jsonl(records: Iterable["GuidelineRecord"], path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(rec.to_json(indent=None) + "\n")
        return p

    @classmethod
    def load_jsonl(cls, path) -> List["GuidelineRecord"]:
        out: List["GuidelineRecord"] = []
        p = Path(path)
        with p.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(cls.from_json(line))
                except Exception as exc:  # noqa: BLE001 - surface line number
                    raise ValueError(f"{p}:{lineno}: {exc}") from exc
        return out

    @staticmethod
    def save_json_array(records: Iterable["GuidelineRecord"], path, *, indent: int = 2) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "documents": [r.to_dict() for r in records],
        }
        p.write_text(json.dumps(payload, indent=indent, ensure_ascii=False, default=str),
                     encoding="utf-8")
        return p

    @classmethod
    def load_json_array(cls, path) -> List["GuidelineRecord"]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, list):
            docs = data
        elif isinstance(data, dict):
            docs = data.get("documents", [])
        else:
            raise TypeError("load_json_array expects a list or {'documents': [...]} object")
        return [cls.from_dict(d) for d in docs]

    # --------------------------- pandas interop ----------------------- #
    @staticmethod
    def to_dataframe(records: Iterable["GuidelineRecord"]):
        import pandas as pd  # lazy import keeps core dependency-free
        return pd.DataFrame([r.to_dict() for r in records])

    @classmethod
    def from_dataframe(cls, df) -> List["GuidelineRecord"]:
        return [cls.from_dict(rec) for rec in df.to_dict("records")]
