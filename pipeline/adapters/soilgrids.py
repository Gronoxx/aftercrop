# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""SoilGrids 2.0 through WCS 2.0.1 at maps.isric.org (plan, sheet M1, criterion M1.5).

First scope: an access test at one point in Londrina. DescribeCoverage
records the service CRS (EPSG:152160, Interrupted Goode Homolosine, 250 m).
Two GetCoverage forms are recorded: a box in EPSG:4326, which the service
answers resampled to a WGS 84 grid, and a box in the service CRS (x, y in
metres), which keeps the native 250 m pixels, as sheet M1 asks. The REST API is
paused, so WCS is the route the ISRIC points pipelines to.

Units on the WCS: clay and sand in g/kg (/10 -> %), soc in dg/kg (/10 -> g/kg).
-32768 is no data.

Run from the repository root:  python -m pipeline.adapters.soilgrids
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

from pipeline.adapters import base

NODATA = -32768
EPSG4326 = "http://www.opengis.net/def/crs/EPSG/0/4326"
NATIVE_PIXEL_M = 250.0
# Interrupted Goode Homolosine, sinusoidal zone only (|lat| < 40 44' 11.8").
# Lobes as (west, east, central meridian), as in PROJ "+proj=igh".
IGH_R = 6378137.0
IGH_PHI_LIM = 40.0 + 44.0 / 60 + 11.8 / 3600
IGH_NORTH = [(-180, -40, -100), (-40, 180, 30)]
IGH_SOUTH = [(-180, -100, -160), (-100, -20, -60), (-20, 80, 20), (80, 180, 140)]
# property -> (divisor, unit after conversion)
CONVERSION = {"clay": (10.0, "%"), "sand": (10.0, "%"), "silt": (10.0, "%"), "soc": (10.0, "g/kg")}


def igh_xy(lat: float, lon: float) -> tuple[float, float]:
    """lat, lon (degrees) -> x, y (metres) in the service CRS, sinusoidal zone only."""
    if abs(lat) >= IGH_PHI_LIM:
        raise NotImplementedError("Mollweide zone of the Homolosine not needed for Parana")
    lobes = IGH_NORTH if lat >= 0 else IGH_SOUTH
    lon0 = next(c for w, e, c in lobes if w <= lon < e or (e == 180 and lon == 180))
    phi = math.radians(lat)
    x = IGH_R * (math.radians(lon0) + math.radians(lon - lon0) * math.cos(phi))
    return x, IGH_R * phi


def specs(config: dict | None = None) -> list[dict]:
    config = config or base.load_config()
    cfg = config["sources"]["soilgrids"]
    t = cfg["access_test"]
    site = next(s for s in config["trial_sites"] if s["id"] == t["site"])
    out = []
    for cov in t["coverages"]:
        common = {"source_id": cfg["source_id"], "url": cfg["url"], "license": cfg["license"],
                  "coverage": cov, "property": cov.split("_")[0], "site": site["id"],
                  "lat": float(site["lat"]), "lon": float(site["lon"]), "half": float(t["half_size_deg"])}
        out.append({**common, "request": "DescribeCoverage"})
        out.append({**common, "request": "GetCoverage"})
        if cov in t.get("native_check", []):
            out.append({**common, "request": "GetCoverageNative"})
    return out


def request(spec: dict) -> tuple[str, dict, str]:
    prop = spec["property"]
    params = {"map": f"/map/{prop}.map", "SERVICE": "WCS", "VERSION": "2.0.1"}
    if spec["request"] == "DescribeCoverage":
        params.update({"REQUEST": "DescribeCoverage", "COVERAGEID": spec["coverage"]})
        return spec["url"], params, f"describe_{spec['coverage']}.xml"
    lat, lon, h = spec["lat"], spec["lon"], spec["half"]
    if spec["request"] == "GetCoverageNative":
        x, y = igh_xy(lat, lon)
        hm = 2 * NATIVE_PIXEL_M  # 4 x 4 native pixels around the point
        params.update({
            "REQUEST": "GetCoverage", "COVERAGEID": spec["coverage"], "FORMAT": "image/tiff",
            "SUBSET": [f"x({x - hm:.1f},{x + hm:.1f})", f"y({y - hm:.1f},{y + hm:.1f})"],
        })
        return spec["url"], params, f"{spec['site']}_{spec['coverage']}_native{int(2 * hm)}m.tif"
    params.update({
        "REQUEST": "GetCoverage", "COVERAGEID": spec["coverage"], "FORMAT": "image/tiff",
        "SUBSET": [f"long({lon - h:.6f},{lon + h:.6f})", f"lat({lat - h:.6f},{lat + h:.6f})"],
        "SUBSETTINGCRS": EPSG4326,
    })
    return spec["url"], params, f"{spec['site']}_{spec['coverage']}_box{h:g}.tif"


