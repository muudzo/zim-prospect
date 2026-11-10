"""Tests for U1 (data dir), U3 (GLO-30 terrain, area-based targets) and U5 (Sentinel-2 settings, seams).

No network: STAC responses, DEM reads and Sentinel-2 band reads are faked.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.windows import Window
from shapely.geometry import box, mapping

from zimprospect import features, s2, sources
from zimprospect.grid import ZIM_CRS, Grid, make_grid
from zimprospect.s2 import S2Settings, dry_seasons, footprint_ids, group_footprints, seam_score
from zimprospect.targets import extract_targets, min_cells_for_area

PROJECT = Path(__file__).resolve().parent.parent
SHAMVA = (-17.3241, 31.5659)  # gold target #2 in the baseline


# ---------- helpers ----------

def _local_grid(lat: float, lon: float, box_km: float, res_m: float) -> Grid:
    """Square all-True grid centred on (lat, lon), like the zoom feature's local_grid."""
    x, y = Transformer.from_crs(4326, ZIM_CRS, always_xy=True).transform(lon, lat)
    n = int(round(box_km * 1000 / res_m))
    half = n * res_m / 2
    return Grid(ZIM_CRS, Affine(res_m, 0, x - half, 0, -res_m, y + half), n, n, float(res_m),
                np.ones((n, n), dtype=bool))


def _small_grid(res_m: float = 1000) -> Grid:
    return make_grid(gpd.GeoDataFrame(geometry=[box(29.0, -19.5, 29.5, -19.0)], crs=4326), res_m, 0)


def _feat(fid: str, tile: str, orbit: int, nodata: float, cloud: float,
          footprint: tuple[float, float, float, float] = (29.0, -19.5, 29.5, -19.0)) -> dict:
    return {"id": fid, "assets": {},
            "geometry": mapping(box(*footprint)),
            "properties": {"grid:code": f"MGRS-{tile}", "eo:cloud_cover": cloud,
                           "s2:nodata_pixel_percentage": nodata, "datetime": "2025-08-01T08:00:00Z",
                           "s2:product_uri": f"S2A_MSIL2A_20250801T0806_N0511_R{orbit:03d}_T{tile}.SAFE"}}


# ---------- U1: data directory ----------

def test_data_dir_defaults_to_project_data(monkeypatch):
    monkeypatch.delenv(sources.DATA_DIR_ENV, raising=False)
    assert sources.data_dir() == PROJECT / "data"


def test_data_dir_reads_env_var_and_expands_user(monkeypatch, tmp_path):
    monkeypatch.setenv(sources.DATA_DIR_ENV, str(tmp_path))
    assert sources.data_dir() == tmp_path.resolve()
    monkeypatch.setenv(sources.DATA_DIR_ENV, "~/zp-data")
    assert sources.data_dir() == Path("~/zp-data").expanduser().resolve()


def test_env_var_moves_raw_and_cache_at_import(tmp_path):
    env = {**os.environ, "ZIMPROSPECT_DATA_DIR": str(tmp_path)}
    code = "import zimprospect.sources as s, zimprospect.features as f; print(s.RAW); print(f.CACHE)"
    out = subprocess.run([sys.executable, "-c", code], cwd=PROJECT, env=env,
                         capture_output=True, text=True, check=True).stdout.split()
    assert out == [str(tmp_path.resolve() / "raw"), str(tmp_path.resolve() / "cache")]


# ---------- U3: Copernicus DEM tiles ----------

def test_cop_dem_urls_uses_30m_tiles_below_200m():
    fine = sources.cop_dem_urls(31.2, -17.5, 31.8, -17.1, res_m=20)
    assert fine == [
        "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_S18_00_E031_00_DEM/"
        "Copernicus_DSM_COG_10_S18_00_E031_00_DEM.tif"]
    coarse = sources.cop_dem_urls(31.2, -17.5, 31.8, -17.1, res_m=250)
    assert len(coarse) == 1 and "copernicus-dem-90m" in coarse[0] and "COG_30_S18_00_E031" in coarse[0]


