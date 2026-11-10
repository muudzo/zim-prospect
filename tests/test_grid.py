import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point, box

from zimprospect.grid import contact_cells, distance_km, make_grid, rasterize_presence, sample


def _grid():
    boundary = gpd.GeoDataFrame(geometry=[box(29.0, -19.5, 30.0, -18.5)], crs=4326)
    return make_grid(boundary, res_m=1000, pad_m=0)


def test_grid_covers_box_at_resolution():
    g = _grid()
    assert 100 < g.width < 115 and 105 < g.height < 120
    assert g.mask.mean() > 0.9


def test_distance_to_line_is_in_km_and_zero_on_line():
    g = _grid()
    line = gpd.GeoDataFrame(geometry=[LineString([(29.5, -19.5), (29.5, -18.5)])], crs=4326)
    d = distance_km(rasterize_presence(line, g), g)
    pts = gpd.GeoDataFrame(geometry=[Point(29.5, -19.0), Point(29.6, -19.0)], crs=4326)
    on, off = sample(d, pts, g)
    assert on == 0.0
    assert 9.0 < off < 12.5  # 0.1 deg lon at 19S ~ 10.5 km


def test_contact_cells_only_on_shared_edge():
    r = np.array([[0, 0, 1, 1], [0, 0, 1, 1]])
    c = contact_cells(r, side_a=[0], side_b=[1])
    assert c[:, 1].all() and not c[:, 0].any() and not c[:, 2:].any()
