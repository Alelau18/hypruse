"""Session discovery for stripped environments.

Desktop apps launched via dbus/systemd activation often run their MCP
servers without the session variables a terminal has, notably
HYPRLAND_INSTANCE_SIGNATURE and WAYLAND_DISPLAY. Both are recoverable,
and they must be recovered TOGETHER: a signature from one compositor and
a Wayland socket from another would send IPC to one desktop and input to
the other.

The source of truth is `hyprctl -j instances`, which needs no signature
and lists only live instances, each with its own `wl_socket`. When
hyprctl cannot answer, the runtime directory is scanned instead:

    $XDG_RUNTIME_DIR/hypr/<instance-signature>/hyprland.lock
        line 1: the compositor's pid, line 2: its Wayland socket

and an instance counts only when that pid is alive, since a crashed
compositor leaves its directory and socket file behind. The most recently
started live instance wins when several run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")


def _hyprctl_instances() -> list[dict[str, Any]] | None:
    """`hyprctl -j instances`, or None when hyprctl cannot answer."""
    if shutil.which("hyprctl") is None:
        return None
    try:
        proc = subprocess.run(
            ["hyprctl", "-j", "instances"], capture_output=True, text=True, timeout=3
        )
        data = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    return [d for d in data if isinstance(d, dict) and isinstance(d.get("instance"), str)]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just not ours to signal
    except OSError:
        return False
    return True


def _started(sig: str, fallback: float) -> float:
    """An instance's start time: the signature is <hash>_<unix time>_<ns>."""
    parts = sig.split("_")
    if len(parts) >= 2 and parts[1].isdigit():
        return float(parts[1])
    return fallback


def _scanned_instances(runtime: Path) -> list[dict[str, Any]]:
    """Instances found on disk whose hyprland.lock names a live pid."""
    hypr = runtime / "hypr"
    if not hypr.is_dir():
        return []
    out = []
    for d in hypr.iterdir():
        try:
            lines = (d / "hyprland.lock").read_text().split("\n")
            pid = int(lines[0].strip())
        except (OSError, ValueError, IndexError):
            continue
        if not (d / ".socket.sock").exists() or not _pid_alive(pid):
            continue
        wl = lines[1].strip() if len(lines) > 1 else ""
        out.append({"instance": d.name, "pid": pid, "wl_socket": wl,
                    "time": _started(d.name, d.stat().st_mtime)})
    return out


def live_instances(runtime: Path) -> list[dict[str, Any]]:
    """The live Hyprland instances: hyprctl's list, else the checked scan."""
    found = _hyprctl_instances()
    return found if found is not None else _scanned_instances(runtime)


def _lone_wayland_display(runtime: Path) -> str | None:
    """The Wayland socket when exactly one exists. With several, nothing
    says which compositor owns which, and guessing could pair the wrong
    one with the signature."""
    socks = [p.name for p in runtime.glob("wayland-*") if not p.name.endswith(".lock")]
    return socks[0] if len(socks) == 1 else None


def ensure_session_env() -> None:
    """Fill missing session variables in-place; never overrides existing ones.
    A missing pair is taken from one instance's record, and a lone missing
    WAYLAND_DISPLAY from the record of the instance the signature names."""
    runtime = _runtime_dir()
    if not os.environ.get("XDG_RUNTIME_DIR"):  # set but empty is a relative path: the cwd
        os.environ["XDG_RUNTIME_DIR"] = str(runtime)
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if sig and os.environ.get("WAYLAND_DISPLAY"):
        return
    instances = live_instances(runtime)
    if sig:
        record = next((i for i in instances if i["instance"] == sig), None)
    else:
        record = max(instances, key=lambda i: float(i.get("time") or 0), default=None)
        if record is not None:
            os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = record["instance"]
    if not os.environ.get("WAYLAND_DISPLAY"):
        display = (record or {}).get("wl_socket") or (
            _lone_wayland_display(runtime) if record is None else None)
        if display:
            os.environ["WAYLAND_DISPLAY"] = display
