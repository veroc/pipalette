# Device

The **Device** page is where you tell piPalette how to reach the
ProPalette 8000, and where you can confirm at a glance that the
recorder is alive and responding. It is the only page concerned with
the connection itself — exposure, slot installation, and roll
playback all happen from the *Rolls* page once a device is connected.

The page is laid out as three panels: a status panel at the top, a
connection panel below it, and a scan-results panel that appears on
demand.

## Hardware or Mock

piPalette can talk to either a real ProPalette 8000 attached to the
host or a fully in-process **mock device** that fakes every command
and response. The mock lets you evaluate piPalette — explore the
interface, build rolls, dry-run the workflow — without a recorder
connected. You cannot expose film with it. The choice is made in
the **Connection** panel under **Mode**:

**Hardware**
: piPalette opens a real transport to the recorder. The status panel
  at the top fills in with live identification and mode data, and the
  topbar pill shows the product and firmware. Use this for everything
  but development.

**Mock**
: piPalette runs against an in-process simulator. The status panel
  collapses to a single muted card that says so explicitly — piPalette
  deliberately does **not** display the mock's invented numbers as if
  they were real device readings. The topbar pill simply reads *Mock*.

Switching modes applies immediately; there is no Save button. The
recorder's actual connection state — and any error from a failed open
— surfaces in the hint line directly under the Mode toggle.

## Target

When the mode is **Hardware**, a second field labelled **Target**
appears below it. This is where you tell piPalette which physical
interface to talk to:

* A bare number — `4`, `5`, `6` — means "SCSI ID *N* reached through
  scsi2pi's `s2pexec` binary". This is the usual answer when the
  recorder is connected via the PiSCSI v2 HAT.
* A path like `/dev/sg2` means "the SCSI generic device at this
  path". piPalette will dispatch through the kernel's SG_IO ioctl.
  Use this when the host has a real SCSI host bus adapter or,
  historically, a PCMCIA SCSI card.

You do not need to pick a transport explicitly — piPalette infers it
from the value's shape.

The field can also be left blank if you would rather have piPalette
find the recorder for you. See **Scan** below.

> **Changing the target re-opens the connection on the next status
> poll.** You will typically see the topbar pill flicker through
> *No device* briefly while the new transport is being probed, then
> settle into *ProPalette 8000 · fw NNN* once the recorder answers.

## Scan

**Scan**, in the top-right of the Device page or in the topbar, asks
piPalette to enumerate every interface it knows how to talk to and
report any ProPalette 8000 it finds:

* Every `/dev/sg*` device on the host, probed with SG_IO.
* Every SCSI ID from 0 to 7, probed via `s2pexec` (only if the
  `s2pexec` binary is on the system `PATH`).

Each candidate gets a short INQUIRY command; only those that respond
with the PP8K identification string `DP2SCSI` are included in the
results. A discovery sweep typically takes a couple of seconds.

When the scan finishes, the **Scan results** panel slides into view
with one row per find, showing the recorder's identification line and
the transport/target it was reached on. Clicking a row writes that
target into the **Target** field and applies it as the new connection
— it is the one-click equivalent of typing the value in by hand.

If the scan turns up nothing, double-check that the recorder is
powered on, terminated correctly, and visible on the bus.

## Identification

In **Hardware** mode with a recorder connected, the upper-left card
of the status panel shows what piPalette read out of the device's
INQUIRY and MODE SENSE responses. These values do not change at
runtime; they are properties of the unit you are talking to.

**Product**
: The device family — always `DP2SCSI`/`ProPalette 8000` on a real
  recorder, never customer-rewriteable.

**Firmware**
: The firmware revision string the unit reports. ProPalette 8000
  firmware versions in the field are typically in the high 500s.

**Revision**
: The hardware revision reported by the unit.

