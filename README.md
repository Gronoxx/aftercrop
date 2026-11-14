# Aftercrop

What follows soybeans on a field in northern Paraná, Brazil: keep second-crop corn, corn with brachiaria grass, or wheat on part of the area. NASA Space Apps 2026, challenge "Field Shift". The answer comes only from published field trials, the farmer's own numbers and NASA data, and it can say "Keep what you do", "Try it on a strip here", "Tied" or "Not enough evidence".


## Current label

**Model not validated here** (`label_id` `model_not_validated_here` in `site/data/scoreboard.json`), under protocol `protocol-v1.1`. L0 ran after the label froze, with field-chain settings decided before T0, and A2 now says why it did not run. Both are `late_notes` entries of the same file, and neither changed the label.

All 10 trial site-seasons completed. Exploratory, not blind. T0 failed (Kendall S 0, needed 21), T1 failed on both rank and error (S 11, RMSE 896 kg/ha against 280 kg/ha for a constant), P1 and A1 failed, and L0 failed (50 unexplained artifacts in the field chains). A3 and N1 are reported as context. L1, A2, H1 and R1 did not run, and H2 was not attempted. Every fix made after the first result is a `late_notes` entry.

A soil sensitivity run decided after the freeze (SA-D77, `site/data/sensitivity.json`), with the SoilGrids texture at the trial points instead of the clay printed in the trial reports, gives the same label: T0 passes (S 23), T1 still fails (S 19, RMSE 1,648 kg/ha), and L0 fails because it reads the field chains, which this run does not change. It never replaces the frozen label.

## State of the work

Four levels, kept apart (SA-D66). How we know shows the same levels, read from the files the page loads.

| Level | What it covers today |
|---|---|
| Verified internally | `make test`: 1348 Python tests collected in `.venv`, 105 engine tests in `.venv-engine`, 234 JavaScript tests. They check that the code agrees with itself and with the protocol. They check the code, not the model, and passing them does not show that any answer is right |
| Data obtained | HLS and HLS-VI v2.0, NASA POWER, NASADEM, GloREDa, the Zarc tables, SoilGrids and the Embrapa trial reports, in the local cache (`data/`, not in git). Every raw file is listed with its URL and sha256 in `site/data/manifest_files.json` |
| Executed | The satellite series and 8-season ribbon of fields A, B and C, the crop model chains on the three trial sites, the bench tasks that produced the label above, the soil sensitivity run, the observation region |
| Not yet executed | L1, A2, H1 (tasks with people) and R1. H2 was not attempted |
| Not yet demonstrated | Predictive skill: the bench failed, so the model says nothing about the wheat option here. Validity outside the three trial sites. Nothing in this repository shows either |

## Open the page without a network

```
python3 -m http.server -d site 8000      # then open http://localhost:8000
```

Everything the page needs is in `site/`, Alpine.js included (`site/vendor/`). How we know is `http://localhost:8000/how-we-know.html`. The fictitious example of the tests opens from the repository root: serve it with `python3 -m http.server 8000` and open `/site/index.html?data=../tests/fixtures/site_example/`. A banner then says it is fictitious.

## Reproduce

From the repository root, with `.venv` (versions in `requirements.lock`) and `.venv-engine` for the crop model (`requirements-engine.lock`). There are three levels.

1. **The floor from the cache.** `make data`, `make hls`, `make nasadem`, `make gloreda` fill `data/` (internet, and an Earthdata login in `~/.netrc` for HLS and NASADEM). `AFTERCROP_OFFLINE=1 make data` then checks the cache with no network (exit 0, `network_calls=0`). `make -k piso` builds the page without the bench. Its decision base reads the trial transcription of step 2. Without it, `DECISION_TRIALS=example` builds the page on fictitious gains, labelled as such.
2. **The bench with your own transcription.** The trial values come from Embrapa Soja Documentos 398, 459 and 476, public PDFs. They are not shipped here until the rights are clear. Transcribe them twice, into `data/manual/trials_a.csv` and `trials_b.csv` (the two are compared by `tests/test_trials.py`), then run `make -k chains` and `make -k bench`. Trial runs are refused unless the protocol is tagged.
3. **The same sha256.** Byte-equal outputs need the same cache. `site/data/manifest.json` and `site/data/manifest_files.json` hold the digest of every raw file, so a different download gives different bytes even with the same code.

