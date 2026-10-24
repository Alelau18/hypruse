"""Window selectors: class:RE, title:RE and pid:N wherever an address goes.

A window's address dies with the window, so a game that restarts has a new
one; agents re-found it with shell loops over `hyprctl clients`. One
resolver answers for every tool, and the properties that matter are that
it never guesses: one match is used, several or none refuse with what the
agent needs to choose."""

import pytest

from hypruse import hyprctl
from hypruse import screenshot as shot
from hypruse import server as srv

SF6 = {"address": "0x55a1", "class": "streetfighter6.exe", "title": "Street Fighter 6",
       "pid": 4242, "mapped": True, "workspace": {"id": 3, "name": "3"},
       "at": [0, 0], "size": [1920, 1080]}
KITTY1 = {"address": "0xa1", "class": "kitty", "title": "~", "pid": 10, "mapped": True,
          "workspace": {"id": 1, "name": "1"}, "at": [0, 0], "size": [800, 600]}
KITTY2 = dict(KITTY1, address="0xa2", title="htop", pid=11, workspace={"id": 2, "name": "2"})
CLIENTS = [SF6, KITTY1, KITTY2]


# --- the resolver ---------------------------------------------------------------


def test_an_address_is_matched_exactly():
    assert hyprctl.select_client("0xa2", CLIENTS) is KITTY2
    with pytest.raises(hyprctl.WindowSelectError, match="'0xdead' not found.*class:REGEX"):
        hyprctl.select_client("0xdead", CLIENTS)


def test_class_title_and_pid_name_one_window():
    assert hyprctl.select_client("class:streetfighter", CLIENTS) is SF6
    assert hyprctl.select_client("class:^StreetFighter6\\.exe$", CLIENTS) is SF6  # any case
    assert hyprctl.select_client("title:htop", CLIENTS) is KITTY2
    assert hyprctl.select_client("pid:4242", CLIENTS) is SF6
    assert hyprctl.select_client("  CLASS:street", CLIENTS) is SF6


def test_several_matches_refuse_with_the_candidates():
    with pytest.raises(hyprctl.WindowSelectError) as e:
        hyprctl.select_client("class:kitty", CLIENTS)
    msg = str(e.value)
    assert "matches 2 windows" in msg
    assert '0xa1 kitty "~" ws 1' in msg and '0xa2 kitty "htop" ws 2' in msg


def test_no_match_lists_windows_of_a_similar_class():
    relaunched = dict(SF6, address="0x55b2", **{"class": "StreetFighter6"})
    with pytest.raises(hyprctl.WindowSelectError, match="similar class: 0x55b2"):
        hyprctl.select_client("class:streetfighter6.exe", [relaunched, KITTY1])


def test_no_match_and_nothing_similar_says_so():
    with pytest.raises(hyprctl.WindowSelectError, match="no window matches 'title:inbox'.*desktop"):
        hyprctl.select_client("title:inbox", CLIENTS)


def test_an_unmapped_window_is_not_a_match():
    assert hyprctl.select_client("class:kitty", [KITTY1, dict(KITTY2, mapped=False)]) is KITTY1


@pytest.mark.parametrize(("sel", "msg"), [
    ("class:(", "bad selector"), ("pid:abc", "takes a number"), ("title:", "empty selector")])
def test_bad_selectors_refuse(sel, msg):
    with pytest.raises(hyprctl.WindowSelectError, match=msg):
        hyprctl.select_client(sel, CLIENTS)


def test_a_selector_error_is_a_value_error():
    # the CLI maps ValueError to exit 1 and the tools surface it as is
    assert issubclass(hyprctl.WindowSelectError, ValueError)


def test_the_screenshot_lookup_takes_a_selector():
    assert shot._find_window("class:street", CLIENTS, None) is SF6
    with pytest.raises(shot.CaptureRefused, match="matches 2 windows"):
        shot._find_window("class:kitty", CLIENTS, None)


