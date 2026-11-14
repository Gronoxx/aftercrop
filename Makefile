# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
# Run every target from the repository root. One target per task (plan, P0.3).
# Offline check of the cache (M1.1):  AFTERCROP_OFFLINE=1 make data
#
# Main runs (plan P0.3, 16.3). Run them with make -k, so one failure never stops the others:
#   make -k piso      the page without the chain (basemap, decide, scoreboard, copy, manifest, ledger, check)
#   make -k chains    engine runs into data/runs/. Trial runs are refused until protocol.status is "tagged"
#   make -k smoke     one short field chain per arm (M3.9), into data/runs/smoke/
#   make -k bench     every bench task (one target each), then the scoreboard
#
# Rehearsal (SA-D63, plan P5): real NASA data on development-strip fields,
# fictitious trials, every document stamped run_mode "rehearsal", nothing in site/data/:
#   make -k piso REHEARSAL=1 FIELDS_GEOJSON=config/fields/rehearsal.geojson SITE_OUT=data/rehearsal/site_data
#   make serve-rehearsal    then open the URL it prints
# A bench task that fails on infrastructure writes "not_completed" in data/bench/tasks/<ID>.json.

PY := .venv/bin/python

ENGINE_PY := .venv-engine/bin/python

# Region switch (SA-D79). Empty by default: every target reads config/region_norte_pr.yaml.
# REGION=free_state exports AFTERCROP_CONFIG=config/region_free_state.yaml, which
# pipeline.adapters.base reads: that region's own raw and interim folders (its paths: block).
REGION ?=
REGION_CONFIG = $(if $(strip $(REGION)),config/region_$(strip $(REGION)).yaml)
ifneq ($(strip $(REGION)),)
export AFTERCROP_CONFIG := $(REGION_CONFIG)
endif

.PHONY: clean-field-interim test test-py test-engine test-js data zarc power soilgrids hls basemap nasadem gloreda manifest ledger assemble sheet-pdf copy-json serve draw fields-check
.PHONY: piso decide field-inputs basemap-site soil relief slopes select engine-config chains chain chains-base chains-a1 chains-p1 chains-field smoke bench scoreboard l0 l1 t0 t1 p1 a1 a2 a3 n1 r1 h1

test: test-py test-engine test-js

test-py:
	$(PY) -m pytest -q

test-engine:
	$(ENGINE_PY) -m pytest -q tests/test_engine_*.py

test-js:
	node --test tests/decision.test.mjs tests/run_states.test.mjs tests/sheet.test.mjs tests/site.test.mjs tests/field_order.test.mjs tests/publish.test.mjs tests/zarc_class.test.mjs tests/sensitivity.test.mjs tests/observation.test.mjs

data:
	$(PY) -m pipeline.adapters.fetch_all

zarc:
	$(PY) -m pipeline.adapters.fetch_all --only zarc

power:
	$(PY) -m pipeline.adapters.fetch_all --only power

soilgrids:
	$(PY) -m pipeline.adapters.fetch_all --only soilgrids

hls:
	$(PY) -m pipeline.adapters.hls --workers 4

basemap:
	$(PY) -m pipeline.fields.basemap

nasadem:
	$(PY) -m pipeline.adapters.nasadem

gloreda:
	$(PY) -m pipeline.adapters.gloreda

manifest:
	$(PY) -m pipeline.build.manifest --out $(SITE_OUT)/manifest.json --files-out $(SITE_OUT)/manifest_files.json $(REHEARSAL_FLAG)

ledger:
	$(PY) -m pipeline.ledger.build --m5-ledger $(BUILD)/ledger_m5.json --strict --site-data $(SITE_OUT) $(REHEARSAL_FLAG)

assemble:
	$(PY) -m pipeline.build.assemble --site-data $(SITE_OUT) $(REHEARSAL_FLAG)

# Both languages (P2.3): build/sheet_en.pdf and build/sheet_pt.pdf.
sheet-pdf:
	$(PY) tests/test_sheet.py --pdf build/sheet.pdf --lang both && pdfinfo build/sheet_en.pdf && pdfinfo build/sheet_pt.pdf

copy-json:
	$(PY) -m pipeline.build.copy_json

serve:
	python3 -m http.server -d site 8000

