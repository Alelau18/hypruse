"""Act-and-observe fusion: an acting tool can append a fresh view of the
result to its own tool result, saving the agent a second round-trip."""

import json

import pytest
from mcp.types import TextContent

from hypruse import server as srv


def test_then_none_returns_bare_string():
    assert srv._acted("did it", "none") == "did it"


def test_then_desktop_appends_snapshot(monkeypatch):
    monkeypatch.setattr(srv.hyprctl, "snapshot", lambda: {"monitors": ["m"], "windows": []})
    out = srv._acted("clicked", "desktop")
    assert isinstance(out, list) and len(out) == 2
    assert out[0].text == "clicked"
    assert json.loads(out[1].text)["monitors"] == ["m"]


def test_then_screenshot_appends_capture(monkeypatch):
    monkeypatch.setattr(
        srv, "_deliver_capture", lambda **kw: [TextContent(type="text", text="IMG")]
    )
    out = srv._acted("clicked", "screenshot")
    assert isinstance(out, list)
    assert [c.text for c in out] == ["clicked", "IMG"]


def test_screenshot_observation_waits_for_stable(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        srv, "_deliver_capture", lambda **kw: seen.update(kw) or [TextContent(type="text", text="")]
    )
    srv._acted("x", "screenshot")
    assert seen.get("stable") is True


def test_unknown_then_raises():
    with pytest.raises(ValueError, match="unknown then"):
        srv._acted("x", "wat")


def test_then_ui_appends_elements(monkeypatch):
    elements = [{"role": "entry", "name": "Email", "x": 5, "y": 6, "value": "hi@x"}]
    monkeypatch.setattr(srv, "_ui_read", lambda window="": elements)
    out = srv._acted("typed", "ui")
    assert out[0].text == "typed"
    assert json.loads(out[1].text) == elements


def test_then_ui_reads_the_callers_window(monkeypatch):
    # a caller that knows which window it acted on passes it through, so
    # the observation shows THAT window, not whatever holds focus now
    seen = {}
    monkeypatch.setattr(
        srv, "_ui_read", lambda window="": seen.update(window=window) or []
    )
    srv._acted("clicked", "ui", window="0xw")
    assert seen["window"] == "0xw"


def test_then_ui_degrades_without_a_tree(monkeypatch):
    monkeypatch.setattr(srv, "_ui_read", lambda window="": "kitty exposes no tree")
    out = srv._acted("typed", "ui")
    assert out[1].text == "kitty exposes no tree"


def test_then_ui_never_masks_the_action(monkeypatch):
    # the action succeeded; a failing observation must not turn it into an error
    def boom(window=""):
        raise ValueError("no active window")

    monkeypatch.setattr(srv, "_ui_read", boom)
    out = srv._acted("clicked", "ui")
    assert out[0].text == "clicked"
    assert "ui read failed" in out[1].text


def test_pointer_fuses_observation(monkeypatch):
    monkeypatch.setattr(srv.hinput, "move", lambda x, y: None)
    monkeypatch.setattr(srv.hyprctl, "cursor_pos", lambda: (10, 20))
    monkeypatch.setattr(srv.hyprctl, "snapshot", lambda: {"cursor": [10, 20]})
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)

    bare = srv.pointer("move", x=10, y=20)
    assert bare == "move ok; cursor now at (10, 20)"

    fused = srv.pointer("move", x=10, y=20, then="desktop")
    assert isinstance(fused, list)
    assert fused[0].text.startswith("move ok")
    assert json.loads(fused[1].text)["cursor"] == [10, 20]


def test_hypr_fuses_observation(monkeypatch):
    monkeypatch.setattr(srv.hyprctl, "dispatch", lambda *a: None)
    monkeypatch.setattr(srv.hyprctl, "snapshot", lambda: {"ok": True})
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)

    assert srv.hypr("workspace", workspace="3") == "on workspace 3"
    fused = srv.hypr("workspace", workspace="3", then="desktop")
    assert isinstance(fused, list) and json.loads(fused[1].text) == {"ok": True}