def test_cop_dem_urls_covers_fractional_bounds_and_matches_legacy_list():
    urls = sources.cop_dem_urls(30.95, -17.4, 31.05, -16.9, res_m=20)
    assert [u.split("/")[-1][22:40] for u in urls] == ["S18_00_E030_00_DEM", "S18_00_E031_00_DEM",
                                                       "S17_00_E030_00_DEM", "S17_00_E031_00_DEM"]
    assert sources.cop_dem_urls(25, -23, 34, -15, res_m=500) == sources.cop90_tile_urls(25, -23, 34, -15)


def test_cop_dem_urls_rejects_bad_bounds():
    with pytest.raises(ValueError):
        sources.cop_dem_urls(32, -17, 31, -16, res_m=20)
    with pytest.raises(ValueError):
        sources.cop_dem_urls(31, -17, 32, -16, res_m=0)


def _fake_dem(calls: list, missing: tuple[str, ...] = ()):
    def fake(url, grid, resampling=Resampling.average, **_kw):
        calls.append((url, resampling))
        if any(m in url for m in missing):
            raise rasterio.errors.RasterioIOError("not found")
        cols = np.arange(grid.width, dtype="float32")[None, :]
        return np.broadcast_to(1000 + 0.1 * cols * grid.res_m, grid.shape).astype("float32")
    return fake


def test_terrain_layers_on_tiny_20m_box_reads_only_intersecting_30m_tile(monkeypatch):
    grid = _local_grid(*SHAMVA, box_km=8, res_m=20)
    calls: list = []
    monkeypatch.setattr(features, "warp_to_grid", _fake_dem(calls))
    out = features.terrain_layers(grid)
    assert [u for u, _ in calls] == sources.cop_dem_urls(31.5, -17.4, 31.6, -17.3, res_m=20)
    assert all(r == Resampling.bilinear for _, r in calls)  # upsampling 30 m -> 20 m
    assert {k: v.shape for k, v in out.items()} == {k: (400, 400) for k in ("elev", "slope", "rough")}
    assert np.allclose(out["slope"][5:-5, 5:-5], np.degrees(np.arctan(0.1)), atol=1e-3)


def test_terrain_layers_skips_missing_tiles_and_fails_when_none(monkeypatch):
    grid = _local_grid(-17.5, 31.0, box_km=8, res_m=20)  # straddles 31 E: two tiles
    calls: list = []
    monkeypatch.setattr(features, "warp_to_grid", _fake_dem(calls, missing=("E030",)))
    assert np.isfinite(features.terrain_layers(grid)["elev"]).all()
    assert len(calls) == 2
    monkeypatch.setattr(features, "warp_to_grid", _fake_dem([], missing=("E03",)))
    with pytest.raises(RuntimeError, match="no Copernicus DEM tiles"):
        features.terrain_layers(grid)


def test_terrain_layers_keeps_average_resampling_on_national_grids(monkeypatch):
    calls: list = []
    monkeypatch.setattr(features, "warp_to_grid", _fake_dem(calls))
    features.terrain_layers(_small_grid(500))
    assert calls and all("copernicus-dem-90m" in u and r == Resampling.average for u, r in calls)


# ---------- U3: area-based target filter ----------

@pytest.mark.parametrize("res_m, cells", [(500, 2), (250, 8), (1000, 1), (20, 1250)])
def test_min_cells_for_area_converts_with_resolution(res_m, cells):
    assert min_cells_for_area(0.5, res_m) == cells


def test_extract_targets_drops_zones_smaller_than_min_area():
    g = Grid(ZIM_CRS, Affine(250, 0, 0, 0, -250, 0), 60, 60, 250.0, np.ones((60, 60), dtype=bool))
    score = np.full(g.shape, 0.1)
    score[5:7, 5:9] = 0.9    # 8 cells = 0.5 km2: kept
    score[30:32, 30:33] = 0.95  # 6 cells = 0.375 km2: dropped despite the higher peak
    t = extract_targets(score, g, np.array([], int), np.array([], int), top_frac=0.01)
    assert list(t["area_km2"]) == [0.5]
    assert len(extract_targets(score, g, np.array([], int), np.array([], int), top_frac=0.01,
                               min_area_km2=0.3)) == 2


