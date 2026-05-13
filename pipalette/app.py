"""Flask application factory + routes."""

import json
import shutil
import time
from pathlib import Path

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from . import updates
from .config import Config
from .device import DeviceManager, discover
from .film_tables import FilmTables, SLOT_MAX, SLOT_MIN
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
    device = DeviceManager(config)

    app.config["PIPALETTE_CONFIG"] = config
    app.config["PIPALETTE_FILM_TABLES"] = film_tables
    app.config["PIPALETTE_ROLLS"] = rolls
    app.config["PIPALETTE_DEVICE"] = device

    @app.context_processor
    def inject_storage():
        return {"storage": _storage_snapshot(data_dir)}

    # ---- pages -----------------------------------------------------------

    @app.route("/")
    def index():
        return redirect(url_for("rolls_page"))

    @app.route("/film-tables")
    def film_tables_page():
        return render_template(
            "film_tables.html",
            view="film-tables",
            status=device.status(),
            profiles=film_tables.profiles(),
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
        return render_template(
            "film_table_detail.html",
            view="film-tables",
            status=device.status(),
            profile=profile,
            table=table,
            curves=curves,
            uploaded_label=_format_timestamp(profile.get("uploaded_at")),
            size_label=_format_bytes(profile.get("size", 0)),
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

    @app.route("/api/film-tables/<profile_id>", methods=["DELETE"])
    def api_film_tables_delete(profile_id):
        ok = film_tables.delete(profile_id)
        return ("", 204) if ok else ("", 404)

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
