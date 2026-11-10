"""Mindat localities (U6): parsing, pagination, caching and key handling, all without network."""
from __future__ import annotations

import json
import logging
import os

import pandas as pd
import pytest
import requests

from zimprospect import mindat, sources

LOC_URL = mindat.LOCALITIES_URL
GEO_URL = mindat.GEOMATERIALS_URL


def _loc(loc_id, lat, lon, minerals=(10,), level=4, txt=None, **extra):
    return {"id": loc_id, "txt": txt or f"Locality {loc_id}", "latitude": lat, "longitude": lon,
            "level": level, "non_hierarchical": 0, "geomaterials": list(minerals), **extra}


PAYLOAD = {
    "version": 1, "country": "Zimbabwe", "fetched": "2026-11-10",
    "localities": [
        _loc(1, -17.3241, 31.5659, [11, 10], txt="Shamva Mine, Shamva District, Zimbabwe"),
        _loc(2, 0.0, 0.0),                               # Mindat's "no coordinates"
        _loc(3, None, None),
        _loc(4, -25.0, 31.0),                            # outside the Zimbabwe envelope
        _loc(5, -18.0, 30.0, level=2),                   # district: coordinates are a centroid
        _loc(6, -18.1, 30.1, non_hierarchical=1),        # geological region: centroid
        _loc(7, -18.2, 30.2, minerals=[]),               # no minerals listed
        _loc(8, "-20.08", "31.9", [12, 13, 99]),         # string coordinates; 99 has no name
        _loc(9, -19.9, 30.6, [{"id": 14, "name": "Beryl"}]),   # expanded objects
        "not a locality",
    ],
    "minerals": {"10": "Gold", "11": "Pyrite", "12": "Petalite", "13": "Spodumene"},
}


def _boom(url, params):
    raise AssertionError(f"unexpected request to {url}")


def _router(pages):
    calls = []

    def get_json(url, params):
        calls.append((url, None if params is None else dict(params)))
        return pages[url] if not callable(pages[url]) else pages[url](params)
    return get_json, calls


# ---------- parsing ----------

def test_parse_localities_keeps_precise_points_with_minerals(caplog):
    with caplog.at_level(logging.INFO, logger="zimprospect.mindat"):
        occ = mindat.parse_localities(PAYLOAD)
    assert list(occ.columns) == ["source", "name", "commodities", "status", "geometry"]
    assert list(occ["name"]) == ["Shamva Mine, Shamva District, Zimbabwe", "Locality 8", "Locality 9"]
    assert list(occ["commodities"]) == ["Gold, Pyrite", "Petalite, Spodumene", "Beryl"]
    assert set(occ["source"]) == {"Mindat"} and occ.crs.to_epsg() == 4326
    assert occ.geometry.iloc[1].x == pytest.approx(31.9) and occ.geometry.iloc[1].y == pytest.approx(-20.08)
    assert "no coordinates" in caplog.text and "centroid" in caplog.text


@pytest.mark.parametrize("bad, msg", [
    ([], "JSON object"),
    ({"localities": {}, "minerals": {}}, "localities"),
    ({"localities": [], "minerals": []}, "minerals"),
    ({"localities": [], "minerals": {"x": "Gold"}}, "mineral id"),
])
def test_parse_localities_rejects_malformed_payload(bad, msg):
    with pytest.raises(ValueError, match=msg):
        mindat.parse_localities(bad)


# ---------- pagination ----------

def test_fetch_localities_follows_next_links():
    page2 = LOC_URL + "?country=Zimbabwe&page=2"
    get_json, calls = _router({
        LOC_URL: {"count": 3, "next": page2, "results": [{"id": 1}, {"id": 2}]},
        page2: {"count": 3, "next": None, "results": [{"id": 3}]},
    })
    out = mindat.fetch_localities(get_json)
    assert [r["id"] for r in out] == [1, 2, 3]
    url, params = calls[0]
    assert url == LOC_URL and params["country"] == "Zimbabwe" and params["expand"] == "geomaterials"
    assert {"latitude", "longitude", "geomaterials", "level"} <= set(params["fields"].split(","))
    assert int(params["page-size"]) <= mindat.MAX_PAGE_SIZE
    assert calls[1] == (page2, None)          # the next link already carries the query


def test_pagination_upgrades_http_and_refuses_foreign_hosts():
    http_next = LOC_URL.replace("https://", "http://") + "?page=2"
    get_json, calls = _router({LOC_URL: {"next": http_next, "results": []},
                               LOC_URL + "?page=2": {"next": None, "results": []}})
    mindat.fetch_localities(get_json)
    assert calls[1][0].startswith("https://")
    evil, _ = _router({LOC_URL: {"next": "https://evil.example.com/steal", "results": []}})
    with pytest.raises(mindat.MindatError, match="evil.example.com"):
        mindat.fetch_localities(evil)


def test_pagination_stops_after_max_pages(monkeypatch):
    monkeypatch.setattr(mindat, "MAX_PAGES", 3)
    loop, calls = _router({LOC_URL: {"next": LOC_URL, "results": [{"id": 1}]}})
    with pytest.raises(mindat.MindatError, match="3 pages"):
        mindat.fetch_localities(loop)
    assert len(calls) == 3