def fetch(spec: dict) -> Path:
    url, params, name = request(spec)
    return base.cached_get(spec["source_id"], url, params=params, filename=name, timeout=180)


def read(path: Path) -> np.ndarray:
    """Raw band of a GetCoverage GeoTIFF as float, with -32768 as NaN. No unit change."""
    import tifffile

    with tifffile.TiffFile(path) as tif:
        arr = tif.pages[0].asarray().astype("float64")
    arr[arr == NODATA] = np.nan
    return arr


def geotags(path: Path) -> dict:
    """Pixel size, tie point and GeoKeys of the GeoTIFF, as stored."""
    import tifffile

    with tifffile.TiffFile(path) as tif:
        tags = tif.pages[0].tags
        out = {}
        for code, name in ((33550, "pixel_scale"), (33922, "tiepoint"), (34737, "ascii_params")):
            if code in tags:
                v = tags[code].value
                out[name] = list(v) if isinstance(v, tuple) else v
        if 42113 in tags:
            out["gdal_nodata"] = tags[42113].value
        out["shape"] = list(tif.pages[0].shape)
        out["dtype"] = str(tif.pages[0].dtype)
    return out


def convert(prop: str, raw: np.ndarray) -> tuple[np.ndarray, str]:
    """Mapped integers to plan units: clay, sand /10 -> %; soc /10 -> g/kg."""
    div, unit = CONVERSION[prop]
    raw = np.asarray(raw, dtype="float64")
    out = np.where(raw == NODATA, np.nan, raw) / div
    return out, unit


def provenance(spec: dict) -> dict:
    url, params, name = request(spec)
    path = base.raw_path(spec["source_id"], url, params, name)
    return base.base_provenance(path, spec["license"], "SoilGrids 2.0 (WCS 2.0.1, maps.isric.org)",
                                coverage=spec["coverage"], request=spec["request"])


def access_test(config: dict | None = None) -> dict:
    """Run the requests and record what the service answered, errors included."""
    result = {"service": None, "responses": []}
    for spec in specs(config):
        url, params, name = request(spec)
        entry = {"coverage": spec["coverage"], "request": spec["request"]}
        try:
            path = fetch(spec)
        except base.FetchError as e:
            entry.update({"ok": False, "http_status": e.status, "body": e.body[:500]})
            result["responses"].append(entry)
            continue
        m = base.read_meta(path)
        entry.update({"ok": True, "http_status": m["http_status"], "content_type": m["content_type"],
                      "bytes": m["bytes"], "path": str(path.relative_to(base.REPO_ROOT))})
        if spec["request"] == "DescribeCoverage":
            text = path.read_text(encoding="utf-8", errors="replace")
            entry["is_exception"] = "ExceptionReport" in text
            entry["srs_hint"] = sorted({t for t in ("152160", "54052", "Homolosine", "4326") if t in text})
        else:
            try:
                raw = read(path)
                vals, unit = convert(spec["property"], raw)
                cy, cx = raw.shape[0] // 2, raw.shape[1] // 2
                entry.update({
                    "geotags": geotags(path), "unit": unit,
                    "center_raw": None if np.isnan(raw[cy, cx]) else float(raw[cy, cx]),
                    "center_value": None if np.isnan(vals[cy, cx]) else round(float(vals[cy, cx]), 2),
                    "box_mean": None if np.isnan(vals).all() else round(float(np.nanmean(vals)), 2),
                    "n_pixels": int(vals.size), "n_nodata": int(np.isnan(vals).sum()),
                })
            except Exception as e:  # keep the raw file, record the decoding problem
                entry.update({"decoded": False, "decode_error": f"{type(e).__name__}: {e}"})
        result["responses"].append(entry)
    result["service"] = "ok" if all(r["ok"] for r in result["responses"]) else "error"
    base.write_json(base.INTERIM_DIR / "soilgrids_access_test.json", result)
    return result


def main() -> int:
    r = access_test()
    for e in r["responses"]:
        print({k: v for k, v in e.items() if k not in ("geotags", "path")})
    print(f"service={r['service']}")
    print(f"network_calls={base.network_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
