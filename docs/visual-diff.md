# Visual comparison (Feature 3)

A mod that draws something is usually judged by a human looking at a picture. That
does not scale to an agent, and it does not leave evidence behind. Feature 3 turns
"does the screen look right?" into numbers, coordinates and a picture of *where*
the difference is — with an explicit statement of what those numbers cannot prove.

```
python3 -m awt.visualdiff shot.png reference.png
python3 -m awt.visualdiff shot.png win11.png win7.png --diffmap diff.png --json out.json
python3 -m awt.visualdiff shot.png --mod examples/taskbar-clock.wh.cpp --roi 0,980,1920,120
python3 -m awt.visualdiff shot.png reference.png --report .sandbox/report.json   # attach
python3 -m awt.visualdiff --show-config --mod-id taskbar-clock                   # what will be applied
```

Exit codes: `0` match, `1` mismatch, `3` incomparable or unavailable, `2` usage
error. The same convention as the rest of the bench, so a script can trust it.

## The disclaimer, which travels with every result

> Descriptive, not a verdict. Visual similarity does not prove that a mod works,
> and a visual difference does not prove that it is broken.

That is not hedging; it is the shape of the evidence. A clock mod that renders the
right time with the wrong font is a mismatch and still works. A mod that hooks
nothing but leaves the desktop untouched is a match and does nothing. The visual
section is **added** to a report next to `verdict.level`; it never changes it, and
`verdict.visual_comparison_note` is written so the picture cannot be quoted out of
context.

## What is measured

Two families of numbers, because they fail in different ways.

**Per-pixel** (`per_pixel`) — exact, implementation-independent, and blind to
structure:

| field | meaning |
| --- | --- |
| `identical_pixels` / `identical_ratio` | pixels equal in every channel |
| `within_tolerance_pixels` / `within_tolerance_ratio` | equal within `tolerance` per channel — how a comparison survives ClearType and lossy encoding |
| `differing_pixels` / `differing_ratio` | the complement of within-tolerance |
| `mean_abs_delta`, `max_abs_delta`, `rmse` | magnitude on the 0–255 scale |
| `similarity` | `1 - mean_abs_delta / 255` |
| `delta_histogram` | coarse buckets (`0-0`, `1-4`, … `129-255`) so a report stays small |

**Structural** (`ssim`) — how a human would judge it, and the number everybody
compares against a textbook value. Two implementations exist, and **their numbers
are not interchangeable**:

| implementation | when | window |
| --- | --- | --- |
| `ssim-gaussian-11x11-numpy` | numpy is installed | the standard 11×11 Gaussian, σ 1.5, separable convolution |
| `ssim-block-8x8-pure` | no numpy | non-overlapping 8×8 blocks, uniform window |

Every result records which one ran, and the pure path carries a note saying it is a
valid structural similarity but not directly comparable with scikit-image, Pillow
or a paper. That is why a configuration may set `ssim_min_numpy` and
`ssim_min_pure` separately: the same pair scores 0.986 (Gaussian) and 0.886
(block) in one of the tests here, and both are correct for their own
implementation.

**Regions** (`regions`) — the part an agent can actually act on. The delta map is
thresholded (`region_threshold`, default 24), connected components are labelled
(4- or 8-connectivity), components below `min_region_area` are dropped and counted,
nearby boxes can be merged (`merge_distance`), and each surviving region is
reported with its bounding box, `area`, `area_scaled` (area in original-image
pixels after any downscaling), `max_delta`, `mean_delta`, `centroid`,
`box_coverage`, and `coordinate_precision` — `exact`, or `+/-N px (computed on a
Nx downscaled image)`. The list is capped at `max_regions`, and a truncated list
says so.

```
region-1  x=40    y=20    w=12   h=9    area=108    max=245 mean=141.713 centroid=[45, 24]
```

## Floors are not enough: the ceilings

