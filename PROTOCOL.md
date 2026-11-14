# Aftercrop evaluation protocol (protocol-v1)

| | |
|---|---|
| Status | **Tagged locally as `protocol-v1` (SA-D71)**, before any simulation on the trial sites and before any read of the trial values |
| Owner of every number | The `protocol:` block of `config/region_norte_pr.yaml`. This file only explains it |
| Challenge | NASA Space Apps Challenge 2026, "Field Shift: Adapting Farms with NASA Data" |

The public repository holds the final version only. The order of protocol and results cannot be checked from it.

This draft was written with an SI tool (Claude, Anthropic) from the written project plan. The participant delegated the open points to the SI tool, which closed them with an outside review as a second input (SA-D61, section 15). The participant may veto any of them until the tag.

## 1. What this protocol fixes

Aftercrop has one engine contrast. It is the same next soybean, under the same climate, soil and window, after a different predecessor. The engine sees only the water mechanism. The bench checks that contrast against published Embrapa trials and the code computes the label. Nobody writes it.

This file fixes the tests, thresholds, nulls, alternatives, ties, seed, stop rule, labels and decision rule before any result exists. A change after the tag needs a new tag.

## 2. Declarations

The observed values have already been read.
The 10 trial values were read in the planning audit and again in two independent transcriptions.

The test is not blind.
The person who set the thresholds has seen the values they are tested against.

The test is exploratory, with a fixed rule.
The 10 site-seasons come from 3 sites and are not independent. The rule is fixed here, before the run.

The contrast and the thresholds derive from the same 10 values.
The T1 size threshold is the error of a constant fitted to those values. Nothing else checks the contrast.

The open points were closed by the SI tool.
The participant delegated them. Each closed point keeps its reason in section 15.

Only cycle and dates are adjusted.
The engine is never fitted to yield. The only crop keys that may differ from the library are listed in `protocol.engine.allowed_overrides`. Cycle keys follow published dates. Zmax and the heat ramp are set once in the protocol and never tuned.

Everything is published, including failures.
A failed test shows as failed. A task that could not run shows as "not completed".

## 3. Record of the work

The public repository holds the final version only. The order of protocol and results cannot be checked from it. The order this file states is protocol, amendment, frozen label.

## 4. Hash of the protocol block

protocol_block_sha256: 9e675acd6bbeade52bf2f730c29f74ddbbcbad7b7f7b1e789a758e51053c2752

The hash covers the UTF-8 bytes of `config/region_norte_pr.yaml` from the line `protocol:` to the end of the file. `protocol:` is the last top-level key. Reproduce it from the repository root with the system Python in isolated mode:

```sh
python3 -I -c 'import hashlib,sys; t=open(sys.argv[1],"rb").read(); i=t.index(b"\nprotocol:\n")+1; print(hashlib.sha256(t[i:]).hexdigest())' config/region_norte_pr.yaml
```

`tests/test_protocol.py::test_hash` recomputes it and compares it with the line above (criterion M0.4).

`site/data/scoreboard.json` and `site/data/sensitivity.json` keep the hash of the block the label was computed under, so the two hashes differ. Since then the block changed only in comments, in removed date and time fields, in dates cut from text values (and the git reference cut from `dates.local_tag`), in the names of the plan-state keys of `opening_checks` (same values), and in the wording of `field.development_strip.role` ("rehearsal only"). No threshold, test, label or rule value changed.

## 5. Data under test

Ten trial site-seasons, soybean after wheat against soybean after second-crop corn:

| Site | Source | Seasons |
|---|---|---|
| Londrina | Embrapa Doc 476 | 2021/22, 2022/23, 2023/24 |
| Floresta | Embrapa Doc 398 | 2012/13, 2013/14, 2014/15, 2015/16 |
| Cafelândia | Embrapa Doc 459 | 2020/21, 2021/22, 2022/23 |

- Fewer than 10 site-seasons gives "Model not validated here".
- Floresta values come from chart labels, with no CV. The season of each value is read from the axis label. Without a legible label, that site-season counts as missing.
- Outside the mechanism, and out of every test: barley, cowpea and intercrops. Corn with brachiaria (option S2) is an intercrop. See D5.
- Climate: NASA POWER daily, 1984-01-01 to 2025-12-31, full seasons only. Rain is `PRECTOTCORR` (MERRA-2 corrected). `IMERG_PRECTOT` is only a check.
- Coordinates: as in `trial_sites`, repeated in `protocol.site_coordinates`. See D2 and D3.

