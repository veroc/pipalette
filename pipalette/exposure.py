"""piPalette exposure runner.

Phase 2a: single-frame exposure.
Phase 2b: sequential roll runner with Start/Stop.

One frame in flight at a time across the whole app (the PP8K can't do two
anyway). The runner spawns a daemon thread that calls pp8k.Device.expose()
and writes status back through RollStore as the exposure progresses.
Roll runs are halted on first error and on user-requested stop; either
condition forces full recalibration on the next start.

The SSE progress stream (2c) will hook into the on_progress callback that
this runner already accepts but doesn't yet wire up.
"""

import queue
import threading

import pp8k


# pp8k.Device.expose() calibration_control values.
CAL_NORMAL = 0   # Full per-frame CRT calibration cycle.
CAL_NO_CAL = 3   # Skip the cycle — only safe when prior frame calibrated.


class ExposureBusyError(RuntimeError):
    """Raised when a second exposure is requested while one is in flight."""


class ExposureRunner:
    """Single-slot exposure runner with two modes:

    - Single-frame: expose_frame(roll_id, frame_id) — one frame, returns.
    - Roll: start_roll(roll_id) — sequential worker over all pending
      frames; stop_roll() halts after the current frame completes.

    Lock semantics: `_busy` is True for the lifetime of either mode.
    Concurrent expose_frame / start_roll requests get ExposureBusyError.
    """

    def __init__(self, device_manager, rolls):
        self._device_manager = device_manager
        self._rolls = rolls
        self._lock = threading.Lock()
        self._busy = False
        self._mode = None             # None | "single" | "roll"
        self._current = None          # (roll_id, frame_id) of active frame
        self._roll_id = None          # active roll (for "roll" mode)
        self._stop_requested = False  # only meaningful while mode == "roll"
        self._thread = None
        # SSE pub/sub. Each subscriber gets a bounded queue; if a slow
        # consumer fills it, we drop oldest events rather than block the
        # exposure thread.
        self._subscribers = []
        self._sub_lock = threading.Lock()

    # ---- public state inspection ---------------------------------------

    def is_busy(self):
        with self._lock:
            return self._busy

    def state(self):
        with self._lock:
            return {
                "busy": self._busy,
                "mode": self._mode,
                "roll_id": self._roll_id,
                "frame_id": self._current[1] if self._current else None,
                "stopping": self._stop_requested,
            }

    # ---- pub/sub (SSE) -------------------------------------------------

    def subscribe(self):
        """Return a new Queue that receives (event_name, data) tuples."""
        q = queue.Queue(maxsize=512)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._sub_lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def _publish(self, event_name, data):
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait((event_name, data))
            except queue.Full:
                # Slow consumer — drop the oldest so we don't lose the
                # newest update, and don't block the exposure thread.
                try:
                    q.get_nowait()
                    q.put_nowait((event_name, data))
                except (queue.Empty, queue.Full):
                    pass

    # ---- single-frame --------------------------------------------------

    def expose_frame(self, roll_id, frame_id):
        """Start a one-shot exposure in a background thread."""
        self._validate_frame_ready(roll_id, frame_id)
        with self._lock:
            if self._busy:
                raise ExposureBusyError("an exposure is already in progress")
            self._busy = True
            self._mode = "single"
            self._roll_id = roll_id
            self._current = (roll_id, frame_id)
            self._stop_requested = False

        self._thread = threading.Thread(
            target=self._run_single,
            args=(roll_id, frame_id),
            daemon=True,
            name=f"exposure-{frame_id}",
        )
        self._thread.start()
        self._publish("state", self.state())

    # ---- roll mode -----------------------------------------------------

    def start_roll(self, roll_id):
        """Start a sequential run over all pending frames in the roll."""
        roll = self._rolls.get(roll_id)
        if roll is None:
            raise KeyError(roll_id)
        if not any(f["status"] == "pending" for f in roll["frames"]):
            raise ValueError("no pending frames in this roll")
        with self._lock:
            if self._busy:
                raise ExposureBusyError("an exposure is already in progress")
            self._busy = True
            self._mode = "roll"
            self._roll_id = roll_id
            self._current = None
            self._stop_requested = False

        self._thread = threading.Thread(
            target=self._run_roll,
            args=(roll_id,),
            daemon=True,
            name=f"roll-{roll_id}",
        )
        self._thread.start()
        self._publish("state", self.state())

    def stop_roll(self):
        """Signal the roll runner to halt after the current frame.

        Returns True if a roll was running; False otherwise.
        """
        with self._lock:
            if self._mode != "roll":
                return False
            self._stop_requested = True
        self._publish("state", self.state())
        return True

    # ---- internals -----------------------------------------------------

    def _validate_frame_ready(self, roll_id, frame_id):
        """Raise KeyError / FileNotFoundError if the frame isn't ready
        to expose — keeps the caller's error path clean."""
        roll = self._rolls.get(roll_id)
        if roll is None:
            raise KeyError(roll_id)
        frame = next((f for f in roll["frames"] if f["id"] == frame_id), None)
        if frame is None:
            raise KeyError(frame_id)
        roll_dir = self._rolls.roll_dir(roll_id)
        if not (roll_dir / "outputs" / (frame_id + ".png")).exists():
            raise FileNotFoundError(f"rendered output missing for {frame_id}")
        if not (roll_dir / "profile.flm").exists():
            raise FileNotFoundError(f"FLM snapshot missing for roll {roll_id}")

    def _run_single(self, roll_id, frame_id):
        try:
            # Single-frame mode always uses normal calibration — we don't
            # know what the device just did before this click.
            self._expose_one_sync(roll_id, frame_id, CAL_NORMAL)
        finally:
            self._clear_state()

    def _run_roll(self, roll_id):
        try:
            while True:
                if self._is_stop_requested():
                    break
                roll = self._rolls.get(roll_id)
                if roll is None:
                    break
                next_frame = next(
                    (f for f in roll["frames"] if f["status"] == "pending"),
                    None,
                )
                if next_frame is None:
                    break  # nothing left to do — run complete

                with self._lock:
                    self._current = (roll_id, next_frame["id"])

                cal = self._calibration_for(roll)
                ok = self._expose_one_sync(roll_id, next_frame["id"], cal)
                if not ok:
                    # Hard stop on first failure — user resumes manually.
                    self._rolls.set_roll_field(roll_id, "recalibrate_next", True)
                    return
                # Frame succeeded; subsequent frames in this run may skip
                # cal if the roll allows it.
                if roll.get("recalibrate_next", True):
                    self._rolls.set_roll_field(
                        roll_id, "recalibrate_next", False,
                    )
            # Loop exited cleanly — either stop-requested or no pending.
            if self._is_stop_requested():
                # Force re-cal on resume.
                self._rolls.set_roll_field(roll_id, "recalibrate_next", True)
        finally:
            self._clear_state()

    def _calibration_for(self, roll):
        """Calibration policy: first frame after start always normal;
        subsequent frames in a continuous run skip cal if the roll allows."""
        if roll.get("recalibrate_next", True):
            return CAL_NORMAL
        if roll.get("skip_calibration"):
            return CAL_NO_CAL
        return CAL_NORMAL

    def _is_stop_requested(self):
        with self._lock:
            return self._stop_requested

    def _clear_state(self):
        with self._lock:
            self._busy = False
            self._mode = None
            self._roll_id = None
            self._current = None
            self._stop_requested = False
        self._publish("state", self.state())

    def _expose_one_sync(self, roll_id, frame_id, calibration_control):
        """Synchronously expose one frame. Returns True on success."""
        roll_dir = self._rolls.roll_dir(roll_id)
        image_path = str(roll_dir / "outputs" / (frame_id + ".png"))
        flm_path = str(roll_dir / "profile.flm")

        # Re-read frame for current settings (resolution may have changed
        # since validation; render is already aligned to whatever the
        # stored settings were).
        roll = self._rolls.get(roll_id)
        frame = next((f for f in roll["frames"] if f["id"] == frame_id), None)
        if frame is None:
            return False
        resolution = frame["resolution"]
        background = frame["background"]

        self._rolls.set_frame_status(roll_id, frame_id, "exposing", error=None)
        self._publish("frame_status", {
            "roll_id": roll_id, "frame_id": frame_id, "status": "exposing",
        })
        self._publish("state", self.state())

        def on_progress(p):
            # p is a pp8k.ExposureProgress NamedTuple. Send the whole thing
            # so the UI can decide what to render.
            self._publish("progress", {
                "roll_id": roll_id,
                "frame_id": frame_id,
                "phase": p.phase,
                "channel": p.channel,
                "lines_sent": p.lines_sent,
                "lines_total": p.lines_total,
                "buffer_free_kb": p.buffer_free_kb,
                "elapsed_seconds": p.elapsed_seconds,
                "eta_seconds": p.eta_seconds,
                "error": p.error,
            })

        try:
            flm = pp8k.load_flm(flm_path)
            device = self._device_manager.acquire_for_exposure()
            try:
                device.expose(
                    image_path=image_path,
                    flm=flm,
                    resolution=resolution,
                    transform="fit",
                    background=background,
                    rotation=0,
                    calibration_control=calibration_control,
                    on_progress=on_progress,
                )
            finally:
                self._device_manager.release()
        except Exception as exc:
            self._rolls.set_frame_status(
                roll_id, frame_id, "failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            self._publish("frame_status", {
                "roll_id": roll_id, "frame_id": frame_id,
                "status": "failed", "error": f"{type(exc).__name__}: {exc}",
            })
            return False
        self._rolls.set_frame_status(
            roll_id, frame_id, "done", error=None, mark_exposed=True,
        )
        self._publish("frame_status", {
            "roll_id": roll_id, "frame_id": frame_id, "status": "done",
        })
        return True
