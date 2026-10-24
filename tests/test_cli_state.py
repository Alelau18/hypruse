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


MARKS = {"window": "0xaaa", "key": "0xaaa#1", "class": "kitty", "ts": 1234.5,
         "items": {1: {"dx": 10, "dy": 20, "label": "b 'Ok'"}}}


# what trust records: hyprctl.window_key, an address plus its stableId
AAA, BBB, DEAD = "0xaaa#1", "0xbbb#2", "0xdead#3"


def _remember_everything():
    trust._owned.update({AAA, BBB})
    trust._seat["cursor"] = (640, 480)
    trust._seat["active"] = AAA
    srv._last_marks = dict(MARKS)
    cli_state.set_flag("marking_installed", True)


def test_round_trip_restores_marks_owned_seat_and_flags(monkeypatch):
    _remember_everything()
    cli_state.save()
    # a fresh process starts with nothing
    trust._owned.clear()
    trust._seat.update(cursor=None, active=None)
    srv._last_marks = {}
    cli_state._flags = {}

    live = [{"address": "0xaaa", "stableId": "1"}, {"address": "0xbbb", "stableId": "2"}]
    monkeypatch.setattr(hyprctl, "query", lambda cmd: live)
    cli_state.restore()
    assert trust.owned() == {AAA, BBB}
    # a tuple again: guard_seat compares with != against hyprctl's tuple
    assert trust._seat == {"cursor": (640, 480), "active": AAA}
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


def test_the_owned_set_drops_windows_that_are_gone(monkeypatch):
    # every consumer reads it, not only `launched` confinement (V51)
    trust._owned.update({AAA, DEAD})
    cli_state.save()
    monkeypatch.setattr(hyprctl, "query", lambda cmd: [{"address": "0xaaa", "stableId": "1"}])
    trust._owned.clear()
    cli_state.restore()
    assert trust.owned() == {AAA}


def test_a_window_at_a_recycled_address_is_not_owned(monkeypatch):
    # F6/F17/F18/F28: Hyprland hands a freed window's address to the next one
    trust._owned.update({AAA})
    cli_state.save()
    monkeypatch.setenv("HYPRUSE_CONFINE", "launched")
    human = {"address": "0xaaa", "stableId": "77", "class": "kitty", "pid": 9}
    monkeypatch.setattr(hyprctl, "query", lambda cmd: [human])
    trust._owned.clear()
    cli_state.restore()
    assert trust.owned() == set()
    with pytest.raises(trust.TrustError, match="confinement scope"):
        trust.guard_client(human)


def test_a_bare_address_from_an_older_hypruse_owns_nothing(monkeypatch):
    path = cli_state.path()
    path.write_text(json.dumps({"v": 1, "instance": "sig-a", "trust": {
        "owned": ["0xaaa"], "seat": {"cursor": [1, 2], "active": "0xaaa"}}}))
    monkeypatch.setattr(hyprctl, "query", lambda cmd: [{"address": "0xaaa", "stableId": "1"}])
    cli_state.restore()
    assert trust.owned() == set() and trust._seat["active"] is None


def test_pruning_keeps_the_set_when_the_window_list_is_unreadable(monkeypatch):
    trust._owned.update({AAA})
    cli_state.save()
    monkeypatch.setenv("HYPRUSE_CONFINE", "launched")

    def down(cmd):
        raise hyprctl.HyprctlError("socket gone")

    monkeypatch.setattr(hyprctl, "query", down)
    trust._owned.clear()
    cli_state.restore()
    assert trust.owned() == {AAA}  # the action itself will hit the same IPC


def test_save_survives_an_unwritable_directory(monkeypatch, tmp_path):
    _remember_everything()
    monkeypatch.setattr(cli_state, "path", lambda: tmp_path / "missing" / "cli-state.json")
    cli_state.save()  # must not raise: state is a convenience, the action already ran


def test_restore_marks_rejects_malformed_items():
    srv.restore_marks({"window": "0xaaa", "key": "0xaaa#1", "items": {
        "1": {"dx": 1}, "x": {"dx": 1, "dy": 2, "label": "l"}}})
    assert srv.marks_state() == {}
    # an older hypruse's record has no window key: it cannot be tied to the
    # window at that address now
    srv.restore_marks({"window": "0xaaa", "items": {"2": {"dx": 1, "dy": 2, "label": "l"}}})
    assert srv.marks_state() == {}
    srv.restore_marks({"window": "0xaaa", "key": "0xaaa#1",
                       "items": {"2": {"dx": 1, "dy": 2, "label": "l"}}})
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
    cli_state.set_flag("known_clients", ["0xa#1"])  # what the wait itself changed
    cli_state.save()
    flags = json.loads(cli_state.path().read_text())["flags"]
    assert flags["held_button"] is None and flags["known_clients"] == ["0xa#1"]


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
    cli_state.set_flag("known_clients", ["0xa#1"])
    cli_state.save()
    cli_state.restore()  # this verb starts
    on_disk = json.loads(cli_state.path().read_text())
    on_disk["flags"]["known_clients"] = ["0xa#1", "0xb#2"]  # another verb saw 0xb meanwhile
    cli_state.path().write_text(json.dumps(on_disk))
    cli_state.set_flag("known_clients", ["0xa#1", "0xc#3"])  # this one saw 0xc
    cli_state.save()
    assert json.loads(cli_state.path().read_text())["flags"]["known_clients"] == [
        "0xa#1", "0xb#2", "0xc#3"]


def test_an_older_hypruses_bare_addresses_do_not_survive_a_merge():
    # a list mixing them in would restore as "nothing known" on every verb
    on_disk = {"v": 1, "instance": "sig-a", "trust": {"owned": ["0xold", "0xa#1"]},
               "flags": {"known_clients": ["0xold", "0xa#1"]}}
    cli_state.path().write_text(json.dumps(on_disk))
    cli_state.restore()
    cli_state.set_flag("known_clients", ["0xb#2"])
    cli_state.save()
    saved = json.loads(cli_state.path().read_text())
    assert saved["flags"]["known_clients"] == ["0xa#1", "0xb#2"]
    assert "0xold" not in saved["trust"]["owned"]


def test_a_closed_window_leaves_the_file_too(monkeypatch):
    # review of C6: the merge re-unioned the file's dead keys on every save
    trust._owned.update({AAA, DEAD})
    cli_state.save()
    monkeypatch.setattr(hyprctl, "query", lambda cmd: [{"address": "0xaaa", "stableId": "1"}])
    trust._owned.clear()
    cli_state.restore()
    cli_state.save()
    assert json.loads(cli_state.path().read_text())["trust"]["owned"] == [AAA]


def test_a_stop_clears_the_pending_launches_in_the_file(monkeypatch):
    # V68: no window a timed-out launch opens is adopted after the panic key
    from hypruse import cli, session

    monkeypatch.setattr(session, "ensure_session_env", lambda: None)
    trust._launches.clear()
    trust.note_launch("tok", pending=True)
    cli_state.save()
    trust._launches.clear()
    cli._clear_pending_launches()
    saved = json.loads(cli_state.path().read_text())["trust"]["launches"]
    assert saved and all(e["pending_until"] == 0.0 for e in saved.values())
    trust._launches.clear()
