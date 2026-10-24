import json
import os

from hypruse import safety


def test_beacon_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    safety.init()
    path = tmp_path / "hypruse" / "state.json"
    state = json.loads(path.read_text())
    assert state["pid"] == os.getpid()
    assert state["started"] > 0

    safety.touch("pointer:click")
    state = json.loads(path.read_text())
    assert state["last_action"] == "pointer:click"
    assert state["last_ts"] >= state["started"]

    safety.shutdown()
    assert not path.exists()


def test_touch_without_init_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    safety.shutdown()  # ensure clean module state
    safety.touch("anything")
    assert not (tmp_path / "hypruse" / "state.json").exists()


def test_touch_sanitizes_agent_controlled_action(tmp_path, monkeypatch):
    # tool arguments reach touch() before validation, and consumers like the
    # Waybar module re-embed last_action in JSON they build: a crafted
    # action string must never carry quotes or structure into the beacon
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    safety.init()
    safety.touch('x","class":"idle","text":"')
    state = json.loads((tmp_path / "hypruse" / "state.json").read_text())
    assert state["last_action"] == "xclass:idletext:"
    safety.touch("a" * 200)
    state = json.loads((tmp_path / "hypruse" / "state.json").read_text())
    assert len(state["last_action"]) == 64
    safety.touch("pointer:click")  # the legitimate vocabulary passes untouched
    state = json.loads((tmp_path / "hypruse" / "state.json").read_text())
    assert state["last_action"] == "pointer:click"
    safety.shutdown()


def test_server_main_registers_drag_release_cleanup(tmp_path, monkeypatch):
    # the glue that arms the kill-switch-mid-drag cleanup: main() must
    # register input.release_held, or shutdown() releases nothing
    from hypruse import server as srv

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(srv.session, "ensure_session_env", lambda: None)
    monkeypatch.setattr(srv.mcp, "run", lambda: None)
    monkeypatch.setattr(srv.sys, "argv", ["hypruse"])

    class Pipe:
        def isatty(self):
            return False

    monkeypatch.setattr(srv.sys, "stdin", Pipe())
    srv.main()
    try:
        assert srv._release_and_sync in safety._cleanups  # releases, then tells the CLI state
    finally:
        safety.shutdown()  # never leave its cleanups for the next test


def test_the_servers_shutdown_releases_then_records_it(tmp_path, monkeypatch):
    # review of 1c9b161: cleanups run last in first out, and main() registered
    # the CLI-state sync after the release, so the sync ran first and the file
    # kept a hold the release then let go
    from hypruse import cli_state
    from hypruse import input as hinput
    from hypruse import server as srv

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "test")
    monkeypatch.setattr(srv.session, "ensure_session_env", lambda: None)
    monkeypatch.setattr(srv.mcp, "run", lambda: None)
    monkeypatch.setattr(srv.sys, "argv", ["hypruse"])

    class Pipe:
        def isatty(self):
            return False

    class Pointer:
        def button(self, button, state):
            pass

    monkeypatch.setattr(srv.sys, "stdin", Pipe())
    monkeypatch.setattr(hinput, "_vp", Pointer())
    monkeypatch.setattr(hinput, "_held_button", None)
    monkeypatch.setattr(hinput, "_down", None)
    cli_state.restore()
    cli_state.save()  # a CLI verb ran on this instance before
    srv.main()
    hinput.restore_held("left")  # a `pointer down` the server made, then the kill switch
    srv._sync_cli_hold()
    assert cli_state.load()["flags"]["held_button"] == "left"
    safety.shutdown()
    assert cli_state.load()["flags"]["held_button"] is None
    assert hinput.held_button() is None


def test_shutdown_runs_registered_cleanups(tmp_path, monkeypatch):
    # the SIGTERM kill switch runs shutdown(); a drag's held button must be
    # released by a registered cleanup before the process dies
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    calls = []
    safety.init()
    safety.on_shutdown(lambda: calls.append("released"))
    safety.on_shutdown(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    safety.shutdown()  # a failing cleanup must not block the others
    assert calls == ["released"]
    safety.shutdown()  # cleanups are drained: no double release
    assert calls == ["released"]


def test_a_server_holding_nothing_leaves_a_shells_hold_on_record(tmp_path, monkeypatch):
    # review of f1444c6: the sync on the way out ran whatever the server held,
    # so a server holding nothing erased a hold a shell `pointer down` recorded
    from hypruse import cli_state
    from hypruse import input as hinput
    from hypruse import server as srv

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "test")
    monkeypatch.setattr(hinput, "_held_button", None)
    monkeypatch.setattr(hinput, "_down", None)
    cli_state.restore()
    cli_state.set_flag("held_button", "left")  # the shell's own hold
    cli_state.save()
    srv._release_and_sync()
    assert cli_state.load()["flags"]["held_button"] == "left"