def test_hypr_targetless_fullscreen_honors_seat_guard(monkeypatch):
    # target-less fullscreen/toggle_floating act on the ACTIVE window; under
    # HYPRUSE_STRICT, if the human refocused since hypruse last acted, they
    # would hit the wrong window, so the seat guard must refuse
    monkeypatch.setenv("HYPRUSE_STRICT", "1")
    monkeypatch.setattr(srv.hyprctl, "dispatch", lambda *a: None)
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    # seat baseline says cursor (1,1)/active 0xa; now it reads (9,9)/0xb -> moved
    srv.trust._seat.update(cursor=(1, 1), active="0xa")
    monkeypatch.setattr(srv.hyprctl, "cursor_pos", lambda: (9, 9))
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: {"address": "0xb"})
    with pytest.raises(srv.trust.TrustError, match="seat moved"):
        srv.hypr("fullscreen")  # no target -> guarded
    # an address-targeted action names its window, so it is not seat-gated
    srv.trust._seat.update(cursor=(1, 1), active="0xa")
    focused = {"address": "0xb"}
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: focused)
    monkeypatch.setattr(srv.hyprctl, "dispatch", lambda *a: focused.update(
        address=a[1].removeprefix("address:")) if a[0] == "focuswindow" else None)
    assert srv.hypr("fullscreen", target="0xabc") == "fullscreen toggled"


def test_desktop_rebaselines_the_strict_seat_guard(monkeypatch):
    # the lockout bug: once the human nudged the seat, the guard refused
    # forever, because its own advice ("re-read desktop() and retry") never
    # re-baselined anything. A desktop() read must re-arm the guard.
    monkeypatch.setenv("HYPRUSE_STRICT", "1")
    srv.trust._seat.update(cursor=(1, 1), active="0xa")
    monkeypatch.setattr(srv.hyprctl, "cursor_pos", lambda: (9, 9))
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: {"address": "0xb"})
    with pytest.raises(srv.trust.TrustError, match="seat moved"):
        srv.trust.guard_seat()
    monkeypatch.setattr(srv.hyprctl, "snapshot", lambda: {"windows": []})
    srv.desktop()
    srv.trust.guard_seat()  # re-armed: no raise


def test_capture_rebaselines_the_strict_seat_guard(monkeypatch):
    # same recovery path for screenshot/zoom: any fresh capture counts as
    # the re-observation the guard error asks for
    monkeypatch.setenv("HYPRUSE_STRICT", "1")
    srv.trust._seat.update(cursor=(1, 1), active="0xa")
    monkeypatch.setattr(srv.hyprctl, "cursor_pos", lambda: (9, 9))
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: {"address": "0xb"})
    monkeypatch.setattr(srv, "_grab_env", lambda *a, **k: (b"IMG", {"format": "jpeg"}))
    monkeypatch.setattr(srv, "_package", lambda data, meta: ["pkg"])
    with pytest.raises(srv.trust.TrustError, match="seat moved"):
        srv.trust.guard_seat()
    srv._deliver_capture()
    srv.trust.guard_seat()  # re-armed: no raise


def _pointer_wired(monkeypatch, covering):
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.trust, "guard_pointer", lambda *a, **k: None)
    monkeypatch.setattr(srv.trust, "covering_layer", covering)
    monkeypatch.setattr(srv.hinput, "click", lambda *a, **k: None)
    monkeypatch.setattr(srv.hinput, "drag", lambda *a, **k: None)
    monkeypatch.setattr(srv.hinput, "scroll", lambda *a, **k: None)
    monkeypatch.setattr(srv.hyprctl, "cursor_pos", lambda: (600, 350))


@pytest.mark.parametrize(
    "action,extra",
    [
        ("click", {}),
        ("scroll", {"scroll_dy": 3}),
        ("drag", {"to_x": 700, "to_y": 400}),
    ],
)
def test_pointer_warns_under_a_focus_stealing_layer(monkeypatch, action, extra):
    # pointer may legitimately aim at the layer itself (that is how a
    # launcher is driven), so every input-delivering arm warns instead of
    # refusing, and the warning tells the truth about where the input went
    _pointer_wired(
        monkeypatch, lambda x, y: {"namespace": "rofi", "kind": "launcher"}
    )
    out = srv.pointer(action, x=600, y=350, **extra)
    assert "rofi" in out and "not to any window beneath" in out


