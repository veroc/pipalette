# Calibration — technical reference

Internal reference for the two-mode calibration system. Goes into enough
depth to discuss future changes intelligently; does **not** duplicate the
user-facing parts of the manual.

Last updated: 2026-05-18.

---

## 1. The problem

A piPalette FLM is a 256-entry look-up table per resolution (Master A for
4K, Master B for 8K) mapping 8-bit input pixel to CRT drive. The chain is:

```
pixel value ──► LUT ──► drive ──► CRT phosphor ──► negative density
                                                  ──► paper density (in the darkroom)
```

The LUT is the only thing piPalette can shape. The film and paper are
out-of-band physics. Calibration is the process of measuring the
*combined* response of (CRT + film + dev) at known drive levels and
solving for an LUT that makes the negative-density-versus-pixel-value
relationship hit a target — Zone I at pixel 25, Zone IX at pixel 255,
linear in pixel value between.

The hard parts:

1. **We can only sample drives the current LUT happens to produce.** If
   the LUT is way off, our 31 fixed pixel positions sample a useless
   region of the response curve.
2. **The film+dev response shifts between rounds** (developer freshness,
   temperature, latent-image keeping). The math assumes the function is
   stable; in practice it varies ~0.5–1 stop per session.
3. **Different film stocks at the same labeled ISO can differ by
   1–2 stops** in practical speed under the user's developer of choice.
   A baseline tuned for one film/dev combo is wrong for another.

The previous single-mode calibration handled (1) by iterating — each
round halved the residual error. That works once the LUT is close, but
not when it's 4 stops off (the original bw100 baseline) or when the
target film is 2 stops slower than expected (different dev).

---

## 2. The two-mode flow

### Mode A — "Find speed point" (first-time)

**Purpose:** locate the drive that produces speed-point density on this
specific film/dev combo. One round, no curve fitting.

**How:**

1. The roll is exposed through a **per-ISO calibration LUT**
   (`pipalette/calibration_lut.py`), not the user's target FLM. The
   calibration LUT encodes 24 log-spaced drives at pixel values 1..24,
   spanning ±2 stops around the predicted speed-point drive for the
   labeled ISO. Drives are *known* at roll-creation time — they don't
   depend on whatever curve the wizard happened to place.
2. The user develops the roll and reads each patch's density.
3. `find_speed_point` interpolates in log-drive space to find the drive
   where measured density crosses `b+f + 0.10`.
4. `place_shape(D_sp, target_range)` builds a 256-LUT anchored at that
   drive, using a parametric sigmoid as the assumed film response shape.

The output FLM is marked `cal_state=speed_point`.

### Mode B — "Refine curve" (existing flow, rebranded)

**Purpose:** fine-tune the curve shape once speed-point is already close.

**How:** the existing 31-step wedge through the target FLM, polynomial
fit in log(drive+1) space, inverted to find drives that produce a
linear-in-pixel target density profile. See `correct_lut` in
`pipalette/calibration.py`.

The output FLM is marked `cal_state=refined`. Refinement is **gated** —
the API returns 409 if attempted on an `uncalibrated` profile.

### Why both

Mode A is **insensitive to where the LUT starts** because the wedge
drives are independent of the target LUT. It handles arbitrarily-bad
starting curves. But it commits to one parametric H&D shape (the
sigmoid), which won't match every real film exactly.

Mode B refines the shape but assumes the speed point is already close,
so the 31-step wedge has useful drive coverage. Together they cover:
brand-new film/dev → Mode A → reasonable LUT → Mode B → curve fit.

---

## 3. Math

### 3.1 Reference H&D shape

`pipalette/calibration_shape.py` defines the assumed film response as a
sigmoid in density vs log-drive relative to the speed point:

```
D(L_rel) = DMAX / (1 + exp(-(L_rel - L_50) / K))
```

with

| symbol | meaning | value |
|---|---|---|
| `L_rel` | `log(drive) - log(D_sp)` | input |
| `DMAX` | total density range (b+f → shoulder) | 2.0 |
| `K` | log-drive scale (slope parameter) | 0.25 |
| `L_50` | log-drive offset where D = DMAX/2 | fixed by anchor |

