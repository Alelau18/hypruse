"""Calls rebuilt from JSON, a `sequence` step and a replayed journal entry,
get their tool's argument types the way a direct MCP call does (C2: V5,
F55, F8, V21). They used to reach the tools raw, so a step's
`"allow_auth": "false"` was a non-empty string that every guard read as an
override, while the journal and the replay plan printed allow_auth=false."""

import functools
import inspect
import json
import re
import typing

import pytest

from hypruse import background as bg
from hypruse import cli, journal, trust
from hypruse import server as srv


def execute(path, *flags):
    """`hypruse replay PATH --execute ...` as a human runs it: with the
    --plan digest its own dry run printed (same filters)."""
    import contextlib
    import io

    filters = [f for f in flags if f != "--yes"]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.replay([str(path), *filters])
    found = re.search(r"--plan ([0-9a-f]{16})", out.getvalue())
    return cli.replay([str(path), "--execute", "--plan", found.group(1) if found else "none",
                       *flags])

POLKIT = {"address": "0xabc", "class": "polkit-gnome-authentication-agent-1", "title": "Auth",
          "pid": 7}


def _recording(monkeypatch, op):
    """Put a recorder in place of a step's tool. It keeps the tool's own
    signature (functools.wraps), which is what the step is checked against."""
    seen = []
    real = srv._SEQ_HANDLERS[op]

    @functools.wraps(real)
    def record(**kw):
        seen.append(kw)
        return f"{op} ok"

    monkeypatch.setitem(srv._SEQ_HANDLERS, op, record)
    return seen


@pytest.fixture
def quiet_sequence(monkeypatch):
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv, "_note_clients", lambda: None)
    monkeypatch.setattr(srv.events, "EventStream", lambda: (_ for _ in ()).throw(
        srv.events.EventError("no stream in a unit test")))


# --- the guards --------------------------------------------------------------


@pytest.mark.parametrize("flag", ["false", "no", "0", "true", 1, "yes"])
def test_the_auth_guard_takes_only_true_as_an_override(flag):
    with pytest.raises(trust.TrustError):
        trust.guard_auth_client(POLKIT, flag)
    trust.guard_auth_client(POLKIT, True)  # the override itself still works


def test_the_background_choice_takes_only_a_boolean_as_a_choice(monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "0")
    assert bg.decide(True, "false") is False
    assert bg.decide(True, True) is True


# --- sequence steps ----------------------------------------------------------


@pytest.mark.parametrize("flag, means", [("false", False), ("no", False), ("0", False),
                                         ("off", False), ("true", True), ("yes", True)])
def test_a_steps_string_flag_is_the_boolean_it_spells(monkeypatch, quiet_sequence, flag,
                                                     means):
    seen = _recording(monkeypatch, "click_ui")
    srv.sequence([{"op": "click_ui", "name": "Allow", "window": "0xabc", "allow_auth": flag,
                   "background": flag}], then="none")
    assert seen == [{"name": "Allow", "window": "0xabc", "allow_auth": means,
                     "background": means}]
    assert type(seen[0]["allow_auth"]) is bool


def test_a_steps_numbers_are_numbers(monkeypatch, quiet_sequence):
    seen = _recording(monkeypatch, "pointer")
    srv.sequence([{"op": "pointer", "action": "move", "x": "10", "y": 20}], then="none")
    assert seen == [{"action": "move", "x": 10.0, "y": 20.0}]


@pytest.mark.parametrize("bad", [
    {"op": "click_ui", "name": "Allow", "allow_auth": "maybe"},
    {"op": "pointer", "action": "click", "x": "left", "y": 1},
    {"op": "keyboard", "action": "key", "keys": "a", "allow_auht": True},  # not a parameter
    {"op": "teleport"},
    "pointer",
])
def test_a_bad_step_refuses_the_sequence_before_any_step_runs(monkeypatch, quiet_sequence,
                                                              bad):
    seen = _recording(monkeypatch, "pointer")
    with pytest.raises(ValueError, match="step 1"):
        srv.sequence([{"op": "pointer", "action": "move", "x": 1, "y": 1}, bad], then="none")
    assert seen == []


def test_a_bad_flag_is_named_but_not_echoed(monkeypatch, quiet_sequence):
    _recording(monkeypatch, "keyboard")
    with pytest.raises(ValueError, match="allow_auth") as err:
        srv.sequence([{"op": "keyboard", "action": "type", "text": "hunter2",
                       "allow_auth": "hunter2"}], then="none")
    assert "hunter2" not in str(err.value)


def test_the_journal_records_the_flag_the_guard_used(monkeypatch, tmp_path, quiet_sequence):
    # V21: the audit view printed allow_auth=false for an override that took
    # effect. A stub with click_ui's signature runs the real auth guard.
    path = tmp_path / "journal.ndjson"
    monkeypatch.setenv("HYPRUSE_JOURNAL", str(path))
    monkeypatch.setattr(journal, "_seq", 0)

    @journal.journaled("act")
    @functools.wraps(srv.click_ui)
    def click_ui(**kw):
        trust.guard_auth_client(POLKIT, kw.get("allow_auth", False))
        return "clicked"

    monkeypatch.setitem(srv._SEQ_HANDLERS, "click_ui", click_ui)
    out = srv.sequence([{"op": "click_ui", "name": "Authenticate", "window": "0xabc",
                         "allow_auth": "false"}], then="none")
    assert "ERROR" in str(out)  # refused: "false" is False
    (entry,) = [e for e in journal.read(path) if e.get("tool") == "click_ui"]
    assert entry["outcome"] == "refused" and entry["args"]["allow_auth"] is False
    assert "allow_auth=false" not in cli._fmt_entry(entry)


