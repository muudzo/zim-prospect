"""Open-data sources (no API keys). Every fetch is cached under data/raw/.

Verified sources (Nov 2026):
- Boundary: geoBoundaries ZWE ADM0 (CC BY 3.0 IGO)
- Geology: USGS OFR 97-470A Africa geology, age units only (public domain)
- Occurrences: USGS MRDS (public domain) + OpenStreetMap mines/quarries (ODbL)
- (No open magnetics: EMAG2v3 has a data gap over Zimbabwe.)
- DEM: Copernicus GLO-90 / GLO-30 COGs on AWS (free, attribution required)
- Imagery: Sentinel-2 L2A COGs via Element84 Earth Search STAC (open)
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import re
import zipfile
from pathlib import Path
from typing import Iterable, Sequence

import geopandas as gpd
import pandas as pd
import requests

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR_ENV = "ZIMPROSPECT_DATA_DIR"


def data_dir() -> Path:
    """Data root: $ZIMPROSPECT_DATA_DIR if set, else <project>/data (holds raw/ and cache/)."""
    value = os.environ.get(DATA_DIR_ENV, "").strip()
    return Path(value).expanduser().resolve() if value else PROJECT_ROOT / "data"


DATA_DIR = data_dir()
RAW = DATA_DIR / "raw"
USER_AGENT = "zim-prospect/0.1 (open-data mineral prospectivity research)"
TIMEOUT = 120

GEOBOUNDARIES_API = "https://www.geoboundaries.org/api/current/gbOpen/ZWE/ADM0/"
NATURAL_EARTH = "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries.zip"
USGS_AFRICA_ZIP = "https://pubs.usgs.gov/of/1997/0470a/ofr97470A.zip"
MRDS_CSV_ZIP = "https://mrdata.usgs.gov/mrds/mrds-csv.zip"
OVERPASS = "https://overpass-api.de/api/interpreter"
COP90_URL = ("https://copernicus-dem-90m.s3.amazonaws.com/Copernicus_DSM_COG_30_{ns}{lat:02d}_00_"
             "{ew}{lon:03d}_00_DEM/Copernicus_DSM_COG_30_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif")
# Same layout with 1 arc-second tiles (verified with an HTTP HEAD on S18 E031, Nov 2026).
COP30_URL = ("https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{ns}{lat:02d}_00_"
             "{ew}{lon:03d}_00_DEM/Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif")
COP30_BELOW_RES_M = 200.0  # grids finer than this use GLO-30 tiles, coarser ones GLO-90
EARTH_SEARCH = "https://earth-search.aws.element84.com/v1/search"
S2_COLLECTION = "sentinel-2-l2a"
STAC_PAGE_LIMIT = 200
S2_DEFAULT_NODATA_PCT = 50.0  # assumed when an item lacks s2:nodata_pixel_percentage
S2_CLOUD_PENALTY = 5.0        # one percent cloud costs as much as five percent empty pixels
_ORBIT = re.compile(r"_R(\d{3})_")  # relative orbit inside s2:product_uri

# USGS Africa map is age-only. Map its GLG codes to the classes the models use.
GEOLOGY_CLASSES = ["basement", "karoo_lower", "karoo_upper", "karoo_volcanic", "cover", "water"]
GLG_TO_CLASS = {
    "pCm": "basement", "pC": "basement", "A": "basement",
    "PC": "karoo_lower",                       # Permo-Carboniferous: Dwyka/Ecca, coal host
    "TrP": "karoo_upper", "Tr": "karoo_upper", "JTr": "karoo_upper", "J": "karoo_upper",
    "Mi": "karoo_volcanic",                    # Mesozoic igneous: Karoo basalts/dolerites
    "QT": "cover", "Qe": "cover", "Qp": "cover", "Q": "cover", "T": "cover",
    "K": "cover", "Kl": "cover", "JC": "cover",
    "H2O": "water",
}


def _get(url: str, **kw) -> requests.Response:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, **kw)
    resp.raise_for_status()
    return resp


def boundary() -> gpd.GeoDataFrame:
    """Zimbabwe national boundary (EPSG:4326)."""
    path = RAW / "zwe_adm0.geojson"
    if not path.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        try:
            meta = _get(GEOBOUNDARIES_API).json()
            path.write_bytes(_get(meta["gjDownloadURL"]).content)
        except (requests.RequestException, KeyError, ValueError) as exc:
            log.warning("geoBoundaries failed (%s); falling back to Natural Earth", exc)
            ne = gpd.read_file(io.BytesIO(_get(NATURAL_EARTH).content))
            ne[ne["ADM0_A3"] == "ZWE"][["geometry"]].to_file(path, driver="GeoJSON")
    return gpd.read_file(path).to_crs(4326)[["geometry"]]


def geology() -> gpd.GeoDataFrame:
    """USGS Africa geology polygons clipped to Zimbabwe, with a `geo_class` column."""
    shp = RAW / "usgs_africa" / "geo2_7g.shp"
    if not shp.exists():
        _extract_usgs_geology(shp.parent)
    gdf = gpd.read_file(shp).set_crs(4326, allow_override=True)
    gdf = gdf.cx[24.5:33.8, -23.0:-15.0].copy()
    unknown = sorted(set(gdf["GLG"]) - set(GLG_TO_CLASS))
    if unknown:
        log.warning("unmapped geology codes treated as 'cover': %s", unknown)
    gdf["geo_class"] = gdf["GLG"].map(GLG_TO_CLASS).fillna("cover")
    return gdf[["GLG", "geo_class", "geometry"]]


def _extract_usgs_geology(out_dir: Path) -> None:
    """Download the USGS zip (221 MB, streamed to memory) and keep only the geology layer."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("downloading USGS Africa geology (~221 MB, one-off)")
    with zipfile.ZipFile(io.BytesIO(_get(USGS_AFRICA_ZIP).content)) as zf:
        for name in zf.namelist():
            base = Path(name).name
            if name.startswith("MAC/") and base.upper().startswith("GEO2_7G."):
                (out_dir / base.lower()).write_bytes(zf.read(name))


