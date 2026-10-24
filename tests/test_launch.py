"""launch() behaviour around single-instance apps and slow startups.

Regression source: real test session 2026-07-16, `google-chrome
--profile-picker` with workspace=2 opened its window from the existing
Chrome process on workspace 5, and the old 3s detection window missed it
entirely.
"""

import itertools

import pytest

from hypruse import server


class FakeHyprctl:
    def __init__(self, snapshots):
        self._snapshots = snapshots  # list of client lists, consumed per query
        self.dispatched = []

    def query(self, cmd):
        assert cmd == "clients"
        return self._snapshots[0] if len(self._snapshots) == 1 else self._snapshots.pop(0)

    def dispatch(self, *args):
        self.dispatched.append(args)


EXISTING = {"address": "0xa", "class": "kitty", "title": "~", "workspace": {"id": 5, "name": "5"}}
CHROME = {
    "address": "0xb",
    "class": "google-chrome",
    "title": "profile picker",
    "workspace": {"id": 5, "name": "5"},
}


class NoSocket:
    def __init__(self):
        raise server.events.EventError("no socket in tests")


class FakeStream:
    """Event-socket double: yields one openwindow event."""

    def __init__(self, payload):
        self._payload = payload

    def wait_for(self, names, matcher, timeout):
        assert "openwindow" in names
        return ("openwindow", self._payload) if self._payload else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(server.time, "sleep", lambda s: None)


def _patch(monkeypatch, fake, stream="none"):
    monkeypatch.setattr(server.hyprctl, "query", fake.query)
    monkeypatch.setattr(server.hyprctl, "dispatch", fake.dispatch)
    if stream == "none":
        monkeypatch.setattr(server.events, "EventStream", NoSocket)
    else:
        monkeypatch.setattr(server.events, "EventStream", lambda: stream)


def test_launch_moves_single_instance_window(monkeypatch, no_sleep):
    fake = FakeHyprctl([[EXISTING], [EXISTING], [EXISTING, CHROME]])
    _patch(monkeypatch, fake)
    out = server.launch("google-chrome", workspace="2")
    assert out["address"] == "0xb"
    assert out["workspace"] == "2"
    assert "moved" in out["note"]
    assert ("movetoworkspacesilent", "2,address:0xb") in fake.dispatched


def test_launch_no_move_when_rule_worked(monkeypatch, no_sleep):
    landed_right = dict(CHROME, workspace={"id": 2, "name": "2"})
    fake = FakeHyprctl([[EXISTING], [EXISTING, landed_right]])
    _patch(monkeypatch, fake)
    out = server.launch("google-chrome", workspace="2")
    assert out["workspace"] == 2
    assert "note" not in out
    assert not any(d[0] == "movetoworkspacesilent" for d in fake.dispatched)


def test_launch_timeout_message_mentions_single_instance(monkeypatch, no_sleep):
    fake = FakeHyprctl([[EXISTING]])  # nothing ever appears
    _patch(monkeypatch, fake)
    clock = itertools.count(start=0, step=3.0)
    monkeypatch.setattr(server.time, "monotonic", lambda: next(clock))
    out = server.launch("slowapp", workspace="2", wait_s=8)
    assert isinstance(out, str)
    assert "single-instance" in out
    assert "desktop()" in out


def test_launch_wait_s_clamped(monkeypatch, no_sleep):
    fake = FakeHyprctl([[EXISTING]])  # nothing ever appears
    _patch(monkeypatch, fake)
    clock = itertools.count(start=0, step=1.0)
    monkeypatch.setattr(server.time, "monotonic", lambda: next(clock))
    server.launch("app", wait_s=999)  # must not loop for 999s
    assert next(clock) < 40


def test_launch_uses_event_socket_when_available(monkeypatch, no_sleep):
    fake = FakeHyprctl([[EXISTING, CHROME]])  # single snapshot: lookup by address
    stream = FakeStream({"address": "0xb", "workspace": "5", "class": "google-chrome"})
    _patch(monkeypatch, fake, stream=stream)
    out = server.launch("google-chrome", workspace="2")
    assert out["address"] == "0xb"
    assert out["workspace"] == "2"  # moved: event said it landed on 5
    assert ("movetoworkspacesilent", "2,address:0xb") in fake.dispatched


def test_launch_event_timeout_message(monkeypatch, no_sleep):
    fake = FakeHyprctl([[EXISTING]])
    _patch(monkeypatch, fake, stream=FakeStream(None))
    out = server.launch("slowapp", wait_s=2)
    assert isinstance(out, str) and "no new window" in out


def test_wait_for_tool_matching(monkeypatch):
    hits = iter([("openwindow", {"address": "0xc", "class": "firefox", "title": "Docs"})])

    class S:
        def wait_for(self, names, matcher, timeout):
            for name, p in hits:
                if matcher is None or matcher(name, p):
                    return name, p
            return None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(server.events, "EventStream", lambda: S())
    out = server.wait_for("window_open", match="FIRE")
    assert out["class"] == "firefox"


def test_wait_for_rejects_unknown_event():
    with pytest.raises(ValueError, match="unknown event"):
        server.wait_for("coffee_ready")


# --- workspace arguments are not free-form ----------------------------------
#
# `hypr action=workspace` is deliberately the one action that skips the
# confinement guard, and on a Lua config `hyprctl dispatch` evaluates its
# argument inside the compositor's own interpreter. A workspace identifier
# never contains this punctuation, so refusing it costs nothing real.


@pytest.mark.parametrize(
    "workspace",
    ['3") or os.execute("touch /tmp/pwn', "3;4", "3,address:0xa", "a=b", "x\ny"],
)
def test_workspace_refuses_expression_punctuation(workspace):
    with pytest.raises(ValueError, match="is not a workspace"):
        server._workspace(workspace)


