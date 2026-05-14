# piPalette

A modern web interface for the **Polaroid ProPalette 8000** film recorder.

The ProPalette 8000 was a high-end digital-to-film recorder sold in the
late 1990s. Decades later, working units are still out there — but the
software that shipped with them ran on Windows 95 and connected over
SCSI cards no modern computer has. piPalette replaces that toolchain
with a browser-based interface that runs on a small Raspberry Pi
next to the recorder. Point any phone, tablet, or laptop on your home
Wi-Fi at it and you've got a tidy modern workflow for exposing real
film from digital images.

## What you can do with it

**Manage film tables.** Drop your `.FLM` recipes into a library. Each
table is shown with its film stock, format, and color/B&W mode; click
any one to see its 4K and 8K transfer curves.

**Build rolls.** A *roll* is the unit of work — an ordered queue of
images paired with one film table. Drag images in from your desktop,
reorder them by drag-and-drop, set per-image options (resolution,
crop/fill, rotation, background colour), and skip anything you'd
rather leave out.

**Expose to film.** Expose a single image with one click, or hit
**Start exposing** to print the whole roll sequentially. Watch the
recorder work in real time — calibration phase, scanline progress,
elapsed time and ETA on the active frame. When you need to stop, the
recorder finishes its current frame and waits for you.

**Run on the LAN.** Everything happens through a browser at
`http://pipalette.local`. No client software to install on your
laptop, no cables to your desk — the Pi sits beside the recorder and
serves the interface to whichever device you grab.

See the [user manual](docs/) for the full walkthrough.

## Getting started

You need three things:

- A Raspberry Pi 3B or newer with a [PiSCSI v2 HAT](https://www.scsi2pi.net/)
  (or any Linux machine with a SCSI HBA).
- A working ProPalette 8000, with a SCSI cable to the Pi.
- A microSD card and [Raspberry Pi Imager](https://www.raspberrypi.com/software/).

### Step 1 — Prepare the Pi

Use Raspberry Pi Imager to write **Raspberry Pi OS Lite (Bookworm,
64-bit)** to your microSD card. In the Imager's customization dialog,
set:

- **Hostname:** `pipalette`
- **SSH:** enabled, with your password or public key
- **Wi-Fi:** your home network credentials
- A user account name and password you'll remember

Insert the card into the Pi and power it on.

### Step 2 — Install piPalette

Once the Pi is on the network, SSH in from your laptop and run the
installer. From here on, everything is automated:

```bash
ssh <your-user>@pipalette.local
curl -sSL https://raw.githubusercontent.com/veroc/pipalette/master/deploy/install.sh | sudo bash
```

The installer takes a couple of minutes. When it finishes, piPalette
is running as a service on the Pi and starts automatically every time
the Pi powers up.

### Step 3 — Open the app

From any device on your home network, open
**[http://pipalette.local](http://pipalette.local)** in a browser.

The first time you open it, head over to **Device** and click
**Scan**. With the recorder powered on and connected, piPalette
finds it and connects automatically. From there: upload a film
table, create a roll, queue up some images, and you're ready to
expose.

## Updates

When new versions are released, piPalette can update itself. Open the
**Device** page, scroll to **System**, and click **Check for updates**.
If a newer version is available, **Update now** pulls it down and
restarts the service in place. No SSH, no manual steps.

## Trying it without a recorder

Curious but don't have a PP8K wired up? On the **Device** page,
switch the **Mode** to **Mock**. piPalette runs against an in-process
simulator: you can create rolls, upload images, queue exposures, and
watch the runner go through its phases — without actually exposing
anything. It's the easiest way to evaluate the software or get
comfortable with the workflow.

## Documentation

The **[user manual](piPalette-Manual.pdf)** covers every page in detail
— per-frame options, the exposure runner, the device panel. Markdown
sources are in [`docs/`](docs/) if you want to read them on GitHub.

## Hardware notes

piPalette is a plain Linux application, so it doesn't require a Pi
specifically. Any Linux host that can talk SCSI to the recorder works:

- A **Raspberry Pi with a PiSCSI HAT** is the recommended path —
  inexpensive, low-power, fits in the same enclosure as the recorder.
- An **old desktop with a real SCSI HBA**, or a **laptop with a PCMCIA
  SCSI card**, both work too.

The Pi installer covers the most common case end-to-end; for other
hosts the steps adapt naturally (skip the PiSCSI-specific bits, point
piPalette at your `/dev/sg*` device).
