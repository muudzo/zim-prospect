"""User-supplied data plug-ins (U4): config, layers, occurrences and model wiring.

All inputs are tiny synthetic files written to tmp_path; nothing touches the network.
"""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from affine import Affine
from shapely.geometry import LineString, Point, box

from zimprospect import models, occurrences, sources, userdata
from zimprospect.features import FeatureStack
from zimprospect.fuzzy import class_lookup
from zimprospect.grid import make_grid

ROOT = Path(__file__).resolve().parent.parent
WEST, SOUTH, EAST, NORTH = 29.0, -19.5, 29.5, -19.0
MID_LON = 29.25


def _grid():
    return make_grid(gpd.GeoDataFrame(geometry=[box(WEST, SOUTH, EAST, NORTH)], crs=4326), 1000, 0)


def _cfg(tmp_path: Path, text: str | None = None):
    path = tmp_path / "user_data.yaml"
    if text is not None:
        path.write_text(text)
    return userdata.load_config(path, root=tmp_path / "user")


def _write_geology(root: Path, column: str = "UNIT", units=("Bulawayan basalt", "Chilimanzi granite")) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    gdf = gpd.GeoDataFrame({column: list(units)},
                           geometry=[box(WEST, SOUTH, MID_LON, NORTH), box(MID_LON, SOUTH, EAST, NORTH)],
                           crs=4326)
    path = root / "geology.gpkg"
    gdf.to_file(path, driver="GPKG")
    return path


GEOLOGY_YAML = """
user_geology:
  column: UNIT
  classes:
    Bulawayan basalt: greenstone_mafic
    Chilimanzi granite: granite_gneiss
"""


def _write_raster(path: Path, crs: str | None = "EPSG:4326", bounds=(WEST, SOUTH, EAST, NORTH),
                  shape=(50, 50)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = shape
    west, south, east, north = bounds
    transform = Affine((east - west) / w, 0, west, 0, -(north - south) / h, north)
    cols = np.tile(np.arange(w, dtype="float32"), (h, 1))   # values increase eastwards
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32",
                       crs=crs, transform=transform, nodata=-9999.0) as ds:
        ds.write(cols, 1)


# ---------- config ----------

def test_load_config_defaults_when_file_missing(tmp_path):
    cfg = _cfg(tmp_path)
    assert cfg.root == tmp_path / "user"
    assert cfg.geology_column == "lith" and cfg.geology_classes == ()
    assert cfg.raster_resampling == "bilinear"
    assert cfg.occurrences_file == tmp_path / "user" / "occurrences.csv"


def test_load_config_parses_blocks_and_is_frozen(tmp_path):
    cfg = _cfg(tmp_path, GEOLOGY_YAML + "user_rasters: {resampling: average}\n")
    assert cfg.geology_column == "UNIT"
    assert dict(cfg.geology_classes) == {"Bulawayan basalt": "greenstone_mafic",
                                         "Chilimanzi granite": "granite_gneiss"}
    assert cfg.raster_resampling == "average"
    with pytest.raises(AttributeError):
        cfg.geology_column = "x"  # type: ignore[misc]


@pytest.mark.parametrize("text, msg", [
    ("- just a list\n", "mapping"),
    ("user_geologee: {}\n", "unknown"),
    ("user_geology: {colum: UNIT}\n", "unknown"),
    ("user_rasters: {resampling: lanczos9}\n", "resampling"),
    ("user_geology: {classes: {A: others}}\n", "class name"),
    ("user_geology: {classes: {A: 'Not Valid!'}}\n", "class name"),
    ("user_geology: {classes: [a, b]}\n", "mapping"),
])
def test_load_config_rejects_bad_input(tmp_path, text, msg):
    with pytest.raises(userdata.UserDataError, match=msg):
        _cfg(tmp_path, text)


def test_example_config_is_valid(tmp_path):
    cfg = userdata.load_config(ROOT / "config" / "user_data.example.yaml", root=tmp_path)
    assert "greenstone_mafic" in cfg.lith_classes and cfg.geology_classes