def mrds() -> pd.DataFrame:
    """MRDS records for Zimbabwe (dep_id, site_name, latitude, longitude, commod1-3, dev_stat...)."""
    path = RAW / "mrds" / "mrds_zwe.csv"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("downloading MRDS global CSV (~26 MB zip)")
        with zipfile.ZipFile(io.BytesIO(_get(MRDS_CSV_ZIP).content)) as zf:
            csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
            with zf.open(csv_name) as fh:
                df = pd.read_csv(fh, low_memory=False, encoding="latin-1")
        df[df["country"].astype(str).str.contains("Zimbabwe", case=False)].to_csv(path, index=False)
    return pd.read_csv(path, low_memory=False)


def osm_mines() -> list[dict]:
    """OpenStreetMap mines, shafts and quarries inside Zimbabwe (Overpass JSON elements)."""
    path = RAW / "osm_mines.json"
    if not path.exists():
        query = """[out:json][timeout:120];
area["ISO3166-1"="ZW"][admin_level=2]->.z;
(nwr["landuse"="quarry"](area.z); nwr["industrial"~"mine|mining"](area.z);
 nwr["man_made"~"mineshaft|adit"](area.z); nwr["historic"~"mine|mine_shaft"](area.z);
 nwr["resource"](area.z); nwr["name"~"[Mm]ine$"](area.z););
out center tags;"""
        resp = requests.post(OVERPASS, data={"data": query},
                             headers={"User-Agent": USER_AGENT}, timeout=180)
        resp.raise_for_status()
        path.write_text(json.dumps(resp.json()["elements"]))
    return json.loads(path.read_text())


def _dem_tile_urls(template: str, west: int, south: int, east: int, north: int) -> list[str]:
    """1x1 degree tile URLs covering integer-degree bounds (tile named by its south-west corner)."""
    return [template.format(ns="S" if lat < 0 else "N", lat=abs(lat), ew="W" if lon < 0 else "E", lon=abs(lon))
            for lat in range(south, north) for lon in range(west, east)]


def cop90_tile_urls(west: int, south: int, east: int, north: int) -> list[str]:
    """Copernicus GLO-90 tile URLs covering integer-degree bounds (tile named by its south edge)."""
    return _dem_tile_urls(COP90_URL, west, south, east, north)