def test_pointer_drag_passes_its_pace_through(monkeypatch):
    _pointer_wired(monkeypatch, lambda x, y: None)
    seen = []
    monkeypatch.setattr(srv.hinput, "drag", lambda *a, **k: seen.append((a, k)))
    srv.pointer("drag", x=1, y=2, to_x=3, to_y=4, duration_ms=500, steps=25)
    ((args, kwargs),) = seen
    assert callable(kwargs.pop("drop_check"))
    assert (args, kwargs) == ((1, 2, 3, 4), {"button": "left", "duration_ms": 500, "steps": 25})


def test_pointer_click_is_silent_without_a_covering_layer(monkeypatch):
    _pointer_wired(monkeypatch, lambda x, y: None)
    assert srv.pointer("click", x=600, y=350) == "click ok; cursor now at (600, 350)"


def test_pointer_coordinate_less_click_resolves_cursor_for_the_note(monkeypatch):
    # a click with no x/y lands at the current cursor; the layer note must
    # check THAT point, not skip
    probed = []

    def covering(x, y):
        probed.append((x, y))
        return {"namespace": "rofi", "kind": "launcher"}

    _pointer_wired(monkeypatch, covering)
    out = srv.pointer("click")
    assert probed == [(600, 350)]
    assert "rofi" in out


def test_layer_note_empty_when_cursor_unreadable(monkeypatch):
    # best-effort: no cursor means no note, never an error blocking the click
    def boom():
        raise srv.hyprctl.HyprctlError("cursorpos timed out")

    monkeypatch.setattr(srv.hyprctl, "cursor_pos", boom)
    assert srv._layer_note(None, None) == ""


def test_then_ui_falls_back_when_the_clicked_window_is_gone(monkeypatch):
    # clicking Close/OK/Discard destroys the very window whose controls
    # were asked for; show the parent instead of a not-found error
    parent = [{"role": "push button", "name": "New", "x": 1, "y": 2}]

    def ui_read(window=""):
        if window:
            raise ValueError(f"window {window!r} not found, call desktop()")
        return parent

    monkeypatch.setattr(srv, "_ui_read", ui_read)
    out = srv._acted("clicked Discard", "ui", window="0xdead")
    assert out[0].text == "clicked Discard"
    assert json.loads(out[1].text) == parent


def test_then_ui_reports_failure_when_nothing_can_be_read(monkeypatch):
    def boom(window=""):
        raise ValueError("no active window")

    monkeypatch.setattr(srv, "_ui_read", boom)
    out = srv._acted("clicked", "ui", window="0xdead")
    assert "ui read failed" in out[1].text


@pytest.mark.parametrize(
    "action,extra",
    [("click", {}), ("scroll", {"scroll_dy": 3}), ("drag", {"to_x": 70, "to_y": 80})],
)
def test_pointer_refuses_while_the_session_is_locked(monkeypatch, action, extra):
    # every input-delivering arm must refuse under a lock, not just click;
    # narrowing the guard to click alone left drag/scroll firing into the
    # credential prompt with the suite green
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.trust, "session_locked", lambda: "hyprlock")
    fired = []
    for name in ("click", "scroll", "drag"):
        monkeypatch.setattr(srv.hinput, name, lambda *a, **k: fired.append(1))
    with pytest.raises(srv.trust.TrustError, match="session is locked"):
        srv.pointer(action, x=10, y=10, **extra)
    assert fired == []  # refused before any input went out


def test_pointer_move_is_allowed_while_locked(monkeypatch):
    # a move only shifts the cursor; it delivers no input to the prompt
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.trust, "session_locked", lambda: "hyprlock")
    monkeypatch.setattr(srv.hinput, "move", lambda x, y: None)
    monkeypatch.setattr(srv.hyprctl, "cursor_pos", lambda: (10, 10))
    assert srv.pointer("move", x=10, y=10).startswith("move ok")


def test_pointer_allow_auth_downgrades_lock_refusal_to_a_note(monkeypatch):
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.trust, "session_locked", lambda: "hyprlock")
    monkeypatch.setattr(srv.trust, "guard_pointer", lambda *a, **k: None)
    monkeypatch.setattr(srv.trust, "covering_layer", lambda x, y: None)
    monkeypatch.setattr(srv.hinput, "click", lambda *a, **k: None)
    monkeypatch.setattr(srv.hyprctl, "cursor_pos", lambda: (10, 10))
    out = srv.pointer("click", x=10, y=10, allow_auth=True)
    assert "session is locked" in out and "hyprlock" in out