def test_lith_classes_start_with_canonical_then_sorted_extras(tmp_path):
    cfg = _cfg(tmp_path, "user_geology: {classes: {X: zeta_rock, Y: alpha_rock, Z: bif}}\n")
    assert cfg.lith_classes[:len(userdata.LITH_CLASSES)] == userdata.LITH_CLASSES
    assert cfg.lith_classes[len(userdata.LITH_CLASSES):] == ("alpha_rock", "zeta_rock")
    assert userdata.class_names(cfg) == {"user_lith": list(cfg.lith_classes)}


# ---------- layers ----------

def test_user_layers_empty_without_user_data(tmp_path):
    assert userdata.user_layers(_grid(), _cfg(tmp_path)) == {}


def test_geology_layers_classes_and_greenstone_contact(tmp_path):
    g = _grid()
    _write_geology(tmp_path / "user")
    cfg = _cfg(tmp_path, GEOLOGY_YAML)
    layers = userdata.user_layers(g, cfg)
    assert set(layers) == {"user_lith", "user_dist_greenstone_contact"}
    lith = layers["user_lith"]
    names = cfg.lith_classes
    mafic, granite = names.index("greenstone_mafic"), names.index("granite_gneiss")
    mid = g.shape[0] // 2
    assert lith[mid, 2] == mafic and lith[mid, g.shape[1] - 3] == granite
    assert np.all(np.isnan(lith[~g.mask]))
    dist = layers["user_dist_greenstone_contact"]
    assert np.nanmin(dist) == 0.0
    assert dist[mid, 2] > dist[mid, g.shape[1] // 2 - 1]   # further from the contact is larger
    assert lith.dtype == np.float32 and dist.dtype == np.float32


def test_geology_unmapped_units_are_nan_and_logged(tmp_path, caplog):
    g = _grid()
    _write_geology(tmp_path / "user", units=("Bulawayan basalt", "Mystery unit"))
    with caplog.at_level(logging.WARNING, logger="zimprospect.userdata"):
        layers = userdata.user_layers(g, _cfg(tmp_path, GEOLOGY_YAML))
    lith = layers["user_lith"]
    assert np.isnan(lith[g.shape[0] // 2, g.shape[1] - 3])
    assert "Mystery unit" in caplog.text
    assert "user_dist_greenstone_contact" not in layers     # no granite: no contact layer


def test_geology_identity_mapping_without_classes(tmp_path):
    g = _grid()
    _write_geology(tmp_path / "user", column="lith", units=("greenstone_ultramafic", "granite_gneiss"))
    cfg = _cfg(tmp_path)
    lith = userdata.user_layers(g, cfg)["user_lith"]
    assert lith[g.shape[0] // 2, 2] == cfg.lith_classes.index("greenstone_ultramafic")


def test_geology_shapefile_fallback(tmp_path):
    g = _grid()
    root = tmp_path / "user"
    root.mkdir()
    gpd.GeoDataFrame({"lith": ["bif"]}, geometry=[box(WEST, SOUTH, EAST, NORTH)], crs=4326).to_file(
        root / "geology.shp")
    assert "user_lith" in userdata.user_layers(g, _cfg(tmp_path))


def test_geology_missing_column_fails_clearly(tmp_path):
    _write_geology(tmp_path / "user", column="OTHER")
    with pytest.raises(userdata.UserDataError, match="column 'UNIT'"):
        userdata.user_layers(_grid(), _cfg(tmp_path, GEOLOGY_YAML))


@pytest.mark.filterwarnings("ignore:'crs' was not provided")
def test_vector_without_crs_fails_clearly(tmp_path):
    root = tmp_path / "user"
    root.mkdir()
    gpd.GeoDataFrame({"lith": ["bif"]}, geometry=[box(WEST, SOUTH, EAST, NORTH)]).to_file(
        root / "geology.gpkg", driver="GPKG")
    with pytest.raises(userdata.UserDataError, match="CRS"):
        userdata.user_layers(_grid(), _cfg(tmp_path))


def test_fault_distance_layer(tmp_path):
    g = _grid()
    root = tmp_path / "user"
    root.mkdir()
    gpd.GeoDataFrame(geometry=[LineString([(MID_LON, SOUTH), (MID_LON, NORTH)])], crs=4326).to_file(
        root / "faults.gpkg", driver="GPKG")
    layers = userdata.user_layers(g, _cfg(tmp_path))
    dist = layers["user_dist_fault"]
    mid = g.shape[0] // 2
    assert np.nanmin(dist) == 0.0 and dist[mid, 0] > 20 and dist[mid, g.shape[1] // 2] < 2
    assert np.all(np.isnan(dist[~g.mask]))


def test_raster_warped_to_grid_with_sanitised_name(tmp_path):
    g = _grid()
    _write_raster(tmp_path / "user" / "rasters" / "Mag TMI-RTP.tif")
    layers = userdata.user_layers(g, _cfg(tmp_path))
    assert list(layers) == ["user_mag_tmi_rtp"]
    arr = layers["user_mag_tmi_rtp"]
    mid = g.shape[0] // 2
    assert arr.dtype == np.float32 and np.all(np.isnan(arr[~g.mask]))
    row = arr[mid][np.isfinite(arr[mid])]
    assert row.size > 40 and np.all(np.diff(row) > 0)        # eastward gradient survives the warp
    assert 0 <= np.nanmin(arr) and np.nanmax(arr) <= 49


def test_raster_projected_crs_and_partial_cover(tmp_path):
    g = _grid()
    _write_raster(tmp_path / "user" / "rasters" / "rad_k.tif",
                  bounds=(WEST, SOUTH, MID_LON, NORTH))          # covers the west half only
    arr = userdata.user_layers(g, _cfg(tmp_path))["user_rad_k"]
    mid = g.shape[0] // 2
    assert np.isfinite(arr[mid, 2]) and np.isnan(arr[mid, g.shape[1] - 3])


def test_raster_without_crs_fails_clearly(tmp_path):
    _write_raster(tmp_path / "user" / "rasters" / "bad.tif", crs=None)
    with pytest.raises(userdata.UserDataError, match="CRS"):
        userdata.user_layers(_grid(), _cfg(tmp_path))


def test_corrupt_raster_fails_clearly(tmp_path):
    rasters = tmp_path / "user" / "rasters"
    rasters.mkdir(parents=True)
    rasters.joinpath("broken.tif").write_bytes(b"this is not a GeoTIFF")
    with pytest.raises(userdata.UserDataError, match="cannot read raster"):
        userdata.user_layers(_grid(), _cfg(tmp_path))


def test_raster_outside_grid_and_hidden_files_are_skipped(tmp_path, caplog):
    rasters = tmp_path / "user" / "rasters"
    _write_raster(rasters / "far.tif", bounds=(10.0, 10.0, 11.0, 11.0))
    rasters.joinpath("._mag.tif").write_bytes(b"AppleDouble junk")
    rasters.joinpath("notes.txt").write_text("ignore me")
    with caplog.at_level(logging.WARNING, logger="zimprospect.userdata"):
        assert userdata.user_layers(_grid(), _cfg(tmp_path)) == {}
    assert "far.tif" in caplog.text


def test_raster_name_collisions_are_skipped(tmp_path, caplog):
    rasters = tmp_path / "user" / "rasters"
    _write_raster(rasters / "mag.tif")
    _write_raster(rasters / "MAG.tiff")
    _write_raster(rasters / "lith.tif")
    with caplog.at_level(logging.WARNING, logger="zimprospect.userdata"):
        layers = userdata.user_layers(_grid(), _cfg(tmp_path))
    assert set(layers) == {"user_mag"}
    assert "lith.tif" in caplog.text


def test_user_layers_do_not_mutate_config_or_grid(tmp_path):
    g = _grid()
    mask_before = g.mask.copy()
    _write_geology(tmp_path / "user")
    cfg = _cfg(tmp_path, GEOLOGY_YAML)
    cfg_before = cfg
    userdata.user_layers(g, cfg)
    assert np.array_equal(g.mask, mask_before) and cfg == cfg_before


# ---------- user occurrences ----------

CSV_HEADER = "name,lat,lon,commodities,source\n"


def test_read_user_occurrences_validates_rows(tmp_path, caplog):
    path = tmp_path / "occ.csv"
    path.write_text(CSV_HEADER
                    + "Good mine,-17.3,31.5,gold; silver,claims register\n"
                    + "Too far south,-23.0,30.0,gold,\n"
                    + "Too far east,-18.0,33.5,gold,\n"
                    + "No commodity,-18.0,30.0, ; ,\n"
                    + "Not a number,abc,30.0,gold,\n"
                    + ",-18.5,29.5,lithium,\n")
    with caplog.at_level(logging.WARNING, logger="zimprospect.userdata"):
        occ = userdata.read_user_occurrences(path)
    assert list(occ.columns) == ["source", "name", "commodities", "status", "geometry"]
    assert list(occ["name"]) == ["Good mine", "(unnamed)"]
    assert list(occ["commodities"]) == ["gold, silver", "lithium"]
    assert list(occ["source"]) == ["user:claims register", "user"]
    assert occ.crs.to_epsg() == 4326
    for reason in ("latitude", "longitude", "commodities", "not a number"):
        assert reason in caplog.text
    assert caplog.text.count("rejected") == 4


def test_read_user_occurrences_missing_columns_fail(tmp_path):
    path = tmp_path / "occ.csv"
    path.write_text("name,lat,commodities\nA,-18,gold\n")
    with pytest.raises(userdata.UserDataError, match="lon"):
        userdata.read_user_occurrences(path)


def test_read_user_occurrences_handles_bom_and_no_source_column(tmp_path):
    path = tmp_path / "occ.csv"
    path.write_bytes("﻿name,lat,lon,commodities\nA,-18,30,Chrome\n".encode("utf-8"))
    occ = userdata.read_user_occurrences(path)
    assert list(occ["source"]) == ["user"] and list(occ["commodities"]) == ["Chrome"]


def _fake_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "RAW", tmp_path / "raw")
    monkeypatch.setattr(sources, "mrds", lambda: pd.DataFrame({
        "site_name": ["Shamva"], "commod1": ["Gold"], "commod2": [None], "commod3": [None],
        "dev_stat": ["Producer"], "longitude": [31.5659], "latitude": [-17.3241]}))
    monkeypatch.setattr(sources, "osm_mines", lambda: [
        {"lat": -18.0, "lon": 30.0, "tags": {"name": "Q1", "landuse": "quarry"}}])
    monkeypatch.setattr(sources, "boundary", lambda: gpd.GeoDataFrame(
        geometry=[box(25.0, -22.5, 33.1, -15.5)], crs=4326))
    monkeypatch.delenv("MINDAT_API_KEY", raising=False)


def test_load_all_without_user_data_is_unchanged(monkeypatch, tmp_path):
    _fake_sources(monkeypatch, tmp_path)
    occ = occurrences.load_all(_cfg(tmp_path))
    assert list(occ.columns) == ["source", "name", "commodities", "status", "geometry"]
    assert list(occ["source"]) == ["MRDS", "OSM"] and list(occ.index) == [0, 1]


def test_load_all_merges_user_points_and_drops_near_duplicates(monkeypatch, tmp_path):
    _fake_sources(monkeypatch, tmp_path)
    user = tmp_path / "user"
    user.mkdir()
    # ~50 m from the MRDS Shamva point -> dropped; two new points 30 m apart -> one kept;
    # one point outside the boundary box filter is rejected by validation.
    user.joinpath("occurrences.csv").write_text(
        CSV_HEADER
        + "Shamva dup,-17.32365,31.5659,gold,\n"
        + "New A,-19.0,29.0,lithium; tantalum,\n"
        + "New A twin,-19.00027,29.0,lithium,\n"
        + "New B,-20.0,30.5,emerald,\n"
        + "Bad,-30.0,30.5,emerald,\n")
    occ = occurrences.load_all(_cfg(tmp_path))
    assert list(occ["source"]) == ["MRDS", "OSM", "user", "user"]
    assert list(occ["name"][2:]) == ["New A", "New B"]
    assert list(occ.index) == [0, 1, 2, 3]
    assert len(occurrences.select(occ, ["lithium"])) == 1


def test_load_all_merges_mindat_after_user(monkeypatch, tmp_path):
    _fake_sources(monkeypatch, tmp_path)
    mindat_pts = gpd.GeoDataFrame(
        {"source": ["Mindat", "Mindat"], "name": ["Shamva Mine", "Bikita"],
         "commodities": ["Gold", "Petalite, Spodumene"], "status": ["Mindat locality"] * 2},
        geometry=[Point(31.5659, -17.3241), Point(31.9, -20.08)], crs=4326)
    seen = []
    monkeypatch.setattr(occurrences.mindat, "load_localities",
                        lambda refresh=False: seen.append(refresh) or mindat_pts)
    occ = occurrences.load_all(_cfg(tmp_path), refresh=True)
    assert list(occ["source"]) == ["MRDS", "OSM", "Mindat"]
    assert list(occurrences.select(occ, ["petalite"])["name"]) == ["Bikita"]
    assert seen == [True]


def test_drop_near_is_greedy_and_pure():
    existing = gpd.GeoDataFrame(geometry=[Point(30.0, -18.0)], crs=4326)
    new = gpd.GeoDataFrame({"name": ["near", "far1", "far1-twin", "far2"]},
                           geometry=[Point(30.0005, -18.0), Point(30.1, -18.0),
                                     Point(30.1005, -18.0), Point(30.2, -18.0)], crs=4326)
    before = new.copy()
    kept = occurrences.drop_near(existing, new, radius_m=100.0)
    assert list(kept["name"]) == ["far1", "far2"]
    pd.testing.assert_frame_equal(new, before)


# ---------- models: class layers + random forest features ----------

def _stack(g, extra: dict | None = None) -> FeatureStack:
    rng = np.random.default_rng(0)
    layers = {n: np.where(g.mask, rng.random(g.shape), np.nan).astype("float32") for n in models.CONTINUOUS}
    geo = np.where(g.mask, rng.integers(0, len(sources.GEOLOGY_CLASSES), g.shape), np.nan)
    layers["geo_class"] = geo.astype("float32")
    return FeatureStack(grid=g, layers={**layers, **(extra or {})})


def test_evidence_layer_geo_unchanged():
    g = _grid()
    stack = _stack(g)
    weights = {"basement": 1.0, "others": 0.1}
    expected = class_lookup(np.nan_to_num(stack.layers["geo_class"], nan=-1).astype(int),
                            sources.GEOLOGY_CLASSES, weights)
    out = models.evidence_layer({"layer": "geo", "weights": weights}, stack)
    np.testing.assert_array_equal(out, expected)
    alias = models.evidence_layer({"layer": "geo_class", "weights": weights}, stack)
    np.testing.assert_array_equal(alias, expected)


def test_evidence_layer_any_class_layer():
    g = _grid()
    lith = np.where(g.mask, 1.0, np.nan).astype("float32")
    lith[0:5, :] = np.where(g.mask[0:5, :], 0.0, np.nan)
    stack = _stack(g, {"user_lith": lith})
    names = {"user_lith": ["greenstone_mafic", "granite_gneiss"]}
    entry = {"layer": "user_lith", "weights": {"greenstone_mafic": 0.9, "granite_gneiss": 0.2}}
    out = models.evidence_layer(entry, stack, class_names=names)
    assert out[2, 2] == pytest.approx(0.9) and out[20, 20] == pytest.approx(0.2)
    assert np.all(np.isnan(out[~g.mask]))
    with pytest.raises(KeyError, match="class list"):
        models.evidence_layer(entry, stack)
    with pytest.raises(ValueError, match="unknown classes"):
        models.evidence_layer({"layer": "user_lith", "weights": {"nope": 1.0}}, stack, class_names=names)


def test_evidence_layer_rejects_codes_beyond_class_list():
    g = _grid()
    stack = _stack(g, {"user_lith": np.where(g.mask, 5.0, np.nan).astype("float32")})
    with pytest.raises(ValueError, match="code"):
        models.evidence_layer({"layer": "user_lith", "weights": {"a": 1.0}}, stack,
                              class_names={"user_lith": ["a", "b"]})


def test_fuzzy_model_class_names_and_optional_entries():
    g = _grid()
    stack = _stack(g, {"user_lith": np.where(g.mask, 0.0, np.nan).astype("float32")})
    base = {"evidence": [{"layer": "geo", "weights": {"basement": 1.0, "others": 0.1}}]}
    with_optional = {"evidence": base["evidence"] + [{"layer": "user_magnetics", "direction": "high",
                                                     "optional": True}]}
    np.testing.assert_array_equal(models.fuzzy_model(base, stack, 0.8),
                                  models.fuzzy_model(with_optional, stack, 0.8))
    spec = {"evidence": base["evidence"] + [{"layer": "user_lith", "weights": {"a": 1.0}}]}
    out = models.fuzzy_model(spec, stack, 0.8, class_names={"user_lith": ["a"]})
    assert np.all(np.isfinite(out[g.mask]))
    with pytest.raises(KeyError):
        models.fuzzy_model({"evidence": [{"layer": "user_magnetics", "direction": "high"}]}, stack, 0.8)


def test_feature_matrix_without_user_layers_matches_baseline_layout():
    g = _grid()
    stack = _stack(g)
    X, names = models.feature_matrix(stack)
    cols = [np.where(np.isfinite(stack.layers[n][g.mask]), stack.layers[n][g.mask],
                     np.nanmedian(stack.layers[n][g.mask])) for n in models.CONTINUOUS]
    geo = stack.layers["geo_class"][g.mask]
    cols += [(geo == i).astype("float32") for i in range(len(sources.GEOLOGY_CLASSES))]
    np.testing.assert_array_equal(X, np.column_stack(cols).astype("float32"))
    assert names == list(models.CONTINUOUS) + [f"geo_{c}" for c in sources.GEOLOGY_CLASSES]


def test_feature_matrix_adds_user_continuous_and_one_hot_class_layers():
    g = _grid()
    lith = np.where(g.mask, 1.0, np.nan).astype("float32")
    extra = {"user_mag": np.where(g.mask, 1.0, np.nan).astype("float32"),
             "user_lith": lith,
             "user_empty": np.full(g.shape, np.nan, dtype="float32"),
             "not_user_layer": np.zeros(g.shape, dtype="float32")}
    X, names = models.feature_matrix(_stack(g, extra), class_names={"user_lith": ["mafic", "granite"]})
    assert "user_mag" in names and "user_empty" in names and "not_user_layer" not in names
    assert "user_lith" not in names
    assert names[-2:] == ["user_lith_mafic", "user_lith_granite"]
    assert np.all(np.isfinite(X))
    assert X[:, names.index("user_lith_granite")].all()


def test_feature_matrix_class_layer_without_names_uses_codes():
    g = _grid()
    extra = {"user_lith": np.where(g.mask, 2.0, np.nan).astype("float32")}
    _, names = models.feature_matrix(_stack(g, extra))
    assert names[-3:] == ["user_lith_0", "user_lith_1", "user_lith_2"]


def test_random_forest_uses_user_raster_feature():
    g = _grid()
    rows, cols = np.nonzero(g.mask)
    pick = np.arange(0, len(rows), 40)[:30]
    signal = np.where(g.mask, 0.0, np.nan).astype("float32")
    signal[rows[pick], cols[pick]] = 1.0
    stack = _stack(g, {"user_mag": signal})
    rf = models.random_forest(stack, rows[pick], cols[pick], 10, 5, class_names={})
    assert "user_mag" in rf.importances
    assert max(rf.importances, key=rf.importances.get) == "user_mag"
    assert rf.full_map.shape == g.shape
