# Film Tables

A **film table** (`.FLM`) is the recipe the ProPalette 8000 follows when
exposing a frame. It describes a single film stock to the recorder — its
colour curves, aspect ratio, whether it is monochrome or colour, the
camera format it was authored for, and the default filter for
single-pass black-and-white exposure. Each frame you expose is the
combination of one image and one film table.

piPalette stores the FLMs you upload in a small library so that you can
pick one when creating a roll, without having to manage files manually
on disk.

> Film tables are produced by the film manufacturer — historically
> Polaroid — and occasionally by labs or individuals for custom film
> stocks. piPalette only **reads** FLMs; it does not author or edit
> them.

## The library

Open **Film Tables** from the sidebar. The page is a single panel with
two areas: a dropzone along the top for adding FLMs, and a list below
of everything currently in the library. If the library is empty the
list area shows *No film tables yet — upload an FLM to get started*.

## Uploading an FLM

There are two ways to add a film table, both equivalent:

* Drag one or more `.FLM` files from your file manager onto the
  dropzone. The zone highlights amber while files are being dragged
  over it.
* Click the dropzone to open the system file picker. Hold **Cmd** or
  **Ctrl** to select multiple files at once.

On upload, piPalette validates each file by parsing it through the
recorder driver. A successful parse reads the following metadata
straight out of the FLM header:

**Name**
: The film stock's identifier, as written into the FLM by its author —
  for example `EKTA100` or `PLUSXPAN`. This is the name you will see
  when selecting a film table for a roll.

**Camera type**
: The format the FLM was authored for: 35mm, 6×4.5, 6×7, 4×5, or
  similar. This determines the aspect ratio of every frame exposed
  against the recipe.

**Color / B&W**
: Whether the film is monochrome or colour. Black-and-white FLMs also
  expose a default filter colour (Green, Red, or Blue) used during
  the single-pass B&W exposure path.

If a file is malformed, encrypted with an unrecognised key, or
otherwise fails to parse, the upload is rejected with an error toast
and the file is **not** stored. Other files in the same batch continue
to upload.

> **Duplicates are quietly ignored.** piPalette identifies each film
> table by a hash of its data, so re-uploading a file already in the
> library is a no-op — no error, no second entry. If you have two
> copies of the same FLM under different filenames, only the first
> one's filename will be remembered.

## The library list

Each row in the library shows, from left to right:

* The film table's name (from the FLM header).
* Its camera type.
* Its colour mode — `Color`, `B&W`, or `B&W (Green)` /
  `B&W (Red)` / `B&W (Blue)` for monochrome stocks that specify a
  default filter.
* The original filename the file was uploaded with, in muted text.
  This is purely informational; piPalette stores the file under its
  own content hash internally.

Rows are sorted in upload order, oldest first.

## Deleting a film table

Click **Delete** on the right of any row. A confirmation dialog
appears with the film table's name and reminds you that deletion only
removes the file from the library.

> **Deleting a film table is safe with respect to existing rolls.**
> Every roll snapshots the FLM at the moment it is created (see the
> *Rolls* chapter). Removing a film table from the library will not
> alter, break, or invalidate any roll that was already created
> against it — those rolls keep their own copy of the file. The only
> consequence of deletion is that the film table is no longer
> available as an option when creating *new* rolls.

The reverse is also true: if you delete a roll, the library copy of
its film table is untouched.

## Authoring your own FLMs

piPalette does not include an FLM editor. If you need a custom film
table — to retune a stock for a different look, or to add a stock
that was never shipped with the recorder — author the file with an
external tool that follows the 2-master FLM convention and upload the
result here.