Similarity floors are what everybody writes first, and they hide small sharp
changes. In this repository's own tests, moving a window by two pixels leaves
**98.7 %** of the pixels similar and SSIM at 0.986 — a pass on every floor, and
obviously wrong. Painting a 12×9 rectangle over a window leaves 99.3 % identical
pixels and also passes.

So two rules run in the other direction:

| rule | direction | default | what it catches |
| --- | --- | --- | --- |
| `ssim_min`, `similarity_min`, `identical_min` | `at_least` | 0.90 / 0.95 / 0.90 | broad wrongness |
| `max_differing_ratio` | `at_most` | 0.01 | a shift or repaint that averages away |
| `max_region_area` | `at_most` | off (`null`) | one compact region that is clearly wrong |

Each check is reported with its `value`, `threshold`, `direction`, `passed`, and —
for the region ceiling — the `label` of the offending region, so a reader can see
*which rule* decided the status instead of trusting a summary word. Set the
ceilings per reference: a clock mod **wants** its clock region to differ, which is
what `--roi` is for.

## Statuses

| status | exit | meaning |
| --- | --- | --- |
| `match` | 0 | every configured rule passed |
| `mismatch` | 1 | at least one rule failed; the failing rules are listed |
| `incomparable` | 3 | the two images cannot be compared as they are (different resolutions, and no `--resize` / `--roi` was given). No percentage is reported, and the reason names both sizes and the two ways forward |
| `unavailable` | 3 | a file could not be read, or the pair could not be prepared (a malformed ROI). Nothing was compared — an unreadable image is never presented as a difference of zero |

With several references the section status is the worst usable outcome
(`mismatch` beats `incomparable` beats `unavailable`), and `best_reference` names
the highest-scoring one with the note that the least different reference is not a
claim that the mod is correct.

## Normalisation, and what it costs

Every step is recorded in `normalisation.steps`, and anything that weakens the
result is repeated in `caveats`:

- **Crop / ROI** — `roi` applies to both images, `roi_obtained` and
  `roi_reference` override per image, which is how a taskbar strip stays
  comparable across captures of different resolutions. Region coordinates are
  relative to the cropped image; add the ROI origin to map them back. Differences
  outside the region are invisible to the result, and the caveat says so.
- **Mode** — `RGB` by default, `L` to compare luma only. Alpha is composited over
  white before luma, so transparency does not read as black.
- **Resize** — off by default. A resolution mismatch is `incomparable`, never a
  silent rescale. With `--resize` the obtained image is box-averaged to the
  reference size and the caveat states that both percentages are approximations
  (in one test, resizing a 140×84 crop to 160×100 drops SSIM to 0.848 on its own).
- **Downscale** — to keep the pure-Python path usable, both images are downscaled
  by the smallest **integer** factor that fits the working limit (400 000 pixels
  pure, 12 000 000 with numpy; `max_pixels` overrides). Coordinates are scaled
  back, `area_scaled` reports original-image pixels, and precision is stated.
- **Colour management** — pHYs (with dpi), gAMA, iCCP and sRGB are read and
  reported in the geometry, but **no profile is applied**: values are compared as
  stored in the file. A 16-bit image is reduced to its high byte and
  `sample_reduction` records that. Differing DPI or colour metadata between the
  two files produces a note, not a resample.

## Configuration

Discovery order (`awt.visualdiff.config.candidate_paths`):

1. `--config <path>` — used even if missing, and a missing explicit file is an
   **error**, not a fall back to defaults;
2. `<mod>.wh.cpp.visual.yaml`, then `<mod>.visual.yaml` next to the source;
3. `configs/visual.local.yaml` (git-ignored);
4. `configs/visual.yaml`.

With none of them, the built-in defaults apply and `_source` says
`built-in defaults`. Precedence for thresholds: **built-in defaults ← file
`defaults:` ← mod ← reference**. A reference may also carry `roi`,
`roi_obtained`, `roi_reference`, `resize`, `mode`, `tolerance` and `max_pixels`
directly. Implementation-specific floors (`ssim_min_numpy`, `ssim_min_pure`)
replace the generic `ssim_min` **at the same level only** — otherwise a global
`ssim_min_pure` would silently override the floor a reference chose for itself.
`_source` in the resolved thresholds lists which keys came from which level.

