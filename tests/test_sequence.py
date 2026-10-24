"""The sequence tool: run ordered actions in one call, stopping (best
effort) when the desktop changes structurally between steps."""

import json

import pytest

from hypruse import server as srv


class FakeStream:
    """Stands in for events.EventStream: yields a scripted list of event
    batches, one per drain() call, then empty batches forever. `waits`
    scripts events for wait_for() calls on the same stream."""

    def __init__(self, batches, waits=None, wait_error=False):
        self.batches = list(batches)
        self.waits = list(waits or [])
        self.wait_error = wait_error
        self.closed = False
        self.drains = 0
        self.wait_calls = []

    def drain(self, settle=0.08):
        self.drains += 1
        kept, self.kept = getattr(self, "kept", []), []
        return kept + (self.batches.pop(0) if self.batches else [])

    def wait_for(self, names, matcher, timeout, keep=frozenset()):
        self.wait_calls.append((set(names), timeout))
        if self.wait_error:
            from hypruse import events

            raise events.EventError("socket died mid-wait")
        self.kept = getattr(self, "kept", [])
        while self.waits:
            n, p = self.waits.pop(0)
            if n in names and (matcher is None or matcher(n, p)):
                return n, p
            if n in keep:  # as EventStream: the next drain returns it
                self.kept.append((n, p))
        return None

    def close(self):
        self.closed = True


@pytest.fixture
def wired(monkeypatch):
    """Record dispatched steps; no real input or compositor."""
    ran = []
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.hyprctl, "snapshot", lambda: {"snap": True})
    # activeworkspace-shaped for most queries; the layer pre-check asks for
    # "layers", whose real shape is a per-monitor dict (empty = none mapped)
    monkeypatch.setattr(
        srv.hyprctl, "query", lambda cmd: {} if cmd == "layers" else {"name": ""}
    )

    def fake_dispatch(step):
        ran.append(step)
        if step.get("keys") == "boom":
            raise RuntimeError("kaboom")
        return f"{step.get('op')} ok"

    monkeypatch.setattr(srv, "_dispatch_step", fake_dispatch)
    return ran


def _on_workspace(monkeypatch, address, ws, active):
    """`address` lives on workspace `ws`; `active` is active after a step."""
    win = {"address": address, "class": "app", "title": "t", "pid": 1, "at": [0, 0],
           "size": [9, 9], "workspace": {"id": int(ws), "name": ws}}
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: (
        [win] if cmd == "clients" else {"id": int(active), "name": active}
        if cmd == "activeworkspace" else {} if cmd == "layers" else {"name": ""}))


def _head(out):
    return out[0].text if isinstance(out, list) else out


def _stream(monkeypatch, batches, waits=None, wait_error=False):
    stream = FakeStream(batches, waits, wait_error)
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    return stream