Missing data:

- A missing Floresta soybean date: both arms take the same date, the first date that meets the sowing rain rule in both arms.
- Infrastructure failure (code error, missing file, timeout) gives "not completed" for that task. It never counts as a fail.
- An engine outcome (zeroed season, no emergence, dead canopy) counts against the model.

## 6. Engine setup

- AquaCrop-OSPy 3.1.0, an unofficial Python implementation that mirrors most of FAO AquaCrop 7.1. Library crops SoybeanGDD, MaizeGDD and WheatGDD.
- Water-only model: only soil water carries over from one crop to the next. Canopy, biomass, roots and straw do not.
- One compartment geometry for every crop, at least 2.4 m deep. A new Soil object per instance. `calc_cn = 1`, `adj_rew = 0`.
- Zmax 1.0 m in every crop. Heat ramp `Tmax_lo`/`Tmax_up` at 40/45 °C in soybean and corn and 35/40 °C in wheat.
- Ksat measured at the surface, with the texture formula as sensitivity. See D1.
- CN 61 and REW 9, and the library Zmax, only as side runs.

Sowing:

- Trials use published dates.
- Field soybean: the first day d whose rain on d-2, d-1 and d sums to 20 mm or more, with all three days on or after the start date. The start is common to both arms. In S3, never before the wheat harvest plus 20 days. If the rule does not trigger by the latest date, the season counts as a failure. See D6.
- The difference in sowing days between arms goes to `artifacts.json`.

Before any delta is read, each arm records days to emergence, zeroed seasons and dead canopy.

## 7. Chains

A chain is one site-season in one arm under one climate. It is the accounting unit. The engine runs the chains of one site and one arm as one continuous run, in chronological order, years without a trial observation included. `python -m pipeline.engine.cli trials --dry-run` lists the runs (35: one per site and variant, and one per site, shift and offset in P1) and counts the chains. The bench has exactly 220 chains:

| Part | Chains | How |
|---|---|---|
| Base run | 20 | 10 site-seasons × 2 arms |
| A1 | 20 | 10 site-seasons × 2 arms, with restart |
| P1 | 180 | 9 shifts × 10 site-seasons × 2 arms |
| Total | 220 | |

Trial regime: the chain replicates the sequence of each trial in both arms, with locked parameters. The initial state is field capacity, the same in both arms, a standard condition and never an observed moisture. Warm-up: soybean, second-crop corn and soybean, common to both arms, before the first trial season. See D7.

- Crop end: the earliest of simulated maturity, published harvest and the day before the next sowing. The tested soybean ends on the same day in both arms. If its canopy dies first in one arm, that arm ends on that day. A warm-up crop that both arms share ends before the earlier of their next sowings.
- P1 warm-up: the warm-up follows the same 1985 to 2024 ring, year by year. A shifted year before 1985 takes the climate of that year plus 40, which keeps the leap years. The ring seam may fall in the warm-up, never in a tested season.
- Yield basis: 13% moisture. A dry yield is divided by 0.87. The basis each Doc declares is checked and recorded.

## 8. Bench tests

### L0 Sanity
Water balance closes within 1 mm at each crop swap. Two runs repeat exactly. No artifact is left unexplained. A failure blocks reading any delta and gives "Model not validated here".

### L1 Data
POWER and IMERG against INMET and IDR-Paraná stations in Londrina. Rain bias up to 10% per season, temperature bias up to 1 °C. Outside that, the series is labelled. It is never corrected in silence. In the event it stays "planned, not run": no station file is prepared (SA-D65).

### T0 Positive control
The soybean after second-crop corn arm of T1, simulated against the published mean in the 10 site-seasons. Statistic: Kendall S over the 10 items, and the sum of the S computed within each site. Passes with S >= 21 and a within-site sum above zero. S = 21 (tau 0.467) is the first S whose exact one-sided P for 10 items falls below 0.05. That P only sets the rule and never goes to the page. Without T0 passing, T1 gives no label.

