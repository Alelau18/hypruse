"""Synthetic input.

Pointer: positioning goes through the compositor's cursor dispatcher (global
logical coordinates, authoritative on any monitor layout); button and
axis events go through the virtual-pointer wire client. This split
sidesteps the known multi-monitor mapping bugs of absolute virtual-pointer
motion (hyprwm/Hyprland#6749). A warp is not motion, though, so every warp
is followed by a net-zero relative wiggle on the wire (VirtualPointer.nudge)
that clients which ignore warps (XWayland, Wine) see as the mouse moving.

Keyboard: wtype, which uploads its own XKB keymap over
zwp_virtual_keyboard_v1, text lands layout-correct including unicode,
with no uinput scancode guessing.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from hypruse import hyprctl, journal
from hypruse.wire import BUTTONS, PRESSED, RELEASED, VirtualKeyboard, VirtualPointer, WireError


class InputError(RuntimeError):
    """Invalid input request or missing input backend."""


MODS = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "super": "logo",
    "meta": "logo",
    "win": "logo",
    "cmd": "logo",
    "altgr": "altgr",
}

KEY_ALIASES = {
    "enter": "Return",
    "return": "Return",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "space": "space",
    "backspace": "BackSpace",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pgup": "Page_Up",
    "pageup": "Page_Up",
    "pgdn": "Page_Down",
    "pagedown": "Page_Down",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
}


def parse_combo(combo: str) -> tuple[list[str], str | None]:
    """'ctrl+shift+t' → (['ctrl','shift'], 't'); 'super' alone is a bare-mod tap."""
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if not parts:
        raise InputError("empty key combo")
    mods: list[str] = []
    key: str | None = None
    for i, part in enumerate(parts):
        lower = part.lower()
        last = i == len(parts) - 1
        if lower in MODS:
            mods.append(MODS[lower])
            if last:
                key = None
        elif not last:
            raise InputError(f"unknown modifier {part!r} in {combo!r}")
        elif lower in KEY_ALIASES:
            key = KEY_ALIASES[lower]
        elif len(part) == 1:
            key = part
        elif lower.startswith("f") and lower[1:].isdigit():
            key = part.upper()
        else:
            key = part  # assume a literal XKB keysym name (case-sensitive)
    return mods, key


def combo_to_wtype_args(mods: list[str], key: str | None) -> list[str]:
    args: list[str] = []
    for m in mods:
        args += ["-M", m]
    if key:
        args += ["-k", key]
    for m in reversed(mods):
        args += ["-m", m]
    if not args:
        raise InputError("combo resolved to nothing")
    return args


def _wtype(args: list[str], stdin: str | None = None) -> None:
    if shutil.which("wtype") is None:
        raise InputError("wtype not found, install wtype for keyboard input")
    proc = subprocess.run(
        ["wtype", *args], input=stdin, text=True, capture_output=True, timeout=15
    )
    if proc.returncode != 0:
        raise InputError(f"wtype failed: {proc.stderr.strip()}")


# There is ONE seat. MCP hosts can issue tool calls in parallel and the
# sync tool functions run on worker threads, so without serialization two
# overlapping calls could interleave a drag's press/move/release with
# another click's, or type into each other's focus. Reentrant because a
# locked drag calls the locked move for its path. The SIGTERM cleanup
# (release_held) deliberately does NOT take it: the handler can interrupt
# a holder on the same thread, and a dying process must not deadlock.
_seat_lock = threading.RLock()


# Every function below delivers input to the seat, so each opens with the
# dry-run barrier: HYPRUSE_DRYRUN promises the caller that nothing reaches
# the desktop, and the acting tools keep that promise by returning their
# plan before they get here. The barrier is what makes the promise hold
# for a path that was missed instead of silently breaking it.
def type_text(text: str) -> None:
    journal.refuse_if_dry("type_text")
    if not text:
        return
    with _seat_lock:
        # wtype has no way to name a seat, so on a second seat it types into the
        # human's. The window= parameter made that worse rather than better: it
        # focused the window on OUR seat and then typed into theirs.
        if _on_named_seat():
            _with_keyboard(lambda k: k.type_text(text))
            return
        _wtype(["-"], stdin=text)  # '-' reads stdin: safe for any content


def key_combo(combo: str) -> None:
    journal.refuse_if_dry("key_combo")
    mods, key = parse_combo(combo)
    with _seat_lock:
        # Same reason as type_text: wtype cannot name a seat, so on a second seat
        # the combo would fire on the human's keyboard.
        if _on_named_seat():
            _with_keyboard(lambda k: k.key_combo(mods, key))
            return
        _wtype(combo_to_wtype_args(mods, key))


# --- pointer ----------------------------------------------------------------

_vp: VirtualPointer | None = None
_vk: VirtualKeyboard | None = None
# What the SIGTERM path releases: any button down right now, a drag's or a
# long click's included (those last one call, under the seat lock).
_held_button: str | None = None
# The button a `pointer down` left held for a later `up`: the only hold
# that outlives a call, and the one the single-hold rule and the results
# are about. Kept apart so a drag in flight on another worker thread does
# not read as a `down` waiting for its `up`.
_down: str | None = None


def release_held() -> bool:
    """Best-effort release of a button hypruse is holding: an in-flight
    drag, a long click, or a `pointer down` still waiting for its `up`.

    Registered on the SIGTERM path (safety.on_shutdown): a plain click's
    press/release pair never spans interpreter checkpoints where a signal
    could land with the button down, but a drag holds it across ~200 ms of
    cursor moves and a `down` holds it across tool calls, and the kill
    switch must not end the process mid-hold. A CLI verb inherits a `down`
    from an earlier process (cli_state), with no pointer of its own yet, so
    one is made for the release.

    True when a release was sent. A release that could not be sent leaves
    the hold on record, so whoever asked (`hypruse stop`, a stopped
    sequence) does not claim it was let go."""
    global _held_button, _down
    if not _held_button:
        return False
    try:
        (_vp or VirtualPointer()).button(_held_button, RELEASED)
    except Exception:
        return False
    _held_button = _down = None
    return True


def held_button() -> str | None:
    """The button a `pointer down` left held for its `up`, if any."""
    return _down


def restore_held(button: str | None) -> None:
    """Adopt a hold an earlier CLI process left (cli_state), so this
    process's SIGTERM cleanup releases it and a `pointer up` clears it."""
    global _held_button, _down
    _held_button = _down = button if button in BUTTONS else None