# The rehearsal folder lies outside site/, so the server runs from the repository root and the
# page reads it with ?data= (copy.json still comes from site/data/).
.PHONY: serve-rehearsal
serve-rehearsal:
	@echo "open http://localhost:8000/site/index.html?data=../$(SITE_OUT)/"
	python3 -m http.server 8000

# Drawing tool for the field candidates: open http://localhost:8000/tools/draw_fields.html
draw:
	python3 -m http.server 8000

fields-check:
	$(PY) -m pipeline.fields.geometry config/fields/candidates.geojson

# ---------------------------------------------------------------------------------------------
# Paths. The tests override CONFIG, DATA and SITE_DATA to run on a synthetic tree.
CONFIG := config/region_norte_pr.yaml
DATA := data
SITE_DATA := site/data
SITE_IMG := site/img
RUNS = $(DATA)/runs
# REHEARSAL=1 (SA-D63): the site documents go to SITE_OUT (default data/rehearsal/site_data, never
# site/data, which every rehearsal CLI refuses), the build files and the scoreboard sidecars to
# data/rehearsal/, the basemap next to the documents (app.mjs reads <data folder>img/).
REHEARSAL ?=
REHEARSAL_ON = $(filter 1,$(strip $(REHEARSAL)))
REHEARSAL_FLAG = $(if $(REHEARSAL_ON),--rehearsal)
REHEARSAL_DIR = $(DATA)/rehearsal
SITE_OUT ?= $(if $(REHEARSAL_ON),$(REHEARSAL_DIR)/site_data,$(SITE_DATA))
BASEMAP_IMG = $(if $(REHEARSAL_ON),$(SITE_OUT)/img,$(SITE_IMG))
BUILD = $(if $(REHEARSAL_ON),$(REHEARSAL_DIR)/build,$(DATA)/build)
BENCH_DIR = $(if $(REHEARSAL_ON),$(REHEARSAL_DIR)/bench,$(DATA)/bench)
TASKS = $(BENCH_DIR)/tasks

# Open points the protocol does not hold yet [DECIDE, PROTOCOL.md D6, D7, D12]. An empty one makes
# the target that needs it stop with a message naming it (bench tasks: "not_completed").
L0_FIELD ?= a b c
N1_PERCENTILE ?=
N1_METHOD ?=
R1_BAND ?=
R1_METHOD ?=
A2_THRESHOLD_MM ?=
A2_SPAN ?=
A2_SITE ?=
RAIN_AFTER_NOT_BEFORE ?=
TRIAL_DATES ?= config/trial_dates.json
FIELDS ?= $(L0_FIELD)
# Field chains for L0, decided by the team lead (R2) before T0, T1, P1 and A1 ran, from
# sourced values (late note): the three product fields, the POWER cell that holds them, field
# capacity as in D7, corn and wheat sowing at the Doc 476 medians (config/decision_settings.yaml),
# one common soybean-corn prefix (protocol.chains.field.common_prefix), and every target soybean
# y/(y+1) of the ribbon seasons whose chain ends by 2025-12-31.
FIELD_YEARS ?= 2018,2019,2020,2021,2022,2023,2024
FIELD_PREFIX_YEARS ?= 1
FIELD_CORN_SOWING ?= 03-22
FIELD_WHEAT_SOWING ?= 05-24
FIELD_INITIAL ?= field_capacity
FIELD_WEATHER_SITE ?= londrina
FIELD_SETUPS ?= base,cn_rew
# Field files (plan 8.5). The candidates are drawn by hand, select keeps Field A, B and C.
CANDIDATES_GEOJSON ?= config/fields/candidates.geojson
FIELDS_GEOJSON ?= config/fields/fields.geojson
SLOPES_JSON ?= $(DATA)/interim/candidates_slopes.json
# field-inputs: the decision base JSON, written by make decision-base (P2), and the POWER site of
# the field window.
DECISION_BASE ?= $(BUILD)/decision_base.json
POWER_SITE ?=
# Decision step (pipeline.decision.precompute), as its module documents it. field-inputs writes
# its inputs file first.
DECIDE_INPUTS ?= $(BUILD)/precompute_inputs.json
ZARC_CSV ?= $(DATA)/interim/zarc_norte_pr.csv
DECIDE_FLAGS ?= --real