def test_unexpected_page_shape_is_an_error():
    get_json, _ = _router({LOC_URL: {"detail": "Invalid token."}})
    with pytest.raises(mindat.MindatError, match="results"):
        mindat.fetch_localities(get_json)


def test_fetch_mineral_names_batches_ids():
    def page(params):
        ids = [int(i) for i in params["id_in"].split(",")]
        return {"next": None, "results": [{"id": i, "name": f"Mineral {i}"} for i in ids]}
    get_json, calls = _router({GEO_URL: page})
    names = mindat.fetch_mineral_names(get_json, list(range(450)))
    assert len(calls) == 3 and len(calls[0][1]["id_in"].split(",")) == mindat.ID_BATCH
    assert names[449] == "Mineral 449" and len(names) == 450
    assert mindat.fetch_mineral_names(_boom, []) == {}


# ---------- load_localities: key, cache, failures ----------

@pytest.fixture
def raw(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "RAW", tmp_path / "raw")
    return tmp_path / "raw"


def test_no_api_key_returns_empty_frame_with_one_warning(monkeypatch, raw, caplog):
    monkeypatch.delenv(mindat.API_KEY_ENV, raising=False)
    with caplog.at_level(logging.WARNING, logger="zimprospect.mindat"):
        occ = mindat.load_localities(get_json=_boom)
    assert occ.empty and list(occ.columns) == ["source", "name", "commodities", "status", "geometry"]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert mindat.API_KEY_ENV in caplog.text


def test_load_localities_fetches_once_then_reads_cache(monkeypatch, raw):
    monkeypatch.setenv(mindat.API_KEY_ENV, "secret-test-key")
    get_json, calls = _router({
        LOC_URL: {"next": None, "results": [_loc(1, -17.3, 31.5, [10])]},
        GEO_URL: {"next": None, "results": [{"id": 10, "name": "Gold"}]},
    })
    first = mindat.load_localities(get_json=get_json)
    cache = raw / mindat.CACHE_NAME
    assert list(first["commodities"]) == ["Gold"] and cache.is_file()
    assert "secret-test-key" not in cache.read_text()
    assert json.loads(cache.read_text())["country"] == "Zimbabwe"
    again = mindat.load_localities(get_json=_boom)
    pd.testing.assert_frame_equal(pd.DataFrame(first), pd.DataFrame(again))
    refreshed = mindat.load_localities(refresh=True, get_json=get_json)
    assert len(calls) == 4 and len(refreshed) == 1


def test_download_failure_returns_empty_without_cache(monkeypatch, raw, caplog):
    monkeypatch.setenv(mindat.API_KEY_ENV, "k")

    def down(url, params):
        raise requests.ConnectionError("connection refused")
    with caplog.at_level(logging.ERROR, logger="zimprospect.mindat"):
        occ = mindat.load_localities(get_json=down)
    assert occ.empty and not (raw / mindat.CACHE_NAME).exists()
    assert "connection refused" in caplog.text


def test_corrupt_cache_fails_clearly(monkeypatch, raw):
    monkeypatch.setenv(mindat.API_KEY_ENV, "k")
    raw.mkdir(parents=True)
    (raw / mindat.CACHE_NAME).write_text("{not json")
    with pytest.raises(ValueError, match="delete"):
        mindat.load_localities(get_json=_boom)


# ---------- HTTP layer (fake session, no network) ----------

class _Resp:
    def __init__(self, status: int, body: object, headers: dict | None = None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self) -> object:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class _Session:
    def __init__(self, responses: list[_Resp]):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return self.responses.pop(0)


def test_http_getter_sends_token_and_retries_rate_limits(monkeypatch):
    waits = []
    monkeypatch.setattr(mindat.time, "sleep", waits.append)
    session = _Session([_Resp(429, {}, {"Retry-After": "2"}), _Resp(200, {"results": []})])
    get_json = mindat.http_getter("abc123", session=session)
    assert get_json(LOC_URL, {"country": "Zimbabwe"}) == {"results": []}
    assert session.calls[0]["headers"]["Authorization"] == "Token abc123"
    assert session.calls[0]["timeout"] is not None and waits == [2.0]


def test_http_getter_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(mindat.time, "sleep", lambda s: None)
    session = _Session([_Resp(503, {}) for _ in range(mindat.MAX_RETRIES + 1)])
    with pytest.raises(requests.HTTPError):
        mindat.http_getter("k", session=session)(LOC_URL, None)
    assert len(session.calls) == mindat.MAX_RETRIES + 1


@pytest.mark.network
def test_live_mindat_zimbabwe_localities(monkeypatch, tmp_path):
    if not os.environ.get(mindat.API_KEY_ENV):
        pytest.skip(f"{mindat.API_KEY_ENV} not set")
    monkeypatch.setattr(sources, "RAW", tmp_path)
    occ = mindat.load_localities(refresh=True)
    assert len(occ) > 0 and occ["commodities"].str.len().gt(0).all()