# ---------- U5: settings ----------

def test_s2_settings_default_is_the_baseline():
    st = S2Settings()
    assert st.seasons == (("2025-07-15", "2025-10-15"),) and st.per_tile == 2 and st.max_cloud == 5.0
    assert st.max_depth == 24 and st.cache_tag() == "" and st == s2.LEGACY_S2
    with pytest.raises(dataclasses.FrozenInstanceError):
        st.per_tile = 6  # type: ignore[misc]


def test_s2_settings_normalises_lists_and_tags_non_default_settings():
    multi = S2Settings(seasons=[["2024-07-15", "2024-10-15"], ["2025-07-15", "2025-10-15"]], per_tile=6)
    assert multi.seasons == dry_seasons([2024, 2025]) and multi.max_depth == 72
    assert multi.cache_tag().startswith("_s2-") and multi.cache_tag() == S2Settings(
        seasons=dry_seasons((2024, 2025)), per_tile=6, max_cloud=5).cache_tag()
    assert multi.cache_tag() != S2Settings(seasons=dry_seasons((2024, 2025)), per_tile=5).cache_tag()
    assert S2Settings.from_mapping({"seasons": [["2024-07-15", "2024-10-15"]], "per_tile": 3}) == \
        S2Settings(seasons=dry_seasons([2024]), per_tile=3)


@pytest.mark.parametrize("kwargs", [
    {"seasons": ()}, {"per_tile": 0}, {"max_cloud": 0}, {"max_cloud": 101},
    {"seasons": (("2025-10-15", "2025-07-15"),)}, {"seasons": (("2025-13-01", "2025-14-01"),)},
])
def test_s2_settings_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        S2Settings(**kwargs)


# ---------- U5: scene selection ----------

def test_select_best_pools_dedupes_and_ranks_per_tile_and_orbit():
    feats = [_feat("a1", "35KQA", 92, 0, 4.0), _feat("a2", "35KQA", 92, 0, 0.1),
             _feat("a3", "35KQA", 92, 10, 0.0), _feat("a2", "35KQA", 92, 0, 0.1),
             _feat("b1", "35KQA", 135, 60, 0.0)]
    picked = sources.select_best(feats, per_tile=2)
    assert [f["id"] for f in picked] == ["a2", "a3", "b1"]


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_s2_scenes_follows_pagination_and_keeps_signature(monkeypatch):
    pages = [{"features": [_feat("a1", "T1", 1, 0, 3.0), _feat("a2", "T1", 1, 0, 1.0)],
              "links": [{"rel": "next", "href": "https://next", "body": {"token": "x"}}]},
             {"features": [_feat("a3", "T1", 1, 0, 0.5)], "links": []}]
    bodies: list = []

    def fake_post(url, json=None, **_kw):
        bodies.append((url, json))
        return _Resp(pages[len(bodies) - 1])

    monkeypatch.setattr(sources.requests, "post", fake_post)
    picked = sources.s2_scenes((29, -20, 30, -19), "2025-07-15", "2025-10-15")
    assert [f["id"] for f in picked] == ["a3", "a2"]
    assert bodies[0][1]["datetime"] == "2025-07-15T00:00:00Z/2025-10-15T23:59:59Z"
    assert bodies[1] == ("https://next", {"token": "x"})


def test_s2_search_rejects_malformed_stac_response(monkeypatch):
    monkeypatch.setattr(sources.requests, "post", lambda *a, **k: _Resp({"type": "error"}))
    with pytest.raises(ValueError, match="STAC"):
        sources.s2_search((29, -20, 30, -19), "2025-07-15", "2025-10-15")