comma := ,
empty :=
space := $(empty) $(empty)
# $(call opt,--flag,value): the flag and its value, or nothing when the value is empty.
opt = $(if $(strip $(2)),$(1) $(2))
# $(call require,VARS): stop the recipe, naming the empty variables.
missing = $(strip $(foreach v,$(1),$(if $(strip $($(v))),,$(v))))
require = @$(if $(call missing,$(1)),echo "make $@: set $(call missing,$(1)) (open points, see the Makefile) [DECIDE]" >&2; exit 2,true)

# The floor (plan 8.4 step 12, 16.3): everything the page needs without the chain, after the
# fields are drawn and selected. The scoreboard reads the task files there are, so before the
# bench it says "not completed".
piso: clean-field-interim basemap-site soil relief geometry series ribbon events noise decision-base field-inputs decide scoreboard copy-json manifest ledger assemble

# The field files of data/interim/ carry no field window or mode in their names, so a run of the
# other mode (rehearsal or product) would leave them behind. piso starts from none of them.
clean-field-interim:
	rm -f $(DATA)/interim/field_*_ndvi.csv $(DATA)/interim/field_*_ribbon.csv $(DATA)/interim/field_*_green_end.csv \
		$(DATA)/interim/field_*_soil.json $(DATA)/interim/field_*_relief.json $(DATA)/interim/noise_floor.json \
		$(DATA)/interim/fields_geometry.json

# Fields (P1) ---------------------------------------------------------------------------------
# The M2 chain of the fields file, in this order, before field-inputs. FIELDS_GEOJSON (above) is
# the polygon file. FIELDS is taken: it holds the engine field ids. REHEARSAL=1 runs the
# development strip (east of protocol.field.development_strip.lon_min, SA-D63) and refuses product
# polygons. Without it every polygon must lie west of protocol.field.product_area.lon_max.
FIELDS_MODE = $(REHEARSAL_FLAG)
.PHONY: geometry series ribbon events noise

# Polygons on the basemap grid: data/interim/<stem>_geometry.json, read by field-inputs.
geometry:
	$(PY) -m pipeline.fields.geometry $(FIELDS_GEOJSON) $(FIELDS_MODE)

series:
	$(PY) -m pipeline.fields.ndvi_series $(FIELDS_GEOJSON) $(FIELDS_MODE)

ribbon:
	$(PY) -m pipeline.fields.ribbon $(FIELDS_GEOJSON) $(FIELDS_MODE)

events:
	$(PY) -m pipeline.fields.events $(FIELDS_GEOJSON) $(FIELDS_MODE)

# N1 inputs: data/interim/noise_floor.json. The interval point and percentile come from the protocol.
noise:
	$(PY) -m pipeline.fields.noise $(FIELDS_GEOJSON) $(FIELDS_MODE)

# M2 field steps (pipeline/fields/). They read and write data/interim/ (pipeline.adapters.base).
soil:
	$(PY) -m pipeline.fields.soil_profile $(FIELDS_GEOJSON)

relief:
	$(PY) -m pipeline.fields.relief $(FIELDS_GEOJSON)

# Slopes of the candidates, the input of select (median slope of each candidate).
slopes:
	$(PY) -m pipeline.fields.relief $(CANDIDATES_GEOJSON) --slopes $(SLOPES_JSON)

select:
	$(PY) -m pipeline.fields.select $(CANDIDATES_GEOJSON) --slopes $(SLOPES_JSON)

# M2 to M5: the precompute inputs of the real fields, to data/build/precompute_inputs.json.
field-inputs:
	$(call require,DECISION_BASE)
	$(PY) -m pipeline.decision.field_inputs $(FIELDS_GEOJSON) --base $(DECISION_BASE) --build-out $(BUILD) $(call opt,--power-site,$(POWER_SITE)) $(call opt,--n1-percentile,$(N1_PERCENTILE)) $(call opt,--n1-method,$(N1_METHOD)) $(REHEARSAL_FLAG)

# M5: the site documents to site/data/, the M5 sidecars to data/build/.
decide:
	$(PY) -m pipeline.decision.precompute --inputs $(DECIDE_INPUTS) --out $(SITE_OUT) --build-out $(BUILD) --zarc $(ZARC_CSV) $(DECIDE_FLAGS) $(REHEARSAL_FLAG)

# The basemap of the field window (sources.hls.window.id) for the page.
HLS_WINDOW = $(eval HLS_WINDOW := $(shell $(PY) -c "import sys, yaml; print(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))['sources']['hls']['window']['id'])" $(CONFIG) 2>/dev/null))$(HLS_WINDOW)

