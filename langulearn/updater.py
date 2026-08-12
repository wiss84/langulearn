"""Shared version/update-check logic for the app version (PyPI) and the
avatar/voice/photo asset bundle (GitHub Release) - used by both cli.py's
`langulearn setup`/every-launch notice and the running web app's top-bar
update notification + Settings > Updates tab (routes_api.py), so there's
one place that knows how to check, download, and apply an update instead
of two copies drifting apart.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from importlib import metadata as importlib_metadata
from pathlib import Path

from .constants import APP_VERSION, ASSETS_DIR, ASSETS_RELEASE_TAG, ASSETS_VERSION, DATA_DIR, RELEASES_DIR

PYPI_PROJECT = "langulearn"
GITHUB_REPO = "wiss84/langulearn"

# Each zip's contents are extracted flat into ASSETS_DIR/<key>/ - e.g.
# avatars.zip should contain the .glb files directly at its root, not
# nested inside an "avatar/" folder of their own - see
# design_plans/workflow/RELEASING_ASSETS.md.
ASSET_FILES = {
    "avatar": "avatars.zip",
    "voices": "voices.zip",
    "photos": "photos.zip",
}


# ---------------------------------------------------------------------------
# App version (PyPI)
# ---------------------------------------------------------------------------


def _installed_app_version() -> str:
    try:
        return importlib_metadata.version(PYPI_PROJECT)
    except importlib_metadata.PackageNotFoundError:
        return APP_VERSION


def installed_app_version() -> str:
    return _installed_app_version()


def _version_tuple(v: str) -> tuple:
    """Best-effort numeric parse ("0.3.1" -> (0, 3, 1)). Ignores any
    pre-release/build suffix - this app's own releases are plain X.Y.Z, so
    that's the only case this needs to get right; a genuinely malformed
    string just sorts as (0,), which reads as "not newer" rather than
    crashing anything that calls this.
    """
    parts = []
    for chunk in v.strip().split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(latest: str, current: str) -> bool:
    return _version_tuple(latest) > _version_tuple(current)


def get_latest_pypi_version(timeout: float = 3.0) -> str | None:
    """None on any failure (offline, PyPI down, unexpected response) -
    callers must treat that as "couldn't check," never as "no update
    available."
    """
    url = f"https://pypi.org/pypi/{PYPI_PROJECT}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "langulearn-update-check"})
    try:
        import json

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("info", {}).get("version")
    except Exception:
        return None


def check_app_update(timeout: float = 3.0) -> dict:
    current = _installed_app_version()
    latest = get_latest_pypi_version(timeout=timeout)
    return {
        "current": current,
        "latest": latest,
        "update_available": bool(latest) and is_newer(latest, current),
    }


def run_pip_upgrade() -> tuple[bool, str]:
    """Blocking - callers on the web app side must run this via
    asyncio.to_thread rather than await it directly on the event loop.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", PYPI_PROJECT],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "pip exited non-zero with no output.")
        return True, result.stdout
    except Exception as e:
        return False, str(e)


def relaunch_app() -> None:
    """Starts a fresh, fully detached `langulearn` process. Detached (own
    process group/session on POSIX, DETACHED_PROCESS on Windows) so the
    new process survives this one exiting - otherwise it'd be a child of a
    process that's about to disappear, which on some platforms tears the
    child down too.

    Does NOT close this process's own window or exit this process - see
    close_this_window() below, called separately by the /api/restart-app
    handler right after this.
    """
    creationflags = 0
    start_new_session = False
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        start_new_session = True
    subprocess.Popen(
        [sys.executable, "-m", "langulearn"],
        creationflags=creationflags,
        start_new_session=start_new_session,
        close_fds=True,
    )


def close_this_window() -> bool:
    """Closes THIS process's own pywebview window, which is what actually
    lets it exit: desktop.py's run() blocks on webview.start() until the
    window closes, then returns normally (uvicorn's server thread is a
    daemon thread - see desktop.py - so nothing keeps the process alive
    once the main thread finishes). webview.windows[0].destroy() is safe
    to call from a background thread (this runs from inside a FastAPI
    request handler, not the pywebview GUI thread) - pywebview dispatches
    it internally.

    Returns False (does nothing) if there's no window to close - e.g. if
    this module ever gets imported somewhere webview was never started
    (tests, or a hypothetical non-desktop server mode).
    """
    try:
        import webview

        if webview.windows:
            webview.windows[0].destroy()
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Assets (GitHub Release)
# ---------------------------------------------------------------------------