### T1 Primary
Δ = soybean after wheat minus soybean after second-crop corn, in kg/ha, simulated against observed. Statistics: Kendall S and the RMSE of Δ. Passes with S >= 21 **and** an RMSE below that of the in-sample constant. The code computes that constant RMSE from the 10 transcribed values. The planning audit gives about 280 kg/ha. The leave-one-out constant (about 311 kg/ha) and the zero-Δ null (about 365 kg/ha) are reported beside it, never used as thresholds. The scoreboard says whether T1 failed on order (S) or on size (RMSE). Signs are reported and never approve anything.

### P1 Wrong year
- P1 uses B = 9 backward shifts: k = 4, 8, 12, 16, 20, 24, 28, 32 and 36 years, circular in 1985 to 2024. No range and no minimum.
- Each k shifts the climate of all site-seasons at once. Shift rule: shifted start year s = y - k, plus 40 when y - k is before 1985. The whole chain of the site-season moves by the same offset. Published dates keep month and day in the shifted years. A 29 Feb date moves to 28 Feb.
- Statistic: Kendall S, for T0 and T1 separately.
- Null hypothesis (H0): the climate of the right year adds nothing. The real S is exchangeable with the wrong-year S.
- Alternative (H1): one-sided. The real S is larger.
- `p = (1 + number of wrong-year S >= real S) / (B + 1)`, so p = (1 + count) / 10.
- Passes with p <= 0.10 in both T0 and T1, that is, with no wrong-year S equal to or above the real S.
- Ties count against.
- P1 draws nothing.
- 180 chains.
- Stop rule: run all 9 shifts, read no partial result, stop only at the hard stop. An incomplete P1 counts as not passed.
- A failure prevents "Direction supported". P1 measures response to the real climate, not forecasting.

### A1 With and without state
- Restart: start of the soybean instance, the day after the predecessor harvest. The fallow runs normally. Restarting at sowing would make the arms identical, with Δ = 0 by construction.
- The restart profile is the same in both arms and never zero water. See D8.
- Statistic: D = T1 Kendall S with state minus T1 Kendall S without state.
- Null hypothesis (H0): with and without state are exchangeable within each site-season.
- Alternative (H1): one-sided. D > 0.
- Paired swap over all 1,024 patterns (2^10), exhaustive, with no draw.
- `p = number of patterns with D* >= D, divided by 1024`.
- Passes with D > 0 and p <= 0.10, the same level as P1.
- Ties count against.
- 20 chains.
- A failure prevents "Direction supported".

### Other tests
- **A2 Rain:** L2 counts with `PRECTOTCORR` against `IMERG_PRECTOT`, from 1998-01-01 to 2025-09-30, both sides on the same period. After 09/2025 IMERG is not Final V07 and does not enter. No threshold. Reported in How we know.
- **A3 HLS:** states that change without HLS, on the fictional example, in the three price scenarios. Zero changes gives "context only".
- **N1 Noise floor:** the larger of the 90th percentiles (nearest rank) of field halves and of L30 against S30, in days, on the midpoint of each interval. The midpoint is a representative value, never shown as an observed date. Nothing below it reaches the screen.
- **Status "reported":** A2, A3, N1 and H1 have no threshold. They show as reported, never as passed.
- **H2 Agronomist:** not attempted, by the participant's decision (SA-D56). Status `not_run`. The sheet and How we know say there was no local review.
- **R1 Robustness:** planned, not run in the event: the engine has no R1 path (SA-D65), so L5 says "inconclusive". As designed, it runs only if T1 passes, with the 10 to 90 band by nearest rank. 50 Latin hypercube members, uniform, paired across arms and years, cut to 20 if needed. Ranges in `protocol.r1.ranges`. Inversion up to 20%. Equivalence ±166 kg/ha. L = the larger of 166 and the T1 RMSE. States: "lower water risk in the model" (P10 > +L and inversion <= 20%), "no water-related difference in the model" (P10 >= -166 and P90 <= +166), "inconclusive" for everything else, including R1 not run.

No p-value and no confidence interval goes to the page. The scoreboard shows the S within each of the 3 sites, leaving none out.

## 9. Seed and stop rule

- Seed: 20261114. It is the one integer for every random draw of the bench. Only R1 draws. P1 and A1 draw nothing.
- One Make target per task, run with `make -k`, so one failure does not stop the others.
- The bench runs right after the local tag (SA-D71). It takes minutes of machine time. The label freezes when T0, T1, P1 and A1 finish with no artifact, at the latest at 11:00 BRT on 15 November 2026, whatever the result. Hard stop at 10:45 BRT.
- A rerun only for an infrastructure error, with a note in `late_notes` and both results published.
- If the bench does not finish by the hard stop, the scoreboard says "not completed".