def test_s2_scenes_multi_queries_each_season_and_pools(monkeypatch):
    windows: list = []
    per_year = {"2024": [_feat("y24", "T1", 1, 0, 0.2)], "2025": [_feat("y25", "T1", 1, 0, 2.0)],
                "2026": [_feat("y26", "T1", 1, 0, 0.1), _feat("y24", "T1", 1, 0, 0.2)]}

    def fake_search(bbox, start, end, max_cloud=5.0):
        windows.append((start, end, max_cloud))
        return per_year[start[:4]]

    monkeypatch.setattr(sources, "s2_search", fake_search)
    picked = sources.s2_scenes_multi((29, -20, 30, -19), dry_seasons((2024, 2025, 2026)), 7.5, per_tile=2)
    assert [f["id"] for f in picked] == ["y26", "y24"]
    assert windows == [(f"{y}-07-15", f"{y}-10-15", 7.5) for y in (2024, 2025, 2026)]


# ---------- U5: mosaic, indices and layers ----------

def test_median_mosaic_keeps_old_default_depth_and_honours_larger_depth():
    pieces = [(Window(0, 0, 2, 2), np.full((2, 2), float(i), "float32")) for i in range(30)]
    assert features._median_mosaic(pieces, (2, 2))[0, 0] == 11.5           # first 24 only
    assert features._median_mosaic(pieces, (2, 2), max_depth=72)[0, 0] == 14.5


def test_s2_indices_formulas_and_zero_division_is_silent():
    bands = {"blue": np.array([0.1, 0.0]), "green": np.array([0.2, 0.2]), "red": np.array([0.3, 0.3]),
             "nir": np.array([0.4, 0.4]), "swir16": np.array([0.5, 0.5]), "swir22": np.array([0.25, 0.25])}
    before = {k: v.copy() for k, v in bands.items()}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        idx = features.s2_indices(bands)
    assert set(idx) == {"s2_ferric", "s2_ferrous", "s2_clay", "s2_ndvi", "s2_albedo"}
    assert idx["s2_ferric"][0] == pytest.approx(3.0) and not np.isfinite(idx["s2_ferric"][1])
    assert idx["s2_ferrous"][0] == pytest.approx(0.25 / 0.4 + 0.2 / 0.3)
    assert idx["s2_clay"][0] == pytest.approx(2.0)
    assert idx["s2_ndvi"][0] == pytest.approx(0.1 / 0.7)
    assert idx["s2_albedo"][0] == pytest.approx(1.75 / 6)
    assert all(np.array_equal(bands[k], before[k]) for k in bands)


def test_s2_indices_reports_missing_bands():
    with pytest.raises(ValueError, match="swir22"):
        features.s2_indices({b: np.ones(1) for b in features.S2_BANDS if b != "swir22"})


def _fake_scene_bands(scene: dict, grid: Grid):
    blue = 0.1 + 0.001 * scene["n"]
    vals = {"blue": blue, "green": 0.2, "red": 0.3, "nir": 0.4, "swir16": 0.5, "swir22": 0.25}
    return Window(0, 0, grid.width, grid.height), {b: np.full(grid.shape, v, "float32") for b, v in vals.items()}


def test_sentinel2_layers_sizes_median_depth_from_settings(monkeypatch):
    grid = _small_grid()
    monkeypatch.setattr(features, "_s2_scene_bands", _fake_scene_bands)
    scenes = [{"id": f"s{i}", "n": i} for i in range(30)]
    deep = features.sentinel2_layers(grid, settings=S2Settings(per_tile=6), scenes=scenes)
    legacy = features.sentinel2_layers(grid, scenes=scenes)
    assert np.nanmax(deep["s2_ferric"]) == pytest.approx(0.3 / 0.1145, rel=1e-5)
    assert np.nanmax(legacy["s2_ferric"]) == pytest.approx(0.3 / 0.1115, rel=1e-5)
    assert np.isnan(deep["s2_ferric"][~grid.mask]).all()


