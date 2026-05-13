"""piPalette in-app updates.

Read-only on dev machines, executable on Pi installs that ran
``deploy/install.sh``. The state of an in-progress update is written to
``<state_dir>/update-status`` by ``deploy/apply-update.sh`` (running as
root via ``pipalette-update.service``); we read it back here.
"""

import re
import subprocess
from pathlib import Path


# Where the installer puts state. Mirrors STATE_DIR in install.sh.
STATE_DIR = Path("/var/lib/pipalette")
PENDING_FILE = STATE_DIR / "pending-update"
STATUS_FILE = STATE_DIR / "update-status"

# Marker that decides whether the in-app "Update now" button is wired up.
# Both must exist: the systemd unit (so we can trigger the worker) and the
# git checkout (so there's something to update).
MANAGED_SERVICE = Path("/etc/systemd/system/pipalette.service")


def repo_root():
    """Return the pipalette git checkout root, or None if not in one."""
    pkg_root = Path(__file__).resolve().parent.parent
    if (pkg_root / ".git").is_dir():
        return pkg_root
    return None


def is_managed_install():
    """Is this process running under a systemd-managed install?"""
    return MANAGED_SERVICE.exists() and repo_root() is not None


def _git(*args, root=None):
    """Run a git command in the repo root; return stripped stdout or None."""
    if root is None:
        root = repo_root()
    if root is None:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _is_version_tag(tag):
    return bool(re.fullmatch(r"v\d+(\.\d+)+(\.\d+)?", tag or ""))


def current_version():
    """Best-effort version label: the closest tag, or the short commit."""
    root = repo_root()
    if root is None:
        # Dev install (e.g., pip install -e .) without a git checkout — fall
        # back to the package version from pyproject.toml.
        try:
            from importlib.metadata import version
            return version("pipalette")
        except Exception:
            return "unknown"
    # `git describe --tags` returns "vX.Y.Z" exactly when HEAD is the tag,
    # or "vX.Y.Z-N-gSHA" when HEAD is N commits past the tag.
    described = _git("describe", "--tags", "--always", "--dirty", root=root)
    return described or "unknown"


def current_commit():
    return _git("rev-parse", "HEAD") or None


def status_message():
    """Last message written by apply-update.sh; None if no update has run."""
    try:
        return STATUS_FILE.read_text().strip() or None
    except (OSError, FileNotFoundError):
        return None


def version_payload():
    """Snapshot for GET /api/version — cheap, no network."""
    return {
        "version": current_version(),
        "commit": current_commit(),
        "managed": is_managed_install(),
        "status": status_message(),
    }


def check_for_updates():
    """Fetch from origin and report whether a newer tagged release exists.

    Returns a dict with the local + remote view; raises RuntimeError if
    git fetch fails (network down, etc.).
    """
    root = repo_root()
    if root is None:
        raise RuntimeError("not a git checkout — updates are unavailable")

    fetch = subprocess.run(
        ["git", "-C", str(root), "fetch", "--tags", "--prune"],
        capture_output=True, text=True, timeout=30,
    )
    if fetch.returncode != 0:
        raise RuntimeError(
            "git fetch failed: " + (fetch.stderr.strip() or "unknown error")
        )

    # All version-shaped tags, sorted newest first.
    all_tags_raw = _git("tag", "--sort=-v:refname", root=root) or ""
    tags = [t for t in all_tags_raw.splitlines() if _is_version_tag(t)]
    latest = tags[0] if tags else None

    current = current_version()
    # Strip the trailing "-N-gSHA" so we can compare to the latest tag cleanly.
    current_tag = current.split("-")[0] if current else None
    on_latest = bool(latest) and current_tag == latest

    # Show what's new between HEAD and the latest tag (newest first, capped).
    notes = []
    if latest and not on_latest:
        log = _git(
            "log",
            f"HEAD..{latest}",
            "--pretty=format:%h\t%s",
            "--no-merges",
            root=root,
        ) or ""
        for line in log.splitlines()[:50]:
            sha, _, subject = line.partition("\t")
            notes.append({"commit": sha, "subject": subject})

    return {
        "current": current,
        "latest": latest,
        "on_latest": on_latest,
        "notes": notes,
        "available_tags": tags[:10],
    }


def trigger_update(target_tag):
    """Write the pending-update file and kick off the systemd worker.

    The worker (``pipalette-update.service``) runs as root via the sudoers
    NOPASSWD entry. ``--no-block`` so this call returns before the worker
    stops pipalette.service.
    """
    if not is_managed_install():
        raise RuntimeError(
            "not a managed install — updates can only be applied on a "
            "Pi that ran deploy/install.sh"
        )
    if not _is_version_tag(target_tag):
        raise ValueError(f"refusing to update to non-version-tag {target_tag!r}")

    # Make sure the target ref is locally known so the worker doesn't have
    # to do its own discovery — clearer failure mode if the tag is bad.
    if _git("rev-parse", "--verify", "--quiet", target_tag + "^{tag}") is None:
        # Try one fetch in case the tag landed since the last check.
        subprocess.run(
            ["git", "-C", str(repo_root()), "fetch", "--tags", "--prune"],
            capture_output=True, timeout=30,
        )
        if _git("rev-parse", "--verify", "--quiet", target_tag + "^{tag}") is None:
            raise ValueError(f"unknown tag: {target_tag}")

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_FILE.with_suffix(".tmp")
    tmp.write_text(target_tag + "\n")
    tmp.replace(PENDING_FILE)

    # Wipe stale status from a previous run before kicking off.
    try:
        STATUS_FILE.unlink()
    except FileNotFoundError:
        pass

    proc = subprocess.run(
        ["sudo", "-n", "/bin/systemctl", "start", "--no-block",
         "pipalette-update.service"],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "systemctl start failed: " + (proc.stderr.strip() or "unknown error")
        )
    return {"queued": target_tag}