## 10. Labels

Computed by code, in this order:

| Condition | Label |
|---|---|
| L0 fails | Model not validated here |
| T0 not run or not completed | not completed |
| T0 fails, or fewer than 10 site-seasons | Model not validated here |
| T1 not run or not completed | not completed |
| T1 fails | Direction not validated |
| P1 or A1 fails | Direction matched, mechanism not shown |
| T0, T1, P1 and A1 pass | Direction supported in 10 trial site-seasons (exploratory, not blind) |

A fail means "not shown", never "refuted". The gain may live in roots, straw or disease, outside the engine. The magnitude of any gain always comes from the trials, never from the engine.

## 11. Decision rule and domain

Written before any result. For each option i (S2, S3), always against S1, on the field:

1. Hard rules and user restrictions remove the option and say why.
2. G_i in sc/ha from the farmer's numbers only, rounded up to steps of 1 sc/ha. An invalid essential field gives "Not enough evidence. We need your {field}".
3. N_i = trial site-seasons with gain >= G_i. n_i = site-seasons available.
4. State, in this order: G_i <= 0 gives Try it on a strip here. n_i < 5 gives Not enough evidence. N_i / n_i >= 0.7 gives Try it on a strip here. N_i / n_i <= 0.3 gives Keep what you do. Otherwise Tied.

The state does not depend on T1. The engine never touches this count.

Soil domain. Only data declared by the user excludes. SoilGrids never decides. Clay below 350 g/kg, or conventional tillage, gives Not enough evidence. Clay from 350 to 600 g/kg gives "Use with care". Clay above 600 g/kg is inside the domain (SiBCS 2018, p. 46 and 47).

Window warning. The end of green is the end of the first green cycle of the season, a proxy for the soybean phase and never a crop classification (section 15, D12). A season counts as "outside" only when the whole end-of-green interval falls after the end of the corn Zarc window, with zero delay. Without a soil report the field has no Zarc class, and the end used is the latest end of the corn window across every class and cycle group of the municipality ("for any soil class"). Text: "Satellite estimate: the first green cycle persisted beyond the corn Zarc window in {k} of {n} seasons." The warning shows "outside" in at least 3 of the 8 seasons. Inference is one-sided. "to confirm" and "no data" seasons stay out of k and n. The page never says "inside". The declared sowing date always rules over HLS.

Descriptive satellite and climate lines (SA-D72). None of them changes a state.
- Ended before: the seasons whose whole end-of-green interval ends before the end of the corn Zarc window, as k of n. Never "inside", never a sowing date.
- Risk at exit: the Zarc corn risk (%) of the ten-day period that holds the end of the interval, per season with status ok. With a declared soil class, that class. Without a report, the lowest and highest across every class and cycle group of the municipality. It describes when the field became free, never when corn was sown. In Cambé the risk does not rise with delay, and no text may say it does.
- Zarc risk band: the risk per ten-day period drawn above the 8-season ribbon, for the declared class or as the lowest to highest across classes.
- No green: the ten-day periods without green per season, median over the seasons, as N of 36. Periods with no clean image are counted apart.
- Climate, NASA POWER at the field cell, 1984 to 2025, over the sourced occupation dates of each crop: years with at least one day below 2 °C, and the rain total with its median, its 10th percentile (nearest rank) and the three driest years by name. Counts of the past, never a forecast.

Field order. The first field has the latest soybean exit in more than half of the comparable seasons, with a difference above N1. Only fields with the option in Try or Tied are compared. The seasons used to choose the fields stay out of the order. See D9.

Other fixed values: rural field window of 10 km by 10 km, inner buffer of 30 m, at least 20 clean pixels and half the inner pixels clean, erosion class "similar" within 5 percentage points of the year, frost alert below 2 °C minimum. Candidate fields only west of 51.25° W. The strip east of 51.245° W serves only the rehearsal. The NASA switch goes on the main page only if it changes at least 1 state on the real fields, otherwise its result goes to How we know.

Corn with grass (S2). The count uses 7 site-seasons from 2 sites, one species sown on the corn sowing day, with two establishment methods. The page shows the method and the count per method. If it cannot, S2 gives Not enough evidence.

## 12. H1 task test