def test_ui_rearms_the_strict_seat_guard(monkeypatch):
    # ui() is an observation of current state, so it must re-arm the strict
    # guard like desktop()/screenshot do, or the recommended read-then-act
    # flow stays locked after a human nudge
    monkeypatch.setenv("HYPRUSE_STRICT", "1")
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    srv.trust._seat.update(cursor=(1, 1), active="0xa")
    monkeypatch.setattr(srv.hyprctl, "cursor_pos", lambda: (9, 9))
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: {"address": "0xb"})
    with pytest.raises(srv.trust.TrustError, match="seat moved"):
        srv.trust.guard_seat()
    monkeypatch.setattr(srv, "_ui_read", lambda *a, **k: [])
    srv.ui()
    srv.trust.guard_seat()  # re-armed: no raise


def test_marks_rearms_the_strict_seat_guard(monkeypatch):
    monkeypatch.setenv("HYPRUSE_STRICT", "1")
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    srv.trust._seat.update(cursor=(1, 1), active="0xa")
    monkeypatch.setattr(srv.hyprctl, "cursor_pos", lambda: (9, 9))
    monkeypatch.setattr(
        srv, "_resolve_window",
        lambda w="": {"address": "0xa", "at": [0, 0], "size": [10, 10], "class": "x"},
    )
    monkeypatch.setattr(
        srv.hyprctl, "query", lambda cmd: {"address": "0xb"} if cmd == "activewindow" else {}
    )
    monkeypatch.setattr(
        srv, "_ui_read",
        lambda w="", name="", actionable=True, handles=False: [
            {"role": "button", "name": "Ok", "x": 5, "y": 5, "clickable": True}
        ],
    )
    monkeypatch.setattr(
        srv, "_grab_env",
        lambda **kw: (b"IMG", {"geometry": [0, 0, 10, 10], "scale": 1.0,
                               "image": [10, 10], "format": "jpeg"}),
    )
    monkeypatch.setattr(srv, "_draw_marks", lambda *a: None)
    with pytest.raises(srv.trust.TrustError, match="seat moved"):
        srv.trust.guard_seat()
    srv.marks(window="0xa")
    srv.trust.guard_seat()  # re-armed


def test_acted_ui_fallback_only_when_a_window_was_named(monkeypatch):
    # the focused-window fallback is conditional on window= having been
    # passed; without a named window a failing read stays a failure and is
    # never silently retried against a different window
    calls = []

    def ui_read(window=""):
        calls.append(window)
        raise ValueError("no active window")

    monkeypatch.setattr(srv, "_ui_read", ui_read)
    out = srv._acted("did it", "ui")  # no window
    assert calls == [""]  # tried once, did NOT retry
    assert "ui read failed" in out[1].text


def test_pointer_guard_pointer_wiring_refuses_out_of_scope(monkeypatch):
    # integration: guard_pointer is NOT stubbed here, so the real
    # confinement check runs on the windows under the click point
    monkeypatch.setenv("HYPRUSE_CONFINE", "class:kitty")
    monkeypatch.setenv("HYPRUSE_AUTH_GUARD", "0")
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    mon = [{"name": "m", "x": 0, "y": 0, "width": 1920, "height": 1080,
            "scale": 1.0, "activeWorkspace": {"id": 1}}]
    windows = [{"address": "0xff", "class": "firefox", "at": [0, 0], "size": [200, 200],
                "workspace": {"id": 1}, "mapped": True}]
    monkeypatch.setattr(srv.hyprctl, "batch_query", lambda cmds: [mon, windows])
    fired = []
    monkeypatch.setattr(srv.hinput, "click", lambda *a, **k: fired.append(1))
    with pytest.raises(srv.trust.TrustError, match="confinement scope"):
        srv.pointer("click", x=50, y=50)  # over the out-of-scope firefox
    assert fired == []


