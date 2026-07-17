---
name: cxr-triage
description: >
  Validate a medical image before chest X-ray analysis. Determines: (1) whether
  the image is a chest X-ray or another modality, (2) the CXR projection/orientation
  (PA, AP, lateral, oblique), and (3) image quality issues such as rotation,
  motion blur, low exposure, foreign objects, or clipping. Returns a structured
  TriageResult. Always invoke this skill first before any CXR reasoning or
  localization.
license: Proprietary
compatibility: >
  Requires Python 3.11+. Requires anthropic>=0.45 and ANTHROPIC_API_KEY.
  Uses Claude claude-opus-4-6 vision capabilities directly (no external model endpoint needed).
metadata:
  author: hmgill
  version: "1.0"
  model: anthropic/claude-opus-4-6
allowed-tools: Bash(python:*)
---

# CXR Triage Skill

This skill performs pre-analysis validation of a medical image. It must be the
**first step in every CXR pipeline run**. Its outputs gate all downstream tools.

## What it evaluates

### 1. Image modality check
Determine whether the submitted image is a chest X-ray. Accept:
- Standard PA (posteroanterior) chest radiographs
- AP (anteroposterior) chest radiographs (portable/supine)
- Lateral chest radiographs

Reject:
- CT scans, MRI, ultrasound, echocardiograms
- Non-medical photographs
- Other radiograph types (abdominal, extremity, skull, spine, pelvis)
- Blank, corrupt, or unreadable images

### 2. Projection / orientation
Classify the CXR projection:

| Orientation | Key visual cues |
|---|---|
| `PA` | Heart appears normal-sized; scapulae project outside lung fields; patient upright |
| `AP` | Heart appears magnified; scapulae overlap lungs; often supine |
| `lateral` | Spine visible posteriorly; sternum anteriorly; lungs overlap |
| `oblique` | Intermediate projection |
| `unknown` | Cannot be determined from image alone |

### 3. Quality assessment
Identify and flag any of the following quality issues:

| Issue tag | Description |
|---|---|
| `rotation` | Patient/image rotated — spinous processes not midline |
| `low_exposure` | Image too dark, lung fields not visible |
| `overexposure` | Image too bright, contrast lost |
| `motion_blur` | Patient motion causing blurring |
| `clipping` | Lung apices, costophrenic angles, or borders cut off |
| `foreign_object` | Non-anatomical opacities (leads, tubes, jewellery) — note, do not flag expected lines/tubes as errors unless they obscure key anatomy |
| `poor_inspiration` | < 6 posterior ribs visible above diaphragm |
| `artefact` | Image processing artefacts, text overlays, watermarks |

## Quality grading

Assign one of three quality grades:

- `acceptable` — No significant quality issues; analysis will be reliable.
- `suboptimal` — One or more minor issues present; analysis may have reduced confidence.
- `non_diagnostic` — Image cannot be meaningfully analysed; stop pipeline.

## Output

Return a `TriageResult` (see `assets/triage_schema.json` for JSON schema):

```json
{
  "valid": true,
  "is_chest_xray": true,
  "orientation": "PA",
  "quality_issues": ["poor_inspiration"],
  "quality_grade": "suboptimal",
  "triage_notes": "PA projection confirmed. Mild underinspiration noted (5 posterior ribs). Analysis can proceed with reduced confidence for lower lobe pathology."
}
```

## Implementation notes

The skill uses Claude claude-opus-4-6's vision capability to inspect the image.
The prompt in `tools/cxr_triage/triage.py` instructs the model to respond **only** with
a JSON object matching the schema — no preamble, no markdown fences.

The image is passed as a base64-encoded content block.

See [tools/cxr_triage/triage.py](tools/cxr_triage/triage.py) for the implementation.
See [assets/triage_schema.json](assets/triage_schema.json) for the output schema.
See [references/REFERENCE.md](references/REFERENCE.md) for quality criteria details.
