import pytest

from hypruse import input as hinput


def test_parse_combo_mods_and_key():
    assert hinput.parse_combo("ctrl+shift+t") == (["ctrl", "shift"], "t")
    assert hinput.parse_combo("super+enter") == (["logo"], "Return")
    assert hinput.parse_combo("Alt+F4") == (["alt"], "F4")
    assert hinput.parse_combo("esc") == ([], "Escape")
    assert hinput.parse_combo("q") == ([], "q")


def test_parse_combo_bare_modifier_tap():
    assert hinput.parse_combo("super") == (["logo"], None)


def test_parse_combo_passes_unknown_keysyms_through():
    assert hinput.parse_combo("XF86AudioPlay") == ([], "XF86AudioPlay")


def test_parse_combo_rejects_garbage():
    with pytest.raises(hinput.InputError):
        hinput.parse_combo("")
    with pytest.raises(hinput.InputError, match="unknown modifier"):
        hinput.parse_combo("banana+t")


# keys that carry a Hyprland window selector after a comma (F1, F10, F49):
# on the Lua config manager the text after the comma used to become
# sendshortcut's window field and send the key to whatever it named
SELECTOR_KEYS = ["x, active", "Return, active", "z, title:WIN_B|", "x, class:^kitty$|\\",
                 "alt+F4, active", "ctrl+a, class:^(kitty)$|", "Return, class:.*polkit.*|",
                 "Page Up", "x\\", "Return,", "a|b"]


@pytest.mark.parametrize("combo", SELECTOR_KEYS)
def test_parse_combo_refuses_a_key_that_is_not_a_key_name(combo):
    with pytest.raises(hinput.InputError, match="not a key name"):
        hinput.parse_combo(combo)


def test_parse_combo_keeps_punctuation_keys_and_keysym_names():
    # one character is a key whatever it is: the background path maps it to
    # its keysym name ("comma", "bar") through the layout before Hyprland
    assert hinput.parse_combo("ctrl+,") == (["ctrl"], ",")
    assert hinput.parse_combo("shift+|") == (["shift"], "|")
    assert hinput.parse_combo("KP_Enter") == ([], "KP_Enter")
    assert hinput.parse_combo("ctrl+dead_acute") == (["ctrl"], "dead_acute")


def test_combo_to_wtype_args_press_release_order():
    args = hinput.combo_to_wtype_args(["ctrl", "shift"], "t")
    assert args == ["-M", "ctrl", "-M", "shift", "-k", "t", "-m", "shift", "-m", "ctrl"]
    assert hinput.combo_to_wtype_args(["logo"], None) == ["-M", "logo", "-m", "logo"]


def test_click_validates_before_touching_compositor():
    with pytest.raises(hinput.InputError, match="both x and y"):
        hinput.click(x=100)  # y missing, must fail before any socket use
    with pytest.raises(hinput.InputError, match="unknown button"):
        hinput.click(button="laser")


def test_scroll_requires_a_direction():
    with pytest.raises(hinput.InputError, match="non-zero"):
        hinput.scroll()


class FakeVP:
    # not on a named seat, so positioning falls back to the compositor the
    # way it does in a default single-seat session (see input.move)
    on_named_seat = False

    def __init__(self):
        self.events = []

    def button(self, button, state):
        self.events.append((button, state))

    def nudge(self, direction=1):
        self.events.append("nudge" if direction == 1 else ("nudge", direction))

    def move_to(self, x, y):
        self.events.append(("move_to", x, y))


def test_drag_tracks_and_clears_held_button(monkeypatch):
    vp = FakeVP()
    seen = []
    monkeypatch.setattr(hinput, "_vp", vp)
    # every path move observes the flag: this is what a SIGTERM landing
    # mid-drag would see, so it must read "left" for the entire hold
    monkeypatch.setattr(hinput, "move", lambda x, y: seen.append(hinput._held_button))
    monkeypatch.setattr(hinput.time, "sleep", lambda s: None)
    hinput.drag(0, 0, 10, 10)
    from hypruse.wire import PRESSED, RELEASED

    assert vp.events == [("left", PRESSED), ("left", RELEASED)]
    assert hinput._held_button is None
    assert len(seen) == 13  # the initial placement move + 12 path moves
    assert seen[1:] == ["left"] * 12  # tracked through the whole hold