def test_runs_all_steps_in_order(wired, monkeypatch):
    _stream(monkeypatch, [])
    steps = [
        {"op": "pointer", "action": "click", "x": 1, "y": 2},
        {"op": "keyboard", "action": "type", "text": "hi"},
        {"op": "keyboard", "action": "key", "keys": "enter"},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["pointer", "keyboard", "keyboard"]
    assert _head(out).startswith("sequence: all 3/3 steps ran")


def test_stops_on_unexpected_open(wired, monkeypatch):
    # a click pops a dialog (openwindow); the type step must not run blind
    _stream(monkeypatch, [[("openwindow", {"address": "0xnew"})]])
    steps = [
        {"op": "pointer", "action": "click", "x": 1, "y": 2},
        {"op": "keyboard", "action": "type", "text": "into-the-void"},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["pointer"]  # second step never ran
    assert "stopped after 1/2" in _head(out) and "openwindow" in _head(out)


def test_focus_and_title_churn_do_not_stop(wired, monkeypatch):
    # a click focuses/retitles a window; that is noise, not structural
    _stream(monkeypatch, [[("activewindow", {"data": "kitty,~"}), ("windowtitlev2", {})]])
    steps = [
        {"op": "pointer", "action": "click", "x": 1, "y": 2},
        {"op": "keyboard", "action": "type", "text": "ok"},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["pointer", "keyboard"]
    assert "all 2/2" in _head(out)


def test_expected_workspace_switch_is_excused(wired, monkeypatch):
    # the sequence's own switch to workspace 3 is not a change under it
    _stream(monkeypatch, [[("workspace", {"name": "3"})]])
    steps = [
        {"op": "hypr", "action": "workspace", "workspace": "3"},
        {"op": "pointer", "action": "click", "x": 5, "y": 5},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["hypr", "pointer"]
    assert "all 2/2" in _head(out)


def test_human_switching_to_other_workspace_stops(wired, monkeypatch):
    # payload-aware: a switch to a DIFFERENT workspace than the step's is a
    # real takeover, even though it is the same event type
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: {"name": "3"})  # our switch landed
    _stream(monkeypatch, [[("workspace", {"name": "5"})]])
    steps = [
        {"op": "hypr", "action": "workspace", "workspace": "3"},
        {"op": "pointer", "action": "click", "x": 5, "y": 5},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["hypr"]  # click never ran
    assert "workspace" in _head(out) and "stopped after 1/2" in _head(out)


def test_relative_workspace_switch_is_excused(wired, monkeypatch):
    # 'workspace +1' emits the RESOLVED name ('4'), which never equals the
    # literal '+1'; the sequence must recognize its own switch and not stop
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: {"name": "4"})
    _stream(monkeypatch, [[("workspace", {"name": "4"})]])
    steps = [
        {"op": "hypr", "action": "workspace", "workspace": "+1"},
        {"op": "pointer", "action": "click", "x": 5, "y": 5},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["hypr", "pointer"]
    assert "all 2/2" in _head(out)


def test_keyboard_window_on_other_workspace_does_not_abort(wired, monkeypatch):
    # keyboard(window=X) focuses X; if X is on another workspace, focusing
    # switches to it and emits a workspace event. That is the sequence's OWN
    # focus-induced switch, not a takeover: the following enter must still run
    _on_workspace(monkeypatch, "0xterm", "8", active="8")  # focus landed on ws 8, its own
    _stream(monkeypatch, [[("workspace", {"name": "8"})]])
    steps = [
        {"op": "keyboard", "action": "type", "text": "echo hi", "window": "0xterm"},
        {"op": "keyboard", "action": "key", "keys": "enter"},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["keyboard", "keyboard"]  # enter ran
    assert "all 2/2" in _head(out)


def test_click_ui_step_focus_switch_is_excused(wired, monkeypatch):
    # click_ui always focuses its target window; a cross-workspace target's
    # switch must not abort a following step either
    _on_workspace(monkeypatch, "0xapp", "5", active="5")
    _stream(monkeypatch, [[("workspace", {"name": "5"})]])
    steps = [
        {"op": "click_ui", "name": "Save", "window": "0xapp"},
        {"op": "keyboard", "action": "key", "keys": "enter"},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["click_ui", "keyboard"]
    assert "all 2/2" in _head(out)


def test_a_switch_the_human_made_during_a_focusing_step_stops(wired, monkeypatch):
    # V60: the step's window is on 5; the workspace active after it is 9
    _on_workspace(monkeypatch, "0xapp", "5", active="9")
    _stream(monkeypatch, [[("workspace", {"name": "9"})]])
    steps = [
        {"op": "click_ui", "name": "Save", "window": "0xapp"},
        {"op": "keyboard", "action": "key", "keys": "enter"},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["click_ui"]
    assert "desktop changed (workspace)" in _head(out)


def test_keyboard_without_window_still_stops_on_workspace_takeover(wired, monkeypatch):
    # the excuse is scoped to focus-capable steps: a plain keyboard step (no
    # window=) cannot switch workspaces, so a workspace event after it IS a
    # human takeover and must still stop the run
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: {"name": "3"})
    _stream(monkeypatch, [[("workspace", {"name": "9"})]])
    steps = [
        {"op": "keyboard", "action": "type", "text": "hi"},
        {"op": "keyboard", "action": "key", "keys": "enter"},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["keyboard"]  # enter did NOT run
    assert "stopped after 1/2" in _head(out) and "workspace" in _head(out)


def test_move_window_expected_by_address(wired, monkeypatch):
    _stream(monkeypatch, [[("movewindow", {"address": "0xA", "workspace": "3"})]])
    steps = [
        {"op": "hypr", "action": "move_window", "target": "0xA", "workspace": "3"},
        {"op": "pointer", "action": "click", "x": 1, "y": 1},
    ]
    srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["hypr", "pointer"]  # own move excused


def test_an_uppercase_address_still_excuses_the_steps_own_move(wired, monkeypatch):
    # review of 889bf85: Hyprland's event names the address in lowercase
    _stream(monkeypatch, [[("movewindow", {"address": "0xab", "workspace": "3"})]])
    steps = [
        {"op": "hypr", "action": "move_window", "target": "0xAB", "workspace": "3"},
        {"op": "pointer", "action": "click", "x": 1, "y": 1},
    ]
    srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["hypr", "pointer"]


def test_click_then_wait_is_not_aborted(wired, monkeypatch):
    # click a launcher (opens a window), then wait_for that window: the
    # openwindow is what the next step waits for, not an unexpected change
    stream = _stream(monkeypatch, [[("openwindow", {"address": "0xnew"})]])
    steps = [
        {"op": "pointer", "action": "click", "x": 1, "y": 2},
        {"op": "wait_for", "event": "window_open"},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["pointer"]  # wait ran on the stream
    assert "all 2/2" in _head(out)
    # the wait was satisfied by the event the settle-drain consumed: a fast
    # app must not turn the documented click-then-wait pattern into a timeout
    assert "0xnew" in _head(out) and "timeout" not in _head(out)
    assert stream.wait_calls == []  # never had to block: backlog had it


def test_wait_step_blocks_on_the_sequence_stream(wired, monkeypatch):
    # nothing drained yet: the wait must listen on the SAME connection the
    # sequence already holds, not open a fresh one that missed history
    stream = _stream(
        monkeypatch, [[]], waits=[("openwindow", {"address": "0xk", "class": "kitty"})]
    )
    steps = [
        {"op": "pointer", "action": "click", "x": 1, "y": 2},
        {"op": "wait_for", "event": "window_open", "match": "kitty"},
    ]
    out = srv.sequence(steps, then="none")
    assert "all 2/2" in _head(out) and "0xk" in _head(out)
    assert len(stream.wait_calls) == 1 and stream.wait_calls[0][0] == {"openwindow"}


def test_wait_step_never_served_a_stale_event(wired, monkeypatch):
    # [switch to 2, switch to 3, wait for the next switch]: the wait must
    # NOT be handed the excused workspace:2 from two steps ago; with the
    # backlog empty of own-changes it blocks for a genuinely new event
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: {"name": ""})
    stream = _stream(
        monkeypatch,
        [[("workspace", {"name": "2"})], [("workspace", {"name": "3"})]],
        waits=[("workspace", {"name": "4"})],
    )
    steps = [
        {"op": "hypr", "action": "workspace", "workspace": "2"},
        {"op": "hypr", "action": "workspace", "workspace": "3"},
        {"op": "wait_for", "event": "workspace"},
    ]
    out = srv.sequence(steps, then="none")
    assert "all 3/3" in _head(out)
    assert "'name': '4'" in _head(out)  # the NEXT switch, not the stale "2"
    assert len(stream.wait_calls) == 1  # it really blocked


def test_wait_step_survives_socket_death_mid_wait(wired, monkeypatch):
    # the event socket dying DURING an in-sequence wait degrades to a note,
    # not a crash, and the sequence still reports its steps
    _stream(monkeypatch, [[]], wait_error=True)
    steps = [
        {"op": "pointer", "action": "click", "x": 1, "y": 2},
        {"op": "wait_for", "event": "window_open"},
    ]
    out = srv.sequence(steps, then="none")
    assert "all 2/2" in _head(out)
    assert "event socket unavailable" in _head(out)


def test_wait_step_without_stream_uses_standalone_dispatch(wired, monkeypatch):
    # with stop_on_change=False there is no sequence stream: a wait step
    # must fall through to the standalone tool (with the budget clamp)
    def boom():
        raise AssertionError("must not open a stream when stop_on_change is False")

    monkeypatch.setattr(srv.events, "EventStream", boom)
    srv.sequence(
        [{"op": "wait_for", "event": "window_open", "timeout_s": 60}],
        stop_on_change=False,
        then="none",
    )
    assert [s["op"] for s in wired] == ["wait_for"]
    assert wired[0]["timeout_s"] <= 30  # clamped to the sequence budget


def test_a_window_the_wait_does_not_match_is_a_change(wired, monkeypatch):
    # V16/V41: before a filtered wait, every openwindow was excused by type,
    # so a window that was not the awaited one went unnoticed
    stream = _stream(monkeypatch, [[("openwindow", {"address": "0xa", "class": "sidebar"})]])
    steps = [
        {"op": "pointer", "action": "click", "x": 1, "y": 2},
        {"op": "wait_for", "event": "window_open", "match": "kitty", "timeout_s": 1},
    ]
    out = srv.sequence(steps, then="none")
    assert "desktop changed (openwindow) before step 1" in _head(out)
    assert stream.wait_calls == []


def test_the_awaited_window_is_excused_once(wired, monkeypatch):
    kitty = ("openwindow", {"address": "0xk", "class": "kitty"})
    other = ("openwindow", {"address": "0xo", "class": "kitty"})  # a second, unawaited one
    _stream(monkeypatch, [[kitty]])
    steps = [{"op": "pointer", "action": "click", "x": 1, "y": 2},
             {"op": "wait_for", "event": "window_open", "match": "kitty", "timeout_s": 1}]
    assert "all 2/2" in _head(srv.sequence(steps, then="none"))
    _stream(monkeypatch, [[kitty, other]])
    out = srv.sequence(steps, then="none")
    assert "desktop changed (openwindow) before step 1" in _head(out)


def test_what_happens_during_a_wait_step_is_seen_by_the_next_check(wired, monkeypatch):
    # V17/V42: the wait read and dropped every event it did not match
    _stream(monkeypatch, [[]], waits=[("openlayer", {"namespace": "wofi"}),
                                      ("openwindow", {"address": "0xk", "class": "kitty"})])
    steps = [{"op": "wait_for", "event": "window_open", "match": "kitty", "timeout_s": 1},
             {"op": "keyboard", "action": "type", "text": "into-the-launcher"}]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == []  # the keyboard step never ran
    assert "desktop changed (openlayer) before step 1" in _head(out)


def test_launcher_layer_opening_stops(wired, monkeypatch):
    # a wofi launcher appearing mid-sequence grabs the keyboard: the type
    # step must not run into it
    _stream(monkeypatch, [[("openlayer", {"namespace": "wofi"})]])
    steps = [
        {"op": "pointer", "action": "click", "x": 1, "y": 2},
        {"op": "keyboard", "action": "type", "text": "into-the-launcher"},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["pointer"]
    assert "openlayer" in _head(out) and "stopped after 1/2" in _head(out)


def test_notification_layer_is_noise(wired, monkeypatch):
    # a mako notification popping up is not a takeover; the run continues
    _stream(monkeypatch, [[("openlayer", {"namespace": "notifications"})]])
    steps = [
        {"op": "pointer", "action": "click", "x": 1, "y": 2},
        {"op": "keyboard", "action": "type", "text": "ok"},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["pointer", "keyboard"]
    assert "all 2/2" in _head(out)


def test_click_then_wait_layer_open_is_excused(wired, monkeypatch):
    # click something that pops a launcher, then wait for that layer: the
    # openlayer is what the next step waits for, not an unexpected change
    _stream(monkeypatch, [[("openlayer", {"namespace": "wofi"})]])
    steps = [
        {"op": "pointer", "action": "click", "x": 1, "y": 2},
        {"op": "wait_for", "event": "layer_open", "match": "wofi"},
    ]
    out = srv.sequence(steps, then="none")
    assert "all 2/2" in _head(out) and "wofi" in _head(out)


def test_final_drain_reports_last_step_change(wired, monkeypatch):
    # a change caused by the LAST step is reported, not silently missed
    _stream(monkeypatch, [[("openwindow", {"address": "0xz"})]])
    out = srv.sequence([{"op": "pointer", "action": "click", "x": 1, "y": 1}], then="none")
    assert [s["op"] for s in wired] == ["pointer"]
    assert "after the last step" in _head(out) and "openwindow" in _head(out)


def test_stops_on_step_error(wired, monkeypatch):
    _stream(monkeypatch, [])
    steps = [{"op": "pointer", "action": "move", "x": 1, "y": 1},
             {"op": "keyboard", "action": "key", "keys": "boom"},
             {"op": "hypr", "action": "workspace", "workspace": "2"}]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["pointer", "keyboard"]
    assert "ERROR kaboom" in _head(out) and "step 1 raised" in _head(out)


def test_then_appends_final_snapshot(wired, monkeypatch):
    _stream(monkeypatch, [])
    out = srv.sequence([{"op": "pointer", "action": "move", "x": 1, "y": 1}], then="desktop")
    assert isinstance(out, list)
    assert json.loads(out[1].text) == {"snap": True}


def test_stop_on_change_false_skips_stream(wired, monkeypatch):
    def boom():
        raise AssertionError("must not open a stream when stop_on_change is False")

    monkeypatch.setattr(srv.events, "EventStream", boom)
    srv.sequence(
        [
            {"op": "pointer", "action": "move", "x": 1, "y": 1},
            {"op": "hypr", "action": "workspace"},
        ],
        stop_on_change=False,
        then="none",
    )
    assert len(wired) == 2


def test_degrades_when_event_socket_unavailable(wired, monkeypatch):
    def boom():
        raise srv.events.EventError("no socket")

    monkeypatch.setattr(srv.events, "EventStream", boom)
    out = srv.sequence([{"op": "pointer", "action": "move", "x": 1, "y": 1}], then="none")
    assert len(wired) == 1 and "all 1/1" in _head(out)


def test_time_budget_stops_before_a_late_step(wired, monkeypatch):
    _stream(monkeypatch, [])
    # deadline is set from the first monotonic() reading; make the second
    # step's budget check land past it
    ticks = iter([0.0, 5.0, 100.0])
    monkeypatch.setattr(srv.time, "monotonic", lambda: next(ticks))
    steps = [
        {"op": "pointer", "action": "move", "x": 1, "y": 1},
        {"op": "pointer", "action": "move", "x": 2, "y": 2},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["pointer"]  # second step budget-gated
    assert "time budget" in _head(out)


def test_wait_for_timeout_clamped_to_budget(monkeypatch):
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.hyprctl, "snapshot", lambda: {})
    stream = _stream(monkeypatch, [])
    # 25s already elapsed of a 30s budget -> a 60s wait must clamp to ~5s
    ticks = iter([0.0, 25.0, 25.0])
    monkeypatch.setattr(srv.time, "monotonic", lambda: next(ticks))
    srv.sequence([{"op": "wait_for", "event": "window_open", "timeout_s": 60}], then="none")
    assert len(stream.wait_calls) == 1
    assert stream.wait_calls[0][1] == pytest.approx(5.0)


def test_real_dispatch_wiring(monkeypatch):
    # exercises the REAL _dispatch_step -> real pointer/keyboard, only the
    # input/IPC backends mocked (closes the over-mocked-tests gap)
    calls = {"moved": [], "typed": [], "dispatch": []}
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.hinput, "move", lambda x, y: calls["moved"].append((x, y)))
    monkeypatch.setattr(srv.hinput, "type_text", lambda t: calls["typed"].append(t))
    monkeypatch.setattr(srv.hyprctl, "dispatch", lambda *a: calls["dispatch"].append(a))
    monkeypatch.setattr(srv.hyprctl, "cursor_pos", lambda: (3, 4))
    _client = {"address": "0xabc", "class": "kitty", "pid": 1, "title": "t",
               "at": [0, 0], "size": [9, 9], "workspace": {"id": 1}}
    monkeypatch.setattr(
        srv.hyprctl, "query",
        lambda cmd: [_client] if cmd == "clients" else _client if cmd == "activewindow" else {},
    )
    monkeypatch.setattr(srv.time, "sleep", lambda s: None)
    _stream(monkeypatch, [])
    steps = [
        {"op": "pointer", "action": "move", "x": 1, "y": 2},
        {"op": "keyboard", "action": "type", "text": "hi", "window": "0xabc"},
    ]
    out = srv.sequence(steps, then="none")
    assert calls["moved"] == [(1, 2)]
    assert calls["typed"] == ["hi"]
    assert ("focuswindow", "address:0xabc") in calls["dispatch"]  # window= focused first
    assert "all 2/2" in _head(out)


def test_empty_and_overlong_rejected(monkeypatch):
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    with pytest.raises(ValueError, match="at least one step"):
        srv.sequence([])
    with pytest.raises(ValueError, match="too long"):
        srv.sequence([{"op": "pointer"}] * 21)


def test_dispatch_rejects_unknown_op():
    with pytest.raises(ValueError, match="unknown step op"):
        srv._dispatch_step({"op": "teleport"})


def test_dispatch_wraps_bad_args(monkeypatch):
    # a wrong/unknown param becomes a friendly ValueError, not a raw crash
    with pytest.raises(ValueError, match="bad args"):
        srv._dispatch_step({"op": "pointer", "nonsense": 1})


def test_dispatch_strips_then(monkeypatch):
    seen = {}
    monkeypatch.setattr(srv, "_SEQ_HANDLERS", {"x": lambda **kw: seen.update(kw) or "ok"})
    srv._dispatch_step({"op": "x", "action": "go", "then": "desktop"})
    assert "then" not in seen and seen["action"] == "go"


def test_expected_signatures_mapping():
    exp = srv._step_expected_signatures
    assert exp({"op": "hypr", "action": "workspace", "workspace": "3"}) == {"workspace:3"}
    assert exp({"op": "hypr", "action": "move_window", "target": "0xA"}) == {"movewindow:0xA"}
    assert exp({"op": "pointer", "action": "click"}) == set()
    excuse = srv._step_wait_excuse({"op": "wait_for", "event": "window_open"})
    assert excuse("openwindow", {"address": "0xa"}) and not excuse("workspace", {"name": "2"})
    assert srv._step_wait_excuse({"op": "pointer"}) is None
    assert srv._event_signature("workspace", {"name": "5"}) == "workspace:5"


def test_sequence_background_applies_to_every_step_that_takes_it(wired):
    srv.sequence([{"op": "click_ui", "name": "Save", "window": "0xa"},
                  {"op": "keyboard", "action": "key", "keys": "enter", "background": False},
                  {"op": "hypr", "action": "close_window", "target": "0xa"}],
                 background=True, stop_on_change=False, then="none")
    assert wired[0]["background"] is True
    assert wired[1]["background"] is False  # a step's own choice wins
    assert "background" not in wired[2]  # hypr takes no background


def test_a_close_step_filtered_by_title_matches_by_address(wired, monkeypatch):
    # Hyprland's closewindow names only the address: a title filter is
    # resolved to the dialog's address when the step starts
    dialog = {"address": "0xD", "class": "chromium", "title": "Open File"}
    monkeypatch.setattr(srv.hyprctl, "query",
                        lambda cmd: [dialog] if cmd == "clients" else {"name": ""})
    stream = _stream(monkeypatch, [[]], waits=[("closewindow", {"address": "0xd"})])
    steps = [
        {"op": "keyboard", "action": "key", "keys": "enter"},
        {"op": "wait_for", "event": "window_close", "match": "Open File"},
    ]
    out = srv.sequence(steps, then="none")
    assert "all 2/2" in _head(out) and "0xd" in _head(out)
    assert len(stream.wait_calls) == 1


def test_an_open_step_finds_a_window_that_mapped_before_it(wired, monkeypatch):
    srv._known_clients = {"0xa"}  # as the keyboard step's refresh left it
    kitty = {"address": "0xA", "class": "kitty", "title": "~", "workspace": {"name": "1"}}
    lg = {"address": "0xB", "class": "lg-remote", "title": "LG", "workspace": {"name": "2"}}
    monkeypatch.setattr(srv.hyprctl, "query",
                        lambda cmd: [kitty, lg] if cmd == "clients" else {"name": ""})
    stream = _stream(monkeypatch, [[]])
    steps = [
        {"op": "keyboard", "action": "key", "keys": "enter"},
        {"op": "wait_for", "event": "window_open", "match": "lg-remote"},
    ]
    out = srv.sequence(steps, then="none")
    assert "'already': True" in _head(out) and "0xB" in _head(out)
    assert stream.wait_calls == []


# --- sleep steps ----------------------------------------------------------------


def test_a_sleep_step_pauses_between_steps(wired, monkeypatch):
    _stream(monkeypatch, [])
    slept = []
    monkeypatch.setattr(srv.time, "sleep", lambda s: slept.append(s))
    steps = [
        {"op": "pointer", "action": "click", "x": 1, "y": 2},
        {"op": "sleep", "ms": 1500},
        {"op": "keyboard", "action": "key", "keys": "enter"},
    ]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["pointer", "keyboard"]
    assert 1.5 in slept
    assert "all 3/3" in _head(out) and "[1] sleep : slept 1500 ms" in _head(out)


@pytest.mark.parametrize(
    "step",
    [{"op": "sleep"}, {"op": "sleep", "ms": 0}, {"op": "sleep", "ms": 60001},
     {"op": "sleep", "ms": "4"}, {"op": "sleep", "ms": True},
     {"op": "sleep", "seconds": 4}],
)
def test_a_bad_sleep_step_refuses_before_any_step_runs(wired, monkeypatch, step):
    _stream(monkeypatch, [])
    with pytest.raises(ValueError, match="step 1: a sleep step"):
        srv.sequence([{"op": "pointer", "action": "click", "x": 1, "y": 2}, step], then="none")
    assert wired == []


def test_a_sleep_longer_than_the_budget_stops_instead_of_overrunning(wired, monkeypatch):
    _stream(monkeypatch, [])
    slept = []
    monkeypatch.setattr(srv.time, "sleep", lambda s: slept.append(s))
    out = srv.sequence([{"op": "sleep", "ms": 45000}], then="none")
    assert "stopped after 0/1" in _head(out) and "would run out" in _head(out)
    assert 45.0 not in slept


def test_a_dry_run_does_not_sleep(wired, monkeypatch):
    monkeypatch.setenv("HYPRUSE_DRYRUN", "1")
    _stream(monkeypatch, [])
    slept = []
    monkeypatch.setattr(srv.time, "sleep", lambda s: slept.append(s))
    out = srv.sequence([{"op": "sleep", "ms": 2000}], then="none")
    assert "would sleep 2000 ms" in _head(out) and 2.0 not in slept


def test_unknown_op_lists_sleep():
    with pytest.raises(ValueError, match="sleep"):
        srv._dispatch_step({"op": "nap"})


def test_after_a_switch_to_a_named_workspace_the_humans_own_switch_stops(wired, monkeypatch):
    # review of C10: `hypr workspace 5` excused whatever was active afterwards
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: (
        {"id": 3, "name": "3"} if cmd == "activeworkspace" else {} if cmd == "layers"
        else [] if cmd == "clients" else {"name": ""}))
    _stream(monkeypatch, [[("workspace", {"name": "5"}), ("workspace", {"name": "3"})]])
    steps = [{"op": "hypr", "action": "workspace", "workspace": "5"},
             {"op": "keyboard", "action": "key", "keys": "enter"}]
    out = srv.sequence(steps, then="none")
    assert [s["op"] for s in wired] == ["hypr"] and "desktop changed (workspace)" in _head(out)


def test_a_relative_switch_is_still_the_steps_own(wired, monkeypatch):
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: (
        {"id": 6, "name": "6"} if cmd == "activeworkspace" else {} if cmd == "layers"
        else [] if cmd == "clients" else {"name": ""}))
    _stream(monkeypatch, [[("workspace", {"name": "6"})]])
    steps = [{"op": "hypr", "action": "workspace", "workspace": "+1"},
             {"op": "keyboard", "action": "key", "keys": "enter"}]
    assert "all 2/2" in _head(srv.sequence(steps, then="none"))
