"""Holding a pointer button: `down` / `up`, and `hold_ms` on a click.

A held button outlives the call that pressed it, so the properties that
matter are the ones the safety story leans on: the hold is recorded
before the press (where the SIGTERM cleanup finds it), cleared by its
release, never doubled, and released by a sequence that stops before its
own `up`. And `down`/`up` are input delivered at a point, so they meet the
same guards as a click."""

import json

import pytest

from hypruse import cli, cli_state, verbs
from hypruse import input as hinput
from hypruse import server as srv
from hypruse.wire import PRESSED, RELEASED


class FakeVP:
    on_named_seat = False

    def __init__(self):
        self.events = []

    def button(self, button, state):
        # what a SIGTERM landing here would see
        self.events.append((button, state, hinput._held_button))

    def click(self, button, double=False):
        self.events.append(("click", button))

    def nudge(self, direction=1):
        pass


@pytest.fixture
def vp(monkeypatch):
    p = FakeVP()
    monkeypatch.setattr(hinput, "_vp", p)
    monkeypatch.setattr(hinput, "_held_button", None)
    monkeypatch.setattr(hinput, "_down", None)
    monkeypatch.setattr(hinput.hyprctl, "dispatch", lambda *a: p.events.append(("warp",) + a[1:]))
    monkeypatch.setattr(hinput.time, "sleep", lambda s: p.events.append(("sleep", s)))
    return p


# --- input ------------------------------------------------------------------


def test_down_records_the_hold_before_pressing_and_up_clears_it(vp):
    hinput.button_down(10, 20)
    assert vp.events[0] == ("warp", "10", "20")
    assert vp.events[-1] == ("left", PRESSED, "left")
    assert hinput.held_button() == "left"
    hinput.button_up()
    assert vp.events[-1] == ("left", RELEASED, "left")
    assert hinput.held_button() is None


def test_a_second_down_is_refused_before_any_input(vp):
    hinput.button_down()
    vp.events.clear()
    with pytest.raises(hinput.InputError, match="already held"):
        hinput.button_down(button="right")
    assert vp.events == []
    assert hinput.held_button() == "left"


def test_up_without_a_matching_down_still_releases(vp):
    # a shell `down` happened in another process: releasing is harmless
    hinput.button_up(button="right")
    assert vp.events == [("right", RELEASED, None)]


def test_up_of_another_button_keeps_the_hold(vp):
    hinput.button_down()
    hinput.button_up(button="right")
    assert hinput.held_button() == "left"


def test_the_shutdown_path_releases_a_down(vp):
    hinput.button_down()
    hinput.release_held()
    assert vp.events[-1][:2] == ("left", RELEASED)
    assert hinput.held_button() is None


def test_release_held_makes_a_pointer_for_a_hold_it_inherited(monkeypatch):
    made = []

    class Fresh(FakeVP):
        def __init__(self):
            super().__init__()
            made.append(self)

    monkeypatch.setattr(hinput, "_vp", None)
    monkeypatch.setattr(hinput, "VirtualPointer", Fresh)
    hinput.restore_held("middle")
    hinput.release_held()
    assert [e[:2] for e in made[0].events] == [("middle", RELEASED)]
    assert hinput.held_button() is None


def test_restore_held_ignores_what_is_not_a_button():
    hinput.restore_held("laser")
    assert hinput.held_button() is None


def test_a_long_click_holds_then_releases(vp):
    hinput.click(5, 6, hold_ms=1500)
    assert vp.events[-3:] == [
        ("left", PRESSED, "left"), ("sleep", 1.5), ("left", RELEASED, "left")]
    assert hinput.held_button() is None


def test_a_long_click_that_dies_mid_hold_still_releases(vp, monkeypatch):
    def interrupted(s):
        raise KeyboardInterrupt

    monkeypatch.setattr(hinput.time, "sleep", interrupted)
    with pytest.raises(KeyboardInterrupt):
        hinput.click(hold_ms=500)
    assert [e[:2] for e in vp.events] == [("left", PRESSED), ("left", RELEASED)]
    assert hinput.held_button() is None


def test_a_click_of_the_held_button_lets_go_of_the_hold(vp):
    hinput.button_down()
    hinput.click()
    assert hinput.held_button() is None