`L_50` is **derived**, not free: it's chosen so `D(L_rel = 0)` equals
`SPEED_POINT_OFFSET = 0.10`. This forces the speed point to land exactly
at `L_rel = 0` regardless of the other parameters.

Solving:

```
0.10 = 2.0 / (1 + exp(L_50 / 0.25))
exp(L_50 / 0.25) = 19
L_50 = 0.25 · ln(19) ≈ 0.736
```

Picked values (DMAX, K) target a typical panchromatic B&W negative:
slope ≈ 0.6 density/stop in the straight section, dmax 2.0. They are
**not Foma-specific** — any specific film's measured curve will deviate,
and refinement corrects it.

Inverse exists in closed form:

```
L_rel(target_D) = L_50 - K · ln(DMAX / target_D - 1)
```

### 3.2 `find_speed_point(measurements)`

Inputs: a list of `(drive, density)` pairs in increasing drive order
(typically 24 of them, the speed-point wedge measurements).

1. `b+f = min(densities)`. Target density = `b+f + 0.10`.
2. Apply a running-max to densities so a noisy reading can't drop the
   curve below an earlier value.
3. Locate the bracket `[i, i+1]` where `densities[i] ≤ target ≤
   densities[i+1]`.
4. Linearly interpolate in log-drive space inside that bracket:
   ```
   t = (target - d_i) / (d_{i+1} - d_i)
   log(D_sp) = log(drives[i]) + t · (log(drives[i+1]) - log(drives[i]))
   ```
5. Return `exp(log(D_sp))`.

Raises `ValueError` if the speed-point density is outside the measured
range — that's the "wedge didn't bracket the speed point" signal, which
the API surfaces to the UI as a re-bracket prompt.

Recovery error in synthetic round-trips:

| true vs predicted D_sp | recovered error |
|---|---|
| same | < 2% |
| 1 stop slow | ~3% |
| 2 stops slow | wedge tops out, ValueError |
| 2 stops fast | ~20% (sparse low-end samples) |

### 3.3 `place_shape(D_sp, target_range)`

Builds the 256-entry display-space LUT:

- **Toe (pixels 0..25):** linear ramp from drive 0 to drive `D_sp`.
  Pixels in this region all print as paper-black on grade-2 paper, so
  the exact shape is cosmetic. Linear gives the LUT graph a clean rise
  instead of a cluster of zeros.

- **Working range (pixels 26..255):**
  ```
  t = (x - 25) / (255 - 25)
  target_D(x) = b+f + 0.10 + t · target_range
  L_rel = reference_log_drive(target_D - b+f)     # sigmoid inverse
  drive(x) = exp(log(D_sp) + L_rel)
  ```
  Target density rises linearly with pixel value from the speed point
  to `b+f + 0.10 + target_range` at pixel 255 (grade-2 paper:
  `target_range = 1.05`, so highlight density = 1.15 above b+f).

Final pass enforces monotone non-decreasing to absorb rounding artifacts.

### 3.4 `correct_lut(old_lut, measurements, target_range)` (refinement)

Existing 31-step fit; unchanged by the two-mode work:

1. Sample drives at each measurement pixel via the *current* LUT.
2. Smooth densities (window-3 moving average) and force monotone.
3. Fit `density ≈ polyfit(L = log(drive+1), deg=3)`.
4. For each output pixel, compute target density (same toe + linear
   working range as `place_shape`), invert the polynomial by bisection
   to find drive.

The deg-3 polynomial is the lowest order that captures toe + straight +
shoulder without overshoot. Deg-4 tested non-monotone at the low end on
real Foma data.

---

## 4. Implementation map

```
pipalette/
  calibration_shape.py    sigmoid model, place_shape, reference_log_drive
  calibration_lut.py      per-ISO calibration FLM builder, wedge_drives,
                          predicted_speed_point
  wedge_render.py         on-demand 35mm 3:2 wedge frame renderer
                          (PNG bytes; never written to disk as static asset)
  calibration.py          find_speed_point, build_speedpoint_lut,
                          correct_lut, build_calibrated_table,
                          create_speedpoint_roll, create_calibration_roll,
                          diagnose, next_versioned_name
  film_tables.py          CAL_STATE_* constants, set_cal_state(),
                          backfill on load
  rolls.py                calibration_mode field on calibration rolls
  app.py                  4 calibration endpoints (2 create + 2 apply +
                          shared measurement entry)
  wizard_baselines.py     sigmoid placeholder for MASTER_A_DISPLAY /
                          MASTER_B_DISPLAY (film-agnostic by construction)

static/calibration/wedge/35mm-3x2-{4k,8k}/frame_NN.png  refinement wedge
                          (static, 16 frames × 2 res, same for every film)
static/calibration/reference/{linear,srgb}_35mm-3x2-4k.png
                          print-side reference ramps (visual eval)
```