**Buffer**
: The size, in kilobytes, of the recorder's internal scanline buffer.
  Real hardware reports around 2456 KB; the mock device reports 4096.
  piPalette uses this to pace large exposures and does not normally
  expose it to the user beyond this read-out.

**Max res**
: The largest horizontal × vertical resolution the unit will accept,
  reported by the firmware. This is the upper bound for *Resolution*
  in per-frame settings; piPalette will not let you queue a frame
  that the recorder cannot expose.

## Mode

The lower-right card shows the recorder's current operating mode —
what it would do *right now* if you asked it to expose. These values
do change as you (or piPalette) issue MODE SELECT commands.

**Active slot**
: The film-table slot currently loaded into the recorder's working
  memory. piPalette manages slot installation for you when a roll
  runs, so this value will normally read `19` (the scratch slot used
  by the driver) outside of an exposure run.

**Resolution**
: The horizontal and vertical resolution the recorder is configured
  to expose at, in scanlines.

**Camera back**
: The recorder's reading of which camera back is mounted (35 mm,
  6×4.5, 6×7, 4×5, and so on). For correct exposure the mounted back
  must match the camera type of the film table being used.

**Luminance**, **Color balance**, **Exposure**
: Three triplets (R, G, B) describing the per-channel intensity,
  trim, and shutter time the recorder will apply on the next
  exposure. They are derived from the active film table; you do not
  edit them directly.

## The topbar pill

Every page in piPalette carries a small connection pill in the
top-right of the topbar. Its dot is green when piPalette is connected
to a recorder (real or mock) and dark grey when it is not. The label
to the right of the dot summarises the current connection:

* `Mock` — running against the simulator.
* `ProPalette 8000 · fw NNN` — connected to real hardware, with the
  firmware revision shown in monospace.
* `No device` — no connection; in **Hardware** mode the word
  *offline* in red on hover reveals the underlying error.

The refresh icon next to the pill forces a fresh status read,
bypassing piPalette's two-second cache. Use it after physically
reconnecting cables or power-cycling the recorder.

## System

A fourth panel at the bottom of the Device page surfaces the host's
piPalette install and offers one-click updates. On a managed install
(one that came from `deploy/install.sh`) the panel shows:

**Version**
: The git tag piPalette is currently running from — for example
  `v0.1.0`. If the host is on a commit past the latest tag the value
  reads `vX.Y.Z-N-gSHA` (the standard `git describe` shape).

**Commit**
: The full commit hash, for the times you need to point at exactly
  what is deployed.

**Install**
: *Managed (systemd)* on a regular install, *Development* on a
  checkout you are hacking on directly.

**Last update**
: The status message left behind by the last update worker — *idle*
  after a clean run, an error string if something went wrong.

### Checking for updates

Click **Check for updates** in the panel header. piPalette runs
`git fetch --tags` against `origin` and reports back:

* If the latest tag is the one already running, the panel reads *Up to
  date — running vX.Y.Z*.
* If the tag on the remote is newer, the panel shows the new tag,
  lists the commits between your current `HEAD` and the new tag
  (newest first, capped at fifty), and offers an **Update now**
  button.

### Applying an update

**Update now** opens a confirmation that names the target tag and
warns that the service will restart — make sure no exposure is
running. Confirming kicks the update flow:

1. piPalette writes the target tag to `/var/lib/pipalette/pending-update`.
2. The systemd unit `pipalette-update.service` runs as root via the
   sudoers entry the installer dropped.
3. The worker fetches, checks out the new tag, reinstalls Python
   dependencies (including any new pinned pp8k version), and
   restarts `pipalette.service`.

The browser stays on the page, watching `/api/version`. As soon as the
service comes back up reporting the new tag, the page reloads
automatically.

> **Development checkouts cannot update via this button.** If the host
> is not a managed install — for example a `pip install -e .` from a
> clone — the **Update now** button is disabled and the panel reads
> *Development* under *Install*. Update by `git pull` and reinstalling
> as usual.