def test_concurrent_drags_never_interleave(monkeypatch):
    # MCP hosts can issue tool calls in parallel and sync tools run on
    # worker threads: the seat lock must keep each drag's press/move/release
    # atomic on the shared virtual pointer
    import threading

    from hypruse.wire import PRESSED, RELEASED

    vp = FakeVP()
    monkeypatch.setattr(hinput, "_vp", vp)
    monkeypatch.setattr(hinput.hyprctl, "dispatch", lambda *a: None)
    monkeypatch.setattr(hinput.time, "sleep", lambda s: None)
    threads = [threading.Thread(target=lambda: hinput.drag(0, 0, 5, 5)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    buttons = [s for e in vp.events if isinstance(e, tuple) for s in e[1:]]
    assert buttons == [PRESSED, RELEASED] * 4


def test_release_held_releases_mid_drag_state(monkeypatch):
    # the state a SIGTERM would see if it lands between press and release
    from hypruse.wire import RELEASED

    vp = FakeVP()
    monkeypatch.setattr(hinput, "_vp", vp)
    monkeypatch.setattr(hinput, "_held_button", "left")
    hinput.release_held()
    assert vp.events == [("left", RELEASED)]
    assert hinput._held_button is None
    hinput.release_held()  # idempotent: nothing held, nothing sent
    assert vp.events == [("left", RELEASED)]


# --- motion apps can see ----------------------------------------------------


@pytest.fixture
def seat(monkeypatch):
    """A default-seat pointer with the compositor's warp recorded."""
    vp = FakeVP()
    log = vp.events
    monkeypatch.setattr(hinput, "_vp", vp)
    monkeypatch.setattr(
        hinput.hyprctl, "dispatch", lambda *a: log.append(("warp", *a[1:])) if a[0] == "movecursor"
        else log.append(a)
    )
    monkeypatch.setattr(hinput.time, "sleep", lambda s: log.append(("sleep", s)))
    monkeypatch.delenv("HYPRUSE_POINTER_NUDGE", raising=False)
    return vp


def test_a_warp_is_followed_by_real_motion(seat):
    # XWayland and Wine games never see a warp; the wiggle after it is what
    # makes hover work there
    hinput.move(10.4, 20.6)
    assert seat.events == [("warp", "10", "21"), "nudge"]


def test_the_nudge_can_be_turned_off(seat, monkeypatch):
    monkeypatch.setenv("HYPRUSE_POINTER_NUDGE", "0")
    hinput.move(1, 2)
    assert seat.events == [("warp", "1", "2")]


def test_a_named_seat_moves_by_motion_and_needs_no_nudge(seat, monkeypatch):
    monkeypatch.setattr(seat, "on_named_seat", True, raising=False)
    hinput.move(5, 6)
    assert seat.events == [("move_to", 5, 6)]


def test_a_failed_nudge_does_not_fail_the_move(seat, monkeypatch):
    # the cursor is already where it was asked to be
    def broken(direction=1):
        raise hinput.WireError("gone")

    monkeypatch.setattr(seat, "nudge", broken)
    monkeypatch.setattr(hinput, "VirtualPointer", FakeVP)  # the reconnect attempt
    hinput.move(1, 2)
    assert ("warp", "1", "2") in seat.events


def test_every_drag_step_is_warp_plus_motion_and_ends_on_target(seat):
    from hypruse.wire import PRESSED, RELEASED

    hinput.drag(0, 0, 0.3, 10, steps=3, duration_ms=300)
    ev = [e for e in seat.events if not (isinstance(e, tuple) and e[0] == "sleep")]
    assert ev == [
        ("warp", "0", "0"), "nudge",
        ("left", PRESSED),
        ("warp", "0", "3"), "nudge",
        ("warp", "0", "7"), "nudge",
        ("warp", "0", "10"), "nudge",
        ("left", RELEASED),
    ]
    # the path is paced over the duration: 3 pauses of 100 ms after the press
    pauses = [e[1] for e in seat.events if isinstance(e, tuple) and e[0] == "sleep"]
    assert pauses[-3:] == [pytest.approx(0.1)] * 3


def test_a_drag_ends_exactly_at_its_target(monkeypatch):
    seen = []
    monkeypatch.setattr(hinput, "_vp", FakeVP())
    monkeypatch.setattr(hinput, "move", lambda x, y: seen.append((x, y)))
    monkeypatch.setattr(hinput.time, "sleep", lambda s: None)
    hinput.drag(0.1, 0.1, 0.3, 0.7, steps=7)
    assert seen[-1] == (0.3, 0.7)  # not 0.30000000000000004
    assert len(seen) == 8


def test_a_drag_that_fails_midway_still_releases(seat, monkeypatch):
    from hypruse.wire import PRESSED, RELEASED

    calls = {"n": 0}
    real = hinput.move

    def flaky(x, y):
        calls["n"] += 1
        if calls["n"] == 3:
            raise hinput.hyprctl.HyprctlError("compositor went away")
        real(x, y)

    monkeypatch.setattr(hinput, "move", flaky)
    with pytest.raises(hinput.hyprctl.HyprctlError):
        hinput.drag(0, 0, 10, 10)
    buttons = [e for e in seat.events if isinstance(e, tuple) and e[0] == "left"]
    assert buttons == [("left", PRESSED), ("left", RELEASED)]
    assert hinput._held_button is None


@pytest.mark.parametrize(
    ("kw", "msg"),
    [({"steps": 0}, "steps"), ({"steps": 101}, "steps"), ({"steps": 2.5}, "steps"),
     ({"duration_ms": -1}, "duration_ms"), ({"duration_ms": 5001}, "duration_ms")],
)
def test_drag_bounds_are_checked_before_any_input(seat, kw, msg):
    with pytest.raises(hinput.InputError, match=msg):
        hinput.drag(0, 0, 1, 1, **kw)
    assert seat.events == []


EDGE = [{"name": "DP-1", "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.0},
        {"name": "DP-2", "x": 1920, "y": 0, "width": 1920, "height": 540, "scale": 1.0}]


def test_the_nudge_goes_inward_at_the_layouts_right_edge(seat, monkeypatch):
    # +1 would be clamped and -1 kept: the move would end a pixel short
    queries = []
    monkeypatch.setattr(hinput.hyprctl, "query", lambda w: queries.append(w) or EDGE)
    hinput.move(3839, 100)
    hinput.move(1919, 800)  # DP-2 is shorter: nothing right of here at y=800
    hinput.move(1919, 100)  # DP-2 continues the layout: outward is fine
    hinput.move(10, 10)
    nudges = [e for e in seat.events if e == "nudge" or isinstance(e, tuple)
              and e[0] == "nudge"]
    assert nudges == [("nudge", -1), ("nudge", -1), "nudge", "nudge"]
    assert queries == ["monitors"]  # cached across a burst of moves


def test_an_unreadable_layout_nudges_the_usual_way(seat):
    hinput.move(3839, 100)  # conftest: no compositor to ask
    assert seat.events[-1] == "nudge"


def test_an_f_key_takes_only_ascii_digits():
    assert hinput.parse_combo("F12") == ([], "F12")
    with pytest.raises(hinput.InputError, match="not a key name"):
        hinput.parse_combo("F٤")  # ARABIC-INDIC DIGIT FOUR: isdigit() but no key


# F9, V14, V15: the caller's guards run again at the moment of delivery,
# under the seat lock, and a refusal delivers nothing (a drag: no drop)


class _Pointer:
    def __init__(self, log):
        self.log = log

    def click(self, button, double=False):
        self.log.append(("click", button))

    def button(self, button, state):
        self.log.append(("button", button, state))

    def scroll(self, dy=0.0, dx=0.0):
        self.log.append(("scroll", dy, dx))


@pytest.fixture
def pointer_log(monkeypatch):
    log = []
    monkeypatch.setattr(hinput, "move", lambda x, y: log.append(("move", x, y)))
    monkeypatch.setattr(hinput, "_with_pointer", lambda fn: fn(_Pointer(log)))
    monkeypatch.setattr(hinput.time, "sleep", lambda s: None)
    monkeypatch.setattr(hinput, "_held_button", None)
    return log


def _refuse():
    assert hinput._seat_lock._is_owned()  # the same lock as the event it gates
    raise RuntimeError("a dialog mapped over the point")


@pytest.mark.parametrize("deliver", [
    lambda c: hinput.click(5, 6, precheck=c),
    lambda c: hinput.button_down(5, 6, precheck=c),
    lambda c: hinput.scroll(dy=1, x=5, y=6, precheck=c),
])
def test_a_refusal_at_delivery_sends_no_event(pointer_log, deliver):
    with pytest.raises(RuntimeError, match="mapped over"):
        deliver(_refuse)
    assert pointer_log == [("move", 5, 6)]  # the cursor went there, nothing was pressed
    assert hinput.held_button() is None


def test_a_drop_refused_at_delivery_is_released_back_where_it_started(pointer_log):
    with pytest.raises(RuntimeError, match="mapped over"):
        hinput.drag(1, 2, 9, 9, steps=2, drop_check=_refuse)
    assert pointer_log[-2:] == [("move", 1, 2), ("button", "left", hinput.RELEASED)]
    assert ("button", "left", hinput.PRESSED) in pointer_log
    assert hinput.held_button() is None  # never left held


def test_a_drop_that_passes_is_released_at_the_target(pointer_log):
    hinput.drag(1, 2, 9, 9, steps=2, drop_check=lambda: None)
    assert pointer_log[-2:] == [("move", 9, 9), ("button", "left", hinput.RELEASED)]