`make test` needs nothing: tests marked "data" skip when the cache is absent.

## Method

Fixed in [PROTOCOL.md](PROTOCOL.md) before any result (`protocol-v1`), with one declared amendment, `protocol-v1.1`, before any bench result (field choice fallback, section 15). The public repository holds the final version only. The order of protocol and results cannot be checked from it. The decision rule is public and runs in the browser (`site/js/decision.mjs`). The crop model (AquaCrop-OSPy 3.1.0) only checks the direction of the wheat option against 10 trial site-seasons, and never enters the money.

## Other regions

**Observation region (SA-D79).** One field in the Free State, South Africa, read by the same end-of-green rule, with NASADEM relief and NASA POWER climate. No field trials and no zoning there, so no verdict. Open `http://localhost:8000/index.html?data=data_regions/free_state/`. Config `config/region_free_state.yaml`, field `config/regions/free_state/fields.geojson`, site files in `site/data_regions/free_state/` (never `site/data/`).

**Add an observation region.** Copy `config/region_free_state.yaml` to `config/region_<id>.yaml` with its own `paths:` block, build its basemap (`make basemap REGION=<id>`) and draw the field polygon by hand on it into `config/regions/<id>/fields.geojson`, add its texts to `config/copy_regions_en.yaml` and `copy_regions_pt.yaml`, then run `make observation REGION=<id>` and open `index.html?data=data_regions/<id>/`. The default region stays `config/region_norte_pr.yaml`.

**A region with a verdict** needs its own Regional Pack: its zoning calendar, its published trials and its own protocol, tagged before any simulation. Where no public trial exists, the path is a network of on-farm trials.

## Data and licenses

See [LICENSES.md](LICENSES.md). Downloaded files are untrusted data: they are read as text, tables or rasters and never executed.

## Use of Super Intelligence

See [AI_USE.md](AI_USE.md).

## Limits

Ten trial site-seasons from three sites on very clayey soil. Exploratory, not blind. The model failed its check here. No real farmer has used the tool. The satellite shows when the soybean canopy left a field, never the sowing date, and never says "inside the window". The prices on the page are examples.

## Layout

| Path | What it holds |
|---|---|
| `config/` | Region configs, every screen text (`copy_*.yaml`), decision settings with their sources |
| `pipeline/adapters/` | Zarc, NASA POWER, SoilGrids, HLS-VI, NASADEM, GloREDa, with a `.meta.json` per raw file |
| `pipeline/fields/` | Basemap, field geometry, NDVI series, ribbon, green-exit interval, noise, relief, soil, field choice |
| `pipeline/engine/` | Crop model chain, weather, soil, crops, sowing rule, run regimes |
| `pipeline/bench/` | The bench tasks, labels and the scoreboard |
| `pipeline/decision/`, `pipeline/ledger/`, `pipeline/build/` | Decision precompute, evidence ledger, manifest, checks, copy JSON |
| `site/` | The page, How we know, the agronomist sheet |

## Publish

`.github/workflows/pages.yml` publishes the `site/` folder, as committed, to GitHub Pages on a push to `main` or by hand. Every path in the site is relative. After a deploy, `make verify-link URL=https://<owner>.github.io/<repository>/` checks that the page, `data/copy.json`, `data/scoreboard.json` and `data/manifest.json` answer 200 and match this checkout byte for byte (sha256). It only reads.

## License

Apache-2.0, see [LICENSE](LICENSE). Copyright 2026 The Aftercrop team. Third-party code and data keep their own licenses, listed in [LICENSES.md](LICENSES.md).
