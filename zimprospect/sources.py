"""Open-data sources (no API keys). Every fetch is cached under data/raw/.

Verified sources (Nov 2026):
- Boundary: geoBoundaries ZWE ADM0 (CC BY 3.0 IGO)
- Geology: USGS OFR 97-470A Africa geology, age units only (public domain)
- Occurrences: USGS MRDS (public domain) + OpenStreetMap mines/quarries (ODbL)
- (No open magnetics: EMAG2v3 has a data gap over Zimbabwe.)
- DEM: Copernicus GLO-90 COGs on AWS (free, attribution required)
- Imagery: Sentinel-2 L2A COGs via Element84 Earth Search STAC (open)
"""
from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

log = logging.getLogger(__name__)

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
USER_AGENT = "zim-prospect/0.1 (open-data mineral prospectivity research)"
TIMEOUT = 120

GEOBOUNDARIES_API = "https://www.geoboundaries.org/api/current/gbOpen/ZWE/ADM0/"
NATURAL_EARTH = "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries.zip"
USGS_AFRICA_ZIP = "https://pubs.usgs.gov/of/1997/0470a/ofr97470A.zip"
MRDS_CSV_ZIP = "https://mrdata.usgs.gov/mrds/mrds-csv.zip"
OVERPASS = "https://overpass-api.de/api/interpreter"
COP90_URL = ("https://copernicus-dem-90m.s3.amazonaws.com/Copernicus_DSM_COG_30_{ns}{lat:02d}_00_"
             "{ew}{lon:03d}_00_DEM/Copernicus_DSM_COG_30_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif")
EARTH_SEARCH = "https://earth-search.aws.element84.com/v1/search"
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


def cop90_tile_urls(west: int, south: int, east: int, north: int) -> list[str]:
    """Copernicus GLO-90 tile URLs covering integer-degree bounds (tile named by its south edge)."""
    urls = []
    for lat in range(south, north):
        for lon in range(west, east):
            urls.append(COP90_URL.format(ns="S" if lat < 0 else "N", lat=abs(lat),
                                         ew="W" if lon < 0 else "E", lon=abs(lon)))
    return urls


def s2_scenes(bbox: tuple[float, float, float, float], start: str, end: str,
              max_cloud: float = 5.0, per_tile: int = 2) -> list[dict]:
    """Best `per_tile` Sentinel-2 L2A scenes per MGRS tile and orbit, ranked by empty pixels + cloud."""
    by_tile: dict[str, list[dict]] = {}
    body = {"collections": ["sentinel-2-l2a"], "bbox": list(bbox),
            "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": max_cloud}}, "limit": 200}
    url: str | None = EARTH_SEARCH
    while url:
        resp = requests.post(url, json=body, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
        page = resp.json()
        for feat in page["features"]:
            props = feat["properties"]
            tile = props.get("grid:code") or props.get("s2:mgrs_tile") or feat["id"]
            # Group by orbit too: swath-edge tiles need scenes from both neighbouring orbits.
            orbit = _ORBIT.search(str(props.get("s2:product_uri", "")))
            by_tile.setdefault(f"{tile}/{orbit.group(1) if orbit else ''}", []).append(feat)
        nxt = next((ln for ln in page.get("links", []) if ln.get("rel") == "next"), None)
        url, body = (nxt["href"], nxt.get("body", body)) if nxt else (None, body)

    def badness(feat: dict) -> float:
        props = feat["properties"]
        return float(props.get("s2:nodata_pixel_percentage", 50)) + 5 * float(props["eo:cloud_cover"])

    return [f for feats in by_tile.values() for f in sorted(feats, key=badness)[:per_tile]]
