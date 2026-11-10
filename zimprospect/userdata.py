"""User-supplied data plug-ins (U4): geology, faults, rasters and occurrence lists.

Everything here is optional. Files live in `<data dir>/user/` (the folder next to
`sources.RAW`, so it follows ZIMPROSPECT_DATA_DIR) and must never be committed:

    user/geology.gpkg (or .shp)   polygons with a map-unit column  -> user_lith,
                                                                       user_dist_greenstone_contact
    user/faults.gpkg (or .shp)    fault lines                       -> user_dist_fault
    user/rasters/*.tif            continuous grids (aeromagnetics,   -> user_<file stem>
                                  radiometrics...), warped to the grid
    user/occurrences.csv          name, lat, lon, commodities (';'-separated), optional source

An optional `config/user_data.yaml` (see `config/user_data.example.yaml`) sets paths, the
map-unit column and the {map unit: lith class} mapping. With no user files every function
returns an empty result, so pipeline outputs stay exactly as they were.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import yaml
from pyogrio.errors import DataLayerError, DataSourceError
from rasterio.warp import Resampling, reproject

from . import sources
from .grid import Grid, contact_cells, distance_km, rasterize_presence, rasterize_values

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "user_data.yaml"
USER_DIR_NAME = "user"

PREFIX = "user_"
LITH_LAYER = "user_lith"
CONTACT_LAYER = "user_dist_greenstone_contact"
FAULT_LAYER = "user_dist_fault"
RESERVED_LAYERS = frozenset({LITH_LAYER, CONTACT_LAYER, FAULT_LAYER})

# Canonical lith classes come first so their codes stay stable; extra classes named in the
# config are appended in sorted order.
LITH_CLASSES = ("greenstone_mafic", "greenstone_ultramafic", "greenstone_felsic", "bif",
                "granite_gneiss", "great_dyke")
GREENSTONE_PREFIX = "greenstone_"
GRANITE_CLASSES = frozenset({"granite_gneiss"})
RESERVED_CLASS_NAMES = frozenset({"others", "rest"})   # fallback keys in fuzzy.class_lookup
_CLASS_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
DEFAULT_UNIT_COLUMN = "lith"

GEOLOGY_FILES = ("geology.gpkg", "geology.shp")
FAULT_FILES = ("faults.gpkg", "faults.shp")
POLYGON_TYPES = ("Polygon", "MultiPolygon")
LINE_TYPES = ("LineString", "MultiLineString")
RASTER_DIR = "rasters"
RASTER_SUFFIXES = frozenset({".tif", ".tiff"})
RESAMPLING = {"nearest": Resampling.nearest, "bilinear": Resampling.bilinear,
              "cubic": Resampling.cubic, "average": Resampling.average}
DEFAULT_RESAMPLING = "bilinear"
MAX_LOGGED_UNITS = 20

# Point validation envelope: Zimbabwe spans about -22.4..-15.6 lat and 25.2..33.1 lon.
LAT_RANGE = (-22.5, -15.5)
LON_RANGE = (25.0, 33.1)
OCCURRENCES_FILE = "occurrences.csv"
OCC_REQUIRED = ("name", "lat", "lon", "commodities")
OCC_COLUMNS = ["source", "name", "commodities", "status", "geometry"]
USER_SOURCE = "user"
USER_STATUS = "user-supplied"
UNNAMED = "(unnamed)"

_ALLOWED_KEYS: Mapping[str, frozenset[str]] = {
    "user_geology": frozenset({"path", "layer", "column", "classes"}),
    "user_faults": frozenset({"path", "layer"}),
    "user_rasters": frozenset({"dir", "resampling"}),
    "user_occurrences": frozenset({"path"}),
}


class UserDataError(ValueError):
    """User-supplied data or config is invalid; the message says what to fix."""


@dataclass(frozen=True)
class UserDataConfig:
    root: Path                                          # the user data folder
    geology_path: str | None = None                     # None: first of GEOLOGY_FILES that exists
    geology_layer: str | None = None
    geology_column: str = DEFAULT_UNIT_COLUMN
    geology_classes: tuple[tuple[str, str], ...] = ()   # (map unit, lith class); empty = identity
    faults_path: str | None = None
    faults_layer: str | None = None
    rasters_dir: str = RASTER_DIR
    raster_resampling: str = DEFAULT_RESAMPLING
    occurrences_path: str = OCCURRENCES_FILE

    @property
    def lith_classes(self) -> tuple[str, ...]:
        extras = sorted({cls for _, cls in self.geology_classes} - set(LITH_CLASSES))
        return LITH_CLASSES + tuple(extras)

    @property
    def geology_file(self) -> Path | None:
        return _existing(self.root, self.geology_path, GEOLOGY_FILES)

    @property
    def faults_file(self) -> Path | None:
        return _existing(self.root, self.faults_path, FAULT_FILES)

    @property
    def rasters_folder(self) -> Path:
        return _resolve(self.root, self.rasters_dir)

    @property
    def occurrences_file(self) -> Path:
        return _resolve(self.root, self.occurrences_path)


def _resolve(root: Path, rel: str) -> Path:
    path = Path(rel).expanduser()
    return path if path.is_absolute() else root / path


def _existing(root: Path, configured: str | None, defaults: Iterable[str]) -> Path | None:
    candidates = [configured] if configured else list(defaults)
    return next((p for p in (_resolve(root, c) for c in candidates) if p.is_file()), None)


# ---------- config ----------

def user_dir() -> Path:
    """Folder holding user-supplied data (follows sources.RAW, hence ZIMPROSPECT_DATA_DIR)."""
    return sources.RAW.parent / USER_DIR_NAME


def load_config(path: Path | None = None, *, root: Path | None = None) -> UserDataConfig:
    """Read config/user_data.yaml (or `path`); defaults when the file is missing."""
    cfg_path = path if path is not None else DEFAULT_CONFIG
    base = root if root is not None else user_dir()
    if not cfg_path.is_file():
        if path is not None:
            log.warning("user data config %s not found; using defaults", cfg_path)
        return UserDataConfig(root=base)
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise UserDataError(f"{cfg_path}: not valid YAML ({exc})") from exc
    return _parse_config({} if raw is None else raw, base, cfg_path)


def _parse_config(raw: Any, root: Path, src: Path) -> UserDataConfig:
    if not isinstance(raw, Mapping):
        raise UserDataError(f"{src}: the top level must be a mapping of blocks "
                            f"({', '.join(_ALLOWED_KEYS)})")
    unknown = sorted(set(map(str, raw)) - set(_ALLOWED_KEYS))
    if unknown:
        raise UserDataError(f"{src}: unknown block(s) {unknown}; allowed: {sorted(_ALLOWED_KEYS)}")
    geo, faults, rasters, occ = (_block(raw, key, src) for key in _ALLOWED_KEYS)
    resampling = str(rasters.get("resampling", DEFAULT_RESAMPLING)).strip().lower()
    if resampling not in RESAMPLING:
        raise UserDataError(f"{src}: user_rasters.resampling must be one of {sorted(RESAMPLING)}, "
                            f"got {resampling!r}")
    return UserDataConfig(
        root=root,
        geology_path=_opt_str(geo.get("path")), geology_layer=_opt_str(geo.get("layer")),
        geology_column=_opt_str(geo.get("column")) or DEFAULT_UNIT_COLUMN,
        geology_classes=_parse_classes(geo.get("classes"), src),
        faults_path=_opt_str(faults.get("path")), faults_layer=_opt_str(faults.get("layer")),
        rasters_dir=_opt_str(rasters.get("dir")) or RASTER_DIR, raster_resampling=resampling,
        occurrences_path=_opt_str(occ.get("path")) or OCCURRENCES_FILE)


def _block(raw: Mapping[str, Any], key: str, src: Path) -> Mapping[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, Mapping):
        raise UserDataError(f"{src}: '{key}' must be a mapping")
    unknown = sorted(set(map(str, value)) - _ALLOWED_KEYS[key])
    if unknown:
        raise UserDataError(f"{src}: unknown key(s) {unknown} in '{key}'; "
                            f"allowed: {sorted(_ALLOWED_KEYS[key])}")
    return value


def _opt_str(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _parse_classes(value: Any, src: Path) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise UserDataError(f"{src}: user_geology.classes must be a mapping {{map unit: lith class}}")
    pairs = []
    for unit, cls in value.items():
        name = str(cls).strip() if cls is not None else ""
        if not _CLASS_NAME.match(name) or name in RESERVED_CLASS_NAMES:
            raise UserDataError(f"{src}: invalid lith class name {cls!r} for unit {unit!r} "
                                "(lowercase letters, digits and '_'; not 'others' or 'rest')")
        pairs.append((str(unit).strip(), name))
    return tuple(pairs)


def class_names(ucfg: UserDataConfig) -> dict[str, list[str]]:
    """Class lists of the categorical user layers, keyed by layer name (index = class code)."""
    return {LITH_LAYER: list(ucfg.lith_classes)}


# ---------- layers ----------

def user_layers(grid: Grid, ucfg: UserDataConfig) -> dict[str, np.ndarray]:
    """All user layers whose inputs exist, on `grid` (float32, NaN outside the study area)."""
    layers = {**geology_layers(grid, ucfg), **fault_layers(grid, ucfg)}
    layers = {**layers, **raster_layers(grid, ucfg, reserved=RESERVED_LAYERS | set(layers))}
    if layers:
        log.info("user data layers: %s", ", ".join(sorted(layers)))
    return layers


def _read_vector(path: Path, layer: str | None, allowed: tuple[str, ...], what: str) -> gpd.GeoDataFrame:
    try:
        gdf = gpd.read_file(path, layer=layer)
    except (DataSourceError, DataLayerError, OSError, ValueError) as exc:
        raise UserDataError(f"cannot read {what} file {path}: {exc}") from exc
    if gdf.crs is None:
        raise UserDataError(f"{path}: {what} has no CRS; assign one (e.g. in QGIS) and save again")
    geom = gdf.geometry
    usable = (geom.notna() & ~geom.is_empty & geom.geom_type.isin(allowed)).to_numpy()
    if not usable.any():
        raise UserDataError(f"{path}: no usable {what} features ({'/'.join(allowed)} expected)")
    if not usable.all():
        log.warning("%s: ignoring %d %s feature(s) that are empty or not %s",
                    path.name, int((~usable).sum()), what, "/".join(allowed))
    return gdf[usable]


def _unit_text(values: pd.Series) -> pd.Series:
    """Map-unit labels as stripped text; integer-valued numeric codes lose their '.0'."""
    finite = values.dropna()
    if pd.api.types.is_numeric_dtype(values) and np.all(np.mod(finite, 1) == 0):
        return values.astype("Int64").astype(str)
    return values.astype(str).str.strip()


def _unit_codes(gdf: gpd.GeoDataFrame, ucfg: UserDataConfig, path: Path) -> pd.Series:
    column = ucfg.geology_column
    if column not in gdf.columns:
        raise UserDataError(f"{path}: column '{column}' not found (columns: "
                            f"{[c for c in gdf.columns if c != 'geometry']}); "
                            "set user_geology.column in config/user_data.yaml")
    names = ucfg.lith_classes
    mapping = dict(ucfg.geology_classes) or {c: c for c in names}
    units = _unit_text(gdf[column])
    lith = units.map(mapping)
    unmapped = sorted(set(units[lith.isna()]))
    if unmapped:
        log.warning("%s: %d polygon(s) with unmapped units left as no-data: %s%s", path.name,
                    int(lith.isna().sum()), unmapped[:MAX_LOGGED_UNITS],
                    " ..." if len(unmapped) > MAX_LOGGED_UNITS else "")
    if lith.isna().all():
        raise UserDataError(f"{path}: no map unit in column '{column}' matches a lith class; "
                            "check user_geology.classes")
    return lith.map({c: i for i, c in enumerate(names)})


def geology_layers(grid: Grid, ucfg: UserDataConfig) -> dict[str, np.ndarray]:
    """user_lith class codes and distance to the greenstone-granite contact (km)."""
    path = ucfg.geology_file
    if path is None:
        return {}
    gdf = _read_vector(path, ucfg.geology_layer, POLYGON_TYPES, "geology")
    codes = _unit_codes(gdf, ucfg, path)
    keep = codes.notna().to_numpy()
    classes = rasterize_values(gdf[keep], codes[keep].astype(int), grid, fill=-1)
    lith = np.where(classes >= 0, classes, np.nan).astype("float32")
    log.info("user geology: %d polygons rasterised from %s", int(keep.sum()), path.name)
    contact = _greenstone_contact(classes, grid, ucfg.lith_classes)
    return {LITH_LAYER: lith} if contact is None else {LITH_LAYER: lith, CONTACT_LAYER: contact}


def _greenstone_contact(classes: np.ndarray, grid: Grid, names: tuple[str, ...]) -> np.ndarray | None:
    greenstone = [i for i, c in enumerate(names) if c.startswith(GREENSTONE_PREFIX)]
    granite = [i for i, c in enumerate(names) if c in GRANITE_CLASSES]
    cells = contact_cells(classes, greenstone, granite)
    if not cells.any():
        log.warning("user geology has no greenstone-granite contact; %s not built", CONTACT_LAYER)
        return None
    return distance_km(cells, grid).astype("float32")


def fault_layers(grid: Grid, ucfg: UserDataConfig) -> dict[str, np.ndarray]:
    """Distance (km) to the nearest user-supplied fault line."""
    path = ucfg.faults_file
    if path is None:
        return {}
    faults = _read_vector(path, ucfg.faults_layer, LINE_TYPES, "faults")
    present = rasterize_presence(faults, grid)
    if not present.any():
        log.warning("%s: no fault crosses the analysis grid; %s not built", path.name, FAULT_LAYER)
        return {}
    log.info("user faults: %d lines from %s", len(faults), path.name)
    return {FAULT_LAYER: distance_km(present, grid).astype("float32")}


def _layer_name(path: Path) -> str | None:
    stem = re.sub(r"[^a-z0-9]+", "_", path.stem.lower()).strip("_")
    return f"{PREFIX}{stem}" if stem else None


def _raster_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in RASTER_SUFFIXES)


def warp_raster(path: Path, grid: Grid, resampling: Resampling = Resampling.bilinear) -> np.ndarray:
    """Band 1 of a local raster reprojected onto `grid` (float32, NaN for no data)."""
    out = np.full(grid.shape, np.nan, dtype="float32")
    try:
        with rasterio.open(path) as ds:
            if ds.crs is None:
                raise UserDataError(f"{path}: raster has no CRS; assign one "
                                    "(e.g. gdal_edit.py -a_srs EPSG:32736) and try again")
            if ds.count > 1:
                log.warning("%s has %d bands; using band 1 only", path.name, ds.count)
            reproject(source=rasterio.band(ds, 1), destination=out, dst_transform=grid.transform,
                      dst_crs=grid.crs, dst_nodata=np.nan, resampling=resampling)
    except rasterio.errors.RasterioError as exc:
        raise UserDataError(f"cannot read raster {path}: {exc}") from exc
    return np.where(grid.mask, out, np.nan).astype("float32")


def raster_layers(grid: Grid, ucfg: UserDataConfig,
                  reserved: frozenset[str] = RESERVED_LAYERS) -> dict[str, np.ndarray]:
    """One continuous layer `user_<stem>` per GeoTIFF in the user rasters folder."""
    folder = ucfg.rasters_folder
    if not folder.is_dir():
        return {}
    resampling = RESAMPLING[ucfg.raster_resampling]
    out: dict[str, np.ndarray] = {}
    for path in _raster_files(folder):
        name = _layer_name(path)
        if name is None or name in reserved or name in out:
            log.warning("%s: skipped (layer name %r is empty or already used)", path.name, name)
            continue
        arr = warp_raster(path, grid, resampling)
        if not np.isfinite(arr).any():
            log.warning("%s: no valid data inside the study area; skipped", path.name)
            continue
        log.info("user raster %s -> %s (%s resampling)", path.name, name, ucfg.raster_resampling)
        out = {**out, name: arr}
    return out


# ---------- occurrences ----------

def points_frame(records: list[dict[str, Any]]) -> gpd.GeoDataFrame:
    """Occurrence records (source, name, commodities, status, lat, lon) as an EPSG:4326 frame."""
    df = pd.DataFrame(records, columns=["source", "name", "commodities", "status", "lat", "lon"])
    return gpd.GeoDataFrame(df.drop(columns=["lat", "lon"]),
                            geometry=gpd.points_from_xy(df["lon"].astype(float), df["lat"].astype(float)),
                            crs=4326)


def in_envelope(lat: float, lon: float) -> bool:
    """True when the point lies inside the Zimbabwe validation envelope."""
    return LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]


def _read_csv(path: Path) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding=encoding)
            return df.rename(columns=lambda c: str(c).strip().lower())
        except UnicodeDecodeError:
            log.warning("%s is not UTF-8; reading it as latin-1", path.name)
        except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            raise UserDataError(f"{path}: cannot parse CSV ({exc})") from exc
    raise UserDataError(f"{path}: cannot decode the file as UTF-8 or latin-1")


def _parse_occurrence(rec: Mapping[str, str]) -> tuple[dict[str, Any] | None, str]:
    """(record, "") for a valid row, or (None, reason) for a rejected one."""
    try:
        lat, lon = float(rec["lat"]), float(rec["lon"])
    except ValueError:
        return None, f"lat/lon not a number ({rec['lat']!r}, {rec['lon']!r})"
    if not (np.isfinite(lat) and np.isfinite(lon)):
        return None, "lat/lon not a number"
    if not LAT_RANGE[0] <= lat <= LAT_RANGE[1]:
        return None, f"latitude {lat} outside {LAT_RANGE[0]}..{LAT_RANGE[1]}"
    if not LON_RANGE[0] <= lon <= LON_RANGE[1]:
        return None, f"longitude {lon} outside {LON_RANGE[0]}..{LON_RANGE[1]}"
    commodities = [c.strip() for c in str(rec["commodities"]).split(";") if c.strip()]
    if not commodities:
        return None, "empty commodities"
    source = str(rec.get("source", "")).strip()
    return {"source": f"{USER_SOURCE}:{source}" if source else USER_SOURCE,
            "name": str(rec["name"]).strip() or UNNAMED, "commodities": ", ".join(commodities),
            "status": USER_STATUS, "lat": lat, "lon": lon}, ""


def read_user_occurrences(path: Path) -> gpd.GeoDataFrame:
    """Validated user occurrence points; each rejected row is logged with its reason."""
    df = _read_csv(path)
    missing = [c for c in OCC_REQUIRED if c not in df.columns]
    if missing:
        raise UserDataError(f"{path}: missing column(s) {missing}; "
                            "expected name, lat, lon, commodities and optionally source")
    records = []
    for line, rec in enumerate(df.to_dict("records"), start=2):   # line 1 is the header
        parsed, reason = _parse_occurrence(rec)
        if parsed is None:
            log.warning("%s line %d (%s) rejected: %s", path.name, line,
                        str(rec["name"]).strip() or UNNAMED, reason)
        else:
            records.append(parsed)
    log.info("%s: kept %d of %d rows", path.name, len(records), len(df))
    return points_frame(records)