def test_a_drag_or_a_long_click_is_refused_during_a_hold(vp, monkeypatch):
    # one tracked hold at a time, so the shutdown path never misses one
    monkeypatch.setattr(hinput, "move", lambda x, y: None)
    hinput.button_down(button="right")
    vp.events.clear()
    with pytest.raises(hinput.InputError, match="right button is already held"):
        hinput.drag(0, 0, 1, 1)
    with pytest.raises(hinput.InputError, match="already held"):
        hinput.click(hold_ms=200)
    assert vp.events == [] and hinput.held_button() == "right"
    hinput.click()  # a plain click is not a hold of its own
    assert vp.events == [("click", "left")]


def test_the_tool_refuses_them_too_and_in_rehearsal(tool, monkeypatch):
    srv.pointer("down")
    for kw in ({"action": "drag", "x": 1, "y": 2, "to_x": 3, "to_y": 4},
               {"action": "click", "hold_ms": 100}):
        with pytest.raises(hinput.InputError, match="already held"):
            srv.pointer(**kw)
    monkeypatch.setenv("HYPRUSE_DRYRUN", "1")
    with pytest.raises(hinput.InputError, match="already held"):
        srv.pointer("drag", x=1, y=2, to_x=3, to_y=4)


@pytest.mark.parametrize(
    ("kw", "msg"),
    [({"hold_ms": -1}, "hold_ms"), ({"hold_ms": 10001}, "hold_ms"),
     ({"hold_ms": 100, "double": True}, "cannot be combined")],
)
def test_hold_bounds_are_checked_before_any_input(vp, kw, msg):
    with pytest.raises(hinput.InputError, match=msg):
        hinput.click(1, 2, **kw)
    assert vp.events == []


# --- the pointer tool ----------------------------------------------------------


@pytest.fixture
def tool(monkeypatch):
    """The pointer tool with its guards and the input layer recorded."""
    calls = {"guard": [], "input": [], "lock": 0}
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.trust, "guard_pointer",
                        lambda x, y, allow_auth=False: calls["guard"].append((x, y)))

    def lock(window_given, allow_auth):
        calls["lock"] += 1
        return ""

    monkeypatch.setattr(srv.trust, "guard_session_lock", lock)
    monkeypatch.setattr(srv.trust, "covering_layer", lambda x, y: None)
    monkeypatch.setattr(srv.hyprctl, "cursor_pos", lambda: (7, 8))
    monkeypatch.setattr(hinput, "_held_button", None)
    monkeypatch.setattr(hinput, "_down", None)

    def down(x=None, y=None, button="left", precheck=None):
        if precheck is not None:
            precheck()
        calls["input"].append(("down", x, y, button))
        hinput.restore_held(button)

    def up(x=None, y=None, button="left", precheck=None):
        if precheck is not None:
            precheck()
        calls["input"].append(("up", x, y, button))
        hinput.restore_held(None)

    monkeypatch.setattr(hinput, "button_down", down)
    monkeypatch.setattr(hinput, "button_up", up)
    monkeypatch.setattr(hinput, "move", lambda x, y: calls["input"].append(("move", x, y)))
    monkeypatch.setattr(hinput, "click", lambda *a, **k: calls["input"].append(("click", a, k)))
    return calls


def test_down_and_up_go_through_the_click_guards(tool):
    out = srv.pointer("down", x=10, y=20, button="right")
    assert tool["input"] == [("down", 10, 20, "right")]
    # checked, then again right before the press (V14)
    assert tool["guard"] == [(10, 20), (10, 20)] and tool["lock"] == 1
    assert "right button is held down until pointer action='up'" in out
    out = srv.pointer("up", x=30, y=40, button="right")  # a move first: guarded
    assert tool["input"][-1] == ("up", 30, 40, "right")
    # checked, then again right before the release, the drop of a paced drag
    assert tool["guard"][-2:] == [(30, 40), (30, 40)] and tool["lock"] == 2
    assert out == "up ok; cursor now at (7, 8)"


def test_a_move_with_a_button_down_is_guarded_like_a_drag(tool):
    srv.pointer("move", x=1, y=2)
    assert tool["guard"] == [] and tool["lock"] == 0  # a bare move only shifts the cursor
    srv.pointer("down")
    tool["guard"].clear()
    srv.pointer("move", x=3, y=4)
    assert tool["guard"] == [(3, 4)] and tool["lock"] == 2


def test_a_second_down_is_refused_by_the_tool(tool):
    srv.pointer("down")
    with pytest.raises(hinput.InputError, match="already held"):
        srv.pointer("down", button="right")
    assert [c[0] for c in tool["input"]] == ["down"]