def _asset_url(filename: str) -> str:
    return f"https://github.com/{GITHUB_REPO}/releases/download/{ASSETS_RELEASE_TAG}/{filename}"


def _assets_marker_path() -> Path:
    return ASSETS_DIR / ".assets_version"


def get_local_assets_version() -> str | None:
    marker = _assets_marker_path()
    if not marker.is_file():
        return None
    return marker.read_text(encoding="utf-8").strip()


def check_assets_update() -> dict:
    """Compares against ASSETS_VERSION from the CURRENTLY INSTALLED code,
    not a remote check - there's no registry of asset-release versions to
    query the way PyPI is queried for the app version. This only catches
    "my assets are stale relative to the code I have"; if the *code* is
    also outdated, a newer ASSETS_VERSION won't be known until after that
    code update - which is exactly why cli.py's cmd_run re-checks this on
    every launch (see that module) rather than only once at setup time.
    """
    current = get_local_assets_version()
    return {
        "current": current,
        "latest": ASSETS_VERSION,
        "update_available": current != ASSETS_VERSION,
    }


def _download_with_progress(url: str, dest: Path, console=None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "langulearn-setup"})
    try:
        from rich.progress import (
            BarColumn,
            DownloadColumn,
            Progress,
            TimeRemainingColumn,
            TransferSpeedColumn,
        )

        with urllib.request.urlopen(req) as resp:
            total = int(resp.headers.get("Content-Length", 0)) or None
            with Progress(
                "[progress.description]{task.description}",
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                task = progress.add_task(dest.name, total=total)
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        f.write(chunk)
                        progress.update(task, advance=len(chunk))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError(
                f"Could not download {dest.name} (404 Not Found) from:\n  {url}\n"
                f"The '{ASSETS_RELEASE_TAG}' GitHub Release probably hasn't been published yet "
                "(or doesn't have this file attached) - see RELEASING_ASSETS.md for how to publish it."
            ) from e
        raise
    except ImportError:
        if console:
            console.print(f"Downloading {dest.name} (no progress bar available)...")
        with urllib.request.urlopen(req) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)


def download_and_extract_assets(force: bool = False, console=None) -> None:
    if not force and not check_assets_update()["update_available"]:
        if console:
            console.print("[dim]Avatar/voice/photo assets already up to date - skipping download.[/dim]")
        return

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="langulearn-assets-") as tmp:
        tmp_path = Path(tmp)
        for kind, filename in ASSET_FILES.items():
            url = _asset_url(filename)
            zip_path = tmp_path / filename
            if console:
                console.print(f"Downloading {filename} from {url} ...")
            _download_with_progress(url, zip_path, console)

            dest_dir = ASSETS_DIR / kind
            dest_dir.mkdir(parents=True, exist_ok=True)
            if console:
                console.print(f"Extracting {filename} ...")
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(dest_dir)

    _assets_marker_path().write_text(ASSETS_VERSION, encoding="utf-8")
    if console:
        console.print("[green]\u2713[/green] Avatar/voice/photo assets ready.")


# ---------------------------------------------------------------------------
# Release notes ("what's new")
# ---------------------------------------------------------------------------


def _whats_new_marker_path() -> Path:
    return DATA_DIR / ".last_seen_version"


def get_last_seen_version() -> str | None:
    marker = _whats_new_marker_path()
    if not marker.is_file():
        return None
    return marker.read_text(encoding="utf-8").strip()


def mark_version_seen(version: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _whats_new_marker_path().write_text(version, encoding="utf-8")


def check_whats_new() -> dict:
    """A brand new install (no marker yet) silently records the current
    version without reporting anything available - there's nothing "new"
    to announce about the version someone just installed for the first
    time. Only a version change relative to a previously recorded one
    counts as available.
    """
    current = _installed_app_version()
    last_seen = get_last_seen_version()
    if last_seen is None:
        mark_version_seen(current)
        return {"available": False, "version": current}
    return {"available": last_seen != current, "version": current}


def list_release_versions() -> list[str]:
    """Every version with a release-notes file on disk, newest first."""
    if not RELEASES_DIR.is_dir():
        return []
    versions = []
    for path in RELEASES_DIR.glob("v*_release.md"):
        version = path.name[len("v") : -len("_release.md")]
        versions.append(version)
    versions.sort(key=_version_tuple, reverse=True)
    return versions


def get_release_notes_markdown(version: str) -> str | None:
    path = RELEASES_DIR / f"v{version}_release.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def render_release_notes_html(version: str) -> str | None:
    raw = get_release_notes_markdown(version)
    if raw is None:
        return None
    import markdown

    return markdown.markdown(raw, extensions=["extra"])