basemap-site:
	@test -n "$(HLS_WINDOW)" || { echo "make basemap-site: no sources.hls.window.id in $(CONFIG)" >&2; exit 2; }
	$(if $(REHEARSAL_ON),$(PY) -m pipeline.common.rehearsal check-out $(SITE_OUT))
	mkdir -p $(BASEMAP_IMG)
	cp $(DATA)/interim/basemap_$(HLS_WINDOW).png $(BASEMAP_IMG)/basemap.png
	cp $(DATA)/interim/basemap_$(HLS_WINDOW).json $(BASEMAP_IMG)/basemap.json

# M3 engine (.venv-engine has no PyYAML: engine-config writes the config as JSON first).
ENGINE_CONFIG = $(RUNS)/config.json
ENGINE_ARGS = --config $(ENGINE_CONFIG) --data $(DATA) $(call opt,--rain-after-not-before,$(RAIN_AFTER_NOT_BEFORE))
FIELD_ARGS = $(foreach f,$(FIELDS),--field $(f)) --prefix-years $(FIELD_PREFIX_YEARS) --corn-sowing $(FIELD_CORN_SOWING) --wheat-sowing $(FIELD_WHEAT_SOWING) --initial $(FIELD_INITIAL) --weather-site $(FIELD_WEATHER_SITE)
FIELD_NEEDS = FIELDS FIELD_YEARS FIELD_PREFIX_YEARS FIELD_CORN_SOWING FIELD_WHEAT_SOWING FIELD_INITIAL FIELD_WEATHER_SITE

engine-config:
	mkdir -p $(RUNS)
	$(PY) -m pipeline.engine.cli config-json --config $(CONFIG) --out $(ENGINE_CONFIG)

# Trial inputs no adapter writes (SA-D65): data/interim/trial_<site>_soil.json (Doc texture, then
# SoilGrids at the trial point, source field by field) and POWER at the printed Floresta point
# (protocol.side_runs, download only). Network through base.cached_get(), then offline.
.PHONY: trial-soil power-floresta-printed
trial-soil:
	$(PY) -m pipeline.adapters.trial_inputs soil

power-floresta-printed:
	$(PY) -m pipeline.adapters.trial_inputs power-printed

chains: chains-base chains-a1 chains-p1 chains-field

chain: chains

# Trial chains: base (T0, T1), a1 (A1), p1 (P1). Refused while the protocol is a draft.
chains-base chains-a1 chains-p1: engine-config
	$(ENGINE_PY) -m pipeline.engine.cli trials $(ENGINE_ARGS) --trial-dates $(TRIAL_DATES) --variants $(subst chains-,,$@)

# Field chains S1 and S3, run twice, with the CN 61 and REW 9 side run (L0).
chains-field: engine-config
	$(call require,$(FIELD_NEEDS))
	$(ENGINE_PY) -m pipeline.engine.cli field $(ENGINE_ARGS) $(FIELD_ARGS) --years $(FIELD_YEARS) --setups $(FIELD_SETUPS) --repeat

# M3.9: one short chain per arm, the first field and year, base setup only.
smoke: engine-config
	$(call require,$(FIELD_NEEDS))
	$(ENGINE_PY) -m pipeline.engine.cli field $(ENGINE_ARGS) $(FIELD_ARGS) --years $(firstword $(subst $(comma), ,$(FIELD_YEARS))) --setups base --runs $(RUNS)/smoke

# Plumbing smoke outside Brazil (Free State, South Africa): a field chain on NASA POWER at a point
# far from every trial site, an invented clay soil, timing per crop season and the water balance
# closure in data/runs/smoke_outside/timing.json. Never a trial, never the Londrina cell.
SMOKE_OUT = data/runs/smoke_outside
.PHONY: smoke-outside
smoke-outside:
	$(PY) -m pipeline.adapters.smoke_outside prepare
	$(ENGINE_PY) -m pipeline.engine.cli field --config $(SMOKE_OUT)/input/config.json --data $(SMOKE_OUT)/input --runs $(SMOKE_OUT) --field smoke_clay --years 2016,2017,2018,2019,2020 --prefix-years 1 --corn-sowing after_harvest --wheat-sowing 05-01 --initial field_capacity --weather-site smoke_freestate --setups base --repeat
	$(ENGINE_PY) -m pipeline.adapters.smoke_outside summarize

