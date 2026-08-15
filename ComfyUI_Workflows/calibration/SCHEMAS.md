# Calibration file contracts

All JSON documents use `schema_version: 1`.

## `batch_spec.json`

Required fields:

- `batch_id`: filesystem-safe unique batch name.
- `purpose`: human explanation of the experiment.
- `target`: control-atlas or named-expression target.
- `stage`: `baseline`, `single_control`, `interaction`, or related generated stage.
- `anchor`: path relative to the Lys character-sheet folder.
- `sample`: optional sampled-expression image; normally null during calibration.
- `fixed`: editor settings shared by all candidates.
- `candidates`: exactly twelve objects with unique `id`, blinded `label`, and a `controls` object.

The runner validates control names, numeric ranges, the candidate count, and the fixed AdvancedLivePortrait defaults.

## `manifest.json`

Top-level provenance includes the batch purpose/stage, source-spec path and hash, immutable anchor name/hash, optional sample name/hash, AdvancedLivePortrait revision, Comfy API address, creation/update timestamps, candidates, and randomized `review_order`.

Each candidate contains:

- `candidate_id`, label, visible controls, and complete effective parameters.
- Relative paths for image, native `.exp`, expression JSON/CSV, and exact API prompt.
- Comfy prompt ID, decoded-pixel hash, and normalized-expression hash.

The expression JSON records `e_shape: [1,21,3]`, tensor `e`, rotation `r`, scale `s`, translation `t`, 63 landmark-axis code rows, and an expression hash.

## `review_draft.json`, `review.json`, and `review.csv`

The JSON contains `batch_id` and exactly one review row for every manifest candidate. Each row has:

- `candidate_id`
- `identity`, `expression`, and `cleanliness`: null while unfinished or integer 1–5
- `keep`: boolean
- `notes`: text
- computed `weighted_score`: 40% identity, 40% expression, 20% cleanliness
- computed `passes`: Keep plus all three scores at least 4

The backend permits null values in drafts but requires all scores before finalization. CSV contains the same decisions and computed fields.

## `recipe_library.json`

`recipes` is keyed by approved expression name. A promoted recipe stores its source batch/candidate, visible and effective controls, review scores, native `.exp` asset, adaptive mask type, validated views, approval timestamp, and polish settings. Default polish settings are 1.5× scale, denoise `[0.08,0.12,0.16]`, fixed seed, and the locally available CyberRealistic Pony checkpoint.

## `control_atlas.json` and analysis files

The atlas associates each tested control value with its human scores, pass decision, tensor analysis, and provenance. Approved values drive interaction grids; excluded extremes cannot enter recipe searches.

`analysis.json` and `analysis_candidates.csv` contain tensor delta norm, affected landmark-axis codes, normalized strength, rotation delta, per-code slope, rotation slope, and approximate linearity for each one-control sweep.