def _with_keyboard(fn: Callable[[VirtualKeyboard], Any]) -> Any:
    """Run fn with the shared virtual keyboard, reconnecting once if stale."""
    global _vk
    for attempt in (0, 1):
        if _vk is None:
            _vk = VirtualKeyboard()
        try:
            return fn(_vk)
        except WireError:
            _vk = None
            if attempt:
                raise


def _on_named_seat() -> bool:
    """True when this compositor gave us a seat other than the default.

    wtype cannot target a seat, so on a named seat every keystroke it sends lands
    on the HUMAN's seat. Text has to go over the wire instead.
    """
    try:
        return bool(_with_pointer(lambda p: p.on_named_seat))
    except WireError:
        return False


def _with_pointer(fn: Callable[[VirtualPointer], Any]) -> Any:
    """Run fn with the shared virtual pointer, reconnecting once if stale."""
    global _vp
    for attempt in (0, 1):
        if _vp is None:
            _vp = VirtualPointer()
        try:
            return fn(_vp)
        except WireError:
            _vp = None
            if attempt:
                raise


# Public because the server calls them BEFORE deciding whether to act: a
# dry run has to raise everything the real call would, and these checks
# used to live only past the point a dry run stops at.
def check_button(button: str) -> None:
    if button not in BUTTONS:
        raise InputError(f"unknown button {button!r}; one of {sorted(BUTTONS)}")


def check_xy(x: float | None, y: float | None) -> bool:
    if (x is None) != (y is None):
        raise InputError("give both x and y, or neither")
    return x is not None


def check_scroll(dy: float, dx: float) -> None:
    if not dy and not dx:
        raise InputError("scroll needs a non-zero dy or dx")


_check_button = check_button  # the private names predate the dry run
_check_xy = check_xy


def _nudge_on() -> bool:
    """HYPRUSE_POINTER_NUDGE=0 turns the post-warp motion off (on by default)."""
    return os.environ.get("HYPRUSE_POINTER_NUDGE", "1").strip().lower() not in (
        "0", "false", "no", "off")


def move(x: float, y: float) -> None:
    journal.refuse_if_dry("move")
    with _seat_lock:
        # On a named seat, positioning MUST go over the wire. `movecursor` moves the
        # single global cursor, which belongs to the human, so using it here would
        # defeat the whole point of having a second seat.
        def run(p: VirtualPointer) -> bool:
            if not p.on_named_seat:
                return False
            p.move_to(x, y)  # relative motion already: clients see it move
            return True

        if _with_pointer(run):
            return
        hyprctl.dispatch("movecursor", str(int(round(x))), str(int(round(y))))
        if _nudge_on():
            # a warp is not motion: XWayland, Wine and GameMaker clients never
            # see it, so hover and drag did nothing there. A net-zero wiggle
            # through the virtual pointer is real motion at the target. The
            # cursor is already where it was asked to be, so a wire failure
            # here costs only the hover, not the move.
            with contextlib.suppress(WireError):
                _with_pointer(lambda p: p.nudge())


MAX_HOLD_MS = 10000.0


def check_hold(hold_ms: float, double: bool = False) -> None:
    """Bounds for a long click: it holds the button, and the seat, for its
    whole length."""
    if not 0 <= hold_ms <= MAX_HOLD_MS:
        raise InputError(f"hold_ms must be from 0 to {MAX_HOLD_MS:.0f}")
    if hold_ms and double:
        raise InputError("hold_ms and double cannot be combined: a held click is one press")