# M4 bench: one target per task. bench runs them all with -k, then writes the scoreboard whatever
# happened: a task with no file counts as "not_completed" (it was planned).
BENCH_ARGS = --config $(CONFIG) --data $(DATA)
BENCH_TASKS := l0 l1 t0 t1 p1 a1 a2 a3 n1 r1 h1
BENCH_PLANNED := L0,L1,T0,T1,P1,A1,A2,A3,N1,R1,H1
PLANNED ?=
# The scoreboard carries no freeze time and no commit (SA-D104).

# Notes of every fix after the first result (protocol section 9): {"late_notes": [...]},
# copied into the scoreboard when the file exists.
LATE_NOTES ?= $(wildcard $(BENCH_DIR)/late_notes.json)

bench:
	-$(MAKE) -k $(BENCH_TASKS)
	$(MAKE) scoreboard PLANNED=$(BENCH_PLANNED)

scoreboard:
	$(if $(REHEARSAL_ON),$(PY) -m pipeline.common.rehearsal check-out $(SITE_OUT))
	$(PY) -m pipeline.bench.scoreboard --config $(CONFIG) --tasks $(TASKS) --planned "$(PLANNED)" --site-out $(SITE_OUT)/scoreboard.json --results-out $(BENCH_DIR)/results.json $(call opt,--extra,$(LATE_NOTES))

l0:
	$(PY) -m pipeline.bench.run_tasks L0 $(BENCH_ARGS) $(call opt,--field,$(subst $(space),$(comma),$(strip $(L0_FIELD)))) \
		--l0-settings "fields $(strip $(FIELDS)); years $(FIELD_YEARS); prefix_years $(FIELD_PREFIX_YEARS); corn $(FIELD_CORN_SOWING); wheat $(FIELD_WHEAT_SOWING); initial $(FIELD_INITIAL); weather $(FIELD_WEATHER_SITE); setups $(FIELD_SETUPS)"

l1 t0 t1 p1 a1 a3 h1:
	$(PY) -m pipeline.bench.run_tasks $@ $(BENCH_ARGS)

a2:
	$(PY) -m pipeline.bench.run_tasks A2 $(BENCH_ARGS) $(call opt,--a2-threshold-mm,$(A2_THRESHOLD_MM)) $(call opt,--a2-span,$(A2_SPAN)) $(call opt,--a2-site,$(A2_SITE))

n1:
	$(PY) -m pipeline.bench.run_tasks N1 $(BENCH_ARGS) $(call opt,--n1-percentile,$(N1_PERCENTILE)) $(call opt,--n1-method,$(N1_METHOD))

# R1 runs only if T1 passed (it reads data/bench/tasks/T1.json): keep it after t1.
r1:
	$(PY) -m pipeline.bench.run_tasks R1 $(BENCH_ARGS) $(call opt,--r1-band,$(R1_BAND)) $(call opt,--r1-method,$(R1_METHOD))

# Soil after the freeze (plan SA-D77) ----------------------------------------------------------
# soil-diagnosis (read only): the water limits the engine derives from each trial soil file (the
# one used and the SoilGrids side file) and from the field soils of L0, with the zero-yield count
# of the frozen trial runs, printed and written to data/bench/soil_diagnosis.json. It also reads the
# frozen L0 task file and the field chains (crops that died, with the lowest minimum temperature of
# the 10 days before, from the field chains' weather): existing outputs only, nothing is run.
# sensitivity-soil: ONE run, never repeated (it refuses a folder that already holds one). The trial
# chains base, A1 and P1 as chains-base, chains-a1 and chains-p1 run them, with the side files
# data/interim/trial_<site>_soil$(SENS_SOIL_SUFFIX).json as the soil and every other input the same
# (config JSON checked byte for byte against $(ENGINE_CONFIG), dates, weather, Ksat choice, code),
# into $(SENS_RUNS). Then T0, T1, P1 and A1 on those chains and L0 on the field chains of
# $(RUNS) (they do not use the trial soils), all into $(SENS_BENCH); the scoreboard builder computes
# the label there, and pipeline.bench.sensitivity copies it to $(SITE_OUT)/sensitivity.json marked
# "sensitivity after the freeze". Never writes $(RUNS), $(BENCH_DIR) or $(SITE_OUT)/scoreboard.json.
SENS_SOIL_SUFFIX ?= _soilgrids
SENS_RUNS ?= $(DATA)/runs_sens
SENS_BENCH ?= $(DATA)/bench_sens
SENS_BENCH_ARGS = $(BENCH_ARGS) --bench $(SENS_BENCH)
.PHONY: soil-diagnosis sensitivity-soil

