"""Flask application factory + routes."""

import json
import queue
import shutil
import time
from pathlib import Path

import pp8k

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from . import calibration
from . import updates
from . import wizard_baselines
from .config import Config
from .device import DeviceManager, discover
from .exposure import ExposureBusyError, ExposureRunner
from . import film_tables as film_tables_mod
from .film_tables import FilmTables, SLOT_MAX, SLOT_MIN, _bw_filter_label
from .rolls import RollStore


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


def _format_bytes(n):
    """Human-readable byte count.  1024-based, simple GB labels (à la df -h)."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}" if n < 100 else f"{n:.0f} {unit}"
        n /= 1024


def _data_dir_size(data_dir):
    total = 0
    for path in Path(data_dir).rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    return total


def _sse(event, data):
    """Format a single SSE message."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _format_timestamp(ts):
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))


def _lut_set_payload(lut_set):
    """Pack one LUT set into a serializable dict for the curve renderer.

    Values are sent in *display* space (stored × per-channel scale) so the
    frontend can plot directly without knowing about scale factors.
    """
    def scaled(channel, scale):
        return [v * scale for v in channel.values]

    return {
        "red": scaled(lut_set.red, lut_set.scale_r),
        "green": scaled(lut_set.green, lut_set.scale_g),
        "blue": scaled(lut_set.blue, lut_set.scale_b),
        "scale_r": lut_set.scale_r,
        "scale_g": lut_set.scale_g,
        "scale_b": lut_set.scale_b,
    }


def _classify_calibration_roll(roll):
    """Map a calibration roll to a UI state machine state."""
    if not roll:
        return "none"
    frames = roll.get("frames") or []
    statuses = {f.get("status") for f in frames}
    if statuses & {"exposing"}:
        return "exposing"
    if "pending" in statuses:
        return "ready_to_expose"
    # All frames exposed (done / skipped / failed).
    measurements = roll.get("measurements") or []
    if len(measurements) < 2:
        return "awaiting_measurements"
    return "measurements_complete"


def _curve_payload(table):
    """Return JSON-serializable curve data for the detail page.

    The PP8K firmware loads Set 7 at HRES=4096 (4K) and Set 9 at HRES=8192
    (8K) — these are the two curves we visualize.
    """
    return json.dumps({
        "4k": _lut_set_payload(table.lut_sets[7]),
        "8k": _lut_set_payload(table.lut_sets[9]),
    })


def _storage_snapshot(data_dir):
    """Compose disk + data-dir stats for the sidebar."""
    try:
        usage = shutil.disk_usage(data_dir)
    except OSError:
        return None
    used_pct = (usage.used / usage.total) if usage.total else 0.0
    if used_pct >= 0.90:
        level = "danger"
    elif used_pct >= 0.70:
        level = "warn"
    else:
        level = "ok"
    data_bytes = _data_dir_size(data_dir)
    return {
        "data_bytes": data_bytes,
        "data_label": _format_bytes(data_bytes),
        "disk_free": usage.free,
        "disk_total": usage.total,
        "disk_free_label": _format_bytes(usage.free),
        "disk_total_label": _format_bytes(usage.total),
        "level": level,
    }