def test_sentinel2_layers_queries_with_settings(monkeypatch):
    seen: dict = {}

    def fake_multi(bbox, seasons, max_cloud=5.0, per_tile=2):
        seen.update(seasons=seasons, max_cloud=max_cloud, per_tile=per_tile, bbox=bbox)
        return [{"id": "s0", "n": 0}]

    monkeypatch.setattr(sources, "s2_scenes_multi", fake_multi)
    monkeypatch.setattr(features, "_s2_scene_bands", _fake_scene_bands)
    features.sentinel2_layers(_small_grid(), ("2024-07-15", "2024-10-15"))
    assert seen["seasons"] == (("2024-07-15", "2024-10-15"),) and seen["per_tile"] == 2
    assert 28.9 < seen["bbox"][0] < 29.1 and -19.6 < seen["bbox"][1] < -19.4


# ---------- U5: cache invalidation ----------

def _stub_build(monkeypatch, tmp_path) -> list:
    selected: list = []
    monkeypatch.setattr(features, "CACHE", tmp_path)
    monkeypatch.setattr(sources, "boundary",
                        lambda: gpd.GeoDataFrame(geometry=[box(29.0, -19.5, 29.5, -19.0)], crs=4326))
    monkeypatch.setitem(features.BUILDERS, "geology", lambda g: {"geo_class": np.zeros(g.shape, "float32")})
    monkeypatch.setitem(features.BUILDERS, "terrain",
                        lambda g: {k: np.ones(g.shape, "float32") for k in ("elev", "slope", "rough")})

    def fake_select(grid, settings):
        selected.append(settings)
        return [_feat("s0", "T1", 1, 0, 0.0) | {"n": 0}]

    monkeypatch.setattr(features, "select_s2_scenes", fake_select)
    monkeypatch.setattr(features, "_s2_scene_bands", _fake_scene_bands)
    return selected


def test_build_caches_sentinel2_per_settings(monkeypatch, tmp_path):
    selected = _stub_build(monkeypatch, tmp_path)
    multi = S2Settings(seasons=dry_seasons((2024, 2025, 2026)), per_tile=6)
    stack = features.build(res_m=1000)
    features.build(res_m=1000)
    assert len(selected) == 1 and (tmp_path / "sentinel2_1000m.npz").exists()
    assert "s2_ferric_ctx5" in stack.layers
    features.build(res_m=1000, s2=multi)
    tagged = tmp_path / f"sentinel2_1000m{multi.cache_tag()}.npz"
    assert selected == [S2Settings(), multi] and tagged.exists()
    side = json.loads(tagged.with_suffix(".json").read_text())
    assert side["settings"]["per_tile"] == 6 and side["scenes"][0]["group"] == "MGRS-T1/001"
    features.build(res_m=1000, s2=multi, refresh=True)
    assert len(selected) == 3


def test_build_rebuilds_when_sidecar_settings_disagree(monkeypatch, tmp_path):
    selected = _stub_build(monkeypatch, tmp_path)
    features.build(res_m=1000)
    side = tmp_path / "sentinel2_1000m.json"
    side.write_text(json.dumps({"settings": {"seasons": [["2020-01-01", "2020-02-01"]],
                                             "per_tile": 1, "max_cloud": 5.0}, "scenes": []}))
    features.build(res_m=1000)
    assert len(selected) == 2


# ---------- U5: seam metric ----------

def test_seam_score_flags_a_step_at_tile_boundaries():
    rng = np.random.default_rng(0)
    _, xx = np.mgrid[0:60, 0:80]
    base = 0.01 * xx + rng.normal(0, 0.02, xx.shape)
    tiles = (xx >= 40).astype(int)
    assert seam_score(base + 0.3 * (xx >= 40), tiles) > 5
    assert 0.7 < seam_score(base, tiles) < 1.4


def test_seam_score_ignores_nan_and_validates_input():
    layer = np.tile(np.r_[np.zeros(4), np.ones(4)], (6, 1))
    layer[0, :] = np.nan
    tiles = np.tile(np.r_[np.zeros(4, int), np.ones(4, int)], (6, 1))
    layer = layer + np.arange(6)[:, None] * 0.1  # within-tile vertical steps of 0.1
    # 5 finite rows: across = 5 steps of 1.0; within = 30 horizontal zeros + 32 vertical steps of 0.1
    assert seam_score(layer, tiles) == pytest.approx(1.0 / (32 * 0.1 / 62), rel=1e-6)
    assert np.isnan(seam_score(layer, np.zeros_like(tiles)))
    with pytest.raises(ValueError):
        seam_score(layer, tiles[:, :4])


