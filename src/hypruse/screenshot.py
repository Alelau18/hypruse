"""Screenshots via grim (wlroots screencopy).

grim captures in *pixel* space while hypruse coordinates are global
*logical* pixels; on monitors with fractional scaling the two differ.
Every capture therefore returns metadata with its origin and scale so an
image pixel maps back to a clickable point:

    global_x = geometry[0] + pixel_x / scale
    global_y = geometry[1] + pixel_y / scale
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from typing import Any

from hypruse import hyprctl, trust


class ScreenshotError(RuntimeError):
    """grim failed or the capture target does not exist."""


class CaptureRefused(ScreenshotError):
    """A capture that trying again will not fix: the window is gone, there
    is no active window, or the session is locked."""


_REGION = re.compile(r"^\s*(-?\d+)\s*,\s*(-?\d+)\s*[, ]\s*(\d+)\s*x\s*(\d+)\s*$")

_SIZE = re.compile(r"^\s*(\d+)\s*x\s*(\d+)\s*$")

DEFAULT_ZOOM_SIZE = "480x360"


def parse_region(region: str) -> tuple[int, int, int, int]:
    """Accepts 'x,y,WxH' or 'x,y WxH' (grim's own format)."""
    m = _REGION.match(region)
    if not m:
        raise ScreenshotError(f"bad region {region!r}, expected 'x,y,WxH'")
    x, y, w, h = map(int, m.groups())
    if w <= 0 or h <= 0:
        raise ScreenshotError(f"bad region {region!r}: empty size")
    return x, y, w, h


def parse_size(size: str) -> tuple[int, int]:
    """Accepts 'WxH'."""
    m = _SIZE.match(size)
    if not m:
        raise ScreenshotError(f"bad size {size!r}, expected 'WxH'")
    w, h = map(int, m.groups())
    if w <= 0 or h <= 0:
        raise ScreenshotError(f"bad size {size!r}: empty size")
    return w, h


def clamp_box(
    x: float, y: float, w: int, h: int, bounds: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """Center a w x h box on (x, y), slid fully inside bounds and shrunk
    only when the box is larger than the bounds themselves."""
    bx, by, bw, bh = bounds
    w, h = min(w, bw), min(h, bh)
    x0 = min(max(round(x - w / 2), bx), bx + bw - w)
    y0 = min(max(round(y - h / 2), by), by + bh - h)
    return x0, y0, w, h


def zoom_region(x: float, y: float, size: str = "", window: str = "") -> tuple[int, int, int, int]:
    """The capture box for zooming at a point of interest: `size` (default
    DEFAULT_ZOOM_SIZE) centered on (x, y) in global logical pixels, clamped
    inside the window (when given) or the monitor containing the point,
    falling back to the focused monitor for an off-layout estimate. A
    window no monitor shows is refused: the box is cropped from the screen,
    which is showing something else there."""
    w, h = parse_size(size or DEFAULT_ZOOM_SIZE)
    if window:
        active = (hyprctl.query("activewindow") or {}).get("address")
        clients = hyprctl.query("clients")
        c = _find_window(window, clients, active)
        reason = _hidden_reason(c, hyprctl.query("monitors"), clients)
        if reason:
            raise _not_shown(c, reason, "zoom crops the screen", final=False)
        (bx, by), (bw, bh) = c["at"], c["size"]
    else:
        monitors = hyprctl.query("monitors")
        m = hyprctl.monitor_at(monitors, x, y) or next(
            (m for m in monitors if m.get("focused")), monitors[0]
        )
        bx, by, bw, bh = hyprctl.logical_rect(m)
    return clamp_box(x, y, w, h, (bx, by, bw, bh))


def _grim(args: list[str]) -> bytes:
    if shutil.which("grim") is None:
        raise ScreenshotError("grim not found, install grim for screenshots")
    proc = subprocess.run(["grim", *args, "-"], capture_output=True, timeout=10)
    if proc.returncode != 0 or not proc.stdout:
        raise ScreenshotError(f"grim failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def _fit_ladder(start_scale: float, lossless: bool = False) -> list[tuple[str, int | None, float]]:
    """(format, jpeg-quality, scale) attempts, best fidelity first, for
    fitting a byte budget.

    The default is full-resolution JPEG. grim's PNG path is dominated by
    zlib and is ~13x slower to encode than JPEG on a 1080p frame (measured
    ~640 ms vs ~50 ms), while full-res q90 reads UI text well, so quality
    degrades before resolution ever does. Resolution is the last resort
    because grim's downscale filter is a convolution that is slower than a
    full-res capture. `lossless` leads with PNG for callers that need exact
    pixels, degrading to JPEG only if PNG blows the budget.
    """
    s = start_scale
    jpeg = [
        ("jpeg", 90, s),
        ("jpeg", 75, s),
        ("jpeg", 60, s),
        ("jpeg", 45, s),
        ("jpeg", 45, s * 0.75),
        ("jpeg", 40, s * 0.5),
    ]
    return [("png", None, s), *jpeg] if lossless else jpeg


def _grab_fitting(
    base_args: list[str],
    start_scale: float,
    max_bytes: int | None,
    lossless: bool = False,
    base_scale: float = 1.0,
) -> tuple[bytes, str, float]:
    """Capture within a byte budget; returns (data, format, applied_scale).

    `start_scale` and the returned applied scale are fractions of the
    capture's NATIVE pixels, but grim's `-s` flag is an ABSOLUTE
    logical-to-pixel factor (grim's default equals the output scale, so
    image = logical_size * factor). The flag therefore carries
    base_scale * s; passing the bare fraction would shrink the image by
    base_scale twice on any scaled monitor and desync the metadata.
    That holds for -g and -o; a -T capture scales the window's own
    buffer instead, so _grab_toplevel passes base_scale 1.0."""
    for fmt, quality, s in _fit_ladder(start_scale, lossless):
        args = list(base_args)
        if abs(s - 1.0) > 1e-6:
            args = ["-s", f"{base_scale * s:g}", *args]
        if fmt == "jpeg":
            args = ["-t", "jpeg", "-q", str(quality), *args]
        data = _grim(args)
        if max_bytes is None or len(data) <= max_bytes:
            return data, fmt, s
    raise ScreenshotError(
        f"even a downscaled JPEG exceeds the {max_bytes}-byte result budget; "
        "capture a window or region instead of the whole monitor"
    )


_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def image_size(data: bytes) -> tuple[int, int]:
    """(width, height) of PNG or JPEG bytes, without a decode library.

    The model reasons about the image it is actually shown, so hypruse
    reports the true output dimensions rather than assuming the geometry
    it asked grim for.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
        return w, h
    if data[:2] == b"\xff\xd8":  # JPEG: find a Start-Of-Frame segment
        i, n = 2, len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xD8 or marker == 0xD9 or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = int.from_bytes(data[i + 2 : i + 4], "big")
            if marker in _SOF_MARKERS:
                h = int.from_bytes(data[i + 5 : i + 7], "big")
                w = int.from_bytes(data[i + 7 : i + 9], "big")
                return w, h
            i += 2 + seg
    raise ScreenshotError("could not read image dimensions")


def _cap_scale(physical_long_edge: float, max_edge: int | None) -> float:
    """Downscale factor so the output's long edge fits max_edge, capped at 1.0.

    Prevents the API from silently downscaling a too-large image *under* the
    model, which would desync the reported scale from what the model sees.
    """
    if not max_edge or physical_long_edge <= max_edge:
        return 1.0
    return max_edge / physical_long_edge


def _rect_intersects(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _scale_for_rect(x: int, y: int, w: int, h: int, monitors: list[dict[str, Any]]) -> float:
    """The buffer scale grim renders a `-g` rect at: the GREATEST scale
    among the outputs the rect intersects. Using the scale under one corner
    would misreport any capture straddling a seam between differently
    scaled monitors, doubling (or scaling) every mapped coordinate."""
    scales = [
        float(m.get("scale", 1.0))
        for m in monitors
        if _rect_intersects(hyprctl.logical_rect(m), (x, y, w, h))
    ]
    return max(scales, default=1.0)


def _find_window(window: str, clients: list[dict[str, Any]], active: str | None) -> dict[str, Any]:
    target = active if window == "active" else window
    if not target:
        raise CaptureRefused("no active window")
    for c in clients:
        if c.get("address") == target:
            return c
    raise CaptureRefused(
        f"window {target!r} not found, call desktop() for current addresses"
    )


def _hidden_reason(
    c: dict[str, Any],
    monitors: list[dict[str, Any]],
    clients: list[dict[str, Any]] | None = None,
) -> str | None:
    """Why the compositor does not draw this window on a monitor, or None
    when it does. That is not "nothing covers it": a floating window over
    a tiled one sets no flag, so a shown window can still be partly covered."""
    ws = c.get("workspace") or {}
    ref = hyprctl.workspace_ref(ws)
    if ref not in hyprctl.visible_workspaces(monitors):
        return f"no monitor is showing its workspace {ws.get('name') or ws.get('id')}"
    # an inactive tab in a group: Hyprland 0.56 reports visible false,
    # older releases hidden true
    if c.get("hidden") is True or c.get("visible") is False:
        return "it is an inactive tab in a group"
    at, size = c.get("at"), c.get("size")
    if at and size and monitors and all(
        "width" in m for m in monitors
    ) and not any(
        _rect_intersects(hyprctl.logical_rect(m), (at[0], at[1], size[0], size[1]))
        for m in monitors
    ):
        # a scrolling-layout column moved out of view, or a floating window
        # dragged off screen: its workspace is shown, the window is not
        return "it is outside every monitor, scrolled out of view or moved off screen"
    if c.get("acceptsInput") is False and not c.get("fullscreen") and any(
        o.get("address") != c.get("address")
        and o.get("fullscreen")
        and hyprctl.workspace_ref(o.get("workspace")) == ref
        for o in clients or []
    ):
        return "a fullscreen window on its workspace hides it"
    return None


def _shown(
    c: dict[str, Any],
    monitors: list[dict[str, Any]],
    clients: list[dict[str, Any]] | None = None,
) -> bool:
    return _hidden_reason(c, monitors, clients) is None


def _readonly() -> bool:
    return os.environ.get("HYPRUSE_READONLY", "").lower() in ("1", "true", "yes", "on")


def _bring_forward(c: dict[str, Any]) -> str:
    """How to get a window on screen, naming only tools that exist: read-only
    mode registers no `hypr`."""
    if _readonly():
        return "ask the human to bring it on screen"
    return f"bring it on screen with hypr(action='focus_window', target='{c['address']}')"


def _not_shown(
    c: dict[str, Any], reason: str, why: str, final: bool = True
) -> ScreenshotError:
    """A refusal for a window that is not on screen. `final` when trying
    again cannot help (no -T, no window id): capture_stable stops on it."""
    cls = CaptureRefused if final else ScreenshotError
    return cls(
        f"window {c['address']} is not shown ({reason}), and {why}. To see it, "
        f"{_bring_forward(c)}."
    )


class _NoToplevel(Exception):
    """grim -T cannot give this window's own pixels; `retry` when a moment
    later it might (a size animation in flight)."""

    def __init__(self, reason: str, retry: bool = False):
        super().__init__(reason)
        self.retry = retry


def _xwayland_popup_over(
    c: dict[str, Any], clients: list[dict[str, Any]], monitors: list[dict[str, Any]]
) -> bool:
    """Whether another XWayland window on screen overlaps this XWayland
    app. An X11 app's open menu, combo list or tooltip is a window of its
    own, which a -T capture of the app leaves out and the screen crop
    shows."""
    if not c.get("xwayland"):
        return False
    shown = hyprctl.visible_workspaces(monitors)
    (x, y), (w, h) = c["at"], c["size"]
    for o in clients:
        if (
            o.get("address") == c.get("address")
            or not o.get("xwayland")
            or not o.get("mapped", True)
            or hyprctl.workspace_ref(o.get("workspace")) not in shown
        ):
            continue
        (ox, oy), (ow, oh) = o["at"], o["size"]
        if ox < x + w and x < ox + ow and oy < y + h and y < oy + oh:
            return True
    return False


def _own_scale(c: dict[str, Any], monitors: list[dict[str, Any]], fallback: float) -> float:
    """The scale of the monitor the window belongs to (clients' `monitor`
    is a monitor id), which is the scale Hyprland renders its buffer at."""
    m = next((m for m in monitors if m.get("id") == c.get("monitor")), None)
    return float(m.get("scale", 1.0)) if m is not None else fallback


def _grab_toplevel(
    c: dict[str, Any],
    scale: float,
    max_edge: int | None,
    max_bytes: int | None,
    lossless: bool,
    own_scale: float,
) -> tuple[bytes, str, float]:
    """The window's own surface via `grim -T`, so a window on a hidden
    workspace or under another window still returns its own pixels.
    Hyprland's stableId is the window's ext-foreign-toplevel-list
    identifier, which is what -T takes. Returns (data, format, factor),
    factor being image pixels per logical pixel.

    grim renders a toplevel from its buffer, which Hyprland sizes at the
    window's size times its OWN monitor's scale, and -s scales that
    buffer. That differs from -g, where -s is an absolute
    logical-to-pixel factor, so the fraction goes to grim bare
    (base_scale 1.0) and the window's own scale is folded in here.

    Raises _NoToplevel when that path is unavailable (no stableId on older
    Hyprland, grim before 1.5, no toplevel capture in the compositor) or
    when the image is not the window's size: a client-side shadow around
    the surface would offset every mapped point, where the screen crop
    maps exactly. Other failures (grim missing, the byte budget) raise
    ScreenshotError as themselves."""
    toplevel = c.get("stableId")
    if not toplevel:
        raise _NoToplevel("this Hyprland gives no window id to capture it by")
    w, h = c["size"]
    start = scale or _cap_scale(max(w, h) * own_scale, max_edge)
    try:
        data, fmt, applied = _grab_fitting(["-T", str(toplevel)], start, max_bytes, lossless, 1.0)
    except ScreenshotError as exc:
        if str(exc).startswith("grim failed"):  # it ran: no -T, or no such toplevel
            raise _NoToplevel(
                "grim cannot capture it directly (that needs grim 1.5+ and Hyprland window capture)"
            ) from exc
        raise
    iw, ih = image_size(data)
    factor = own_scale * applied
    if abs(iw - w * factor) > 2 or abs(ih - h * factor) > 2:
        raise _NoToplevel(
            f"its capture came back {iw}x{ih}, not the window's size (it may be mid-animation)",
            retry=True,
        )
    return data, fmt, factor


def capture(
    window: str = "",
    region: str = "",
    scale: float = 0.0,
    max_bytes: int | None = None,
    max_edge: int | None = None,
    lossless: bool = False,
) -> tuple[bytes, dict[str, Any]]:
    """Returns (image_bytes, metadata). Exactly one of window/region, or
    neither for the focused monitor. `scale` (0 = auto) forces a capture
    scale; `max_edge` caps the output's long edge (so the API never
    downscales it under the model); `max_bytes` fits a transport budget by
    degrading format before resolution. The default format is JPEG q90
    (fast, small, reads UI text well); `lossless` leads with PNG instead.
    Any applied downscale is folded into the metadata `scale`, so the
    pixel→global mapping stays exact."""
    if window and region:
        raise ScreenshotError("pass window OR region, not both")
    if scale and not 0.1 <= scale <= 1.0:
        raise ScreenshotError(f"scale {scale} out of range (0.1-1.0, or 0 for auto)")

    monitors = hyprctl.query("monitors")

    if region:
        x, y, w, h = parse_region(region)
        base = ["-g", f"{x},{y} {w}x{h}"]
        meta: dict[str, Any] = {"target": "region", "geometry": [x, y, w, h]}
        base_scale = _scale_for_rect(x, y, w, h, monitors)
        physical_long = max(w, h) * base_scale
    elif window:
        # Hyprland shares a window with no session-lock check, so a window
        # capture would show what the human's lock screen hides
        locker = trust.session_locked()
        if locker:
            raise CaptureRefused(
                f"the session is locked ({locker}): window captures are refused "
                "until a human unlocks it, since they would show what the lock "
                "screen hides. A monitor screenshot shows the lock screen."
            )
        active = (hyprctl.query("activewindow") or {}).get("address")
        clients = hyprctl.query("clients")
        c = _find_window(window, clients, active)
        (x, y), (w, h) = c["at"], c["size"]
        base = ["-g", f"{x},{y} {w}x{h}"]
        hidden = _hidden_reason(c, monitors, clients)
        meta = {
            "target": "window",
            "window": c["address"],
            "class": c.get("class", ""),
            "geometry": [x, y, w, h],
            "visible": hidden is None,
        }
        base_scale = _scale_for_rect(x, y, w, h, monitors)
        physical_long = max(w, h) * base_scale
    else:
        m = next((m for m in monitors if m.get("focused")), monitors[0])
        base = ["-o", m["name"]]
        # geometry in logical coords like everything else; grim captures the
        # physical output, so the physical long edge drives the byte-budget cap
        meta = {
            "target": "monitor",
            "monitor": m["name"],
            "geometry": list(hyprctl.logical_rect(m)),
        }
        base_scale = float(m.get("scale", 1.0))
        physical_long = max(m["width"], m["height"])

    # explicit scale wins; otherwise fit the long edge to keep the mapping honest
    start_scale = scale or _cap_scale(physical_long, max_edge)
    grabbed = None
    # cropping the window's rect returns whatever the screen shows there,
    # another workspace's windows when its own is hidden (#4); but an
    # XWayland app's own open menu is a separate window only the crop shows
    if window and (hidden is not None or not _xwayland_popup_over(c, clients, monitors)):
        grabbed = _toplevel_or_reason(c, scale, max_edge, max_bytes, lossless,
                                      _own_scale(c, monitors, base_scale), hidden)
        if isinstance(grabbed, _NoToplevel):
            if hidden is not None:
                # _toplevel_or_reason already retried what a moment could fix
                raise _not_shown(c, hidden, str(grabbed))
            grabbed = None
    if grabbed is not None:
        data, fmt, factor = grabbed
        meta["source"] = "window"
    else:
        data, fmt, applied = _grab_fitting(base, start_scale, max_bytes, lossless, base_scale)
        factor = base_scale * applied
        if window:
            meta["source"] = "screen"
            meta["note"] = (
                "cropped from the screen: anything over the window shows in its place"
            )
    iw, ih = image_size(data)
    meta["image"] = [iw, ih]
    meta["format"] = fmt
    meta["scale"] = round(factor, 6)
    meta["coords"] = "click a target at global = geometry[:2] + image_pixel / scale"
    if meta.get("visible") is False:
        meta["coords"] += (
            f"; but the window is not on screen ({hidden}), so {_bring_forward(c)} "
            "and capture again before clicking: these coordinates now point at "
            "whatever is shown there, and bringing it forward can move it"
        )
        # Hyprland marks a window on a hidden workspace suspended, and many
        # apps (kitty, ghostty) stop drawing while suspended
        meta["note"] = (
            "not on screen: many apps stop drawing while hidden, so this may "
            "show the window as it last looked, not as it is now."
        )
    return data, meta


def _toplevel_or_reason(
    c: dict[str, Any],
    scale: float,
    max_edge: int | None,
    max_bytes: int | None,
    lossless: bool,
    own_scale: float,
    hidden: str | None,
) -> tuple[bytes, str, float] | _NoToplevel:
    """_grab_toplevel, or why it could not. A hidden window caught
    mid-animation (its buffer not yet at the size hyprctl reports) is
    retried for up to about a second, since nothing else can capture it."""
    for attempt in range(5):
        try:
            return _grab_toplevel(c, scale, max_edge, max_bytes, lossless, own_scale)
        except _NoToplevel as exc:
            if not exc.retry or hidden is None or attempt == 4:
                return exc
            time.sleep(0.25)
    raise AssertionError("unreachable")


def capture_stable(
    window: str = "",
    region: str = "",
    scale: float = 0.0,
    max_bytes: int | None = None,
    max_edge: int | None = None,
    lossless: bool = False,
    interval: float = 0.15,
    timeout: float = 2.0,
) -> tuple[bytes, dict[str, Any]]:
    """Capture until two consecutive frames are byte-identical, so a
    screenshot taken right after an action does not land mid-animation.
    Returns the settled frame with meta['stable'] = True, or the last
    frame with False when the content never settles within `timeout`
    (blinking cursors, video). The byte-compare relies on the encoder being
    deterministic: both grim JPEG and PNG map identical pixels to identical
    bytes, so the default JPEG format is safe here.

    A window that is not on screen gets stable = None after one capture:
    an app that stopped drawing while hidden yields a frozen frame that
    always matches itself, so a match would vouch for stale content."""
    data, meta = capture(window, region, scale, max_bytes, max_edge, lossless)
    if meta.get("visible") is False:
        meta["stable"] = None
        return data, meta
    deadline = time.monotonic() + timeout
    failed: ScreenshotError | None = None
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            nxt, nmeta = capture(window, region, scale, max_bytes, max_edge, lossless)
        except CaptureRefused:
            raise  # gone or locked: an older frame would hide that
        except ScreenshotError as exc:
            failed = exc  # e.g. mid-animation: keep the last good frame and try again
            continue
        failed = None
        if nmeta.get("visible") is False:
            nmeta["stable"] = None  # it left the screen: see the note above
            return nxt, nmeta
        if nxt == data:
            nmeta["stable"] = True
            return nxt, nmeta
        data, meta = nxt, nmeta
    if failed is not None:
        raise failed  # every capture since the last good one failed: it is stale
    meta["stable"] = False
    return data, meta
