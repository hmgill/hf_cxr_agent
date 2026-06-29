# CXR Triage — Technical Reference

## Model

Claude claude-opus-4-6 via the Anthropic Python SDK (`anthropic>=0.45`).  
The model is invoked with a structured system prompt that demands JSON-only output.

## Why Claude for triage (not NV-Reason-CXR-3B)?

NV-Reason-CXR-3B is optimised for pathology reasoning on *confirmed* CXRs.
Sending non-CXR images to it produces unreliable outputs and wastes NIM quota.
Claude claude-opus-4-6's general-purpose vision is better suited for the binary
classification and quality assessment tasks that triage requires.

## Orientation classification logic

| Feature | PA | AP |
|---|---|---|
| Heart size | Normal (< 50% of thoracic width) | Magnified (magnification effect) |
| Scapulae | Outside lung fields | Overlap lung fields |
| Patient position | Upright | Often supine or semi-recumbent |
| Clavicles | Level, symmetrical | Often asymmetric / rotated appearance |

**Lateral** views: spine visible posteriorly, sternum anteriorly, both lungs overlap.

When AP vs PA cannot be determined from image features alone, use `unknown`.

## Quality issue reference

| Tag | Threshold for flagging |
|---|---|
| `rotation` | Spinous processes visibly deviated from midline vertebral bodies |
| `low_exposure` | Lung parenchyma not visible; vertebrae not seen through cardiac shadow |
| `overexposure` | Soft tissue detail lost; ribs barely visible |
| `motion_blur` | Cardiac borders or diaphragm blurred |
| `clipping` | Any lung apex, costophrenic angle, or cardiac border cut off by image edge |
| `foreign_object` | External objects overlying key anatomy (ECG leads are normal; jewellery is not) |
| `poor_inspiration` | Fewer than 6 posterior rib ends visible above the diaphragm |
| `artefact` | Text overlays, watermarks, JPEG compression blocks, processing stripes |

## Quality grade assignment

| Grade | Criteria |
|---|---|
| `acceptable` | No flags, or only `foreign_object` (expected lines/tubes) |
| `suboptimal` | `poor_inspiration`, `rotation` (mild), or `low_exposure` (mild) |
| `non_diagnostic` | Severe `low_exposure`/`overexposure`, severe `motion_blur`, `clipping` of critical anatomy, or `is_chest_xray=false` |

## Error handling

- `FileNotFoundError`: image path does not exist
- `ValueError`: model returned non-JSON text (retry once before raising)
- Network/API errors: propagate from `anthropic.Anthropic` client

## Token usage

Typical triage call: ~350–500 input tokens (image + prompts) + ~150 output tokens.