def test_hold_ms_reaches_the_click(tool):
    srv.pointer("click", x=1, y=2, hold_ms=750)
    ((_, args, kwargs),) = tool["input"]
    assert callable(kwargs.pop("precheck"))
    assert (args, kwargs) == ((1, 2), {"button": "left", "double": False, "hold_ms": 750})


def test_down_up_and_a_long_click_rehearse(tool, monkeypatch):
    monkeypatch.setenv("HYPRUSE_DRYRUN", "1")
    out = srv.pointer("down", x=1, y=2)
    assert "would press left at (1, 2) and keep it held until pointer action='up'" in out
    assert "would release middle at the cursor" in srv.pointer("up", button="middle")
    assert "holding it 300 ms" in srv.pointer("click", x=1, y=2, hold_ms=300)
    assert tool["input"] == [] and hinput.held_button() is None
    assert tool["guard"] == [(1, 2), (None, None), (1, 2)]


def test_the_guarantee_refuses_down_and_up(tool, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    for action in ("down", "up"):
        with pytest.raises(srv.trust.TrustError, match="HYPRUSE_BACKGROUND"):
            srv.pointer(action, x=1, y=1)
    assert tool["input"] == []


def test_automatic_mode_refuses_a_down_over_another_window(tool, monkeypatch):
    monkeypatch.delenv("HYPRUSE_BACKGROUND")
    monkeypatch.setattr(srv, "_active_address", lambda: "0xme")
    monkeypatch.setattr(srv.trust, "other_window_at",
                        lambda x, y, active: {"address": "0xother", "class": "foot"})
    with pytest.raises(srv.trust.TrustError, match="not the window the human is in"):
        srv.pointer("down", x=1, y=1)
    with pytest.raises(srv.trust.TrustError):
        srv.pointer("up")  # in place: judged at the cursor, like a click
    assert tool["input"] == []


def test_unknown_action_lists_down_and_up(tool):
    with pytest.raises(ValueError, match="move|click|down|up|drag|scroll"):
        srv.pointer("press")


# --- sequence -------------------------------------------------------------------


def test_a_sequence_that_stops_before_its_up_releases_the_button(tool, monkeypatch):
    monkeypatch.setattr(srv.events, "EventStream", lambda: (_ for _ in ()).throw(
        srv.events.EventError("no socket")))
    released = []
    monkeypatch.setattr(hinput, "release_held",
                        _releases(released))
    steps = [
        {"op": "pointer", "action": "down", "x": 1, "y": 1},
        {"op": "pointer", "action": "move", "x": 50, "y": 50},
        {"op": "pointer", "action": "fling"},
        {"op": "pointer", "action": "up"},
    ]
    out = srv.sequence(steps, then="none")
    assert released == ["left"]
    assert "released the left button a step had pressed" in out
    assert ("up", None, None, "left") not in tool["input"]


def test_a_sequence_that_runs_to_the_end_leaves_a_deliberate_hold(tool, monkeypatch):
    monkeypatch.setattr(srv.events, "EventStream", lambda: (_ for _ in ()).throw(
        srv.events.EventError("no socket")))
    out = srv.sequence([{"op": "pointer", "action": "down", "x": 1, "y": 1}], then="none")
    assert "all 1/1" in out and hinput.held_button() == "left"


def _releases(log, ok=True):
    """A release_held that records the button and reports `ok`."""
    def release():
        log.append(hinput.held_button())
        if ok:
            hinput.restore_held(None)
        return ok

    return release


def test_a_stopped_sequence_does_not_claim_a_release_that_failed(tool, monkeypatch):
    monkeypatch.setattr(srv.events, "EventStream", lambda: (_ for _ in ()).throw(
        srv.events.EventError("no socket")))
    monkeypatch.setattr(hinput, "release_held", _releases([], ok=False))
    out = srv.sequence([{"op": "pointer", "action": "down"}, {"op": "pointer", "action": "x"}],
                       then="none")
    assert "the left button a step pressed is still held" in out


# --- the shell verbs ----------------------------------------------------------------


def _kwargs(*argv):
    ns = verbs.build_parser().parse_args(list(argv))
    return verbs.normalize(ns.verb, ns)[0]


def test_cli_down_up_and_hold_ms():
    assert _kwargs("pointer", "down", "10", "20", "--button", "right") == {
        "action": "down", "x": 10.0, "y": 20.0, "button": "right"}
    assert _kwargs("pointer", "up") == {"action": "up"}
    assert _kwargs("pointer", "click", "1", "2", "--hold-ms", "800") == {
        "action": "click", "x": 1.0, "y": 2.0, "hold_ms": 800.0}
    with pytest.raises(verbs.Usage, match="down takes X Y"):
        _kwargs("pointer", "down", "10")


def test_a_cli_hold_is_carried_to_the_next_process(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "test")
    monkeypatch.setattr(verbs, "_lock", lambda: None)
    monkeypatch.setattr(cli, "_take_beacon", lambda: True)
    monkeypatch.setattr(hinput, "_held_button", None)
    monkeypatch.setattr(hinput, "_down", None)
    seen = []

    def pointer(**kw):
        seen.append(hinput.held_button())
        if kw["action"] == "down":
            hinput.restore_held("left")
        else:
            hinput.restore_held(None)
        return f"{kw['action']} ok"

    monkeypatch.setattr(srv, "pointer", pointer)
    assert verbs.run("pointer", {"action": "down"}) == 0
    assert json.loads(cli_state.path().read_text())["flags"]["held_button"] == "left"
    hinput.restore_held(None)  # a new process starts with nothing held
    assert verbs.run("pointer", {"action": "up"}) == 0
    assert seen == [None, "left"]  # the second process adopted the hold
    assert json.loads(cli_state.path().read_text())["flags"]["held_button"] is None


def test_stop_releases_a_hold_a_shell_down_left(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "test")
    cli_state.restore()
    cli_state.set_flag("held_button", "left")
    cli_state.save()
    released = []
    monkeypatch.setattr(hinput, "release_held",
                        _releases(released))
    assert cli.stop() == 0
    assert released == ["left"]
    assert "released the left button" in capsys.readouterr().out
    assert cli_state.load()["flags"]["held_button"] is None


def test_stop_finds_the_hold_without_the_signature_in_its_env(tmp_path, monkeypatch, capsys):
    # a hotkey daemon or a bar module runs `hypruse stop` with a bare env:
    # the state file is per instance, so the instance is discovered first
    import os

    sig = "abc_1700000000_1"
    inst = tmp_path / "hypr" / sig
    inst.mkdir(parents=True)
    (inst / ".socket.sock").touch()
    (inst / "hyprland.lock").write_text(f"{os.getpid()}\nwayland-1\n")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", sig)
    cli_state.restore()
    cli_state.set_flag("held_button", "right")
    cli_state.save()
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    released = []
    monkeypatch.setattr(hinput, "release_held", _releases(released))
    assert cli.stop() == 0
    assert released == ["right"]
    assert "released the right button" in capsys.readouterr().out


def test_stop_keeps_the_hold_on_record_when_the_release_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "test")
    cli_state.restore()
    cli_state.set_flag("held_button", "left")
    cli_state.save()
    monkeypatch.setattr(hinput, "release_held", _releases([], ok=False))
    cli.stop()
    out = capsys.readouterr().out
    assert "could not release the left button" in out and "released the left" not in out
    assert cli_state.load()["flags"]["held_button"] == "left"