# --- replay ------------------------------------------------------------------


def _journal(tmp_path, args, tool="click_ui"):
    entry = {"v": 1, "seq": 1, "ts": "2026-10-24T00:00:00.000Z", "kind": "act", "tool": tool,
             "args": args, "outcome": "ok", "ms": 1}
    path = tmp_path / "j.ndjson"
    path.write_text(json.dumps(entry) + "\n")
    return path


@pytest.fixture
def replaying(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_countdown", lambda s: True)
    monkeypatch.setattr(cli, "_take_beacon", lambda: True)
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: [{"address": "0xabc"}])
    from hypruse import safety, session

    monkeypatch.setattr(session, "ensure_session_env", lambda: None)
    monkeypatch.setattr(safety, "on_shutdown", lambda fn: None)
    for name in ("pointer", "keyboard", "click_ui"):
        monkeypatch.setattr(srv, name, lambda _n=name, **kw: seen.append((_n, kw)) or "ok")
    return seen


@pytest.mark.parametrize("flag", ["false", "no", "0", 0, "true"])
def test_replay_refuses_a_flag_that_is_not_a_boolean(tmp_path, replaying, capsys, flag):
    # V5: the plan said allow_auth=false and --execute ran with the guard off
    path = _journal(tmp_path, {"name": "Allow", "window": "0xabc", "allow_auth": flag})
    assert cli.replay([str(path)]) == 0
    plan = capsys.readouterr().out
    assert "allow_auth=false" not in plan and "allow_auth=<not a boolean" in plan
    assert "arguments are not the tool's" in plan and "allow_auth" in plan
    assert execute(path, "--yes") == 1
    assert replaying == []


def test_replay_refuses_a_number_recorded_as_text(tmp_path, replaying, capsys):
    path = _journal(tmp_path, {"action": "click", "x": "800", "y": 60}, tool="pointer")
    assert execute(path, "--yes") == 1
    assert replaying == [] and "x: Input should be a valid number" in capsys.readouterr().out


def test_replay_runs_the_plan_it_printed(tmp_path, replaying, capsys):
    path = _journal(tmp_path, {"name": "Allow", "window": "0xabc", "allow_auth": True,
                               "then": "desktop"})
    assert cli.replay([str(path)]) == 0
    assert "click_ui name=Allow window=0xabc allow_auth=True" in capsys.readouterr().out
    assert execute(path, "--yes") == 0
    assert replaying == [("click_ui", {"name": "Allow", "window": "0xabc", "allow_auth": True})]
    assert replaying[0][1]["allow_auth"] is True


def test_the_journal_view_marks_a_flag_that_is_not_a_boolean():
    line = cli._fmt_args({"name": "Authenticate", "allow_auth": "false", "background": "no"})
    assert "allow_auth=false" not in line and "background=no" not in line
    assert "allow_auth=<not a boolean: 'false'>" in line


def test_the_journal_views_flags_are_the_tools_boolean_arguments():
    flags = set()
    for tool in srv.TOOLS.values():
        hints = typing.get_type_hints(inspect.unwrap(tool))
        flags |= {k for k, t in hints.items() if k != "return" and t in (bool, bool | None)}
    assert flags == cli._FLAGS


@pytest.mark.parametrize("tool, args", [
    ("hypr", {"action": "workspace", "workspace": 2}),
    ("pointer", {"action": "move", "x": "10", "y": 20}),
    ("click_ui", {"name": "Allow", "allow_auth": "false"}),
    ("click_ui", {"name": "Allow", "allow_auth": "maybe"}),
    ("keyboard", {"action": "key", "keys": "a", "background": "no"}),
    ("wait_for", {"event": "window_open", "timeout_s": "5"}),
])
def test_a_step_is_coerced_exactly_as_a_direct_mcp_call(tool, args):
    # the review found a step coerced a number to text where a direct call
    # refused it: the two validators must agree
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata

    fn = srv.TOOLS[tool]
    try:
        direct = func_metadata(inspect.unwrap(fn)).arg_model.model_validate(args)
        expected = {k: getattr(direct, k) for k in args}
    except Exception:
        expected = None
    try:
        got = srv.validated_args(fn, args)
    except ValueError:
        got = None
    assert got == expected


@pytest.mark.parametrize("call", [
    lambda: srv.launch("foot", wait_s=float("nan")),
    lambda: srv.wait_for("window_open", timeout_s=float("inf")),
    lambda: srv.pointer("move", x=float("nan"), y=1),
])
def test_a_number_that_is_not_finite_is_refused_before_acting(monkeypatch, call):
    # V73: pydantic let "nan" through and the clamps kept it, so launch had
    # exec'd the app when the wait raised
    acted = []
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.hyprctl, "dispatch", lambda *a: acted.append(a))
    monkeypatch.setattr(srv.hinput, "move", lambda *a: acted.append(a))
    with pytest.raises(ValueError, match="finite"):
        call()
    assert acted == []


def test_a_wait_step_that_is_not_finite_stops_the_sequence(monkeypatch, quiet_sequence):
    out = str(srv.sequence([{"op": "wait_for", "event": "window_open", "timeout_s": "nan"}],
                           then="none"))
    assert "finite" in out
