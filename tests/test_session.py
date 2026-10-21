import os

import pytest

from hypruse import session

DEAD_PID = 2**22 + 12345  # above the default pid_max, so never a live process


def make_instance(runtime, sig, pid=None, wl="wayland-1", socket=True):
    d = runtime / "hypr" / sig
    d.mkdir(parents=True)
    if socket:
        (d / ".socket.sock").touch()
    (d / "hyprland.lock").write_text(f"{pid or os.getpid()}\n{wl}\n")
    return d


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    return tmp_path


def test_discovers_both_from_one_instance(runtime):
    make_instance(runtime, "abc_1700000000_1", wl="wayland-1")
    (runtime / "wayland-1").touch()
    (runtime / "wayland-1.lock").touch()
    session.ensure_session_env()
    assert os.environ["HYPRLAND_INSTANCE_SIGNATURE"] == "abc_1700000000_1"
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-1"


def test_a_dead_instance_is_not_live_even_with_its_socket_file(runtime):
    # a crashed compositor leaves its directory, socket file and lock behind
    make_instance(runtime, "dead_1800000000_1", pid=DEAD_PID, wl="wayland-2")
    make_instance(runtime, "live_1700000000_1", wl="wayland-10")
    session.ensure_session_env()
    assert os.environ["HYPRLAND_INSTANCE_SIGNATURE"] == "live_1700000000_1"
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-10"  # its own, not wayland-2


def test_the_newest_live_instance_wins_and_brings_its_own_socket(runtime):
    make_instance(runtime, "old_1700000000_1", wl="wayland-2")
    make_instance(runtime, "new_1700000500_1", wl="wayland-10")
    session.ensure_session_env()
    assert os.environ["HYPRLAND_INSTANCE_SIGNATURE"] == "new_1700000500_1"
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-10"  # not wayland-10 < wayland-2 by name


def test_hyprctl_instances_is_preferred(runtime, monkeypatch):
    make_instance(runtime, "scanned_1700000000_1", wl="wayland-1")
    monkeypatch.setattr(session, "_hyprctl_instances", lambda: [
        {"instance": "a_1_1", "time": 10, "pid": 1, "wl_socket": "wayland-3"},
        {"instance": "b_2_2", "time": 20, "pid": 2, "wl_socket": "wayland-4"},
    ])
    session.ensure_session_env()
    assert os.environ["HYPRLAND_INSTANCE_SIGNATURE"] == "b_2_2"
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-4"


def test_a_set_signature_gets_its_own_instances_socket(runtime, monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "a_1_1")
    monkeypatch.setattr(session, "_hyprctl_instances", lambda: [
        {"instance": "a_1_1", "time": 10, "wl_socket": "wayland-3"},
        {"instance": "b_2_2", "time": 20, "wl_socket": "wayland-4"},
    ])
    session.ensure_session_env()
    assert os.environ["HYPRLAND_INSTANCE_SIGNATURE"] == "a_1_1"
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-3"


def test_an_unknown_signature_takes_a_socket_only_when_there_is_one(runtime, monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "gone")
    (runtime / "wayland-1").touch()
    (runtime / "wayland-2").touch()
    session.ensure_session_env()
    assert "WAYLAND_DISPLAY" not in os.environ  # which one is a guess
    (runtime / "wayland-2").unlink()
    session.ensure_session_env()
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-1"


def test_never_overrides_existing_env(runtime, monkeypatch):
    make_instance(runtime, "discovered_1_1")
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "explicit")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-7")
    session.ensure_session_env()
    assert os.environ["HYPRLAND_INSTANCE_SIGNATURE"] == "explicit"
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-7"


def test_graceful_when_nothing_to_discover(runtime):
    session.ensure_session_env()  # must not raise
    assert "HYPRLAND_INSTANCE_SIGNATURE" not in os.environ
    assert "WAYLAND_DISPLAY" not in os.environ


def test_a_lock_without_a_socket_or_with_junk_is_skipped(runtime):
    make_instance(runtime, "nosock_1_1", socket=False)
    junk = runtime / "hypr" / "junk_2_2"
    junk.mkdir(parents=True)
    (junk / ".socket.sock").touch()
    (junk / "hyprland.lock").write_text("not a pid\n")
    assert session._scanned_instances(runtime) == []
