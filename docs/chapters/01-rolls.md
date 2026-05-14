# Rolls

A **roll** is the basic unit of work in piPalette: an ordered queue of frames
to expose against one film stock. Every roll is paired with one film table
(`.FLM`) at the moment it is created, and a copy of that FLM is stored inside
the roll. Subsequent edits or deletions in the **Film Tables** library never
affect rolls that already exist — the film recipe a roll was created with is
the recipe it will be exposed with.

> The "Rolls" page is what you will spend most of your time in. Creating a
> roll, queuing up images, adjusting per-frame settings, and starting the
> exposure run are all roll-level actions.

## Creating a roll

Open the **Rolls** page from the sidebar and click **New roll** in the
top-right of the panel. A small dialog asks for two pieces of information:

**Name**
: Anything that helps you find the roll later — a customer name, an order
  number, a date. It is not exposed onto the film and you can rename a roll
  at any time after creation.

**Film table**
: The FLM the roll will be exposed against. Only film tables already present
  in the library can be selected; if the list is empty, upload an FLM on the
  **Film Tables** page first. The dropdown shows each entry as
  `Name — Camera type · Color / B&W` so you can match the stock to the format
  you intend to shoot.

If the selected film table is black-and-white, a third option appears:

**B&W filter**
: The colour channel used for the single-pass exposure of a B&W stock —
  **Green**, **Red**, or **Blue**. Green is the usual default and is what
  most monochrome stocks were tested against; pick another channel only if
  the film stock or the look you are after asks for it.

Click **Create**. The new roll appears at the top of the list, immediately
ready to receive frames.

> **The film table is locked the moment a roll is created.** piPalette
> snapshots the FLM bytes into the roll's folder. From this point on, the
> roll is self-contained: deleting the film table from the library, or
> editing its curves, has no effect on the roll. If you need different
> behaviour you must create a new roll.

## The roll list

Each card on the **Rolls** page summarises one roll:

* The **name** you gave it, with a status badge to the right
  (*active*, *done*, *archived*).
* The film recipe — profile name, camera type, and colour mode. B&W rolls
  also show the chosen filter in parentheses.
* Pill counters showing the total frame count and, where non-zero, the
  number of frames that are `done`, `failed`, or currently `exposing`.

Click any card to open the roll's detail view.

## Roll detail

The detail page is divided into two panels.

The **header panel** restates the roll's identity — profile, camera type,
colour mode, aspect ratio, frame count, and the disk footprint of the roll
(images, output renders, and thumbnails combined). The breadcrumb in the
top-left links back to the roll list.

Below the header sits the **frames panel**, where you queue and arrange the
images that make up the roll.

### Renaming a roll

Click the roll title in the header. The text becomes editable in place.
Type a new name and press **Enter**, or click away, to save. Press
**Esc** to discard the change.

### Deleting a roll

Use **Delete roll** in the top-right of the header. A confirmation dialog
appears summarising what will be removed; deletion is permanent and also
discards all uploaded source images, rendered output PNGs, and thumbnails
that belong to the roll. The snapshot FLM is removed as well — your
library copy is untouched.

## Roll-wide options

A single roll-wide control lives under the header:

**Skip recalibration between continuous frames**
: When unchecked (the default), the recorder runs a calibration cycle
  before each frame. When checked, calibration runs only on the first frame
  of an uninterrupted sequence. Any pause, stop, or hardware error forces
  the next resumed frame back through a full calibration. Skipping
  recalibration is faster but, depending on the film stock and ambient
  conditions, can produce subtly inconsistent exposures across a long roll.

Changes apply instantly — there is no **Save** button.

## Adding frames

The **Frames** panel has a dotted **dropzone** along the top. There are two
equivalent ways to add images:

* Drag one or more files from your file manager and drop them onto the
  zone. The zone highlights amber while files are being dragged over it.
* Click the dropzone to open the system file picker.

Supported formats are JPEG, PNG, TIFF, and BMP. For multi-file uploads a
progress modal appears that streams one file at a time, showing the current
filename, the running count (e.g. *3 of 17*), and a progress bar. Each
upload can be cancelled mid-batch; files already finished remain.

When an image lands in the roll, piPalette:

1. Saves the original under the roll's folder so the source is preserved.
2. Renders it through the FLM at the roll's aspect ratio to produce an
   exposure-ready PNG.
3. Generates a small JPEG thumbnail for the grid.

The new frame is appended to the end of the queue.

## Frame settings

Each frame is a card in the grid. The card shows the thumbnail, the
original filename, the source pixel dimensions, and the current per-frame
settings. Below those are the controls you can adjust:

**Resolution**
: **4K** or **8K**. Defaults to 8K when the source image's long edge is
  6000 pixels or more, otherwise 4K. Doubling the resolution roughly
  quadruples the exposure time and the output PNG size; on a 4K master use
  4K to save time without losing fidelity.

**Transform**
: **Fit** preserves the entire source image and pads to the FLM's aspect
  ratio with the background colour. **Fill** crops the source so it covers
  the full frame without padding. **1:1** places the source at its native
  pixel size — no resampling. If the source is larger than the canvas in
  either dimension it is centre-cropped, and the card shows an amber
  warning explaining what was cropped. The aspect ratio of the frame
  itself is fixed by the FLM; you choose how the source is mapped into
  it.