# --- the tools resolve once, at the start of the call ---------------------------------


@pytest.fixture
def desk(monkeypatch):
    sent = []
    active = [KITTY1]
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.hyprctl, "query",
                        lambda cmd: CLIENTS if cmd == "clients" else active[0])

    def dispatch(*a):
        sent.append(a)
        if a[0] == "focuswindow":  # the compositor moves the focus there
            active[0] = next(c for c in CLIENTS if f"address:{c['address']}" == a[1])

    monkeypatch.setattr(srv.hyprctl, "dispatch", dispatch)
    monkeypatch.setattr(srv.trust, "guard_window", lambda t: None)
    monkeypatch.setattr(srv.trust, "remember_seat", lambda: None)
    monkeypatch.setattr(srv.time, "sleep", lambda s: None)
    return sent


def test_hypr_focuses_the_window_a_selector_names(desk):
    out = srv.hypr("focus_window", target="class:streetfighter")
    assert desk == [("focuswindow", "address:0x55a1")]
    assert out == "focused 0x55a1"


def test_an_ambiguous_selector_acts_on_nothing(desk):
    with pytest.raises(ValueError, match="matches 2 windows"):
        srv.hypr("close_window", target="class:kitty")
    assert desk == []


def test_screenshot_and_zoom_capture_the_selected_window(desk, monkeypatch):
    seen = []
    monkeypatch.setattr(srv, "_deliver_capture",
                        lambda window="", region="", **k: seen.append(window) or [])
    srv.screenshot(window="title:street")
    assert seen == ["0x55a1"]
    monkeypatch.setattr(srv.shot, "zoom_region",
                        lambda x, y, size, window: seen.append(window) or (0, 0, 10, 10))
    srv.zoom(5, 5, window="pid:4242")
    assert seen[1] == "0x55a1"  # the box is clamped to the selected window


def test_keyboard_focuses_and_reports_the_selected_window(desk, monkeypatch):
    typed = []
    monkeypatch.setattr(srv.hinput, "type_text", lambda t: typed.append(t))
    monkeypatch.setattr(srv.trust, "guard_session_lock", lambda *a: "")
    monkeypatch.setattr(srv.trust, "guard_keyboard_layer", lambda *a: "")
    out = srv.keyboard("type", text="hi", window="title:^htop$")
    assert ("focuswindow", "address:0xa2") in desk and typed == ["hi"]
    assert out == "typed 2 characters into 0xa2"


def test_ui_reads_the_selected_window(desk, monkeypatch):
    seen = []
    monkeypatch.setattr(srv, "_ui_read", lambda window, *a, **k: seen.append(window) or [])
    srv.ui(window="class:street")
    assert seen == ["0x55a1"]


def test_a_sequence_excuses_its_own_move_of_a_selected_window(desk, monkeypatch):
    # the move's event names the address, so the step's selector is resolved
    # for the expected-change check too, or the sequence would stop on itself
    class Stream:
        def __init__(self):
            self.batches = [[("movewindow", {"address": "0x55a1", "workspace": "9"})]]

        def drain(self, settle=0.08):
            return self.batches.pop(0) if self.batches else []

        def close(self):
            pass

    monkeypatch.setattr(srv.events, "EventStream", Stream)
    monkeypatch.setattr(srv, "_dispatch_step", lambda step: f"{step['op']} ok")
    steps = [{"op": "hypr", "action": "move_window", "target": "class:street", "workspace": "9"},
             {"op": "pointer", "action": "click", "x": 1, "y": 1}]
    out = srv.sequence(steps, then="none")
    assert "all 2/2" in out


# V44: a selector names whichever window claims that class or title; when it
# picks one the agent never saw, every acting tool says so, in one wording

UNSEEN = ("a window that was not there at the agent's previous call: its class and title are "
          "the app's own claim")