def cop_dem_urls(west: float, south: float, east: float, north: float, res_m: float) -> list[str]:
    """Copernicus DEM tiles intersecting lon/lat bounds: GLO-30 when res_m < 200 m, else GLO-90."""
    if not (west < east and south < north):
        raise ValueError(f"invalid lon/lat bounds: {(west, south, east, north)}")
    if res_m <= 0:
        raise ValueError("res_m must be positive")
    template = COP30_URL if res_m < COP30_BELOW_RES_M else COP90_URL
    return _dem_tile_urls(template, math.floor(west), math.floor(south), math.ceil(east), math.ceil(north))


# ---------- Sentinel-2 scene search and selection ----------

def _valid_item(feat: object) -> bool:
    return (isinstance(feat, dict) and isinstance(feat.get("id"), str)
            and isinstance(feat.get("properties"), dict) and "eo:cloud_cover" in feat["properties"])


def s2_search(bbox: Sequence[float], start: str, end: str, max_cloud: float = 5.0) -> list[dict]:
    """All Sentinel-2 L2A items over `bbox` between `start` and `end` (ISO dates) below `max_cloud`%."""
    body: dict = {"collections": [S2_COLLECTION], "bbox": list(bbox),
                  "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
                  "query": {"eo:cloud_cover": {"lt": max_cloud}}, "limit": STAC_PAGE_LIMIT}
    items: list[dict] = []
    url: str | None = EARTH_SEARCH
    while url:
        resp = requests.post(url, json=body, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
        page = resp.json()
        if not isinstance(page, dict) or not isinstance(page.get("features"), list):
            raise ValueError(f"unexpected STAC response from {url}: no 'features' list")
        good = [f for f in page["features"] if _valid_item(f)]
        if len(good) < len(page["features"]):
            log.warning("dropped %d malformed STAC items", len(page["features"]) - len(good))
        items.extend(good)
        nxt = next((ln for ln in page.get("links", []) if ln.get("rel") == "next"), None)
        url, body = (nxt["href"], nxt.get("body", body)) if nxt else (None, body)
    return items


def s2_group_key(feat: dict) -> str:
    """MGRS tile + relative orbit: swath-edge tiles need scenes from both neighbouring orbits."""
    props = feat["properties"]
    tile = props.get("grid:code") or props.get("s2:mgrs_tile") or feat["id"]
    orbit = _ORBIT.search(str(props.get("s2:product_uri", "")))
    return f"{tile}/{orbit.group(1) if orbit else ''}"


def s2_badness(feat: dict) -> float:
    """Ranking score (lower is better): percent empty pixels + 5 x percent cloud."""
    props = feat["properties"]
    nodata = float(props.get("s2:nodata_pixel_percentage", S2_DEFAULT_NODATA_PCT))
    return nodata + S2_CLOUD_PENALTY * float(props["eo:cloud_cover"])


def select_best(feats: Iterable[dict], per_tile: int) -> list[dict]:
    """Best `per_tile` items per MGRS tile and orbit (duplicates by id dropped, first kept)."""
    by_group: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for feat in feats:
        if feat["id"] in seen:
            continue
        seen.add(feat["id"])
        by_group.setdefault(s2_group_key(feat), []).append(feat)
    return [f for group in by_group.values() for f in sorted(group, key=s2_badness)[:per_tile]]


def s2_scenes(bbox: tuple[float, float, float, float], start: str, end: str,
              max_cloud: float = 5.0, per_tile: int = 2) -> list[dict]:
    """Best `per_tile` Sentinel-2 L2A scenes per MGRS tile and orbit, ranked by empty pixels + cloud."""
    return select_best(s2_search(bbox, start, end, max_cloud), per_tile)


def s2_scenes_multi(bbox: tuple[float, float, float, float], seasons: Sequence[tuple[str, str]],
                    max_cloud: float = 5.0, per_tile: int = 2) -> list[dict]:
    """Like `s2_scenes`, but pools the candidates of several date windows before ranking."""
    pooled = [feat for start, end in seasons for feat in s2_search(bbox, start, end, max_cloud)]
    return select_best(pooled, per_tile)