**Rotation**
: Click the curved-arrow icon to rotate the source 90° clockwise. Each
  click advances through 0°, 90°, 180°, 270° and back to 0°. The thumbnail
  updates immediately.

**Background**
: Black or white — the colour piPalette pads with under **Fit**, and the
  colour the recorder uses around the image area. The recorder firmware
  only supports these two values.

Any change to a per-frame setting triggers a re-render of the exposure
PNG on the server. The frame card dims and shows an amber spinner while
this is happening — typically a fraction of a second for 4K and a few
seconds for 8K. The thumbnail refreshes when the re-render completes.

## Reordering and removing frames

Drag a frame by its body to reorder. The drop target is highlighted in
amber, and the ordinal in the top-left of each card (`01`, `02`, …)
updates immediately on release.

To remove a single frame, click the trash icon on its card. A
confirmation dialog appears with the filename and warns that the upload
and its renders will be deleted.

## Skipping a frame

Use the **X** icon (top of the icon row on a pending frame) to mark the
frame as **skipped**. Skipped frames stay in the roll for reference but
the runner walks past them during roll runs. They are visually muted
(greyscaled thumbnail, dimmed card, italic *Skipped — won't be exposed
in roll runs* label) so you can tell at a glance that they aren't part
of the queue.

To bring a skipped frame back into the queue, click the **circled +**
icon on the card. It returns to `pending`.

Skipping is for frames you want kept in the list but excluded for now —
say, an alternate crop you might come back to. If you simply don't want
the frame at all, use **Delete** instead.

## Frame status

Each card carries a small coloured dot in the top-right corner and a
matching vertical stripe along its left edge:

| Indicator | Meaning |
|-----------|---------|
| muted     | `pending` &mdash; queued, not yet exposed |
| amber     | `exposing` &mdash; currently on the recorder |
| green     | `done` &mdash; successfully exposed at least once |
| red       | `failed` &mdash; the last exposure attempt did not finish |
| dim grey  | `skipped` &mdash; in the roll but excluded from roll runs |

These statuses are reflected in the count pills on the roll list and
update live during an exposure run.

## Exposing

There are two ways to expose: one frame at a time from the per-frame
button, or the whole queue in a sequential roll run.

### Single-frame Expose

On every pending frame card there is a primary **Expose** button. Done
and failed frames show **Re-expose** or **Retry** in its place. Either
way, clicking opens a confirmation dialog — the recorder cannot detect
whether film is actually loaded, so the dialog asks you to confirm that
it is, with bold text. Cancelling closes the dialog and nothing
happens. Confirming kicks off the exposure.

While a single frame is exposing, piPalette puts up a **modal** that
blocks the rest of the page. The modal shows the active phase
(*Setup*, *Calibrating*, *Exposing R/G/B*, *Finishing*), a progress
bar, elapsed time, and an ETA. There is no cancel button: an exposure
cannot be aborted mid-burst without wasting the frame of film, so the
modal stays in place until the recorder reports the frame complete.
A typical single-frame exposure takes 60–100 seconds at 4K.

### Roll runs

The roll header has a primary **Start exposing** button (disabled when
no frames are pending) and a ghost **Stop after current** button
(hidden until a run is active). Starting opens the same film-loaded
confirmation; once confirmed, the runner walks every pending frame in
order, exposing each one through the recorder.

Roll runs are intentionally **non-blocking**. While a run is in
progress you can navigate to other pages — create new rolls, edit film
tables, look at the device status — and the runner keeps going on the
server. The currently-exposing frame card carries a phase + progress
overlay so it is obvious which frame is active, and a status banner
under the roll header tracks the overall *N / M done* count.

**Stop after current** is the only halt option. piPalette will let the
active frame finish (so no film is wasted) and then halt. The banner
turns yellow and reads *Stopping — finishing current frame* until the
current burst lands. Resuming is just **Start exposing** again, which
picks up at the next pending frame. piPalette forces a full
recalibration on the next start after a stop or error, regardless of
the *Skip recalibration* setting.

When a run completes, the banner turns green: *Run complete — all
frames exposed*.

## Re-exposing exposed frames

A frame that is `done` is, from the runner's point of view, finished —
it is no longer pending and won't be picked up by **Start exposing**.
There are three ways to re-expose a frame that has already been done:

**Re-expose one frame, now**
: Click the **Re-expose** button on a done frame. It triggers a
  single-frame exposure of that frame against the next available
  film — same modal, same flow as the initial Expose.

**Reset one frame for the next roll run**
: Click the small refresh icon in the frame's icon row. The frame flips
  back to `pending` — it stays where it is in the queue, history
  (exposure count, last-exposed timestamp) is preserved, and the next
  **Start exposing** will include it. No exposure happens yet.

**Reset every done frame for the next roll run**
: The roll header gains a **Reset done frames** button whenever the
  roll has any done frames. Clicking it (with confirmation) flips every
  done frame in the roll back to `pending` — useful when you want to
  re-shoot the entire roll on fresh film. Skipped and failed frames are
  left alone.

In all three cases, the frame's render and source image stay on disk —
piPalette just clears the *done* flag.