H1 measures page comprehension, not farm impact. It is informal. Invitations go out in waves until 4 people confirm, for 3 slots of 3 minutes each (SA-D57).

Language: English, the version the judges see (SA-D60). The language of each session is recorded.

Profile: people who read English, with no farming background required, who have not seen the project and are not team members (SA-D60).

Slot rule: the 3 slots go to the first 3 confirmations, in the order they arrive.

Reserve rule: later confirmations form the reserve list, in the order they arrive. A reserve joins only if someone does not attend or does not finish the session, never to replace a result, until 15:45 on Sunday.

| Task | Passes when the person |
|---|---|
| 1. Say the answer in your own words | names the option and the state the page shows for it |
| 2. Say what you would test and where | names the option and a strip in a named field |
| 3. Read N of n as a count, not a chance | calls it a number of trial site-seasons, with no probability or percent chance |
| 4. Say one thing the tool does not know | names one limit the page states, such as roots, nitrogen, disease, nematodes or the yield of this field |
| 5. Say what you would ask the agronomist | asks one question about a local check listed on the agronomist sheet |

The pass criteria are fixed before the sessions. See D11.

## 13. Opening checks, done at the local tag (SA-D71)

Filled in `protocol.opening_checks`, with the result of each check and the state the plan recorded (criterion M0.6):

1. Last date of `IMERG_PRECTOT` at the Londrina point (2026-09-14 in the plan).
2. IMERG V08 in CMR (only V07 in the plan, Final ended 09/2025).
3. MERRA-2 and GEOS-IT boundary in the POWER `sources` field (MERRA2 on 2026-09-30, GEOSIT on 2026-10-01).
4. SoilGrids REST (503 in the plan, no date given). WCS stays the access path.

## 14. Declared limits

1. The test is not blind. The observed values were read in the audit.
2. Exploratory. Contrast and thresholds come from the same 10 values.
3. 10 site-seasons from 3 sites, not independent.
4. The engine sees only water. Wheat may dry the profile and corn may leave early. Water alone may give a null or opposite signal.
5. What is tested is not what goes to the screen. T1 uses plots, published dates and maintained systems. The field uses estimated dates, SoilGrids and a single switch.
6. Trials sit above 750 g/kg clay under no-till. A different soil triggers a warning.
7. P1 at p <= 0.10 accepts 10% false positives.
8. Ablation measures dependence, not accuracy.
9. T1 tests only S3 against S1. S2, L2, the erosivity class and the 0.7 and 0.3 cut-offs have no external test.
10. No real farmer will have used the tool by the submission.

## 15. Points closed by the SI tool

The participant delegated these points to the SI tool (SA-D61). An outside review, without access to the code, was the second input. Where the two differ, the reason is given. Each value is a project rule unless a source is named.

**D1, Ksat.** 60.5 mm/h, Barbosa et al. 2004 (RBCS 28(2)), Londrina, 760 to 820 g/kg clay, harrowed, Table 3, on 0 to 0.3 m. 78 mm/h (Tavares Filho et al. 2006) is the second source, with the same engine state. The texture formula is a sensitivity run. Neither value is called a measurement of the field.

**D2, Floresta.** 23°35' S, labelled "to confirm", as the probable typo of the printed 23°55' S. The printed point is kept as a side run. If T0 or T1 changes result with it, Floresta leaves the bench, and the rule of fewer than 10 site-seasons applies.

**D3, Londrina.** The trial point of Doc 476, at Embrapa Soja.

**D4, Zarc for wheat.** The 2025/26 table, as a historical reference labelled "Zarc 2025/26, latest with wheat", never called the 2026/27 zoning. Rechecked in the first hour of 14 November.

**D5, S2.** Out of T1 and of the engine. The engine has no intercrop, and brachiaria is never simulated by proxy.

**D6, rain rule.** 1 September to 31 December, the legal soybean window of Região 2 (Adapar), which includes the north of Paraná. The three rain days count only from the minimum date.

**D7, trial chains.** One continuous run per site and arm, in chronological order, with the years without a trial observation. Warm-up of soybean, second-crop corn and soybean, common to both arms. Field capacity as the initial state.

**D8, A1 restart.** Field capacity in every compartment, a standard condition of the control.

**D9, seasons to choose the fields.** 2025/26 only. With no ok series, no silent switch to another season.