@pytest.fixture
def unseen_desk(desk, monkeypatch):
    from conftest import REAL_NOTE_CLIENTS

    monkeypatch.setattr(srv, "_known_clients", set())  # the agent has seen nothing
    monkeypatch.setattr(srv, "_note_clients", lambda *a: None)
    monkeypatch.setattr(srv.hinput, "type_text", lambda t: None)
    monkeypatch.setattr(srv.trust, "guard_session_lock", lambda *a: "")
    monkeypatch.setattr(srv.trust, "guard_keyboard_layer", lambda *a: "")
    return REAL_NOTE_CLIENTS


def test_every_acting_tool_says_its_selector_picked_an_unseen_window(unseen_desk):
    outs = [srv.keyboard("type", text="hi", window="title:^htop$"),
            srv.hypr("focus_window", target="class:streetfighter")]
    assert all(UNSEEN in str(o) for o in outs)
    assert "title:^htop$ matched 0xa2" in str(outs[0])


def test_a_window_the_agent_saw_or_launched_has_no_note(unseen_desk, monkeypatch):
    unseen_desk()  # the agent looked at the desktop
    assert UNSEEN not in str(srv.hypr("focus_window", target="class:streetfighter"))


def test_a_note_never_reaches_a_later_call(unseen_desk, monkeypatch):
    monkeypatch.setattr(srv.trust, "guard_window", lambda t: (_ for _ in ()).throw(
        srv.trust.TrustError("outside the scope")))
    with pytest.raises(srv.trust.TrustError):
        srv.hypr("close_window", target="class:streetfighter")  # set the note, then refused
    monkeypatch.setattr(srv.trust, "guard_window", lambda t: None)
    assert UNSEEN not in str(srv.hypr("workspace", workspace="2"))


# review of bb1ad8c: with the real refresh, every call starts by marking the
# open windows as seen, so the note must be judged against the windows the
# agent knew BEFORE the call (a sequence refreshes before its steps resolve)


@pytest.fixture
def decoy(desk, monkeypatch):
    from conftest import REAL_NOTE_CLIENTS

    monkeypatch.setattr(srv, "_note_clients", REAL_NOTE_CLIENTS)
    # the agent's previous call saw Street Fighter and the first kitty; the
    # htop window and its "class" appeared since
    monkeypatch.setattr(srv, "_known_clients", {srv.hyprctl.window_key(c) for c in (SF6, KITTY1)})
    monkeypatch.setattr(srv.hinput, "type_text", lambda t: None)
    monkeypatch.setattr(srv.trust, "guard_session_lock", lambda *a: "")
    monkeypatch.setattr(srv.trust, "guard_keyboard_layer", lambda *a: "")
    return desk


def test_a_standalone_call_notes_a_new_window_once(decoy):
    assert UNSEEN in str(srv.keyboard("type", text="hi", window="title:^htop$"))
    assert UNSEEN not in str(srv.keyboard("type", text="hi", window="title:^htop$"))


def test_a_sequence_notes_a_new_window_once(decoy):
    steps = [{"op": "keyboard", "action": "type", "text": "hi", "window": "title:^htop$"},
             {"op": "hypr", "action": "focus_window", "target": "title:^htop$"}]
    first = str(srv.sequence(steps, stop_on_change=False, then="none"))
    assert first.count(UNSEEN) == 2  # the keyboard step and the hypr step
    second = str(srv.sequence(steps, stop_on_change=False, then="none"))
    assert UNSEEN not in second


def test_a_step_refused_after_its_selector_resolved_still_notes_it(decoy, monkeypatch):
    monkeypatch.setattr(srv.trust, "guard_window", lambda t: (_ for _ in ()).throw(
        srv.trust.TrustError("outside the scope")))
    out = str(srv.sequence([{"op": "hypr", "action": "close_window", "target": "title:^htop$"}],
                           stop_on_change=False, then="none"))
    assert "ERROR" in out and UNSEEN in out