def test_pointer_guard_pointer_wiring_refuses_auth_dialog(monkeypatch):
    # the default-on auth guard refuses a click over a polkit dialog, wired
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    mon = [{"name": "m", "x": 0, "y": 0, "width": 1920, "height": 1080,
            "scale": 1.0, "activeWorkspace": {"id": 1}}]
    windows = [{"address": "0xpk", "class": "hyprpolkitagent", "at": [0, 0],
                "size": [400, 200], "workspace": {"id": 1}, "mapped": True}]
    monkeypatch.setattr(srv.hyprctl, "batch_query", lambda cmds: [mon, windows])
    fired = []
    monkeypatch.setattr(srv.hinput, "click", lambda *a, **k: fired.append(1))
    with pytest.raises(srv.trust.TrustError, match="authentication dialog"):
        srv.pointer("click", x=10, y=10)
    assert fired == []


def test_pointer_single_note_under_lock(monkeypatch):
    # a lock DOMINATES the layer note: only one note, not two contradictory
    # ones about where the input went
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.trust, "session_locked", lambda: "hyprlock")
    monkeypatch.setattr(srv.trust, "guard_pointer", lambda *a, **k: None)
    monkeypatch.setattr(
        srv.trust, "covering_layer", lambda x, y: {"namespace": "rofi", "kind": "launcher"}
    )
    monkeypatch.setattr(srv.hinput, "click", lambda *a, **k: None)
    monkeypatch.setattr(srv.hyprctl, "cursor_pos", lambda: (10, 10))
    out = srv.pointer("click", x=10, y=10, allow_auth=True)
    assert out.count("NOTE") == 1  # not two
    assert "session is locked" in out and "rofi" not in out


def test_then_screenshot_never_masks_the_action_success(monkeypatch):
    # the action was delivered; a capture error must not read as its failure
    def boom(**k):
        raise srv.shot.ScreenshotError("grim failed: no output")

    monkeypatch.setattr(srv, "_deliver_capture", boom)
    out = srv._acted("click ok", "screenshot")
    assert isinstance(out, str)  # one result, read like any action's
    assert out.startswith("click ok") and "then='screenshot' failed: grim failed" in out


def test_then_screenshot_keeps_the_action_line_on_any_capture_error(monkeypatch):
    def boom(**k):
        raise OSError("no space left on device")

    monkeypatch.setattr(srv, "_deliver_capture", boom)
    out = srv._acted("click ok", "screenshot")
    assert out.startswith("click ok\n") and "then='screenshot' failed: no space left" in out


def test_then_desktop_failure_keeps_the_action_line(monkeypatch):
    def boom():
        raise srv.hyprctl.HyprctlError("hyprctl -j clients timed out")

    monkeypatch.setattr(srv.hyprctl, "snapshot", boom)
    out = srv._acted("focused 0xa", "desktop")
    assert out == ("focused 0xa\n(then='desktop' failed: hyprctl -j clients timed out; "
                   "the action itself went through)")


def test_a_hypr_focus_whose_screenshot_times_out_reports_the_focus(monkeypatch):
    # the mined case: grim timed out on a sleeping monitor after the focus
    # had landed, and the tool reported an error
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.trust, "guard_window", lambda t: None)
    monkeypatch.setattr(srv.trust, "remember_seat", lambda: None)
    sent = []
    monkeypatch.setattr(srv.hyprctl, "dispatch", lambda *a: sent.append(a))

    def grim_timeout(**k):
        raise srv.shot.ScreenshotError("grim timed out after 10s")

    monkeypatch.setattr(srv, "_deliver_capture", grim_timeout)
    out = srv.hypr("focus_window", target="0xa", then="screenshot")
    assert sent == [("focuswindow", "address:0xa")]
    assert out.startswith("focused 0xa") and "grim timed out" in out


def test_an_unknown_then_is_refused_before_acting(monkeypatch):
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    clicked = []
    monkeypatch.setattr(srv.hinput, "click", lambda *a, **k: clicked.append(a))
    with pytest.raises(ValueError, match="unknown then 'image'"):
        srv.pointer("click", x=1, y=2, then="image")
    assert clicked == []


def test_a_background_screenshot_shows_the_acted_on_window(monkeypatch):
    # the human's monitor shows their own work, not the hidden window
    seen = []
    monkeypatch.setattr(srv, "_deliver_capture",
                        lambda **kw: seen.append(kw) or [TextContent(type="text", text="IMG")])
    srv._acted("pressed", "screenshot", window="0xa", fallback=False)
    assert seen == [{"window": "0xa", "stable": True}]


