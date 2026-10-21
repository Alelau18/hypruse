"""cli_state: what a one-shot CLI verb remembers between processes, and
what it must forget (another compositor's addresses, a broken file)."""

import json

import pytest

from hypruse import cli_state, hyprctl, trust
from hypruse import server as srv


@pytest.fixture(autouse=True)
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "sig-a")
    monkeypatch.delenv("HYPRUSE_CONFINE", raising=False)
    trust._owned.clear()
    monkeypatch.setattr(trust, "_seat", {"cursor": None, "active": None})
    monkeypatch.setattr(srv, "_last_marks", {})
    monkeypatch.setattr(cli_state, "_flags", {})
    yield
    trust._owned.clear()


MARKS = {"window": "0xaaa", "class": "kitty", "ts": 1234.5,
         "items": {1: {"dx": 10, "dy": 20, "label": "b 'Ok'"}}}


def _remember_everything():
    trust._owned.update({"0xaaa", "0xbbb"})
    trust._seat["cursor"] = (640, 480)
    trust._seat["active"] = "0xaaa"
    srv._last_marks = dict(MARKS)
    cli_state.set_flag("marking_installed", True)


def test_round_trip_restores_marks_owned_seat_and_flags():
    _remember_everything()
    cli_state.save()
    # a fresh process starts with nothing
    trust._owned.clear()
    trust._seat.update(cursor=None, active=None)
    srv._last_marks = {}
    cli_state._flags = {}

    cli_state.restore()
    assert trust.owned() == {"0xaaa", "0xbbb"}
    # a tuple again: guard_seat compares with != against hyprctl's tuple
    assert trust._seat == {"cursor": (640, 480), "active": "0xaaa"}
    # mark numbers come back as ints after the JSON round trip
    assert srv.marks_state() == MARKS
    assert cli_state.flag("marking_installed") is True


def test_another_compositor_instance_is_forgotten(monkeypatch):
    _remember_everything()
    cli_state.save()
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "sig-b")
    trust._owned.clear()
    cli_state.restore()
    # addresses are heap pointers in the OLD compositor: none survive
    assert trust.owned() == set()
    assert srv.marks_state() == {}
    assert trust._seat["cursor"] is None


def test_unreadable_or_foreign_files_mean_nothing_remembered():
    path = cli_state.path()
    path.write_text("{not json")
    assert cli_state.load() == {}
    path.write_text(json.dumps({"v": 99, "instance": "sig-a"}))
    assert cli_state.load() == {}
    path.write_text(json.dumps([1, 2, 3]))
    assert cli_state.load() == {}
    path.write_text(json.dumps({"v": 1, "instance": "sig-a", "trust": "junk", "marks": 5}))
    cli_state.restore()  # must not raise
    assert trust.owned() == set() and srv.marks_state() == {}


def test_launched_confinement_prunes_windows_that_are_gone(monkeypatch):
    trust._owned.update({"0xaaa", "0xdead"})
    cli_state.save()
    monkeypatch.setenv("HYPRUSE_CONFINE", "launched")
    monkeypatch.setattr(hyprctl, "query", lambda cmd: [{"address": "0xaaa"}])
    trust._owned.clear()
    cli_state.restore()
    # 0xdead can be handed to an unrelated window later; it is not ours any more
    assert trust.owned() == {"0xaaa"}


def test_pruning_keeps_the_set_when_the_window_list_is_unreadable(monkeypatch):
    trust._owned.update({"0xaaa"})
    cli_state.save()
    monkeypatch.setenv("HYPRUSE_CONFINE", "launched")

    def down(cmd):
        raise hyprctl.HyprctlError("socket gone")

    monkeypatch.setattr(hyprctl, "query", down)
    trust._owned.clear()
    cli_state.restore()
    assert trust.owned() == {"0xaaa"}  # the action itself will hit the same IPC


def test_save_survives_an_unwritable_directory(monkeypatch, tmp_path):
    _remember_everything()
    monkeypatch.setattr(cli_state, "path", lambda: tmp_path / "missing" / "cli-state.json")
    cli_state.save()  # must not raise: state is a convenience, the action already ran


def test_restore_marks_rejects_malformed_items():
    srv.restore_marks({"window": "0xaaa", "items": {"1": {"dx": 1}, "x": {"dx": 1, "dy": 2,
                                                                        "label": "l"}}})
    assert srv.marks_state() == {}
    srv.restore_marks({"window": "0xaaa", "items": {"2": {"dx": 1, "dy": 2, "label": "l"}}})
    assert srv.marks_state()["items"] == {2: {"dx": 1, "dy": 2, "label": "l"}}
    assert srv.marks_state()["ts"] == 0.0  # no timestamp recorded: expired, never fresh


def test_a_flag_this_process_did_not_change_keeps_the_files_value():
    # a long wait_for restored the flags, a `pointer up` cleared the hold
    # meanwhile; the wait's save must not write the stale hold back
    cli_state.set_flag("held_button", "left")
    cli_state.save()
    cli_state.restore()  # the waiting verb starts
    on_disk = json.loads(cli_state.path().read_text())
    on_disk["flags"]["held_button"] = None  # another verb released it
    cli_state.path().write_text(json.dumps(on_disk))
    cli_state.set_flag("known_clients", ["0xa"])  # what the wait itself changed
    cli_state.save()
    flags = json.loads(cli_state.path().read_text())["flags"]
    assert flags["held_button"] is None and flags["known_clients"] == ["0xa"]


def test_each_compositor_instance_keeps_its_own_file(monkeypatch):
    _remember_everything()
    cli_state.save()
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "sig-b")
    cli_state._flags = {}
    cli_state.restore()
    cli_state.set_flag("marking_installed", False)
    cli_state.save()  # a verb under the other instance writes its own file
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "sig-a")
    cli_state.restore()
    assert cli_state.flag("marking_installed") is True  # sig-a's state survived
    assert srv.marks_state() == MARKS


def test_the_file_name_is_sanitised(monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "../../etc/x y")
    assert cli_state.path().name == "cli-state-_.._etc_x_y.json"
    assert cli_state.path().parent.name == "hypruse"
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "")
    assert cli_state.path().name == "cli-state-none.json"


def test_known_windows_from_two_verbs_are_merged():
    cli_state.set_flag("known_clients", ["0xa"])
    cli_state.save()
    cli_state.restore()  # this verb starts
    on_disk = json.loads(cli_state.path().read_text())
    on_disk["flags"]["known_clients"] = ["0xa", "0xb"]  # another verb saw 0xb meanwhile
    cli_state.path().write_text(json.dumps(on_disk))
    cli_state.set_flag("known_clients", ["0xa", "0xc"])  # this one saw 0xc
    cli_state.save()
    assert json.loads(cli_state.path().read_text())["flags"]["known_clients"] == [
        "0xa", "0xb", "0xc"]