`configs/visual.example.yaml` is the template: copy it to
`configs/visual.local.yaml`. Structural mistakes are refused with every problem
listed at once (unknown keys, a reference without a `path`, a non-mapping entry).

## Reference screenshots

Reference images are pictures of Microsoft's interfaces. They are **never
committed**. The template points at `.cache/refshots/<windows>/<mod>.png`, which is
git-ignored and is where `python3 -m awt.refshots` writes regenerated captures
(Feature 4; see `docs/reference-vms.md` for the three Windows versions and the
resolutions they run at). Until those captures exist, a configured comparison
reports `unavailable` and names the missing file — it does not invent a match.

## Artifacts

- `--diffmap <png>` — the difference map: unchanged pixels are the obtained image
  darkened to 45 %, differing pixels are on a yellow→pink heat ramp scaled between
  `region_threshold` and 255, and each region is outlined in cyan with corner
  ticks so a one-pixel-high region is still visible.
- `--side-by-side <png>` — obtained | reference | diff on one canvas, labelled with
  a built-in 5×7 bitmap font (no font rendering, so no font differences leak into
  the evidence).
- `--json <path>` — the same document shape as `awt.symbols --json`, including a
  probe of the machine that produced it.
- `--report <path>` — attach the section to an existing bench report from any
  backend. Additive: `visual_comparison`, plus `verdict.visual_comparison_note`.
  `verdict.level`, `verified`, `not_verifiable_here` and `summary` are untouched.

With several references each artifact is numbered (`diff-1.png`, `diff-2.png`), and
no artifact is claimed for a reference that could not be read.

## The PNG reader

`awt/png.py` exists so none of this needs Pillow or numpy: colour types 0/2/3/4/6,
bit depths 1/2/4/8/16, all five filter types, tRNS, pHYs, gAMA, iCCP, sRGB and
tEXt, writing 8-bit L/LA/RGB/RGBA. It refuses — with a reason, never a
guess — interlaced images, truncated files, bad CRCs, a missing IDAT, palette
indexes outside the palette, and anything that is not a PNG. It was checked
byte-for-byte against Pillow for solid, gradient, grey, grey+alpha, palette (4, 16
and 256 colours, adaptive), 1-bit, 4-bit and 2-bit images and hand-built rows for
each filter type (`tests/unit/test_png.py`); where Pillow is not installed those
cross-checks report themselves as skipped.

Two traps worth recording: sub-byte **palette indexes must not be scaled** (a
4-bit index of 15 means entry 15, not white), and a hand-built 1-bit row of 8
pixels is one byte — two bytes make the decoder read the next byte as a filter
type.

## What this cannot see

No OCR, so a wrong glyph at the right size can pass. No font-rendering model, so
ClearType, sub-pixel positioning and hinting differences have to be absorbed by
`tolerance` or excluded by `roi`. No notion of time, so a clock, a blinking caret
or an animation makes a correct mod differ — again `roi` or `tolerance`. No DPI
virtualisation: a 150 %-scaled capture is simply a different image. And nothing
here observes the Windhawk engine; the picture is of whatever the backend produced
(`docs/backends.md`), so a `match` inherits that backend's limits.

## Tests

`tests/unit/test_png.py`, `tests/unit/test_visual_diff.py` and
`tests/unit/test_visual_config.py` (78 assertions groups across three files) use
generated images only, so the expectations are exact: one painted rectangle is one
region at known coordinates with a known worst delta, an identical pair scores
exactly 1.0000, a change outside the ROI is invisible, and a report attachment
never changes the verdict it was attached to. One test forces the pure-Python path
even where numpy is installed, and one compares both paths on the same pair and
requires the same verdict. Both interpreters run in CI: the system Python (no
numpy) and, when available, `.sandbox/venv`.