def test_footprint_ids_label_each_coverage_combination():
    grid = _small_grid()
    fps = [box(29.0, -19.5, 29.3, -19.0), box(29.2, -19.5, 29.5, -19.0)]
    ids = footprint_ids(fps, grid)
    pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy([29.1, 29.25, 29.4, 29.12], [-19.25] * 4), crs=4326)
    from zimprospect.grid import sample
    a_only, both, b_only, a_again = sample(ids.astype(float), pts, grid)
    assert len({a_only, both, b_only}) == 3 and a_only == a_again
    assert ids.dtype == np.int32 and ids.shape == grid.shape


def test_group_footprints_unions_scenes_of_the_same_tile_and_orbit():
    recs = s2.scene_records([_feat("a", "T1", 1, 0, 0, (29.0, -19.5, 29.2, -19.0)),
                             _feat("b", "T1", 1, 0, 0, (29.1, -19.5, 29.3, -19.0)),
                             _feat("c", "T2", 1, 0, 0, (29.3, -19.5, 29.5, -19.0))])
    groups = group_footprints(recs)
    assert set(groups) == {"MGRS-T1/001", "MGRS-T2/001"}
    assert groups["MGRS-T1/001"].bounds == pytest.approx((29.0, -19.5, 29.3, -19.0))


def test_layer_seam_score_on_synthetic_mosaic():
    grid = _small_grid()
    recs = s2.scene_records([_feat("a", "T1", 1, 0, 0, (28.9, -19.6, 29.25, -18.9)),
                             _feat("b", "T2", 1, 0, 0, (29.25, -19.6, 29.6, -18.9))])
    ids = footprint_ids(list(group_footprints(recs).values()), grid)
    rng = np.random.default_rng(1)
    smooth = rng.normal(1.0, 0.01, grid.shape)
    stepped = np.where(ids == ids[0, -1], smooth + 0.2, smooth)
    assert s2.layer_seam_score(stepped, recs, grid) > 5 * s2.layer_seam_score(smooth, recs, grid)


def test_seam_report_script_scores_a_cached_layer(monkeypatch, tmp_path, caplog):
    import importlib.util
    import logging
    spec = importlib.util.spec_from_file_location("seam_report", PROJECT / "scripts" / "seam_report.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    zone = gpd.GeoDataFrame(geometry=[box(29.0, -19.5, 29.5, -19.0)], crs=4326)
    monkeypatch.setattr(features, "CACHE", tmp_path)
    monkeypatch.setattr(sources, "boundary", lambda: zone)
    grid = make_grid(zone, 1000)  # the script builds the same grid (default padding)
    recs = s2.scene_records([_feat("a", "T1", 1, 0, 0, (28.8, -19.7, 29.25, -18.8)),
                             _feat("b", "T2", 1, 0, 0, (29.25, -19.7, 29.7, -18.8))])
    ids = footprint_ids(list(group_footprints(recs).values()), grid)
    layer = np.random.default_rng(2).normal(1.0, 0.01, grid.shape) + 0.2 * (ids == ids[0, -1])
    np.savez(tmp_path / "sentinel2_1000m.npz", s2_ferric=layer.astype("float32"))
    (tmp_path / "sentinel2_1000m.json").write_text(json.dumps({"scenes": recs}))
    caplog.set_level(logging.INFO)
    assert script.main(["--res", "1000"]) == 0
    score = float(caplog.text.split("seam_score=")[1].split()[0])
    assert score > 5
    assert script.main(["--res", "1000", "--layer", "s2_nope"]) == 1
    assert script.main(["--res", "1000", "--per-tile", "6"]) == 1  # no cache for those settings


@pytest.mark.network
def test_cop30_tile_url_pattern_resolves():
    import requests
    resp = requests.head(sources.cop_dem_urls(31.2, -17.5, 31.8, -17.1, res_m=20)[0], timeout=30)
    assert resp.status_code == 200