def create_app(data_dir=None):
    pkg_root = Path(__file__).resolve().parent.parent
    data_dir = Path(data_dir or pkg_root / "data")

    app = Flask(
        __name__,
        template_folder=str(pkg_root / "templates"),
        static_folder=str(pkg_root / "static"),
    )

    config = Config(data_dir / "config.json")
    # One-time migration from the pre-rename "library" folder name.
    legacy = data_dir / "library"
    target = data_dir / "film-tables"
    if legacy.exists() and not target.exists():
        legacy.rename(target)

    film_tables = FilmTables(target)
    rolls = RollStore(data_dir / "rolls")
    # Clear any "exposing" status left over from a service restart mid-burst.
    rolls.reset_exposing_frames()
    device = DeviceManager(config)
    runner = ExposureRunner(device, rolls)

    app.config["PIPALETTE_CONFIG"] = config
    app.config["PIPALETTE_FILM_TABLES"] = film_tables
    app.config["PIPALETTE_ROLLS"] = rolls
    app.config["PIPALETTE_DEVICE"] = device
    app.config["PIPALETTE_RUNNER"] = runner

    @app.context_processor
    def inject_storage():
        return {"storage": _storage_snapshot(data_dir)}

    # ---- pages -----------------------------------------------------------

    @app.route("/")
    def index():
        return redirect(url_for("rolls_page"))

    @app.route("/film-tables")
    def film_tables_page():
        wizard_data = json.dumps({
            "master_a": list(wizard_baselines.MASTER_A_DISPLAY),
            "master_b": list(wizard_baselines.MASTER_B_DISPLAY),
            "ref_iso": wizard_baselines.REF_ISO,
            "iso_options": sorted(wizard_baselines._BASE_BY_ISO.keys()),
        })
        return render_template(
            "film_tables.html",
            view="film-tables",
            status=device.status(),
            profiles=film_tables.profiles(),
            wizard_data=wizard_data,
        )

    @app.route("/film-tables/<profile_id>")
    def film_table_detail_page(profile_id):
        profile = film_tables.profile(profile_id)
        if profile is None:
            abort(404)
        try:
            table = film_tables.read_table(profile_id)
        except Exception as exc:
            abort(500, description=f"Failed to parse FLM: {exc}")
        if table is None:
            abort(404)
        curves = _curve_payload(table)

        # Active calibration session, if any.  The roll itself stays
        # hidden from /rolls; the UI on this page is the only entry
        # point to its measurement form + progress display.
        cal_roll = rolls.get_calibration_roll(profile_id)
        cal_roll_state = (_classify_calibration_roll(cal_roll)
                          if cal_roll else "none")
        cal_mode = (cal_roll.get("calibration_mode", "refinement")
                    if cal_roll else None)
        cal_steps_4k = None
        cal_steps_8k = None
        if cal_roll:
            if cal_mode == "speed_point":
                from . import calibration_lut
                px = calibration_lut.wedge_pixel_values()
            else:
                px = calibration.wedge_pixel_values()
            cal_steps_4k = [{"index": i + 1, "pixel": p}
                            for i, p in enumerate(px)]
            cal_steps_8k = list(cal_steps_4k)

        return render_template(
            "film_table_detail.html",
            view="film-tables",
            status=device.status(),
            profile=profile,
            table=table,
            curves=curves,
            bw_filter_label=_bw_filter_label(table.bw_filter),
            uploaded_label=_format_timestamp(profile.get("uploaded_at")),
            size_label=_format_bytes(profile.get("size", 0)),
            cal_roll=cal_roll,
            cal_state=cal_roll_state,
            cal_mode=cal_mode,
            cal_steps_4k=cal_steps_4k,
            cal_steps_8k=cal_steps_8k,
        )

    @app.route("/device")
    def device_page():
        return render_template(
            "device.html",
            view="device",
            status=device.status(),
            config=config.all(),
            version=updates.version_payload(),
        )

    # ---- partials (for in-page swaps) ------------------------------------

    @app.route("/partials/topbar")
    def partial_topbar():
        return render_template("partials/topbar.html", status=device.status(force=True))

    @app.route("/partials/roll/<roll_id>/frame/<frame_id>")
    def partial_roll_frame(roll_id, frame_id):
        roll = rolls.get(roll_id)
        if roll is None:
            abort(404)
        frame = next((fr for fr in roll["frames"] if fr["id"] == frame_id), None)
        if frame is None:
            abort(404)
        return render_template("partials/frame_card.html", roll=roll, f=frame)

    # ---- JSON / actions --------------------------------------------------

    @app.route("/api/status")
    def api_status():
        force = request.args.get("force") == "1"
        return jsonify(device.status(force=force))

    @app.route("/api/version")
    def api_version():
        return jsonify(updates.version_payload())

    @app.route("/api/update/check", methods=["POST"])
    def api_update_check():
        try:
            return jsonify(updates.check_for_updates())
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503

    @app.route("/api/update/apply", methods=["POST"])
    def api_update_apply():
        payload = request.get_json(silent=True) or {}
        target = (payload.get("target") or "").strip()
        if not target:
            return jsonify({"error": "target tag required"}), 400
        try:
            return jsonify(updates.trigger_update(target))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503

    @app.route("/api/discover", methods=["POST"])
    def api_discover():
        return jsonify({"hits": discover()})

    @app.route("/api/config", methods=["POST"])
    def api_config():
        payload = request.get_json(silent=True) or request.form.to_dict()
        cleaned = {}
        if "mock_mode" in payload:
            cleaned["mock_mode"] = _coerce_bool(payload["mock_mode"])
        if "target" in payload:
            target = payload["target"]
            if target in ("", None):
                cleaned["target"] = None
            else:
                cleaned["target"] = (
                    int(target) if str(target).isdigit() else str(target)
                )

        # Auto-discover on the Mock→Hardware flip when target is still unset.
        new_mock = cleaned.get("mock_mode", config.get("mock_mode"))
        new_target = cleaned.get("target", config.get("target"))
        if (
            not new_mock
            and new_target in (None, "")
            and config.get("mock_mode")  # we were in mock before
        ):
            hits = discover()
            if len(hits) == 1:
                cleaned["target"] = hits[0]["target"]

        config.update(**cleaned)
        try:
            device.reopen()
        except Exception:
            pass
        return jsonify({"config": config.all(), "status": device.status(force=True)})

    @app.route("/api/film-tables")
    def api_film_tables_list():
        return jsonify(film_tables.profiles())

    @app.route("/api/film-tables", methods=["POST"])
    def api_film_tables_upload():
        uploads = request.files.getlist("file")
        if not uploads:
            return jsonify({"error": "no file"}), 400
        added = []
        errors = []
        for upload in uploads:
            raw = upload.read()
            try:
                profile = film_tables.add(raw, upload.filename or "uploaded.flm")
                added.append(profile)
            except Exception as exc:
                errors.append({"filename": upload.filename, "error": str(exc)})
        return jsonify({"added": added, "errors": errors})

    @app.route("/api/film-tables/new", methods=["POST"])
    def api_film_tables_create():
        payload = request.get_json(silent=True) or request.form.to_dict()
        try:
            iso = int(payload.get("iso"))
            camera_type = int(payload.get("camera_type"))
            bw_filter = int(payload.get("bw_filter"))
        except (TypeError, ValueError):
            return jsonify({"error": "iso, camera_type, bw_filter must be integers"}), 400
        try:
            profile = film_tables.create(
                name=payload.get("name", ""),
                internal_name=payload.get("internal_name", ""),
                is_color=_coerce_bool(payload.get("is_color", False)),
                bw_filter=bw_filter,
                camera_type=camera_type,
                iso=iso,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(profile), 201

    @app.route("/api/film-tables/<profile_id>", methods=["DELETE"])
    def api_film_tables_delete(profile_id):
        ok = film_tables.delete(profile_id)
        return ("", 204) if ok else ("", 404)

    @app.route("/api/film-tables/<profile_id>/calibrate-speedpoint",
               methods=["POST"])
    def api_film_tables_calibrate_speedpoint(profile_id):
        """Create a SPEED-POINT calibration roll for the given film table.

        First-time calibration: wedge spans +-2 stops around the predicted
        speed-point drive for the FLM's labeled ISO.  The roll is exposed
        through a per-ISO calibration LUT, not the user's target FLM, so
        the drive at each patch is deterministic and toe coverage is
        guaranteed regardless of how off the target's curve is.
        """
        try:
            roll = calibration.create_speedpoint_roll(
                rolls, film_tables, profile_id,
            )
        except KeyError:
            return jsonify({"error": "film table not found"}), 404
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 410
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(roll), 201

    @app.route("/api/film-tables/<profile_id>/calibrate", methods=["POST"])
    def api_film_tables_calibrate(profile_id):
        """Create a REFINEMENT calibration roll for the given film table.

        Run after speed-point calibration to fine-tune the curve shape.
        Roll appears under /rolls and is populated with one ID frame
        plus 31 flat-tone wedge frames. After exposure and development
        the user enters densities via POST /api/calibration/<roll_id>.
        """
        profile = film_tables.profile(profile_id)
        if profile is None:
            return jsonify({"error": "film table not found"}), 404
        # Refinement only makes sense once speed-point has placed the
        # LUT close.  Reject early with a clear hint to the UI rather
        # than letting the user expose a wasted roll.
        if profile.get("cal_state") == film_tables_mod.CAL_STATE_UNCALIBRATED:
            return jsonify({
                "error": "refinement requires a prior speed-point calibration; "
                         "run /api/film-tables/<id>/calibrate-speedpoint first",
            }), 409
        try:
            roll = calibration.create_calibration_roll(
                rolls, film_tables, profile_id,
            )
        except KeyError:
            return jsonify({"error": "film table not found"}), 404
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 410
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(roll), 201

    @app.route("/api/calibration/<roll_id>/measurements", methods=["POST"])
    def api_calibration_measurements(roll_id):
        """Store the densitometer readings on a calibration roll and
        return diagnostic verdicts for the 4K and 8K wedges separately.
        Does NOT apply the new LUT -- that happens via /apply.

        Expected payload:
            {
              "measurements_4k": [{"pixel": 0, "density": 0.20}, ...],
              "measurements_8k": [{"pixel": 0, "density": 0.20}, ...]
            }
        Either list may be omitted (e.g., calibrating only 4K).
        """
        payload = request.get_json(silent=True) or {}
        m4k = payload.get("measurements_4k") or []
        m8k = payload.get("measurements_8k") or []
        if not (m4k or m8k):
            return jsonify({"error": "no measurements provided"}), 400
        # Tag each measurement with its resolution before storing.
        tagged = []
        for m in m4k:
            tagged.append({"resolution": "4k",
                           "pixel": int(m["pixel"]),
                           "density": float(m["density"])})
        for m in m8k:
            tagged.append({"resolution": "8k",
                           "pixel": int(m["pixel"]),
                           "density": float(m["density"])})
        try:
            roll = rolls.set_measurements(roll_id, tagged)
        except KeyError:
            return jsonify({"error": "roll not found"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        result = {"roll": roll}
        mode = roll.get("calibration_mode", "refinement")
        if mode == "speed_point":
            # Convert (pixel, density) to (drive, density) and report the
            # recovered D_sp per resolution.  No full curve diagnose --
            # speed-point only commits to one point.
            from . import calibration_lut
            source_id = roll.get("calibration_for")
            source_profile = (film_tables.profile(source_id)
                              if source_id else None)
            iso = source_profile.get("iso") if source_profile else None
            if iso is None:
                return jsonify({"error": "source profile has no iso"}), 400
            drives_4k = calibration_lut.wedge_drives(
                calibration_lut.predicted_speed_point(iso, "4k"))
            drives_8k = calibration_lut.wedge_drives(
                calibration_lut.predicted_speed_point(iso, "8k"))

            def _summarize(meas, drives):
                pairs = sorted(
                    (float(drives[int(m["pixel"]) - 1]), float(m["density"]))
                    for m in meas
                    if 1 <= int(m["pixel"]) <= len(drives)
                )
                if len(pairs) < 4:
                    return None
                try:
                    return {
                        "D_sp": round(calibration.find_speed_point(pairs), 1),
                        "b_plus_f": round(min(p[1] for p in pairs), 3),
                        "d_max": round(max(p[1] for p in pairs), 3),
                        "patches": len(pairs),
                        "verdict": "ok",
                    }
                except ValueError as e:
                    return {"error": str(e), "verdict": "wedge_off_target"}

            if m4k:
                result["speedpoint_4k"] = _summarize(m4k, drives_4k)
            if m8k:
                result["speedpoint_8k"] = _summarize(m8k, drives_8k)
        else:
            if len(m4k) >= 5:
                pairs_4k = sorted(((int(m["pixel"]), float(m["density"]))
                                   for m in m4k),
                                  key=lambda t: t[0])
                result["diagnostic_4k"] = calibration.diagnose(pairs_4k)
            if len(m8k) >= 5:
                pairs_8k = sorted(((int(m["pixel"]), float(m["density"]))
                                   for m in m8k),
                                  key=lambda t: t[0])
                result["diagnostic_8k"] = calibration.diagnose(pairs_8k)
        return jsonify(result)

    @app.route("/api/calibration/<roll_id>/apply", methods=["POST"])
    def api_calibration_apply(roll_id):
        """Compute the corrected LUTs (Master A from 4K, Master B from 8K),
        save them as a new versioned FLM."""
        payload = request.get_json(silent=True) or {}
        try:
            target_range = float(
                payload.get("target_range", calibration.PAPER_GRADE_RANGE[2])
            )
        except (TypeError, ValueError):
            return jsonify({"error": "target_range must be a float"}), 400

        roll = rolls.get(roll_id)
        if roll is None:
            return jsonify({"error": "roll not found"}), 404
        if "calibration_for" not in roll:
            return jsonify({"error": "roll is not a calibration roll"}), 400
        meas = roll.get("measurements") or []
        if len(meas) < 5:
            return jsonify({"error": "no measurements stored yet"}), 400

        source_profile_id = roll["calibration_for"]
        source = film_tables.read_table(source_profile_id)
        if source is None:
            return jsonify({"error": "source film table missing"}), 410

        # Split measurements by resolution.
        pairs_4k = sorted(((m["pixel"], m["density"]) for m in meas
                           if m.get("resolution") == "4k"),
                          key=lambda t: t[0])
        pairs_8k = sorted(((m["pixel"], m["density"]) for m in meas
                           if m.get("resolution") == "8k"),
                          key=lambda t: t[0])

        # Master A from 4K data; if 8K not provided, keep Master B in sync
        # with the new Master A (×0.5 -- matches the wizard convention).
        if len(pairs_4k) >= 5:
            old_a = calibration.calibrated_master_a_display(source)
            new_a = list(calibration.correct_lut(old_a, pairs_4k,
                                                 target_range=target_range))
        else:
            new_a = list(calibration.calibrated_master_a_display(source))

        if len(pairs_8k) >= 5:
            old_b = calibration.calibrated_master_b_display(source)
            new_b = list(calibration.correct_lut(old_b, pairs_8k,
                                                 target_range=target_range))
        else:
            new_b = None  # will be derived from new_a via the wizard convention

        existing_ids = {p["id"] for p in film_tables.profiles()}
        new_internal = calibration.next_versioned_name(
            source_profile_id, existing_ids,
        )
        new_table = calibration.build_calibrated_table(
            source, new_a, new_internal_name=new_internal,
            new_name=source.name, new_master_b_display=new_b,
        )
        raw = pp8k.serialize_flm(new_table)
        ft_files = film_tables._files_dir
        ft_files.mkdir(parents=True, exist_ok=True)
        filename = new_internal + ".flm"
        (ft_files / filename).write_bytes(raw)
        new_profile = {
            "id": new_internal,
            "filename": filename,
            "original_name": filename,
            "name": new_table.name,
            "camera_type": new_table.camera_type_name,
            "is_bw": bool(new_table.is_bw),
            "bw_filter": new_table.bw_filter,
            "bw_filter_name": source.bw_filter_name,
            "aspect_w": new_table.aspect_w,
            "aspect_h": new_table.aspect_h,
            "size": len(raw),
            "uploaded_at": int(time.time()),
            "calibrated_from": source_profile_id,
            "calibration_roll": roll_id,
            "cal_state": film_tables_mod.CAL_STATE_REFINED,
        }
        with film_tables._lock:
            film_tables._index["profiles"].append(new_profile)
            film_tables._save()
        return jsonify(new_profile), 201

    @app.route("/api/calibration/<roll_id>/apply-speedpoint", methods=["POST"])
    def api_calibration_apply_speedpoint(roll_id):
        """Compute D_sp_4k and D_sp_8k from the speed-point roll's
        measurements, then build a new FLM whose LUT lands the speed
        point at pixel 25 with a sigmoid-shape working range above it.

        Writes the new FLM with cal_state=speed_point and returns the
        new profile dict.  Refinement is enabled on the new profile.
        """
        from . import calibration_lut

        payload = request.get_json(silent=True) or {}
        try:
            target_range = float(
                payload.get("target_range", calibration.PAPER_GRADE_RANGE[2])
            )
        except (TypeError, ValueError):
            return jsonify({"error": "target_range must be a float"}), 400

        roll = rolls.get(roll_id)
        if roll is None:
            return jsonify({"error": "roll not found"}), 404
        if roll.get("calibration_mode") != "speed_point":
            return jsonify({"error": "roll is not a speed-point roll"}), 400
        meas = roll.get("measurements") or []
        if len(meas) < 4:
            return jsonify({"error": "no measurements stored yet"}), 400

        source_profile_id = roll["calibration_for"]
        source = film_tables.read_table(source_profile_id)
        if source is None:
            return jsonify({"error": "source film table missing"}), 410
        source_profile = film_tables.profile(source_profile_id)
        iso = source_profile.get("iso")
        if iso is None:
            return jsonify({"error": "source profile has no iso"}), 400

        # Convert (pixel, density) to (drive, density) per resolution
        # using the calibration LUT's known patch drives.  Pixel value
        # i corresponds to the i-th log-spaced wedge drive.
        drives_4k = calibration_lut.wedge_drives(
            calibration_lut.predicted_speed_point(iso, "4k"))
        drives_8k = calibration_lut.wedge_drives(
            calibration_lut.predicted_speed_point(iso, "8k"))

        def _to_drive_pairs(measurements, drives):
            pairs = []
            for m in measurements:
                pixel = int(m["pixel"])
                # patch index = pixel value (1..N_PATCHES); drives[i] is
                # for patch i+1.  Pixels outside the patch range are
                # dropped (caller probably picked the wrong wedge).
                if 1 <= pixel <= len(drives):
                    pairs.append((float(drives[pixel - 1]),
                                  float(m["density"])))
            pairs.sort(key=lambda t: t[0])
            return pairs

        pairs_4k = _to_drive_pairs(
            (m for m in meas if m.get("resolution") == "4k"), drives_4k)
        pairs_8k = _to_drive_pairs(
            (m for m in meas if m.get("resolution") == "8k"), drives_8k)
        if len(pairs_4k) < 4 and len(pairs_8k) < 4:
            return jsonify({
                "error": "need at least 4 measurements per resolution"
            }), 400

        try:
            D_sp_4k = (calibration.find_speed_point(pairs_4k)
                       if len(pairs_4k) >= 4 else None)
            D_sp_8k = (calibration.find_speed_point(pairs_8k)
                       if len(pairs_8k) >= 4 else None)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 422

        # If one resolution wasn't measured, fall back to the 4x ratio
        # convention (Master B drives ~ Master A / 4).
        if D_sp_4k is None and D_sp_8k is not None:
            D_sp_4k = D_sp_8k * 4
        if D_sp_8k is None and D_sp_4k is not None:
            D_sp_8k = D_sp_4k / 4
        if D_sp_4k is None or D_sp_8k is None:
            return jsonify({"error": "could not compute speed points"}), 422

        new_a, new_b = calibration.build_speedpoint_lut(
            D_sp_4k, D_sp_8k, target_range=target_range)

        existing_ids = {p["id"] for p in film_tables.profiles()}
        new_internal = calibration.next_versioned_name(
            source_profile_id, existing_ids,
        )
        new_table = calibration.build_calibrated_table(
            source, new_a, new_internal_name=new_internal,
            new_name=source.name, new_master_b_display=new_b,
        )
        raw = pp8k.serialize_flm(new_table)
        ft_files = film_tables._files_dir
        ft_files.mkdir(parents=True, exist_ok=True)
        filename = new_internal + ".flm"
        (ft_files / filename).write_bytes(raw)
        new_profile = {
            "id": new_internal,
            "filename": filename,
            "original_name": filename,
            "name": new_table.name,
            "camera_type": new_table.camera_type_name,
            "is_bw": bool(new_table.is_bw),
            "bw_filter": new_table.bw_filter,
            "bw_filter_name": source.bw_filter_name,
            "aspect_w": new_table.aspect_w,
            "aspect_h": new_table.aspect_h,
            "size": len(raw),
            "iso": iso,
            "uploaded_at": int(time.time()),
            "calibrated_from": source_profile_id,
            "calibration_roll": roll_id,
            "cal_state": film_tables_mod.CAL_STATE_SPEED_POINT,
            "D_sp_4k": round(D_sp_4k, 1),
            "D_sp_8k": round(D_sp_8k, 1),
        }
        with film_tables._lock:
            film_tables._index["profiles"].append(new_profile)
            film_tables._save()
        return jsonify(new_profile), 201

    # ---- rolls -----------------------------------------------------------

    @app.route("/rolls")
    def rolls_page():
        return render_template(
            "rolls.html",
            view="rolls",
            status=device.status(),
            rolls=rolls.list(),
            profiles=film_tables.profiles(),
        )

    @app.route("/rolls/<roll_id>")
    def roll_detail_page(roll_id):
        roll = rolls.get(roll_id)
        if roll is None:
            abort(404)
        return render_template(
            "roll_detail.html",
            view="rolls",
            status=device.status(),
            roll=roll,
            roll_size_label=_format_bytes(rolls.size(roll_id)),
        )

    @app.route("/api/rolls", methods=["POST"])
    def api_roll_create():
        payload = request.get_json(silent=True) or request.form.to_dict()
        name = (payload.get("name") or "").strip()
        profile_id = payload.get("profile_id")
        if not name or not profile_id:
            return jsonify({"error": "name and profile_id required"}), 400
        profile = film_tables.profile(profile_id)
        if profile is None:
            return jsonify({"error": "film table not found"}), 404
        if "aspect_w" not in profile or "aspect_h" not in profile:
            return jsonify({"error": "film table missing aspect metadata"}), 400
        flm_bytes = film_tables.read_bytes(profile_id)
        if flm_bytes is None:
            return jsonify({"error": "profile file missing"}), 410
        bw_filter = payload.get("bw_filter")
        try:
            roll = rolls.create(name, profile, flm_bytes, bw_filter=bw_filter)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(roll), 201

    @app.route("/api/rolls/<roll_id>", methods=["PATCH", "POST"])
    def api_roll_update(roll_id):
        payload = request.get_json(silent=True) or request.form.to_dict()
        changes = {}
        if "name" in payload:
            try:
                rolls.rename(roll_id, payload["name"])
            except (ValueError, KeyError) as exc:
                return jsonify({"error": str(exc)}), 400
        for key in ("skip_calibration",):
            if key in payload:
                changes[key] = payload[key]
        if changes:
            try:
                roll = rolls.update(roll_id, **changes)
            except (ValueError, KeyError) as exc:
                return jsonify({"error": str(exc)}), 400
        else:
            roll = rolls.get(roll_id)
            if roll is None:
                return jsonify({"error": "not found"}), 404
        return jsonify(roll)

    @app.route("/api/rolls/<roll_id>", methods=["DELETE"])
    def api_roll_delete(roll_id):
        ok = rolls.delete(roll_id)
        return ("", 204) if ok else ("", 404)

    @app.route("/api/rolls/<roll_id>/images", methods=["POST"])
    def api_roll_upload(roll_id):
        if rolls.get(roll_id) is None:
            return jsonify({"error": "roll not found"}), 404
        uploads = request.files.getlist("file")
        if not uploads:
            return jsonify({"error": "no file"}), 400
        added = []
        errors = []
        for upload in uploads:
            raw = upload.read()
            try:
                frame = rolls.add_image(roll_id, raw, upload.filename or "image.jpg")
                added.append(frame)
            except Exception as exc:
                errors.append({"filename": upload.filename, "error": str(exc)})
        return jsonify({"added": added, "errors": errors})

    @app.route("/api/rolls/<roll_id>/frames/<frame_id>", methods=["PATCH", "POST"])
    def api_frame_update(roll_id, frame_id):
        payload = request.get_json(silent=True) or request.form.to_dict()
        changes = {}
        for key in ("resolution", "transform", "rotation", "background"):
            if key in payload:
                changes[key] = payload[key]
        try:
            frame = rolls.update_frame(roll_id, frame_id, **changes)
        except (ValueError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(frame)

    @app.route("/api/rolls/<roll_id>/frames/<frame_id>", methods=["DELETE"])
    def api_frame_delete(roll_id, frame_id):
        try:
            ok = rolls.delete_frame(roll_id, frame_id)
        except KeyError:
            return ("", 404)
        return ("", 204) if ok else ("", 404)

    @app.route("/api/rolls/<roll_id>/frames/<frame_id>/reset", methods=["POST"])
    def api_frame_reset(roll_id, frame_id):
        """Flip a done/failed frame back to pending so the next roll run
        re-queues it. History (exposure_count, exposed_at) is preserved.
        Refused while the runner is busy on this frame."""
        state = runner.state()
        if state.get("busy") and state.get("frame_id") == frame_id:
            return jsonify({"error": "frame is currently exposing"}), 409
        roll = rolls.get(roll_id)
        if roll is None:
            return jsonify({"error": "roll not found"}), 404
        frame = next((f for f in roll["frames"] if f["id"] == frame_id), None)
        if frame is None:
            return jsonify({"error": "frame not found"}), 404
        if frame["status"] not in ("done", "failed"):
            return jsonify({
                "error": f"can't reset a {frame['status']} frame",
            }), 409
        rolls.set_frame_status(roll_id, frame_id, "pending", error=None)
        runner._publish("frame_status", {
            "roll_id": roll_id, "frame_id": frame_id, "status": "pending",
        })
        return jsonify({"frame_id": frame_id, "status": "pending"})

    @app.route("/api/rolls/<roll_id>/frames/<frame_id>/skip-toggle", methods=["POST"])
    def api_frame_skip_toggle(roll_id, frame_id):
        """Flip a frame between 'pending' and 'skipped'. Only those two
        statuses participate in the toggle — done/failed/exposing frames
        return 409."""
        roll = rolls.get(roll_id)
        if roll is None:
            return jsonify({"error": "roll not found"}), 404
        frame = next((f for f in roll["frames"] if f["id"] == frame_id), None)
        if frame is None:
            return jsonify({"error": "frame not found"}), 404
        if frame["status"] == "pending":
            new_status = "skipped"
        elif frame["status"] == "skipped":
            new_status = "pending"
        else:
            return jsonify({
                "error": f"can't toggle skip on a {frame['status']} frame",
            }), 409
        rolls.set_frame_status(roll_id, frame_id, new_status, error=None)
        # Tell SSE subscribers so other browser tabs / the running UI
        # re-render the frame card.
        runner._publish("frame_status", {
            "roll_id": roll_id, "frame_id": frame_id, "status": new_status,
        })
        return jsonify({"frame_id": frame_id, "status": new_status})

    @app.route("/api/rolls/<roll_id>/frames/<frame_id>/expose", methods=["POST"])
    def api_frame_expose(roll_id, frame_id):
        try:
            runner.expose_frame(roll_id, frame_id)
        except ExposureBusyError as exc:
            return jsonify({"error": str(exc)}), 409
        except KeyError:
            return jsonify({"error": "not found"}), 404
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 410
        return jsonify({"queued": frame_id, "roll_id": roll_id}), 202

    @app.route("/api/rolls/<roll_id>/frames/<frame_id>")
    def api_frame_get(roll_id, frame_id):
        roll = rolls.get(roll_id)
        if roll is None:
            abort(404)
        frame = next((f for f in roll["frames"] if f["id"] == frame_id), None)
        if frame is None:
            abort(404)
        return jsonify(frame)

    @app.route("/api/runner")
    def api_runner_status():
        return jsonify(runner.state())

    @app.route("/api/runner/events")
    def api_runner_events():
        """SSE stream of exposure progress + lifecycle events.

        Emits the current state on connect, then any (event, data) tuples
        the runner publishes. A 15s keepalive comment keeps idle
        connections alive through reverse proxies.
        """
        def stream():
            sub = runner.subscribe()
            try:
                # Initial snapshot so a late-arriving client knows where we are.
                yield _sse("state", runner.state())
                while True:
                    try:
                        event, data = sub.get(timeout=15)
                        yield _sse(event, data)
                    except queue.Empty:
                        yield ": keepalive\n\n"
            except GeneratorExit:
                pass
            finally:
                runner.unsubscribe(sub)

        resp = Response(stream(), mimetype="text/event-stream")
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"
        return resp

    @app.route("/api/rolls/<roll_id>/start", methods=["POST"])
    def api_roll_start(roll_id):
        try:
            runner.start_roll(roll_id)
        except ExposureBusyError as exc:
            return jsonify({"error": str(exc)}), 409
        except KeyError:
            return jsonify({"error": "roll not found"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"started": roll_id}), 202

    @app.route("/api/rolls/<roll_id>/reset-done", methods=["POST"])
    def api_roll_reset_done(roll_id):
        """Flip all 'done' frames back to 'pending' so the next roll run
        re-exposes them. History (exposure_count, exposed_at) is preserved.
        Skipped/failed/exposing frames are intentionally left alone."""
        state = runner.state()
        if state.get("busy") and state.get("roll_id") == roll_id:
            return jsonify({"error": "can't reset while this roll is running"}), 409
        roll = rolls.get(roll_id)
        if roll is None:
            return jsonify({"error": "roll not found"}), 404
        reset_ids = []
        for f in roll["frames"]:
            if f["status"] == "done":
                rolls.set_frame_status(roll_id, f["id"], "pending", error=None)
                runner._publish("frame_status", {
                    "roll_id": roll_id, "frame_id": f["id"], "status": "pending",
                })
                reset_ids.append(f["id"])
        # Reset recalibrate_next so the next run starts with a fresh cal cycle.
        rolls.set_roll_field(roll_id, "recalibrate_next", True)
        return jsonify({"reset": len(reset_ids), "frame_ids": reset_ids})

    @app.route("/api/rolls/<roll_id>/stop", methods=["POST"])
    def api_roll_stop(roll_id):
        state = runner.state()
        if not state.get("busy") or state.get("roll_id") != roll_id \
                or state.get("mode") != "roll":
            return jsonify({"error": "this roll is not currently running"}), 409
        runner.stop_roll()
        return jsonify({"stopping": roll_id}), 202

    @app.route("/api/rolls/<roll_id>/reorder", methods=["POST"])
    def api_roll_reorder(roll_id):
        payload = request.get_json(silent=True) or {}
        order = payload.get("frame_ids")
        if not isinstance(order, list):
            return jsonify({"error": "frame_ids list required"}), 400
        try:
            rolls.reorder(roll_id, order)
        except (ValueError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True})

    @app.route("/rolls/<roll_id>/thumb/<frame_id>")
    def serve_thumb(roll_id, frame_id):
        path = rolls.asset_path(roll_id, "thumbs", frame_id + ".jpg")
        if path is None:
            abort(404)
        return send_file(path, mimetype="image/jpeg", max_age=0)

    @app.route("/rolls/<roll_id>/output/<frame_id>")
    def serve_output(roll_id, frame_id):
        path = rolls.asset_path(roll_id, "outputs", frame_id + ".png")
        if path is None:
            abort(404)
        return send_file(path, mimetype="image/png", max_age=0)

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, debug=True)
