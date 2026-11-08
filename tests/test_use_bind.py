import pytest

from hypruse import hyprctl, server

BINDS = [
    {"combo": "SUPER+F", "action": "exec", "arg": "kitty --class wb-float-files -e yazi"},
    {"combo": "SUPER+2", "action": "workspace", "arg": "2"},
    {"combo": "SUPER+W", "action": "togglefloating", "arg": ""},
]


@pytest.fixture
def fake_binds(monkeypatch):
    monkeypatch.setattr(hyprctl, "binds", lambda: BINDS)
    dispatched = []
    monkeypatch.setattr(hyprctl, "dispatch", lambda *a: dispatched.append(a))
    return dispatched


def test_find_bind_case_insensitive(fake_binds):
    assert hyprctl.find_bind("super+f")["action"] == "exec"
    assert hyprctl.find_bind("SUPER + 2")["arg"] == "2"
    assert hyprctl.find_bind("super+nope") is None


def test_use_bind_runs_exec_action(fake_binds):
    out = server.use_bind("super+f")
    assert fake_binds == [("exec", "kitty --class wb-float-files -e yazi")]
    assert "SUPER+F" in out


def test_use_bind_runs_dispatcher_action(fake_binds):
    server.use_bind("SUPER+2")
    assert fake_binds == [("workspace", "2")]


def test_use_bind_empty_arg_dispatched_bare(fake_binds):
    server.use_bind("super+w")
    assert fake_binds == [("togglefloating",)]


def test_use_bind_unknown_raises(fake_binds):
    with pytest.raises(ValueError, match="no keybind"):
        server.use_bind("super+q")


def test_wait_for_close_already_gone(monkeypatch):
    # no client matches -> already closed, returns without touching the socket
    def _no_connect():
        raise AssertionError("should not connect when already satisfied")

    monkeypatch.setattr(hyprctl, "query", lambda cmd: [] if cmd == "clients" else {})
    monkeypatch.setattr(server.events, "EventStream", _no_connect)
    out = server.wait_for("window_close", match="0xdead")
    assert out["already"] is True


def test_wait_for_close_still_open_waits(monkeypatch):
    monkeypatch.setattr(
        hyprctl, "query", lambda cmd: [{"address": "0xabc", "class": "kitty", "title": "x"}]
    )

    class S:
        def wait_for(self, names, matcher, timeout):
            return ("closewindow", {"address": "0xabc"})

        def __enter__(self):
            return self

        def __exit__(self, *e):
            pass

    monkeypatch.setattr(server.events, "EventStream", lambda: S())
    out = server.wait_for("window_close", match="0xabc")
    assert out["event"] == "closewindow" and "already" not in out


def test_wait_for_workspace_already_active(monkeypatch):
    def _no_connect():
        raise AssertionError("should not connect when already satisfied")

    monkeypatch.setattr(hyprctl, "query", lambda cmd: {"name": "3"})
    monkeypatch.setattr(server.events, "EventStream", _no_connect)
    out = server.wait_for("workspace", match="3")
    assert out["already"] is True and out["name"] == "3"


def test_use_bind_refused_under_confinement(fake_binds, monkeypatch):
    # integration: the guard_use_bind call site is real; a bind runs an
    # arbitrary compositor action that cannot be scoped, so confinement
    # refuses it wholesale, before dispatching anything
    monkeypatch.setenv("HYPRUSE_CONFINE", "class:kitty")
    with pytest.raises(server.trust.TrustError, match="cannot be confined"):
        server.use_bind("super+f")
    assert fake_binds == []  # nothing dispatched


def test_hypr_focus_window_refused_out_of_scope(monkeypatch):
    # integration: hypr's guard_window(target) call site is real; focusing
    # an out-of-scope window by address is refused before dispatch
    monkeypatch.setenv("HYPRUSE_CONFINE", "class:kitty")
    monkeypatch.setattr(server.safety, "touch", lambda *a: None)
    firefox = {"address": "0xff", "class": "firefox", "workspace": {"id": 1},
               "at": [0, 0], "size": [10, 10], "mapped": True}
    dispatched = []
    monkeypatch.setattr(server.hyprctl, "query", lambda cmd: [firefox])
    monkeypatch.setattr(server.hyprctl, "dispatch", lambda *a: dispatched.append(a))
    with pytest.raises(server.trust.TrustError, match="confinement scope"):
        server.hypr("focus_window", target="0xff")
    assert dispatched == []


LUA_BINDS = [
    {"combo": "SUPER+F", "action": "lua", "description": "open files"},
    {"combo": "SUPER+L", "action": "lua", "dispatcher": "close"},
]


@pytest.fixture
def fake_lua_binds(monkeypatch):
    monkeypatch.setattr(hyprctl, "binds", lambda: LUA_BINDS)
    ran, dispatched = [], []
    monkeypatch.setattr(hyprctl, "dispatch", lambda *a: dispatched.append(a))
    handlers = {"SUPER+L": "HL.Dispatcher(close)", "SUPER+F": "function: 0x1"}

    def run(combo, expect=None):
        ran.append(combo)
        return handlers[combo], ""

    monkeypatch.setattr(hyprctl, "run_lua_bind", run)
    return ran, dispatched


def test_use_bind_runs_a_lua_bind(fake_lua_binds):
    ran, dispatched = fake_lua_binds
    assert "ran SUPER+L: lua close" in str(server.use_bind("super+l"))
    assert "ran SUPER+F: lua function" in str(server.use_bind("SUPER+F"))
    assert ran == ["SUPER+L", "SUPER+F"] and dispatched == []


def test_a_lua_bind_rehearses_under_dry_run(fake_lua_binds, monkeypatch):
    ran, _ = fake_lua_binds
    monkeypatch.setenv("HYPRUSE_DRYRUN", "1")
    out = str(server.use_bind("super+l"))
    assert "run SUPER+L: lua close" in out and ran == []


def test_use_bind_passes_what_it_read_and_reports_what_ran(fake_lua_binds, monkeypatch):
    seen = []

    def run(combo, expect=None):
        seen.append(expect)
        return "HL.Dispatcher(close)", "warning: hl.window.close: no target"

    monkeypatch.setattr(hyprctl, "run_lua_bind", run)
    out = str(server.use_bind("super+l"))
    assert seen == ["close"]
    assert "ran SUPER+L: lua close (Hyprland warned: warning: hl.window.close: no target)" in out


def test_a_lua_bind_that_moved_under_the_call_says_to_retry(fake_lua_binds, monkeypatch):
    def stale(combo, expect=None):
        raise hyprctl.LuaBindMoved(f"use_bind {combo}: reloaded")

    monkeypatch.setattr(hyprctl, "run_lua_bind", stale)
    with pytest.raises(ValueError, match="use_bind again"):
        server.use_bind("super+l")


def test_a_lua_bind_s_own_failure_is_reported_as_it_is(fake_lua_binds, monkeypatch):
    def boom(combo, expect=None):
        raise hyprctl.HyprctlError(f"use_bind {combo}: hl.dispatch: boom")

    monkeypatch.setattr(hyprctl, "run_lua_bind", boom)
    with pytest.raises(hyprctl.HyprctlError, match="boom"):
        server.use_bind("super+l")


def test_a_lua_bind_is_refused_under_confinement(fake_lua_binds, monkeypatch):
    ran, _ = fake_lua_binds
    monkeypatch.setenv("HYPRUSE_CONFINE", "class:kitty")
    with pytest.raises(server.trust.TrustError, match="cannot be confined"):
        server.use_bind("super+l")
    assert ran == []