def check_down(button: str) -> None:
    """One held button at a time: the shutdown path releases the one it
    knows about, so a second hold would be one it could strand."""
    check_button(button)
    if _down:
        raise InputError(
            f"the {_down} button is already held down; release it with "
            "pointer action='up' first"
        )


def click(
    x: float | None = None,
    y: float | None = None,
    button: str = "left",
    double: bool = False,
    hold_ms: float = 0,
) -> None:
    journal.refuse_if_dry("click")
    _check_button(button)
    has_xy = _check_xy(x, y)
    check_hold(hold_ms, double)
    with _seat_lock:
        if hold_ms:
            check_down(button)  # a hold of its own: one tracked hold at a time
        if has_xy:
            move(x, y)  # type: ignore[arg-type]
            time.sleep(0.02)
        if not hold_ms:
            _with_pointer(lambda p: p.click(button, double=double))
            if _down == button:  # its release let go of a `down` too
                restore_held(None)
            return

        def held(p: VirtualPointer) -> None:
            global _held_button
            _held_button = button  # before the press, as in drag
            p.button(button, PRESSED)
            try:
                time.sleep(hold_ms / 1000)
            finally:
                p.button(button, RELEASED)
                _held_button = None

        _with_pointer(held)


def button_down(x: float | None = None, y: float | None = None, button: str = "left") -> None:
    """Press a button and leave it held, for a hold or a drag the caller
    paces itself (down, a few moves, a screenshot, up). The hold outlives
    this call, so it is recorded where the shutdown path will find it."""
    journal.refuse_if_dry("button_down")
    check_down(button)
    has_xy = _check_xy(x, y)
    with _seat_lock:
        check_down(button)  # again, under the lock: two downs can race
        if has_xy:
            move(x, y)  # type: ignore[arg-type]
            time.sleep(0.02)

        def press(p: VirtualPointer) -> None:
            global _held_button, _down
            _held_button = _down = button  # before the press: see drag
            p.button(button, PRESSED)

        _with_pointer(press)


def button_up(x: float | None = None, y: float | None = None, button: str = "left") -> None:
    """Release a button, after moving to (x, y) when given. Releasing one
    that is not down is harmless, so this never refuses for want of a
    matching `down` (a CLI `down` happened in another process)."""
    journal.refuse_if_dry("button_up")
    _check_button(button)
    has_xy = _check_xy(x, y)
    with _seat_lock:
        if has_xy:
            move(x, y)  # type: ignore[arg-type]
            time.sleep(0.02)
        _with_pointer(lambda p: p.button(button, RELEASED))
        if _down == button:
            restore_held(None)


DRAG_STEPS = 12
DRAG_DURATION_MS = 200.0
MAX_DRAG_STEPS = 100
MAX_DRAG_MS = 5000.0


def check_drag(duration_ms: float, steps: int) -> None:
    """Bounds for a drag's path. A drag holds a button down for its whole
    duration, so it is capped like everything else that holds the seat."""
    whole = isinstance(steps, int | float) and not isinstance(steps, bool) and steps == int(steps)
    if not whole or not 1 <= steps <= MAX_DRAG_STEPS:
        raise InputError(f"drag steps must be a whole number from 1 to {MAX_DRAG_STEPS}")
    if not 0 <= duration_ms <= MAX_DRAG_MS:
        raise InputError(f"drag duration_ms must be from 0 to {MAX_DRAG_MS:.0f}")


def drag(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    button: str = "left",
    duration_ms: float = DRAG_DURATION_MS,
    steps: int = DRAG_STEPS,
) -> None:
    journal.refuse_if_dry("drag")
    _check_button(button)
    check_drag(duration_ms, steps)
    steps = int(steps)
    pause = duration_ms / 1000 / steps

    def run(p: VirtualPointer) -> None:
        global _held_button
        # flag BEFORE the press: the press blocks in a sync roundtrip with
        # the button already down compositor-side, and a SIGTERM landing
        # there must see the hold. Releasing an un-pressed button is
        # harmless; dying with one pressed is not.
        _held_button = button
        p.button(button, PRESSED)
        try:
            # every step is a warp plus real motion (move's nudge): a path
            # of bare warps showed a game the press and the release with no
            # motion between them, which no app reads as a drag
            for i in range(1, steps + 1):
                if i == steps:  # exactly the target, not a float near it
                    move(x2, y2)
                else:
                    move(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
                time.sleep(pause)
        finally:
            p.button(button, RELEASED)
            _held_button = None

    with _seat_lock:
        check_down(button)  # the drag's own hold is the one tracked; none may wait
        move(x1, y1)
        time.sleep(0.03)
        _with_pointer(run)


def scroll(
    dy: float = 0.0, dx: float = 0.0, x: float | None = None, y: float | None = None
) -> None:
    journal.refuse_if_dry("scroll")
    check_scroll(dy, dx)
    has_xy = _check_xy(x, y)
    with _seat_lock:
        if has_xy:
            move(x, y)  # type: ignore[arg-type]
            time.sleep(0.02)
        _with_pointer(lambda p: p.scroll(dy=dy, dx=dx))
