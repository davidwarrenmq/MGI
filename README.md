# Medical Guideline Index (MGI)

A standalone, importable Python library that **indexes reputable medical
guideline documents** and **resolves guideline citations to a direct,
verifiable document link** — not just the issuing society's homepage.

MGI is a *complement* to the **Citation Checker** (Paper A). It is designed to
become an additional **resolver backend** for that project's
institutional/guideline existence fallback, but it stands entirely alone and
**never imports from the Citation Checker**.

## Why MGI?

The Citation Checker's `Exists` stage runs a cascade
(`PMID -> DOI -> URL -> author/year -> institutional/guideline fallback`). The
guideline fallback is the weak link: coverage is patchy, so guideline citations
resolve weakly or only to a society homepage. MGI fixes this for guidelines by
returning the **specific document URL** with a calibrated confidence.

## Key properties

- **`resolve(citation)`** returns the exact 7-key dict the Citation Checker
  expects (see *Resolver contract* below). It **never raises**.
- **Direct document links** for ~30+ seeded, real guidelines out of the box
  (NICE, WHO, USPSTF, CDC, NCCN, SIGN, NHMRC, ESC, ACC/AHA, IDSA, and more).
- **Polite crawler** (robots.txt, rate-limit + jitter, identifying User-Agent,
  on-disk cache) that stores **metadata + URL only** — no full-text/PDF.
- **Lightweight store**: local SQLite + FTS5, shipped as a single `mgi.db`.
- **Data-driven issuer registry** (`issuers.yaml`): add an issuer with no code
  change.
- **Zero hard third-party dependencies in the core** — `requests`,
  `beautifulsoup4`, `fastapi`, `PyYAML`, and `pandas` are all optional and
  imported lazily.

## Install

```bash
pip install -e .                 # core only (stdlib)
pip install -e ".[crawl]"        # + requests, beautifulsoup4 (crawler)
pip install -e ".[app]"          # + fastapi, uvicorn (HTTP service)
pip install -e ".[all]"          # everything, incl. pandas + pytest
```

The core import works with **only the standard library** (the SQLite store uses
stdlib `sqlite3` + FTS5).

## Library usage

```python
from mgi import resolve, build_index, GuidelineRecord

# Resolve a guideline citation -> direct link + confidence
result = resolve("NICE NG28, type 2 diabetes in adults")
print(result["status"])        # "verified"
print(result["resolved_url"])  # https://www.nice.org.uk/guidance/ng28
print(result["confidence"])    # ~0.94
```

`resolve()` works out of the box: if `mgi.db` is missing or empty it
auto-loads the bundled seed index (`mgi/seed_guidelines.jsonl`).

### Resolver contract (the integration seam)

`resolve(citation: str, *, min_confidence: float = 0.45) -> dict` returns a
plain dict — identical in shape to the Citation Checker's institutional
fallback resolver:

```python
{
    "status": "verified" | "likely_exists" | "no_match",  # (also "error")
    "confidence": 0.0 - 1.0,
    "backend": "mgi",            # always
    "resolved_url": str | None,  # DIRECT document link
    "resolved_doi": str | None,  # bare, lower-cased (e.g. 10.1001/jama.2021.6238)
    "resolved_pmid": None,       # MGI never resolves PMIDs
    "details": str,              # human-readable explanation
    "record": dict | None,       # full GuidelineRecord.to_dict() (MGI extra)
}
```

**Thresholds** (identical to the Citation Checker):

| similarity score | status | confidence |
|---|---|---|
| `>= 0.78` | `verified` | `min(1.0, 0.6 + 0.4 * score)` |
| `>= 0.45` | `likely_exists` | `min(0.9, 0.3 + 0.6 * score)` |
| else | `no_match` | `0.0` |

Title similarity is `difflib.SequenceMatcher(None, a, b).ratio()` on
lower-cased, stripped strings, plus small boosts for issuer / year / identifier
matches. A fabricated citation returns `no_match`.

### Building / rebuilding the index

```bash
python scripts/build_index.py                  # seed-only -> mgi.db
python scripts/build_index.py --reset          # full rebuild
python scripts/build_index.py --crawl          # seed + polite crawl
python scripts/build_index.py --crawl --issuer NICE --issuer WHO
```

```python
from mgi import build_index
build_index("mgi.db", reset=True)              # programmatic rebuild
```

## App usage (FastAPI)

```bash
pip install -e ".[app]"
uvicorn mgi.service:app --reload
```

Endpoints:

- `GET /health` -> `{"status": "ok", ...}`
- `GET /resolve?citation=...&min_confidence=0.45` -> the resolver dict above
- `GET /search?q=...&limit=10` -> ranked candidate guidelines

```bash
curl "http://127.0.0.1:8000/resolve?citation=NICE%20NG28%20type%202%20diabetes"
```

If FastAPI is not installed, `import mgi.service` still succeeds (`app` is
`None`); `create_app()` raises a clear install hint.

## Notebook / Colab

See `notebooks/mgi_quickstart.ipynb` — builds the index and runs a few
resolves in a Colab-friendly way.

## Architecture

```
medical_guideline_index/
|- mgi/
|  |- __init__.py          # exports resolve(), build_index(), GuidelineRecord
|  |- registry.py          # loads issuers.yaml; Issuer dataclass
|  |- issuers.yaml         # data-driven issuer list + trigger terms
|  |- guideline_record.py  # GuidelineRecord dataclass + (de)serialisation
|  |- store.py             # SQLite + FTS5 store (stdlib)
|  |- search.py            # normalise + FTS recall + SequenceMatcher re-rank
|  |- resolve.py           # resolve(citation) -> 7-key dict (never raises)
|  |- build.py             # build_index(): seed + (optional) crawl -> store
|  |- service.py           # optional FastAPI app (/resolve, /search)
|  |- seed_guidelines.jsonl
|  `- crawl/
|     |- base.py           # PoliteCrawler: robots, rate-limit, UA, cache
|     |- sitemap.py        # sitemap.xml + HTML link/meta extraction
|     `- strategies.py     # per-issuer parse rules (NICE/WHO/NHMRC/CDC, ...)
|- scripts/build_index.py  # CLI: crawl + populate the store
|- tests/
`- notebooks/mgi_quickstart.ipynb
```

## Data model (`GuidelineRecord`)

One row per guideline document. Every record carries a non-null `country`
(ISO 3166 alpha-2 or `"international"`) and captures guideline-track fields
(`doc_type`, `status`, `superseded_by`, `doi`, `year`, `issuer`, `title`,
`url`). Serialisation tolerates schema drift: `from_dict` ignores unknown
fields (recording a note) and coerces `None` list-fields to `[]`.

## Crawling ethics

The crawler respects `robots.txt`, rate-limits per host (with jitter), sends an
identifying User-Agent with a contact URL, caches responses on disk, and
collects **metadata + the direct URL only** — no full-text or PDF is downloaded
or stored.

## Testing

```bash
pip install -e ".[all]"
pytest -q
```

Tests use mocked HTTP — **no network calls**. Store/service tests that need
`sqlite3`/`fastapi` skip automatically where those are unavailable.

## Relationship to the Citation Checker

MGI is standalone and never imports the Citation Checker. Compatibility is by
contract only: the resolver dict, the record serialisation conventions, the
trigger terms, and the guideline-track fields are all reproduced from the
frozen specification. See `mgi/integration.py` (Phase 4) for a drop-in adapter
shaped for the Checker's fallback.

## License

MIT
