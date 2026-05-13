"""piPalette exposure runner.

Phase 2a: single-frame exposure. One frame in flight at a time across the
whole app (the PP8K can't do two anyway). The runner spawns a daemon
thread that calls pp8k.Device.expose() and writes status back through
RollStore as the exposure progresses.

The roll-level runner (Phase 2b) and SSE progress stream (2c) will build
on this — the same `_busy`/lock pattern carries forward.
"""

import threading

import pp8k


class ExposureBusyError(RuntimeError):
    """Raised when a second exposure is requested while one is in flight."""


class ExposureRunner:
    """Single-slot exposure runner.

    Owns the lifecycle of the worker thread. Public surface:
      - is_busy() / current() — what's running, if anything.
      - expose_frame(roll_id, frame_id) — kick off an exposure.
    """

    def __init__(self, device_manager, rolls):
        self._device_manager = device_manager
        self._rolls = rolls
        self._lock = threading.Lock()
        self._busy = False
        self._current = None  # (roll_id, frame_id) when busy
        self._thread = None

    def is_busy(self):
        with self._lock:
            return self._busy

    def current(self):
        with self._lock:
            return self._current

    def expose_frame(self, roll_id, frame_id):
        """Start an exposure in a background thread.

        Raises ExposureBusyError if one is already running, KeyError if
        the roll/frame doesn't exist, FileNotFoundError if the rendered
        output PNG is missing.
        """
        roll = self._rolls.get(roll_id)
        if roll is None:
            raise KeyError(roll_id)
        frame = next((f for f in roll["frames"] if f["id"] == frame_id), None)
        if frame is None:
            raise KeyError(frame_id)

        roll_dir = self._rolls.roll_dir(roll_id)
        image_path = roll_dir / "outputs" / (frame_id + ".png")
        flm_path = roll_dir / "profile.flm"
        if not image_path.exists():
            raise FileNotFoundError(f"rendered output missing: {image_path}")
        if not flm_path.exists():
            raise FileNotFoundError(f"FLM snapshot missing: {flm_path}")

        with self._lock:
            if self._busy:
                raise ExposureBusyError("an exposure is already in progress")
            self._busy = True
            self._current = (roll_id, frame_id)

        self._thread = threading.Thread(
            target=self._run_one,
            args=(roll_id, frame_id, str(image_path), str(flm_path),
                  frame["resolution"], frame["background"]),
            daemon=True,
            name=f"exposure-{frame_id}",
        )
        self._thread.start()

    def _run_one(self, roll_id, frame_id, image_path, flm_path,
                 resolution, background):
        try:
            self._rolls.set_frame_status(roll_id, frame_id, "exposing", error=None)
            try:
                flm = pp8k.load_flm(flm_path)
                device = self._device_manager.acquire_for_exposure()
                try:
                    # The rendered PNG is already at the exact canvas size
                    # for this resolution + aspect + transform + rotation,
                    # so we pass it as-is with no further geometry.
                    device.expose(
                        image_path=image_path,
                        flm=flm,
                        resolution=resolution,
                        transform="fit",
                        background=background,
                        rotation=0,
                    )
                finally:
                    self._device_manager.release()
                self._rolls.set_frame_status(
                    roll_id, frame_id, "done", error=None, mark_exposed=True,
                )
            except Exception as exc:
                self._rolls.set_frame_status(
                    roll_id, frame_id, "failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
        finally:
            with self._lock:
                self._busy = False
                self._current = None