def test_release_held_reports_a_failed_release_and_keeps_the_hold(monkeypatch):
    class Broken(FakeVP):
        def button(self, button, state):
            raise hinput.WireError("no compositor")

    monkeypatch.setattr(hinput, "_vp", Broken())
    hinput.restore_held("left")
    assert hinput.release_held() is False
    assert hinput.held_button() == "left"
    hinput.restore_held(None)
    assert hinput.release_held() is False  # nothing held, nothing sent


# --- an `up` that ends the hold is never refused ---------------------------------------


def _refusing(monkeypatch, calls):
    def refuse(*a, **k):
        raise srv.trust.TrustError("refused")

    for name in ("guard_seat", "guard_pointer", "guard_session_lock"):
        monkeypatch.setattr(srv.trust, name, refuse)
    monkeypatch.setattr(srv, "_pointer_policy", refuse)


def test_a_bare_up_of_the_held_button_always_releases(tool, monkeypatch):
    srv.pointer("down", x=1, y=2)
    _refusing(monkeypatch, tool)
    out = srv.pointer("up")
    assert tool["input"][-1] == ("up", None, None, "left")
    assert out.startswith("up ok") and hinput.held_button() is None


def test_an_up_elsewhere_or_of_another_button_keeps_the_guards(tool, monkeypatch):
    srv.pointer("down", x=1, y=2)
    _refusing(monkeypatch, tool)
    with pytest.raises(srv.trust.TrustError):
        srv.pointer("up", x=50, y=60)  # a move first
    with pytest.raises(srv.trust.TrustError):
        srv.pointer("up", button="right")  # not the hold it would end
    hinput.restore_held(None)
    with pytest.raises(srv.trust.TrustError):
        srv.pointer("up")  # nothing held: an ordinary release
    assert [c[0] for c in tool["input"]] == ["down"]