**Amendment protocol-v1.1 (after the tag protocol-v1 and before any bench result).** The real run found only 1 of the 10 product candidates ok in 2025/26: cloud gaps of 34 to 40 days between clean observations from 1 December to the exit. That is a rule outcome, not an infrastructure error. Without a change no Field A or B exists, and the page has no fields. The amendment, declared and not silent: if fewer than 2 candidates are ok in the choice season, the next older season is used alone, one season at a time, 2024/25 then 2023/24 then 2022/23, until Field A and B exist. Every season tried is published in the selection file, and the season used stays out of the field order. No threshold of the end-of-green rule changes. At the time of the amendment the trial chains had been simulated but no simulated value had been compared with any trial value. The amendment was decided at execution level and is open to the review of the participant's delegate (SA-D61).

**D10, ribbon.** `ndvi_green` 0.30, absolute, for the ribbon cells and for "canopy seen". `min_clean_frac` 0.5, on top of the 20 clean pixels. Both are project rules, not validated for HLS here.

**D11, H1.** The five tasks and criteria of section 12, fixed before the sessions. A reserve only covers an absence, never an unfavourable result.

**D12, end of the first green cycle (SA-D62).**
- M(t) is the largest clean NDVI of the field from 1 September to the date t.
- The exit is the first pair of consecutive clean observations (low, high), with high from 1 December to 31 May, where NDVI(low) is above 0.5 M and NDVI(high) is at or below 0.5 M, with a drop M minus NDVI(high) of at least 0.3.
- The cycle must have started in the soybean window: a clean observation at or below half height, from 1 September to 31 December, before the peak. Otherwise "to confirm".
- Rebound: if a clean observation within 10 days after high comes back to M minus 0.1 or more, the pair is dropped and the search goes on.
- "ok" only if no gap between clean observations, from 1 December to high, is longer than 25 days (20 in the draft, see below). Otherwise "to confirm". With no pair by 31 May: "to confirm" if a canopy was seen, "no data" if there was no clean observation.
- The second green cycle stays in the data and in the ribbon. It does not change the status, and it never confirms that the first cycle was soybean.
- Why not 10% of the season amplitude, as the review proposed: between soybean and corn the NDVI rarely falls that low before the corn rises, so the two cycles would merge and the corn exit would be read as the soybean exit. Rodigheri et al. 2023 (Remote Sensing 15, 5366) also report that no method handled double cropping well. The half height follows the MCD12Q2 MidGreendown (50% of the segment amplitude), adapted to a running maximum. The 10% variant is computed in the rehearsal and reported beside.
- The only adjustment allowed before the tag: the gap from 20 to 25 days, if fewer than half of the seasons of the development strip come out ok. Applied: the rehearsal gave 9 of 24 field-seasons ok with 20 days and 12 of 24 with 25. The gap is now 25 days, with no further change.
- Rehearsal finding: in 21 of the 24 field-seasons the dip between soybean and corn fell below the 10% variant too, so both rules found the same exit (the 10% one 0 to 25 days later, median 10). In 3 the cycles merged under the 10% rule. The v2 rule stays.
- The original interval is kept in every output.

**Further rules closed with D12.**
- N1 at the 90th percentile, R1 band 10 to 90, nearest rank, interval midpoint for N1.
- Relief `ring_m` and `drop_m`: left unset. The low-spot alert says "rule not set".
- Dry autumn, cold period and occupation dates: only with a threshold and a source. Without one, the variable goes.
- L0 failed gives "Model not validated here". A2, A3, N1 and H1 show as "reported".
- Field soil class for the Zarc (SA-D64): from a soil report only, the declared class AD1 to AD6, or sand, silt and clay of 0 to 40 cm by the IN MAPA 1/2022 formula. SoilGrids never gives a class. Without a report the class is unknown.
- Field municipality: from the IBGE municipal mesh, by the polygon.
- Slope: the median.
- GloREDa: the erosivity line stays only if the monthly shares match the local source within 5 percentage points. Otherwise it goes.

## 16. Rules this draft adds where the plan is silent

- The hash definition in section 4.
- The three-day window of the rain rule lies wholly on or after the start date.
- The P1 shift arithmetic, the whole chain moving by one offset and the 29 February rule.
- The T1 constant RMSE is computed by code from the transcription, with the audit figure beside it.
- The seed value, 20261114, the event start date, chosen before any run.