soil-diagnosis:
	$(ENGINE_PY) -m pipeline.engine.soil_diagnosis --config $(ENGINE_CONFIG) --data $(DATA) --runs $(RUNS) \
		--out $(BENCH_DIR)/soil_diagnosis.json --fields $(subst $(space),$(comma),$(strip $(L0_FIELD))) \
		--bench $(BENCH_DIR) --weather-site $(FIELD_WEATHER_SITE)

sensitivity-soil:
	@test ! -e $(SENS_RUNS)/timing.json || { echo "make $@: $(SENS_RUNS) already holds the sensitivity run (SA-D77: one run only)" >&2; exit 2; }
	@test -e $(BENCH_DIR)/soil_diagnosis.json || { echo "make $@: run make soil-diagnosis first" >&2; exit 2; }
	mkdir -p $(SENS_RUNS) $(SENS_BENCH)
	$(PY) -m pipeline.engine.cli config-json --config $(CONFIG) --out $(SENS_RUNS)/config.json
	cmp $(SENS_RUNS)/config.json $(ENGINE_CONFIG)
	-$(ENGINE_PY) -m pipeline.engine.cli trials --config $(SENS_RUNS)/config.json --data $(DATA) $(call opt,--rain-after-not-before,$(RAIN_AFTER_NOT_BEFORE)) --trial-dates $(TRIAL_DATES) --variants base --trial-soil-suffix $(SENS_SOIL_SUFFIX) --runs $(SENS_RUNS)
	-$(ENGINE_PY) -m pipeline.engine.cli trials --config $(SENS_RUNS)/config.json --data $(DATA) $(call opt,--rain-after-not-before,$(RAIN_AFTER_NOT_BEFORE)) --trial-dates $(TRIAL_DATES) --variants a1 --trial-soil-suffix $(SENS_SOIL_SUFFIX) --runs $(SENS_RUNS)
	-$(ENGINE_PY) -m pipeline.engine.cli trials --config $(SENS_RUNS)/config.json --data $(DATA) $(call opt,--rain-after-not-before,$(RAIN_AFTER_NOT_BEFORE)) --trial-dates $(TRIAL_DATES) --variants p1 --trial-soil-suffix $(SENS_SOIL_SUFFIX) --runs $(SENS_RUNS)
	-$(PY) -m pipeline.bench.run_tasks L0 $(SENS_BENCH_ARGS) --runs $(RUNS) $(call opt,--field,$(subst $(space),$(comma),$(strip $(L0_FIELD)))) \
		--l0-settings "fields $(strip $(FIELDS)); years $(FIELD_YEARS); prefix_years $(FIELD_PREFIX_YEARS); corn $(FIELD_CORN_SOWING); wheat $(FIELD_WHEAT_SOWING); initial $(FIELD_INITIAL); weather $(FIELD_WEATHER_SITE); setups $(FIELD_SETUPS)"
	-$(PY) -m pipeline.bench.run_tasks T0 $(SENS_BENCH_ARGS) --runs $(SENS_RUNS)
	-$(PY) -m pipeline.bench.run_tasks T1 $(SENS_BENCH_ARGS) --runs $(SENS_RUNS)
	-$(PY) -m pipeline.bench.run_tasks P1 $(SENS_BENCH_ARGS) --runs $(SENS_RUNS)
	-$(PY) -m pipeline.bench.run_tasks A1 $(SENS_BENCH_ARGS) --runs $(SENS_RUNS)
	$(PY) -m pipeline.bench.scoreboard --config $(CONFIG) --tasks $(SENS_BENCH)/tasks --planned "L0,T0,T1,P1,A1" --site-out $(SENS_BENCH)/scoreboard.json --results-out $(SENS_BENCH)/results.json
	$(PY) -m pipeline.bench.sensitivity --scoreboard $(SENS_BENCH)/scoreboard.json --frozen $(SITE_OUT)/scoreboard.json --diagnosis $(BENCH_DIR)/soil_diagnosis.json --runs $(SENS_RUNS) --out $(SITE_OUT)/sensitivity.json