### 4.1 Calibration LUT (per-ISO, built on demand)

For labeled ISO `X`:

- `D_sp_4k_predicted = 150 · (100 / X)` (4K speed point at ISO 100 ≈ 150)
- `D_sp_8k_predicted = 38 · (100 / X)` (1/4 of the 4K drive)
- 24 log-spaced drives, ±2 stops around each predicted value
- LUT stored values at pixel `i` (1..24) = the i-th drive directly
  (scale 1, no multiplication on firmware side)
- Pixel 0 = 0 (black background); pixels 25..255 cap at the last drive

Two-master invariant preserved (`stored_a` repeated at Sets 0/2/4/6/7,
`half_a` at 1/3/5, `2 · stored_b` at 8, `stored_b` at 9) so the file
passes `pp8k.validate_masters`. Per-set headers carry the per-set
scale_r byte that pp8k reads on load — Set 9 needs an explicit override
since it uses a different scale than the rest.

### 4.2 Wedge rendering

Speed-point wedge is rendered **per roll** because patch drives depend
on the labeled ISO. `wedge_render.render_frame(iso, resolution,
left_patch, right_patch)` returns PNG bytes; `create_speedpoint_roll`
calls it 12 times per resolution and feeds the bytes to
`_attach_prerendered_frame` so the thumb-generation overhead is skipped
(calibration rolls don't render in the rolls UI).

Patch order mirrors the in-canister film-flip convention from the
refinement wedge: higher step on the left, so a right-to-left read of
the developed strip walks the wedge ascending S01..S24.

Labels per patch: `S## RES p### D####` — step number, resolution, pixel
value, and *drive* (the calibration LUT's known drive at that pixel).
That makes the densitometer entry grid self-describing — no separate
metadata file on the roll.

### 4.3 State machine

Profile-level state in `cal_state`:

```
uncalibrated  (wizard creates here)
    │
    │  POST /api/film-tables/<id>/calibrate-speedpoint
    │  → creates speed-point roll
    │  → user develops + measures
    │  → POST /api/calibration/<roll_id>/apply-speedpoint
    │
    ▼
speed_point  (new FLM, sigmoid shape anchored at measured D_sp)
    │
    │  POST /api/film-tables/<id>/calibrate
    │  → creates refinement roll (rejected if uncalibrated)
    │  → user develops + measures
    │  → POST /api/calibration/<roll_id>/apply
    │
    ▼
refined  (new FLM, polyfit-derived curve shape)
    │
    └─► loop: "Find speed point" or "Refine curve" can run again
        from any non-uncalibrated state to update for changed
        conditions (new dev batch, new film, etc.)
```

Existing profiles get backfilled to `uncalibrated` on load — conservative
because we don't know whether they were calibrated by an older flow.

Roll-level state in `calibration_mode`:
- `"speed_point"` — speed-point wedge through the calibration LUT
- `"refinement"` — 31-step wedge through the target FLM

The detail page reads this off the roll to choose:
- which wedge step list to show in the measurement grid (pixels 1..24
  vs `wedge_pixel_values()` which is 0, 8, 17, …, 255)
- which apply endpoint to hit
- which analyse-result block to render (`speedpoint_4k/8k` vs
  `diagnostic_4k/8k`)

### 4.4 Wizard placeholder

`wizard_baselines.MASTER_A_DISPLAY` and `MASTER_B_DISPLAY` are no longer
static tuples but `calibration_shape.place_shape()` calls evaluated at
module import. The placeholder is anchored at the predicted ISO-100
speed-point drives (150 / 38) — **deliberately the same** as the
calibration LUT's wedge center, so:

- a new ISO 100 wizard FLM's pixel 25 lands at drive 150,
- the speed-point wedge's center patch (pixel 12) also lands at drive
  150,

which means the wedge perfectly brackets the placeholder's predicted
speed point. If the actual film+dev combo lands within ±2 stops of the
prediction (always true in practice), the wedge bracket holds.

---

## 5. Open questions / future work

### 5.1 Convergence past round 2

For Foma + the user's developer, round 1 (bw100 baseline → FPNB1) and
round 2 (FPNB1 → FPNB2) showed ~1 stop residual error each time, mostly
from session-to-session dev variation. The two-mode flow short-circuits
the first big error; subsequent rounds remain limited by physical
variability. Open: should the apply step apply a < 1 dampening factor
to avoid overshoot? Worth measuring on round 3.

### 5.2 sRGB-faithful target

The current `place_shape` puts a *linear-in-pixel* target density above
speed point. For strict screen-to-print fidelity, target density should
follow the sRGB EOTF (`D ∝ log(sRGB_to_linear(pixel))`). The print-side
LINEAR + sRGB reference frames (in `static/calibration/reference/`) are
the diagnostic that tells us how far off we are. Once the user prints
them, we'll know whether to bake an EOTF into the target.

### 5.3 Per-ISO predicted speed point

`predicted_speed_point(iso, '4k')` returns `150 · 100/iso`. The 150 was
picked from Foma round-2 data — empirical, not first-principles. A
better number would average across several films; we don't have that
data yet. Films outside the wizard ISO bracket (e.g., ISO 6 or 3200) are
*not* supported because the calibration LUT can't encode their predicted
drives in u16 without rescaling the stored→display mapping.

### 5.4 Asymmetric speed-point cases

`find_speed_point` returns `ValueError` if the wedge doesn't bracket the
speed point — the UI surfaces this as "wedge tops out too low" or "wedge
starts too hot". Currently the only remedy is to switch the labeled ISO
of the target FLM and re-shoot. A wider wedge (±3 stops, 36 patches)
would handle this in one shot at the cost of one frame's worth of
roll space — possibly worth doing when it happens routinely.

### 5.5 8K Master B derivation

When the 8K wedge is *not* measured (only 4K is), `apply-speedpoint`
falls back to `D_sp_8k = D_sp_4k / 4`. This matches the 4×-ratio
convention but isn't film-physics — different films may have different
4K:8K ratios because the exposure time per scanline differs. Currently
ignored; revisit if a refinement round shows systematic 8K mismatch.

### 5.6 Halation threshold

User-observed halation starts above drive ≈ 2000–3000 on the test
hardware. The placeholder peak at ISO 100 is 338 (well clear); the
post-calibration peak depends on D_sp and target_range and can exceed
the threshold. Open: should `build_speedpoint_lut` clamp peak drive and
report the resulting reduction in target_range, instead of producing
LUTs that physically over-drive? Wait until we have hardware-side
halation data to set a defensible threshold.

---

## 6. How to discuss changes

When proposing a change, label it by which layer it touches:

- **Reference shape** — `DMAX`, `K`, the sigmoid form. Affects every
  speed-point output. Test by regenerating wizard FLMs and re-running
  synthetic recovery tests.
- **Wedge geometry** — patch count, ±2 stops, patch spacing. Touches
  `calibration_lut` + `wedge_render`. Mostly affects measurement
  resolution, not the math.
- **Target curve** — what pixel-to-density relationship we aim for.
  Currently linear-in-pixel above speed point. sRGB EOTF would change
  this without touching shape or wedge.
- **State machine** — when each mode is available, what gating applies.
  Touches `film_tables.cal_state`, `rolls.calibration_mode`, and the
  detail-page UI.
- **Apply path** — how measurements turn into LUT. Two distinct paths
  (`apply-speedpoint` vs `apply`); shared math via the shape module.

The shape module (`calibration_shape.py`) is the single source of truth
for the reference H&D. Anything that wants a "what density should this
drive produce" answer goes through `reference_density()`. Anything that
wants "what drive produces this density" goes through
`reference_log_drive()`. Don't duplicate the sigmoid evaluation anywhere
else — it makes future shape changes painful.
