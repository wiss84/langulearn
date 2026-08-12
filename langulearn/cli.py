"""LanguLearn CLI entry point - `pip install langulearn` puts `langulearn`
on your PATH, pointed at main() below via [project.scripts] in
pyproject.toml.

First run (or an explicit `langulearn setup`) bootstraps three things this
package can't express as plain dependencies/package-data:

 1. webrtcvad-wheels + resemblyzer --no-deps. pip has no built-in way to
    substitute one package for another package's own declared dependency,
    so these are installed directly instead of being listed in
    pyproject.toml.
 2. The avatar/voice/photo assets, downloaded from a GitHub Release rather
    than bundled in the wheel.
 3. A desktop shortcut for this platform, using the right icon.

After that (or immediately, if it's already been done), it launches the
app via desktop.run().
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from .constants import ASSETS_VERSION, DATA_DIR
from .updater import check_app_update, download_and_extract_assets

_SETUP_MARKER = DATA_DIR / ".setup_complete"


def _console():
    """rich.Console if available, else a minimal print-based stand-in that
    strips rich markup, so a missing rich install degrades gracefully
    instead of crashing.
    """
    try:
        from rich.console import Console

        return Console()
    except ImportError:

        class _Plain:
            def print(self, *args, **kwargs):
                # Strip rich markup crudely - good enough for the plain-text
                # fallback path, which only exists so this never hard-fails.
                import re

                for a in args:
                    print(re.sub(r"\[/?[a-z ]+\]", "", str(a)))

        return _Plain()


# ---------------------------------------------------------------------------
# Step 1: webrtcvad-wheels + resemblyzer --no-deps
# ---------------------------------------------------------------------------


def _extra_deps_installed() -> bool:
    return importlib.util.find_spec("webrtcvad") is not None and importlib.util.find_spec("resemblyzer") is not None


def _install_extra_deps(console) -> None:
    if _extra_deps_installed():
        console.print("[dim]webrtcvad/resemblyzer already installed - skipping.[/dim]")
        return
    console.print("Installing webrtcvad-wheels + resemblyzer (needed for hands-free speaker verification)...")
    subprocess.run([sys.executable, "-m", "pip", "install", "webrtcvad-wheels"], check=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "resemblyzer"], check=True)
    console.print("[green]\u2713[/green] webrtcvad + resemblyzer installed.")


# ---------------------------------------------------------------------------
# Step 2: avatar/voice/photo assets - see updater.py (shared with the
# running app's own "Update Assets" button, so there's one place that
# knows how to check/download these instead of two copies drifting apart).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Step 3: desktop shortcut
# ---------------------------------------------------------------------------


def _icon_path() -> Path:
    static_dir = Path(__file__).parent / "static"
    if sys.platform == "darwin":
        return static_dir / "LanguLearn.icns"
    if sys.platform.startswith("linux"):
        return static_dir / "LanguLearn.png"
    return static_dir / "LanguLearn.ico"


def _desktop_dir() -> Path:
    return Path.home() / "Desktop"


def _create_shortcut_windows(console) -> None:
    """A real .lnk (not just a renamed .bat), so it gets a proper custom
    icon - built via pywin32's WScript.Shell COM automation (the standard
    way to do this without hand-rolling the binary .lnk format). Points at
    `pythonw -m langulearn` rather than the langulearn.exe console-script
    stub directly, so double-clicking the icon doesn't flash a console
    window - langulearn/__main__.py exists specifically to make `-m
    langulearn` work for this.
    """
    try:
        import win32com.client
    except ImportError:
        console.print("[yellow]pywin32 not available - skipping desktop shortcut.[/yellow]")
        return

    pythonw = Path(sys.executable).parent / "pythonw.exe"
    if not pythonw.is_file():
        pythonw = Path(sys.executable)  # fallback - still works, just shows a console

    shortcut_path = _desktop_dir() / "LanguLearn.lnk"
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(shortcut_path))
    shortcut.TargetPath = str(pythonw)
    shortcut.Arguments = "-m langulearn"
    shortcut.WorkingDirectory = str(Path.home())
    icon = _icon_path()
    if icon.is_file():
        shortcut.IconLocation = f"{icon},0"
    shortcut.Description = "LanguLearn - AI language tutor"
    shortcut.save()
    console.print(f"[green]\u2713[/green] Desktop shortcut created: {shortcut_path}")


def _create_shortcut_macos(console) -> None:
    """A minimal real .app bundle (Info.plist + a launcher shell script +
    the .icns) rather than a bare .command file, so it gets a proper Finder
    icon and behaves like a normal double-clickable app.
    """
    langulearn_bin = shutil.which("langulearn")
    if not langulearn_bin:
        console.print("[yellow]Could not locate the langulearn executable on PATH - skipping desktop shortcut.[/yellow]")
        return

    app_dir = _desktop_dir() / "LanguLearn.app"
    macos_dir = app_dir / "Contents" / "MacOS"
    resources_dir = app_dir / "Contents" / "Resources"
    macos_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)

    icon = _icon_path()
    if icon.is_file():
        shutil.copy2(icon, resources_dir / "LanguLearn.icns")

    info_plist = app_dir / "Contents" / "Info.plist"
    info_plist.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        "  <key>CFBundleName</key><string>LanguLearn</string>\n"
        "  <key>CFBundleExecutable</key><string>LanguLearn</string>\n"
        "  <key>CFBundleIconFile</key><string>LanguLearn.icns</string>\n"
        "  <key>CFBundleIdentifier</key><string>com.wissam.langulearn</string>\n"
        "  <key>CFBundlePackageType</key><string>APPL</string>\n"
        "  <key>CFBundleShortVersionString</key><string>1.0</string>\n"
        "</dict>\n</plist>\n",
        encoding="utf-8",
    )

    launcher = macos_dir / "LanguLearn"
    launcher.write_text(f'#!/bin/bash\nexec "{langulearn_bin}" "$@"\n', encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    console.print(f"[green]\u2713[/green] Desktop app created: {app_dir}")


def _create_shortcut_linux(console) -> None:
    """A .desktop file - the standard Linux equivalent of a shortcut.
    Written to both ~/Desktop (if it exists) and ~/.local/share/applications
    (so it also shows up in the app launcher/menu, not just as a desktop
    icon - desktop environments vary on whether they show Desktop icons at
    all by default).
    """
    langulearn_bin = shutil.which("langulearn")
    if not langulearn_bin:
        console.print("[yellow]Could not locate the langulearn executable on PATH - skipping desktop shortcut.[/yellow]")
        return

    icon = _icon_path()
    entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=LanguLearn\n"
        f"Exec={langulearn_bin}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Education;\n"
    )

    targets = [Path.home() / ".local" / "share" / "applications" / "langulearn.desktop"]
    desktop_dir = _desktop_dir()
    if desktop_dir.is_dir():
        targets.append(desktop_dir / "langulearn.desktop")

    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(entry, encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    console.print(f"[green]\u2713[/green] Desktop entry created: {targets[-1]}")


def _create_desktop_shortcut(console) -> None:
    if sys.platform == "win32":
        _create_shortcut_windows(console)
    elif sys.platform == "darwin":
        _create_shortcut_macos(console)
    elif sys.platform.startswith("linux"):
        _create_shortcut_linux(console)
    else:
        console.print(f"[yellow]Unrecognized platform {sys.platform!r} - skipping desktop shortcut.[/yellow]")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_setup(force: bool = False) -> None:
    console = _console()
    console.print("[bold cyan]LanguLearn setup[/bold cyan]")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    _install_extra_deps(console)
    download_and_extract_assets(force=force, console=console)
    _create_desktop_shortcut(console)

    _SETUP_MARKER.write_text(ASSETS_VERSION, encoding="utf-8")
    console.print(
        "\n[bold green]Setup complete.[/bold green] Run 'langulearn' (or use the new desktop shortcut) to start the app."
    )


def _setup_already_done() -> bool:
    return _SETUP_MARKER.is_file() and _SETUP_MARKER.read_text(encoding="utf-8").strip() == ASSETS_VERSION


def _print_app_update_notice(console) -> None:
    """Short timeout and swallows every failure, so a normal launch never
    visibly waits on this or breaks over a flaky connection - offline just
    means no notice gets printed, same as if the check were never run at
    all. The web app runs the equivalent check itself (see routes_api.py's
    /api/update-status) for anyone launching via a desktop shortcut who'd
    never see this console output.
    """
    try:
        info = check_app_update(timeout=2.0)
    except Exception:
        return
    if info.get("update_available"):
        console.print(
            f"[yellow]A newer LanguLearn is available: v{info['current']} \u2192 v{info['latest']}.[/yellow] "
            "Run 'pip install --upgrade langulearn' to update, or use the update notification in the app itself."
        )


def cmd_setup(args: argparse.Namespace) -> None:
    run_setup(force=args.force)


def cmd_run(args: argparse.Namespace) -> None:
    if not _setup_already_done():
        run_setup(force=False)
    else:
        _print_app_update_notice(_console())
    from . import desktop

    desktop.run(host=args.host, port=args.port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="langulearn",
        description="LanguLearn - a self-hosted, real-time voice AI language tutor.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind the local server to (default 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind the local server to (default 8000).",
    )
    parser.set_defaults(func=cmd_run)

    sub = parser.add_subparsers(dest="command")

    p_setup = sub.add_parser(
        "setup",
        help="Install extra dependencies, download avatar/voice/photo assets, and create a desktop shortcut.",
    )
    p_setup.add_argument(
        "--force",
        action="store_true",
        help="Re-download assets even if already up to date.",
    )
    p_setup.set_defaults(func=cmd_setup)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except RuntimeError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