def test_a_foreground_screenshot_says_it_is_the_humans_monitor(monkeypatch):
    seen = []
    monkeypatch.setattr(srv, "_deliver_capture",
                        lambda **kw: seen.append(kw) or [TextContent(type="text", text="IMG")])
    srv._acted("clicked", "screenshot", window="0xa")  # fallback: a foreground click_ui
    srv._acted("clicked", "screenshot")
    assert [kw.get("window") for kw in seen] == [None, None]
    assert all(kw["extra"]["view"].startswith("the focused monitor") for kw in seen)


def test_fullscreen_of_a_target_needs_the_focus_to_have_landed(monkeypatch):
    # V55/V67: fullscreen acts on the focused window, which is checked now
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    sent = []
    monkeypatch.setattr(srv.hyprctl, "dispatch", lambda *a: sent.append(a))
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: {"address": "0xother"})
    with pytest.raises(srv.trust.TrustError, match="did not move to 0xabc"):
        srv.hypr("fullscreen", target="0xabc")
    assert ("fullscreen", "0") not in sent


# V56: an action on "the active window" acted on whatever had the focus when
# the dispatch landed, not the window the guards checked


@pytest.fixture
def focused(monkeypatch):
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    state = {"active": {"address": "0xa", "stableId": "1", "class": "kitty"}, "sent": []}

    def query(cmd):
        if cmd == "clients":
            return [{"address": "0xa", "stableId": "1", "class": "kitty"},
                    {"address": "0xb", "stableId": "2", "class": "firefox"}]
        return state["active"]

    monkeypatch.setattr(srv.hyprctl, "query", query)
    monkeypatch.setattr(srv.hyprctl, "dispatch", lambda *a: state["sent"].append(a))
    return state


def test_targetless_floating_is_pinned_to_the_checked_window(focused):
    out = srv.hypr("toggle_floating")
    assert focused["sent"] == [("togglefloating", "address:0xa")]
    assert "0xa (kitty, 0xa#1)" in out


def test_targetless_fullscreen_on_lua_names_its_window(focused, monkeypatch):
    monkeypatch.setattr(srv.hyprctl, "_provider", srv.hyprctl.LUA)
    srv.hypr("fullscreen")
    assert focused["sent"] == [("fullscreen", "0", "address:0xa")]
    assert srv.hyprctl.lua_dispatch("fullscreen", ("0", "address:0xa")).endswith(
        'window = "address:0xa" })')


def test_targetless_fullscreen_on_hyprlang_says_the_focus_was_checked(focused):
    out = srv.hypr("fullscreen")
    assert focused["sent"] == [("fullscreen", "0")] and "its focus checked right before" in out


def test_targetless_fullscreen_on_hyprlang_refuses_a_focus_that_moved(focused, monkeypatch):
    reads = iter([{"address": "0xa", "stableId": "1", "class": "kitty"},  # what the guards saw
                  {"address": "0xb", "stableId": "2", "class": "firefox"}])  # right before
    monkeypatch.setattr(srv, "_guarded_active_client", lambda: next(reads))
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: next(reads))
    with pytest.raises(srv.trust.TrustError, match="focus moved from 0xa"):
        srv.hypr("fullscreen")
    assert focused["sent"] == []


def test_a_bind_reads_the_seat_again_right_before_it_runs(monkeypatch):
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    checks = []

    def seat():
        checks.append(1)
        if len(checks) > 1:
            raise srv.trust.TrustError("the seat moved since hypruse last acted")

    monkeypatch.setattr(srv.trust, "guard_seat", seat)
    monkeypatch.setattr(srv.trust, "guard_use_bind", lambda *a, **k: None)
    monkeypatch.setattr(srv.hyprctl, "find_bind",
                        lambda combo: {"combo": "SUPER+Q", "action": "killactive", "arg": ""})
    sent = []
    monkeypatch.setattr(srv.hyprctl, "dispatch", lambda *a: sent.append(a))
    with pytest.raises(srv.trust.TrustError, match="seat moved"):
        srv.use_bind("SUPER+Q")
    assert len(checks) == 2 and sent == []  # refused by the check next to the dispatch
