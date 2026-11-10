"""Mindat.org localities in Zimbabwe as an optional occurrence source (U6).

Needs a free personal API key in the MINDAT_API_KEY environment variable (Mindat account
settings). Without it this module logs one warning and returns no points.

API, checked Nov 2026 against the OpenMindat reference client
(https://github.com/ChuBL/OpenMindat: openmindat/mindat_api.py, localities.py, geomaterials.py)
and the API docs at https://api.mindat.org/v1/schema/redoc/#tag/localities
(schema https://api.mindat.org/v1/schema/):
  GET https://api.mindat.org/v1/localities/?country=Zimbabwe&fields=...&expand=geomaterials
      header `Authorization: Token <key>`; `page-size` (max 1500) and `page`; each response
      is {"next": <url or null>, "results": [...]}.
  Locality fields: txt (name), latitude, longitude, level (0 = country), non_hierarchical,
      geomaterials (mineral ids, only with expand=geomaterials). Mindat stores "no coordinates"
      as 0.0/0.0. There is no coordinate-precision field, so imprecise points are dropped by
      proxy: country/province/district levels (< MIN_POINT_LEVEL) and non-hierarchical areas
      (belts, regions) carry area centroids, not workings.
  GET https://api.mindat.org/v1/geomaterials/?id_in=1,2,3&fields=id,name  -> mineral names.

Terms of use - read before relying on these points:
  Mindat data are licensed CC BY-NC-SA 4.0, and while the API is in beta Mindat states the data
  "are for private, non-commercial use only" and "not yet licensed for redistribution"
  (https://github.com/ChuBL/OpenMindat#license). Mindat's site terms
  (https://www.mindat.org/terms.php) also forbid copying data into another product or website.
  Do not use Mindat points for commercial targeting, and do not share target lists or maps
  built with them, without written permission from Mindat (Hudson Institute of Mineralogy).
  Credit "Mindat.org" wherever the points are shown. The cache file lives under data/ and must
  never be committed or redistributed.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

import geopandas as gpd
import numpy as np
import requests

from . import sources, userdata

log = logging.getLogger(__name__)

API_HOST = "api.mindat.org"
API_ROOT = f"https://{API_HOST}/v1/"
LOCALITIES_URL = API_ROOT + "localities/"
GEOMATERIALS_URL = API_ROOT + "geomaterials/"
API_KEY_ENV = "MINDAT_API_KEY"
COUNTRY = "Zimbabwe"
CACHE_NAME = "mindat_zwe.json"
CACHE_VERSION = 1

MAX_PAGE_SIZE = 1500               # server limit per page
PAGE_SIZE = 1000
LOCALITY_FIELDS = "id,txt,latitude,longitude,country,level,non_hierarchical,geomaterials"
ID_BATCH = 200                     # ids per geomaterials lookup (long id lists overflow the URL)
MAX_PAGES = 500                    # guards against a pagination loop
TIMEOUT_S = (10, 120)              # (connect, read)
MAX_RETRIES = 4
RETRY_STATUS = frozenset({429, 502, 503, 504})
MAX_RETRY_WAIT_S = 30.0
MIN_POINT_LEVEL = 3                # 0 country, 1 province, 2 district: centroids, not workings

SOURCE = "Mindat"
STATUS = "Mindat locality"

JsonGetter = Callable[[str, Mapping[str, str] | None], Any]


class MindatError(ValueError):
    """The Mindat API answered with something this module cannot use."""


# ---------- HTTP ----------

def api_key() -> str | None:
    """The API key from the environment (never from code or config)."""
    key = os.environ.get(API_KEY_ENV, "").strip()
    return key or None


def _retry_wait(retry_after: str | None, attempt: int) -> float:
    try:
        wait = float(retry_after) if retry_after is not None else 2.0 ** attempt
    except ValueError:
        wait = 2.0 ** attempt
    return min(max(wait, 1.0), MAX_RETRY_WAIT_S)


def http_getter(key: str, session: requests.Session | None = None) -> JsonGetter:
    """GET-and-decode function that authenticates with `key` and retries rate limits."""
    sess = session if session is not None else requests.Session()
    headers = {"Authorization": f"Token {key}", "User-Agent": sources.USER_AGENT,
               "Accept": "application/json"}

    def get_json(url: str, params: Mapping[str, str] | None) -> Any:
        for attempt in range(MAX_RETRIES + 1):
            resp = sess.get(url, params=params, headers=headers, timeout=TIMEOUT_S)
            if resp.status_code in RETRY_STATUS and attempt < MAX_RETRIES:
                wait = _retry_wait(resp.headers.get("Retry-After"), attempt)
                log.warning("Mindat API returned HTTP %d; retrying in %.0f s", resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        raise MindatError(f"no response from {url}")   # unreachable: the last attempt raises
    return get_json


def _checked_next(url: str) -> str:
    """Follow pagination only on the Mindat API host (the token must never leave it)."""
    parts = urlsplit(url)
    if parts.hostname != API_HOST or parts.scheme not in ("http", "https"):
        raise MindatError(f"refusing to follow a pagination link to {parts.hostname or url!r}")
    return urlunsplit(("https", *parts[1:]))


def collect_pages(get_json: JsonGetter, url: str, params: Mapping[str, str]) -> list[dict[str, Any]]:
    """All `results` of a paginated listing, following `next` links."""
    results: list[dict[str, Any]] = []
    page_url, page_params = url, params
    for _ in range(MAX_PAGES):
        page = get_json(page_url, page_params)
        if not isinstance(page, Mapping) or not isinstance(page.get("results"), list):
            raise MindatError(f"unexpected Mindat response from {page_url}: no 'results' list "
                              f"({str(page)[:200]})")
        results.extend(r for r in page["results"] if isinstance(r, Mapping))
        nxt = page.get("next")
        if not nxt:
            return results
        page_url, page_params = _checked_next(str(nxt)), None   # the link carries the query
    raise MindatError(f"Mindat pagination did not finish within {MAX_PAGES} pages")


def fetch_localities(get_json: JsonGetter) -> list[dict[str, Any]]:
    """Every Zimbabwe locality with coordinates fields and mineral ids."""
    params = {"country": COUNTRY, "fields": LOCALITY_FIELDS, "expand": "geomaterials",
              "page-size": str(min(PAGE_SIZE, MAX_PAGE_SIZE)), "format": "json"}
    return collect_pages(get_json, LOCALITIES_URL, params)


def fetch_mineral_names(get_json: JsonGetter, ids: list[int]) -> dict[int, str]:
    """{geomaterial id: name}, looked up in batches of ID_BATCH ids."""
    names: dict[int, str] = {}
    for start in range(0, len(ids), ID_BATCH):
        batch = ids[start:start + ID_BATCH]
        params = {"id_in": ",".join(map(str, batch)), "fields": "id,name",
                  "page-size": str(len(batch)), "format": "json"}
        for rec in collect_pages(get_json, GEOMATERIALS_URL, params):
            gid, name = rec.get("id"), rec.get("name")
            if isinstance(gid, int) and isinstance(name, str) and name.strip():
                names = {**names, gid: name.strip()}
    return names


def _mineral_ids(loc: Mapping[str, Any]) -> set[int]:
    refs = loc.get("geomaterials") or []
    return {r for r in refs if isinstance(r, int)} if isinstance(refs, list) else set()


def fetch_payload(get_json: JsonGetter) -> dict[str, Any]:
    """Localities plus the names of their minerals, in the cache-file layout."""
    localities = fetch_localities(get_json)
    ids = sorted(set().union(*(_mineral_ids(loc) for loc in localities)))
    names = fetch_mineral_names(get_json, ids)
    log.info("Mindat: %d localities, %d distinct minerals downloaded", len(localities), len(names))
    return {"version": CACHE_VERSION, "country": COUNTRY, "fetched": date.today().isoformat(),
            "localities": localities, "minerals": {str(k): v for k, v in names.items()}}


# ---------- parsing ----------

def _validate_payload(payload: Any) -> dict[int, str]:
    """Raise ValueError unless `payload` has the cache layout; return the mineral-name table."""
    if not isinstance(payload, Mapping):
        raise ValueError("Mindat payload is not a JSON object")
    if not isinstance(payload.get("localities"), list):
        raise ValueError("Mindat payload has no 'localities' list")
    minerals = payload.get("minerals")
    if not isinstance(minerals, Mapping):
        raise ValueError("Mindat payload has no 'minerals' mapping")
    try:
        return {int(k): str(v) for k, v in minerals.items()}
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Mindat payload has a non-integer mineral id ({exc})") from exc


def _coord(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if np.isfinite(num) else None


def _mineral_names(loc: Mapping[str, Any], minerals: Mapping[int, str]) -> list[str]:
    refs = loc.get("geomaterials") or []
    names = set()
    for ref in refs if isinstance(refs, list) else []:
        if isinstance(ref, Mapping) and isinstance(ref.get("name"), str):
            names.add(ref["name"].strip())
        elif isinstance(ref, int) and ref in minerals:
            names.add(minerals[ref])
    return sorted((n for n in names if n), key=str.lower)


def _locality_record(loc: Any, minerals: Mapping[int, str]) -> tuple[dict[str, Any] | None, str]:
    """(record, "") for a usable locality, else (None, reason it was dropped)."""
    if not isinstance(loc, Mapping):
        return None, "not an object"
    lat, lon = _coord(loc.get("latitude")), _coord(loc.get("longitude"))
    if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
        return None, "no coordinates"
    if not userdata.in_envelope(lat, lon):
        return None, "outside the Zimbabwe envelope"
    level = loc.get("level")
    if isinstance(level, int) and not isinstance(level, bool) and level < MIN_POINT_LEVEL:
        return None, "country/province/district centroid"
    if loc.get("non_hierarchical") in (1, True):
        return None, "non-hierarchical area centroid"
    names = _mineral_names(loc, minerals)
    if not names:
        return None, "no minerals listed"
    return {"source": SOURCE, "name": str(loc.get("txt") or userdata.UNNAMED).strip(),
            "commodities": ", ".join(names), "status": STATUS, "lat": lat, "lon": lon}, ""


def parse_localities(payload: Any) -> gpd.GeoDataFrame:
    """Usable Mindat localities as occurrence points (source, name, commodities, status, geometry)."""
    minerals = _validate_payload(payload)
    parsed = [_locality_record(loc, minerals) for loc in payload["localities"]]
    records = [rec for rec, _ in parsed if rec is not None]
    dropped = Counter(reason for rec, reason in parsed if rec is None)
    log.info("Mindat: %d of %d localities usable; dropped %s", len(records), len(parsed),
             dict(dropped) or "none")
    return userdata.points_frame(records)


# ---------- cache + entry point ----------

def _read_cache(path: Path) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        _validate_payload(payload)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"{path} is not a valid Mindat cache ({exc}); delete it to download again") from exc
    return payload


def _write_cache(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)   # atomic: an interrupted download never leaves a half-written cache


def load_localities(refresh: bool = False, get_json: JsonGetter | None = None) -> gpd.GeoDataFrame:
    """Zimbabwe Mindat localities as points; empty without MINDAT_API_KEY or on download failure.

    Downloads once and caches to `sources.RAW / "mindat_zwe.json"`; `refresh=True` re-downloads.
    """
    key = api_key()
    if key is None:
        log.warning("%s is not set; skipping Mindat occurrences (free key: mindat.org account "
                    "settings)", API_KEY_ENV)
        return userdata.points_frame([])
    cache = sources.RAW / CACHE_NAME
    if cache.is_file() and not refresh:
        return parse_localities(_read_cache(cache))
    try:
        payload = fetch_payload(get_json if get_json is not None else http_getter(key))
    except (requests.RequestException, ValueError) as exc:
        log.error("Mindat download failed (%s); continuing without Mindat points", exc)
        return userdata.points_frame([])
    _write_cache(cache, payload)
    log.info("Mindat localities cached to %s (non-commercial use only; see mindat.py)", cache)
    return parse_localities(payload)