@pytest.mark.parametrize(
    "workspace", ["3", "-3", "+1", "e+1", "previous", "special:magic", "name:my notes"]
)
def test_workspace_keeps_every_shape_hyprland_accepts(workspace):
    assert server._workspace(workspace) == workspace


def test_hypr_refuses_a_bad_workspace_before_dispatching(monkeypatch):
    monkeypatch.setattr(server.safety, "touch", lambda *a: None)
    dispatched = []
    monkeypatch.setattr(server.hyprctl, "dispatch", lambda *a: dispatched.append(a))
    with pytest.raises(ValueError, match="is not a workspace"):
        server.hypr("workspace", workspace='3") or os.execute("id')
    assert dispatched == []


def test_launch_refuses_a_bad_workspace_before_spawning(monkeypatch):
    monkeypatch.setattr(server.safety, "touch", lambda *a: None)
    dispatched = []
    monkeypatch.setattr(server.hyprctl, "dispatch", lambda *a: dispatched.append(a))
    with pytest.raises(ValueError, match="is not a workspace"):
        server.launch("foot", workspace="2] tag pwned [")
    assert dispatched == []


@pytest.mark.parametrize(
    "ws, expect",
    [({"address": "special:scratch", "type": "special", "name": "special:scratch"},
      "special:scratch"),
     ({"address": "notes", "type": "normal", "name": "notes"}, "notes")],
)
def test_launch_reports_an_id_less_057_workspace_by_name(monkeypatch, no_sleep, ws, expect):
    # Hyprland 0.57 gives special and named workspaces no id
    fake = FakeHyprctl([[EXISTING], [EXISTING, dict(CHROME, workspace=ws)]])
    _patch(monkeypatch, fake)
    out = server.launch("google-chrome")
    assert out["workspace"] == expect


# C7 (F4, F16, F40, V45): a launch adopted the first window anyone opened
# during its wait (with no placement rule), or any window whose class shared
# a substring with a word of the command, and made it the agent's: in
# `launched` scope, tagged hypruse-owned, moved.
HUMAN = {"address": "0xh", "class": "kitty", "title": "~", "pid": 777,
         "workspace": {"id": 1, "name": "1"}}
OURS = {"address": "0xo", "class": "foot", "title": "~", "pid": 4242,
        "workspace": {"id": 1, "name": "1"}}


@pytest.fixture
def owned(monkeypatch):
    from hypruse import trust

    trust._owned.clear()
    monkeypatch.setattr(server, "_carries_token", lambda pid, token: pid == 4242 and bool(token))
    yield trust.owned
    trust._owned.clear()


def test_a_launch_without_a_rule_does_not_take_the_humans_window(monkeypatch, no_sleep, owned):
    fake = FakeHyprctl([[EXISTING], [EXISTING, HUMAN]])
    _patch(monkeypatch, fake)  # the polling fallback (V45) as well as the event path
    clock = itertools.count(start=0, step=1.0)
    monkeypatch.setattr(server.time, "monotonic", lambda: next(clock))
    out = server.launch("foot", background=False, wait_s=3)
    assert isinstance(out, str) and "no new window" in out
    assert owned() == set()


def test_the_launchs_own_window_is_the_agents(monkeypatch, no_sleep, owned):
    fake = FakeHyprctl([[EXISTING], [EXISTING, HUMAN, OURS]])
    _patch(monkeypatch, fake)
    out = server.launch("foot", background=False)
    assert out["address"] == "0xo" and "note" not in out
    assert owned() == {server.hyprctl.window_key(OURS)}
    (exec_,) = [d for d in fake.dispatched if d[0] == "exec"]
    assert exec_[1].startswith("export HYPRUSE_LAUNCH=") and exec_[1].endswith("; foot")


def test_a_single_instance_window_is_found_and_moved_but_not_the_agents(monkeypatch, no_sleep,
                                                                       owned):
    # its window opens from the running process: no token, not on the rule's
    # workspace, so only its class says it is this launch's
    fake = FakeHyprctl([[EXISTING], [EXISTING, dict(CHROME, pid=999)]])
    _patch(monkeypatch, fake)
    out = server.launch("google-chrome", workspace="2")
    assert out["address"] == "0xb" and ("movetoworkspacesilent", "2,address:0xb") in fake.dispatched
    assert "not counted as one the agent opened" in out["note"] and "moved" in out["note"]
    assert owned() == set()


def test_a_command_word_that_contains_a_class_owns_nothing(monkeypatch, no_sleep, owned):
    # F40: the model chose a command whose name contains the human app's class
    fake = FakeHyprctl([[EXISTING], [EXISTING, HUMAN]])
    _patch(monkeypatch, fake)
    out = server.launch("kittyfoo", background=False)
    assert owned() == set() and "not counted" in out["note"]


def test_the_token_goes_after_a_rule_the_command_starts_with():
    assert server._with_token("[float] kitty", "t") == "[float] export HYPRUSE_LAUNCH=t; kitty"
    assert server._with_token("sh -c 'a; b'", "t") == "export HYPRUSE_LAUNCH=t; sh -c 'a; b'"


def test_a_process_carries_the_token_it_was_started_with():
    import os
    import subprocess
    import time

    child = subprocess.Popen(["sleep", "5"], env={**os.environ, "HYPRUSE_LAUNCH": "abc123"})
    try:
        # Popen can return before the child has exec'd; a window maps long after
        deadline = time.monotonic() + 2
        while not server._carries_token(child.pid, "abc123") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._carries_token(child.pid, "abc123")
        assert not server._carries_token(child.pid, "abc12")
        assert not server._carries_token(child.pid, "")
        assert not server._carries_token(None, "abc123")
    finally:
        child.kill()
        child.wait()
