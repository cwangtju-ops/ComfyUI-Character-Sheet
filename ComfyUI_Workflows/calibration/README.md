# Expression Parameter Calibration

This directory contains the deterministic AdvancedLivePortrait calibration
system used by Expression Wizard. Calibration uses raw `ExpressionEditor`
output; masking, upscaling, and image washing are reserved for approved
finalists.

Character anchors are read from the repository root. Generated batches are
written to `ComfyUI_Generated/Calibration/`.

## Reviewer

Expression Wizard serves the existing reviewer routes alongside its Explore
page. You can also start the reviewer by itself:

```powershell
python .\ComfyUI_Workflows\calibration\serve_reviews.py
```

For every candidate the reviewer records:

- Identity preservation, 1–5.
- Expression usefulness, 1–5.
- Artifact cleanliness, 1–5.
- Keep/reject and free-form notes.

Final review requires all three scores for all twelve candidates. A candidate
passes only when every score is at least 4 and Keep is checked.

## Commands

```powershell
$tool = '.\ComfyUI_Workflows\calibration\calibrate.py'
python $tool status
python .\ComfyUI_Workflows\calibration\audit_calibration.py --live-replay
python $tool build-atlas eyes_v1 brow_gaze_v1 mouth_phonemes_v1 smile_v1
python $tool make-interaction soft_smile
python $tool generate .\ComfyUI_Workflows\calibration\specs\generated\interaction_soft_smile_r0.json
python $tool make-refinement interaction_soft_smile_r0
python $tool promote interaction_soft_smile_r0 soft_smile
python $tool validate-views soft_smile
python $tool polish soft_smile
```

Set `ADVANCED_LIVEPORTRAIT_DIR` or `COMFYUI_DIR` if you want generated
manifests to record the installed plugin revision.

## Determinism and safety

- Batch specifications declare exactly 12 unique candidates.
- Only explicitly declared controls differ from fixed settings.
- Defaults are `src_ratio=1`, `sample_ratio=0`, `OnlyExpression`, and
  `crop_factor=1.7`.
- Generation resumes safely unless `--force` is supplied.
- Images, prompts, tensors, and source files are hashed in the manifest.
- No model or upscaler is downloaded.
- Human review is authoritative.

See [SCHEMAS.md](SCHEMAS.md) for the file contracts.