def test_a_drag_in_flight_is_not_a_down_waiting_for_its_up(monkeypatch):
    # the tool checks before taking the seat lock; another worker's drag
    # holds a button then, which must not read as a stranded `down`
    monkeypatch.setattr(hinput, "_held_button", "left")
    monkeypatch.setattr(hinput, "_down", None)
    hinput.check_down("left")  # no raise
    assert hinput.held_button() is None


def test_a_hold_from_before_the_sequence_is_left_alone(tool, monkeypatch):
    monkeypatch.setattr(srv.events, "EventStream", lambda: (_ for _ in ()).throw(
        srv.events.EventError("no socket")))
    released = []
    monkeypatch.setattr(hinput, "release_held", _releases(released))
    srv.pointer("down")  # the caller's own hold, from an earlier call
    out = srv.sequence([{"op": "pointer", "action": "move", "x": 5, "y": 5},
                        {"op": "pointer", "action": "fling"}], then="none")
    assert released == [] and hinput.held_button() == "left" and "released" not in out


def test_a_button_released_and_pressed_again_in_the_run_is_released(tool, monkeypatch):
    # the hold before the run and after it are the same button, but the
    # press that is left is the run's own
    monkeypatch.setattr(srv.events, "EventStream", lambda: (_ for _ in ()).throw(
        srv.events.EventError("no socket")))
    released = []
    monkeypatch.setattr(hinput, "release_held", _releases(released))
    srv.pointer("down")
    out = srv.sequence([{"op": "pointer", "action": "up"},
                        {"op": "pointer", "action": "down", "x": 9, "y": 9},
                        {"op": "pointer", "action": "fling"}], then="none")
    assert released == ["left"] and "released the left button a step had pressed" in out


# --- the server's holds reach a CLI verb run beside it -----------------------------------


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "test")
    return tmp_path


def test_the_servers_down_and_up_update_an_existing_cli_state(tool, state_dir):
    cli_state.restore()
    cli_state.set_flag("marking_installed", True)
    cli_state.save()  # a CLI verb ran on this instance before
    srv.pointer("down", button="right")
    flags = cli_state.load()["flags"]
    assert flags["held_button"] == "right" and flags["marking_installed"] is True
    srv.pointer("up", button="right")
    assert cli_state.load()["flags"]["held_button"] is None


def test_no_cli_state_file_is_created_for_the_server(tool, state_dir):
    srv.pointer("down")
    assert not cli_state.path().exists()


def test_a_cli_verb_does_not_go_through_the_server_sync(tool, state_dir, monkeypatch):
    # verbs.run writes the flag itself, after the tool, under its own merge
    monkeypatch.setattr(srv, "_plain_blocks", True)
    cli_state.restore()
    cli_state.save()
    srv.pointer("down")
    assert cli_state.load()["flags"].get("held_button") is None


def test_a_click_refused_at_delivery_names_the_guard(tool, monkeypatch):
    # V14: the point passed when checked; a dialog mapped there before the press
    answers = iter([None])

    def guard(x, y, allow_auth=False):
        if next(answers, "refuse") == "refuse":
            raise srv.trust.TrustError("an authentication dialog covers (1, 2)")

    monkeypatch.setattr(srv.trust, "guard_pointer", guard)
    monkeypatch.setattr(hinput, "click", lambda x, y, precheck=None, **k: (
        precheck(), tool["input"].append(("click", x, y))))
    with pytest.raises(srv.trust.TrustError,
                       match="the pointer guard refused the click right before it was delivered"):
        srv.pointer("click", x=1, y=2)
    assert tool["input"] == []


def test_a_release_at_a_point_refused_at_delivery_names_the_guard(tool, monkeypatch):
    answers = iter([None])

    def guard(x, y, allow_auth=False):
        if next(answers, "refuse") == "refuse":
            raise srv.trust.TrustError("an authentication dialog covers (30, 40)")

    monkeypatch.setattr(srv.trust, "guard_pointer", guard)
    with pytest.raises(srv.trust.TrustError, match="the pointer guard refused the release "
                       r"\(cancelled: released back where the cursor was\) right before"):
        srv.pointer("up", x=30, y=40, button="right")
    assert tool["input"] == []