# ---------------------------------------------------------------------------------------------
# Publishing (P4). .github/workflows/pages.yml publishes site/ to GitHub Pages on a push to main.
# verify-link reads the published page, data/copy.json, data/scoreboard.json and
# data/manifest.json, and checks HTTP 200 and the sha256 of each against this checkout's site/.
# It sends nothing:  make verify-link URL=https://<owner>.github.io/<repository>/
.PHONY: verify-link
URL ?=

verify-link:
	@test -n "$(URL)" || { echo "make verify-link: set URL=https://<owner>.github.io/<repository>/" >&2; exit 2; }
	$(PY) -m pipeline.build.verify_link "$(URL)"

# Decision base (P2) -------------------------------------------------------------------------
# The decision base for field-inputs (pipeline.decision.base_file): fields with their IBGE
# municipality, the trials block (steps via precompute.steps_from_gains) and the values the
# protocol does not fix (DECISION_SETTINGS: config/decision_settings.yaml, every value with its
# source, or a plain JSON). DECISION_TRIALS=manual reads data/manual/trials_a.csv and is refused
# until protocol.status is "tagged". Before the tag use DECISION_TRIALS=example (fictitious gains,
# labelled "example, fictitious"). REHEARSAL=1 takes example, and the base is stamped "rehearsal".
DECISION_TRIALS ?= $(if $(REHEARSAL_ON),example,manual)
DECISION_SETTINGS ?= config/decision_settings.yaml
.PHONY: decision-base municipalities

decision-base:
	$(PY) -m pipeline.decision.base_file --trials $(DECISION_TRIALS) --fields $(FIELDS_GEOJSON) --zarc $(ZARC_CSV) --out $(DECISION_BASE) $(call opt,--settings,$(DECISION_SETTINGS)) $(REHEARSAL_FLAG)

# Municipalities of the field window from the IBGE mesh (data/interim/municipalities_<window>.json).
municipalities:
	$(PY) -m pipeline.adapters.ibge_malhas

# Presentation (R3) ---------------------------------------------------------------------------
# Demo video of 28 s at most (PROJETO-DEFINITIVO 15.4): headless Chrome films the local page,
# the English caption is HTML over it, ffmpeg joins the frames and ffprobe checks the length.
# The pitch text comes from config/pitch.json, chosen by label_id in the
# scoreboard of DEMO_DATA. It starts python3 -m http.server on DEMO_PORT if nothing answers
# there, and stops it at the end. Rehearsal: make demo-video DEMO_DATA=data/rehearsal/site_data/
# Team and challenge (title of the first 2.5 s, Space Apps 2026 guide) come from config/pitch.json.
# Override without editing it: make demo-video TEAM_NAME="Team" MEMBERS="Name One, Name Two".
# make hands these command-line variables to the tool as environment variables, so any quoting
# is safe. A name still in {BRACES} renders, with a loud warning. Every frame carries the SI
# watermark of pitch.json (overlays.watermark).
DEMO_DATA ?= site/data/
DEMO_PORT ?= 8000
DEMO_FIELD ?=
DEMO_OUT ?= build/video/aftercrop_demo.mp4
.PHONY: demo-video test-demo

demo-video:
	node tools/record_demo.mjs --data $(DEMO_DATA) --port $(DEMO_PORT) --out $(DEMO_OUT) $(call opt,--field,$(DEMO_FIELD))

test-demo:
	node --test tests/record_demo.test.mjs

# Observation region (SA-D79, plan 5.6 and 8.8) ----------------------------------------------
# One field of a region without trials: basemap, HLS-VI of the 8 ribbon seasons, NASADEM and
# POWER at the field point, then the ribbon, the relief and the climate lines into the region's
# paths.site_out (site/data_regions/<region>/). Never site/data/. No verdict:
#   make observation REGION=free_state     then open index.html?data=data_regions/free_state/
# The field polygon is drawn by hand on the basemap first (paths.fields of the region config).
.PHONY: observation copy-regions
observation:
	@test -n "$(REGION_CONFIG)" || { echo "make observation: set REGION, for example REGION=free_state" >&2; exit 2; }
	$(PY) -m pipeline.fields.basemap
	$(PY) -m pipeline.regions.observation all --config $(REGION_CONFIG)
	$(PY) -m pipeline.build.copy_json --regions

# Copy of the observation regions: site/data_regions/copy.json from config/copy_regions_*.yaml.
copy-regions:
	$(PY) -m pipeline.build.copy_json --regions
