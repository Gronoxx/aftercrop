# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Observation region (SA-D79): the config switch, the copied rule values, the region copy and
the observation documents. The default region must stay as it is.

- ``AFTERCROP_CONFIG`` unset: ``base`` reads config/region_norte_pr.yaml and data/raw/, data/interim/;
- set to config/region_free_state.yaml: that region's own raw and interim folders, at import;
- the end-of-green values of the Free State config equal the tagged ones of norte_pr;
- config/copy_regions_*.yaml: same keys and placeholders, house style, built copy current;
- pipeline.regions.observation refuses site/data/ and the default data folders;
- the built Free State documents (when present) carry no state, no G and no strategy.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from pipeline.adapters import base
from pipeline.build import copy_json
from pipeline.regions import observation as obs

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "config" / "region_norte_pr.yaml"
FREE_STATE = ROOT / "config" / "region_free_state.yaml"
SITE_OUT = ROOT / "site" / "data_regions" / "free_state"
DASHES_AND_SEMICOLONS = ("—", "–", "‒", "―", ";", "；")
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Field keys the copy leaves out or changes on purpose (the SA-D63 split, the field choice and
# the notes of the tag): everything else must equal the tagged protocol of norte_pr.
NOT_COPIED = {"product_area", "development_strip", "green_end_allowed_adjustment",
              "choice_seasons", "choice_fallback", "municipality", "slope", "candidates", "first_field"}


@pytest.fixture
def restore_region():
    yield
    base.use_region(DEFAULT)


def _probe(env_value: str | None) -> dict:
    env = {k: v for k, v in os.environ.items() if k != base.CONFIG_ENV}
    if env_value is not None:
        env[base.CONFIG_ENV] = env_value
    code = ("import json; from pipeline.adapters import base as b; "
            "print(json.dumps({'config': str(b.config_path()), 'raw': str(b.RAW_DIR), "
            "'interim': str(b.INTERIM_DIR), 'region': b.load_config()['region']}))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                         timeout=60, check=True)
    return json.loads(out.stdout)


# ---- the switch ----------------------------------------------------------------------------

def test_default_region_is_unchanged(monkeypatch):
    monkeypatch.delenv(base.CONFIG_ENV, raising=False)
    assert base.config_path() == DEFAULT
    assert base.load_config()["region"] == "norte_pr"
    assert base.RAW_DIR == ROOT / "data" / "raw"
    assert base.INTERIM_DIR == ROOT / "data" / "interim"
    assert base.CONFIG_PATH == DEFAULT
    p = _probe(None)
    assert p == {"config": str(DEFAULT), "raw": str(ROOT / "data" / "raw"),
                 "interim": str(ROOT / "data" / "interim"), "region": "norte_pr"}


def test_env_switch_takes_the_region_folders_at_import():
    p = _probe("config/region_free_state.yaml")
    assert p["region"] == "free_state"
    assert p["config"] == str(FREE_STATE)
    assert p["raw"] == str(ROOT / "data" / "regions" / "free_state" / "raw")
    assert p["interim"] == str(ROOT / "data" / "regions" / "free_state" / "interim")
    assert _probe(str(FREE_STATE))["region"] == "free_state", "absolute path"
    assert _probe("")["region"] == "norte_pr", "an empty value is the default"


def test_use_region_and_back(monkeypatch, restore_region):
    monkeypatch.delenv(base.CONFIG_ENV, raising=False)
    cfg = base.use_region(FREE_STATE)
    assert cfg["mode"] == "observation"
    assert base.RAW_DIR == ROOT / "data" / "regions" / "free_state" / "raw"
    base.use_region(DEFAULT)
    assert base.RAW_DIR == ROOT / "data" / "raw" and base.INTERIM_DIR == ROOT / "data" / "interim"
    assert base.load_config()["region"] == "norte_pr"


def test_make_region_switch():
    def dry(*args):
        env = {k: v for k, v in os.environ.items() if k != base.CONFIG_ENV}
        return subprocess.run(["make", "-n", *args], cwd=ROOT, env=env, capture_output=True, text=True,
                              timeout=60, check=True).stdout
    assert "--config config/region_free_state.yaml" in dry("observation", "REGION=free_state")
    show = ["-f", "Makefile", "-f", "-", "show"]
    probe = "show:\n\t@echo cfg=$$AFTERCROP_CONFIG\n"
    env = {k: v for k, v in os.environ.items() if k != base.CONFIG_ENV}
    out = subprocess.run(["make", *show], cwd=ROOT, env=env, input=probe, capture_output=True, text=True, timeout=60).stdout
    assert "cfg=\n" in out or out.strip() == "cfg=", out
    out = subprocess.run(["make", *show, "REGION=free_state"], cwd=ROOT, env=env, input=probe, capture_output=True,
                         text=True, timeout=60).stdout
    assert "cfg=config/region_free_state.yaml" in out, out


# ---- the copied rule -----------------------------------------------------------------------

def test_end_of_green_rule_is_the_tagged_one():
    tagged = yaml.safe_load(DEFAULT.read_text(encoding="utf-8"))["protocol"]
    fs = yaml.safe_load(FREE_STATE.read_text(encoding="utf-8"))
    p = fs["protocol"]
    assert p["dates"]["ribbon_seasons"] == tagged["dates"]["ribbon_seasons"]
    assert p["frost"] == tagged["frost"]
    assert p["observation_climate"]["frost"]["below_c"] == tagged["climate"]["frost"]["below_c"]
    for key, value in p["field"].items():
        if key in NOT_COPIED:
            continue
        assert value == tagged["field"][key], key
    for key in tagged["field"]:
        if key not in NOT_COPIED:
            assert key in p["field"], f"{key} not copied"
    lon_max = max(fs["sources"]["hls"]["window"]["bbox"][0::2])
    assert p["field"]["product_area"]["lon_max"] > lon_max, "the one field lies in the product area of the region"
    assert fs["mode"] == "observation" and fs["trial_sites"] == []
    assert "decision" not in p and "tests" not in p and "label" not in p, "no verdict rule in an observation region"


def test_region_config_has_its_own_folders():
    fs = yaml.safe_load(FREE_STATE.read_text(encoding="utf-8"))
    paths = fs["paths"]
    assert paths["raw"].startswith("data/regions/free_state/") and paths["interim"].startswith("data/regions/free_state/")
    assert paths["site_out"] == "site/data_regions/free_state"


def test_observation_refuses_the_product_folders(tmp_path, restore_region):
    fs = yaml.safe_load(FREE_STATE.read_text(encoding="utf-8"))
    for key, value, msg in (("site_out", "site/data", "site/data"), ("raw", "data/raw", "data/raw"),
                            ("interim", "data/interim", "data/interim"), ("site_out", "data/x", "inside site")):
        bad = json.loads(json.dumps(fs))
        bad["paths"][key] = value
        path = tmp_path / f"bad_{key}.yaml"
        path.write_text(yaml.safe_dump(bad), encoding="utf-8")
        with pytest.raises(obs.ObservationError, match=msg):
            obs.setup(path)
    not_obs = json.loads(json.dumps(fs))
    not_obs["mode"] = "decision"
    path = tmp_path / "decision.yaml"
    path.write_text(yaml.safe_dump(not_obs), encoding="utf-8")
    with pytest.raises(obs.ObservationError, match="mode"):
        obs.setup(path)
    with pytest.raises(obs.ObservationError, match="default region"):
        obs.main(["build", "--config", str(DEFAULT)])


def test_open_ring():
    assert obs.open_ring([[0, 0], [1, 0], [1, 1], [0, 0]]) == [(0, 0), (1, 0), (1, 1)]
    assert obs.open_ring([[0, 0], [1, 0], [1, 1]]) == [(0, 0), (1, 0), (1, 1)]


# ---- the region copy ----------------------------------------------------------------------

REGION_COPY = {lang: yaml.safe_load(p.read_text(encoding="utf-8")) for lang, p in copy_json.REGION_SOURCES.items()}
LEAVES = {lang: dict(copy_json.leaves(tree)) for lang, tree in REGION_COPY.items()}


def test_region_copy_same_keys_and_placeholders():
    assert set(LEAVES["en"]) == set(LEAVES["pt"])
    for key, en in LEAVES["en"].items():
        assert sorted(set(PLACEHOLDER.findall(en))) == sorted(set(PLACEHOLDER.findall(LEAVES["pt"][key]))), key


def test_region_copy_house_style():
    from tests.test_copy import FORBIDDEN
    for lang, flat in LEAVES.items():
        for key, value in flat.items():
            assert not any(c in value for c in DASHES_AND_SEMICOLONS), f"{lang} {key}: {value!r}"
            for name, pat in FORBIDDEN.items():
                assert not re.search(pat, value, re.IGNORECASE), f"{lang} {key}: {name}"
    for path in copy_json.REGION_SOURCES.values():
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.match(r"^\s+\w+: \"", line) or re.match(r"^\w+: \"", line):
                # Every text carries its review mark: drafted, checked or fixed by the plan.
                assert re.search(r"# (drafted, review|checked \(SI\)|plan section 3)", line), \
                    f"{path.name}: {line}"
    assert REGION_COPY["en"]["observation"]["sentence"] == "No field trials, no verdict. The rule travels. The verdict does not."
    assert REGION_COPY["en"]["how_link"] == "Another region, observation only"


def test_region_copy_json_is_current_and_product_copy_untouched():
    current = copy_json.REGION_OUT.read_text(encoding="utf-8")
    assert current == copy_json.render(copy_json.build_copy(copy_json.REGION_SOURCES))
    assert copy_json.main(["--check"]) == 0, "site/data/copy.json must stay as built from copy_en/pt.yaml"
    assert "regions" not in json.loads(copy_json.OUT.read_text(encoding="utf-8"))["langs"]["en"]


# ---- the built documents ------------------------------------------------------------------

@pytest.mark.skipif(not (SITE_OUT / "field_a.json").exists(), reason="run make observation REGION=free_state")
def test_built_documents_hold_no_verdict():
    region = json.loads((SITE_OUT / "region.json").read_text(encoding="utf-8"))
    field = json.loads((SITE_OUT / "field_a.json").read_text(encoding="utf-8"))
    assert region["mode"] == "observation" and field["mode"] == "observation"
    assert not any(region["pack"].values())
    # pack lists the five items of the Regional Pack, all false: read apart, above.
    text = json.dumps([{k: v for k, v in region.items() if k != "pack"}, field])
    for key in ('"state"', '"G"', '"window_reading"', '"window_basis"', '"calendar"', '"risk"', '"strategies"',
                '"price_scenarios"', '"label"'):
        assert key not in text, key
    assert len(field["ribbon"]["seasons"]) == 8
    assert len(field["green_end"]) == 8
    assert 15 <= field["area_ha"]["v"] <= 80
    assert field["n_inner_pixels"] >= 40
    assert {s["id"] for s in region["sources"]} == {"hls_vi_v2", "nasadem", "power_daily"}
    assert (SITE_OUT / "img" / "basemap.png").is_file() and (SITE_OUT / "img" / "basemap.json").is_file()
