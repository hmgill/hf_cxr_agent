# cxr-overlay (sandbox skill)

Render an interactive HTML overlay of the localized findings (bounding boxes
from NV-Locate-Anything-3B) on top of the chest X-ray. This skill runs **inside
the sandbox**; it is staged into the workspace at `skills/cxr-overlay/`.

## Inputs (already placed in the workspace by the `stage_overlay` tool)

- `inputs/cxr.png`       — the chest X-ray image (RGB, possibly downscaled).
- `inputs/findings.json` — `{ "image_id": "...", "image_width": W,
                              "image_height": H, "coords": "normalized",
                              "regions": [ {"finding","x","y","w","h","score",
                                            "severity","location","confidence"} ],
                              "impression": "...", "findings": [...] }`
                            Each region's `x,y,w,h` are **fractions of the image
                            in [0, 1]** (already normalized host-side), so you
                            never have to reason about pixel space — multiply by
                            the displayed size. `severity` is one of
                            `mild|moderate|severe|unknown`.
- `inputs/lut.json`      — `{ "names": [severities], "colors": {severity:[r,g,b]},
                              "default": [r,g,b] }`

Do **not** paste these files' contents into the conversation — the base64 image
is large. Operate on them only through shell commands.

## Procedure

1. Confirm the inputs exist: `ls inputs`.
2. Render: `python skills/cxr-overlay/render_overlay.py inputs output`
   This reads the three input files and writes
   `output/overlay_<image_id>.html` — a single self-contained file with the
   X-ray, severity-colour-coded boxes, per-finding toggles, an opacity slider,
   and the impression text (the drawing happens in-browser via SVG, so no
   Python image libraries are needed).
3. Verify it was written: `ls output`. Report the output path.

## Adapting (optional)

The renderer is `skills/cxr-overlay/render_overlay.py` and the HTML scaffold is
`skills/cxr-overlay/overlay_template.html`. If a case needs a different colour
map, only certain findings shown, thicker strokes, or a different default fill
opacity, edit those files with apply_patch and re-run step 2.

## Authoring a different visualization

When the user wants something other than the standard box overlay (a severity
breakdown, a confidence ranking, per-finding zoom crops, a side-by-side, a
finding-density heatmap, an annotated figure …), write your own script instead
of running `render_overlay.py`. The same inputs are already staged:

- `inputs/cxr.png` — the X-ray.
- `inputs/findings.json` — regions with normalized `x,y,w,h` in [0,1], plus
  `score`/`confidence`, `severity`, `location`, the free-text `impression`, and
  the structured `findings` list.
- `inputs/lut.json` — the severity colour table.

Procedure:
1. `cat inputs/findings.json | head -c 600` to confirm the shape (don't dump the
   whole file; the impression can be long).
2. Create a script with apply_patch (Python **stdlib only** — no numpy / PIL /
   matplotlib, and no pip installs are available in the sandbox). Do the heavy
   lifting in the browser: read the inputs, base64 the PNG, and emit a
   **self-contained** HTML file that embeds the data as JS and renders with
   vanilla canvas / SVG / DOM. `render_overlay.py` is a complete worked example
   of this pattern — copy it and change the rendering.
3. Run it, writing to `output/<name>.html`. Confirm with `ls output`.

Examples of what the data supports:
- **Severity bars** — count regions per `severity`, colour with the LUT.
- **Confidence ranking** — sort findings by `score`/`confidence`, draw a bar per
  finding.
- **Zoom crops** — for each region, draw the X-ray into a `<canvas>` and use
  `drawImage` with the region's normalized box (× natural pixel size) as the
  source rect to produce a cropped, magnified panel beside the label.
- **Density heatmap** — accumulate box coverage across the frame to show where
  findings cluster.

Keep every output **decision-support only** and include the same disclaimer the
template uses (boxes are model output, may be imprecise, confirm with a
radiologist). Crop/zoom panels are especially useful when boxes are small.
