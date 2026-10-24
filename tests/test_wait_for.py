"""wait_for: level-triggered pre-checks vs edge-triggered waits."""

import pytest

from hypruse import server as srv


class OneShotStream:
    def __init__(self, hit):
        self.hit = hit
        self.calls = []

    def wait_for(self, names, matcher, timeout):
        self.calls.append((set(names), timeout))
        return self.hit

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def test_unfiltered_workspace_wait_blocks_for_next_switch(monkeypatch):
    # 'the next workspace switch, whatever it is' must not be answered
    # instantly with the CURRENT workspace: that wait would never wait
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: {"name": "3"})
    stream = OneShotStream(("workspace", {"name": "4"}))
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    out = srv.wait_for("workspace")
    assert out == {"event": "workspace", "name": "4"}
    assert len(stream.calls) == 1  # it actually subscribed and waited


def test_filtered_workspace_wait_already_active(monkeypatch):
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: {"name": "mail"})

    def boom():
        raise AssertionError("must not subscribe when already satisfied")

    monkeypatch.setattr(srv.events, "EventStream", boom)
    out = srv.wait_for("workspace", match="mail")
    assert out.get("already") is True


def test_window_close_already_gone(monkeypatch):
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: [])

    def boom():
        raise AssertionError("must not subscribe when already satisfied")

    monkeypatch.setattr(srv.events, "EventStream", boom)
    out = srv.wait_for("window_close", match="gedit")
    assert out.get("already") is True


LAYERS_RAW = {
    "DP-1": {"levels": {"3": [{"namespace": "rofi", "x": 560, "y": 300, "w": 800, "h": 480}]}}
}


def test_layer_open_already_mapped(monkeypatch):
    # the classic late subscribe: use_bind returns AFTER the launcher maps,
    # so openlayer fired before wait_for could connect; the mapped surface
    # must satisfy the wait instead of timing out forever
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: LAYERS_RAW)

    def boom():
        raise AssertionError("must not subscribe when already satisfied")

    monkeypatch.setattr(srv.events, "EventStream", boom)
    out = srv.wait_for("layer_open", match="rofi")
    assert out == {"event": "openlayer", "namespace": "rofi", "already": True}


def test_layer_open_not_yet_mapped_subscribes(monkeypatch):
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: {})
    stream = OneShotStream(("openlayer", {"namespace": "rofi"}))
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    out = srv.wait_for("layer_open", match="rofi")
    assert out == {"event": "openlayer", "namespace": "rofi"}
    assert len(stream.calls) == 1  # it actually subscribed and waited


@pytest.mark.parametrize("layers_state", [{}, LAYERS_RAW])
def test_layer_close_never_answers_from_state(monkeypatch, layers_state):
    # 'nothing matching is mapped' also describes a layer whose trigger
    # fired but whose surface has not mapped yet, so layer_close must
    # always subscribe and wait for the real event, whatever the state
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: layers_state)
    stream = OneShotStream(("closelayer", {"namespace": "rofi"}))
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    out = srv.wait_for("layer_close", match="rofi")
    assert out == {"event": "closelayer", "namespace": "rofi"}
    assert len(stream.calls) == 1  # subscribed, did not answer from state


def test_unfiltered_layer_open_still_waits(monkeypatch):
    # some layer (a bar) is always mapped: an unfiltered wait means 'the
    # NEXT layer, whatever it is' and must not answer from current state
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: LAYERS_RAW)
    stream = OneShotStream(("openlayer", {"namespace": "mako"}))
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    out = srv.wait_for("layer_open")
    assert out == {"event": "openlayer", "namespace": "mako"}
    assert len(stream.calls) == 1


def test_title_change_waits_on_v2_only(monkeypatch):
    # plain windowtitle carries only an address, so it can never satisfy a
    # title match; it must not be in the wait set at all
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    stream = OneShotStream(("windowtitlev2", {"address": "0xa", "title": "Inbox (3)"}))
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    out = srv.wait_for("title_change", match="inbox")
    assert out["title"] == "Inbox (3)"
    assert stream.calls[0][0] == {"windowtitlev2"}


def test_layer_open_precheck_matches_by_substring(monkeypatch):
    # real launcher namespaces are rarely bare words (wofi-dialog,
    # swaync-notification-window), so the pre-check must be a substring
    # match, not exact, or it fails to rescue the late-subscribe case
    raw = {"DP-1": {"levels": {"3": [
        {"namespace": "wofi-dialog", "x": 0, "y": 0, "w": 8, "h": 8}]}}}
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: raw)

    def boom():
        raise AssertionError("must not subscribe when already satisfied")

    monkeypatch.setattr(srv.events, "EventStream", boom)
    out = srv.wait_for("layer_open", match="wofi")  # substring of 'wofi-dialog'
    assert out.get("already") is True


def test_layer_open_wait_listens_only_for_openlayer(monkeypatch):
    # widening the names set to also accept closelayer would let a launcher
    # CLOSING satisfy a wait for it OPENING
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.hyprctl, "query", lambda cmd: {})
    stream = OneShotStream(("openlayer", {"namespace": "rofi"}))
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    srv.wait_for("layer_open", match="rofi")
    assert stream.calls[0][0] == {"openlayer"}


# Hyprland 0.57 (hyprwm/Hyprland#16234): workspace events name a workspace
# by its address, which is its number for a numbered workspace, while
# hyprctl JSON's "name" is its display name ("web" after a rename)
WEB_057 = {"address": "2", "id": 2, "type": "normal", "name": "web"}


def test_a_renamed_workspace_matches_by_its_display_name_in_events(monkeypatch):
    monkeypatch.setattr(srv.hyprctl, "query",
                        lambda what: [WEB_057] if what == "workspaces" else {})
    matches = srv._event_matcher("web")
    assert matches("workspace", {"name": "2"})
    opened = {"address": "0xa", "workspace": "2", "class": "foot", "title": "t"}
    assert matches("openwindow", opened)
    assert not matches("workspace", {"name": "3"})


def test_the_already_active_check_knows_both_names(monkeypatch):
    monkeypatch.setattr(srv.hyprctl, "query", lambda what: WEB_057)
    assert srv._already_satisfied("workspace", "web")["already"] is True
    assert srv._already_satisfied("workspace", "2")["already"] is True


def test_a_sequence_excuses_its_own_switch_under_either_name():
    assert srv._workspace_signatures(WEB_057) == {"workspace:web", "workspace:2"}
    assert srv._workspace_signatures({"id": 2, "name": "2"}) == {"workspace:2"}  # 0.56


def test_a_digit_filter_does_not_match_a_named_workspace_on_056(monkeypatch):
    # 0.56 gives a named workspace a negative id (-1337); its digits are not
    # a name anyone filtered on
    named = {"id": -1337, "name": "web"}
    renamed = {"id": 2, "name": "mail"}
    monkeypatch.setattr(srv.hyprctl, "query",
                        lambda what: [named, renamed] if what == "workspaces" else {})
    matches = srv._event_matcher("3")
    assert not matches("workspace", {"name": "web"})
    # a renamed numbered workspace still matches its number
    assert srv._event_matcher("2")("workspace", {"name": "mail"})


# --- window_open: a window that mapped before the wait subscribed ------------


class ScriptedStream:
    """Feeds scripted events through the wait's own matcher, the way the
    real stream does, so a matcher that cannot match is caught."""

    def __init__(self, events):
        self.events = list(events)
        self.calls = 0

    def wait_for(self, names, matcher, timeout):
        self.calls += 1
        for n, p in self.events:
            if n in names and (matcher is None or matcher(n, p)):
                return n, p
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


KITTY = {"address": "0xA", "class": "kitty", "title": "~", "mapped": True,
         "workspace": {"id": 1, "name": "1"}}
LG = {"address": "0xB", "class": "lg-remote", "title": "LG Remote", "mapped": True,
      "workspace": {"id": 2, "name": "2"}}


@pytest.fixture
def clients(monkeypatch):
    from conftest import REAL_NOTE_CLIENTS

    monkeypatch.setattr(srv, "_note_clients", REAL_NOTE_CLIENTS)
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    state = {"clients": [KITTY]}
    monkeypatch.setattr(srv.hyprctl, "query",
                        lambda cmd: state["clients"] if cmd == "clients" else {})
    return state


def _no_stream(monkeypatch):
    def boom():
        raise AssertionError("must not subscribe when already satisfied")

    monkeypatch.setattr(srv.events, "EventStream", boom)


def test_a_window_that_opened_since_the_last_action_is_already_there(clients, monkeypatch):
    srv._note_clients()  # what an acting tool does when it starts
    clients["clients"] = [KITTY, LG]  # the launch mapped before wait_for ran
    _no_stream(monkeypatch)
    out = srv.wait_for("window_open", match="lg-remote")
    assert out == {"event": "openwindow", "address": "0xB", "workspace": "2",
                   "class": "lg-remote", "title": "LG Remote", "launched_by_agent": False,
                   "already": True}


def test_it_is_reported_once_then_the_next_one_is_waited_for(clients, monkeypatch):
    srv._note_clients()
    clients["clients"] = [KITTY, LG]
    _no_stream(monkeypatch)
    srv.wait_for("window_open", match="lg")
    stream = ScriptedStream([("openwindow", {"address": "0xC", "class": "lg-remote"})])
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    assert srv.wait_for("window_open", match="lg")["address"] == "0xC"
    assert stream.calls == 1


def test_a_window_the_agent_already_saw_is_not_new(clients, monkeypatch):
    clients["clients"] = [KITTY, LG]
    monkeypatch.setattr(srv.hyprctl, "snapshot", lambda: {"windows": [KITTY, LG]})
    monkeypatch.setattr(srv.trust, "remember_seat", lambda: None)
    srv.desktop()  # LG is in the snapshot the agent read
    stream = ScriptedStream([])
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    assert srv.wait_for("window_open", match="lg").startswith("timeout")
    assert stream.calls == 1


def test_nothing_known_yet_means_no_precheck(clients, monkeypatch):
    # before any refresh every window would look new
    clients["clients"] = [KITTY, LG]
    stream = ScriptedStream([])
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    srv.wait_for("window_open", match="lg", timeout_s=1)
    assert stream.calls == 1


@pytest.mark.parametrize("match", ["", "nomatch"])
def test_unfiltered_or_unmatched_window_open_still_waits(clients, monkeypatch, match):
    srv._note_clients()
    clients["clients"] = [KITTY, LG]
    stream = ScriptedStream([])
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    srv.wait_for("window_open", match=match, timeout_s=1)
    assert stream.calls == 1


def test_an_unmapped_window_is_not_open_yet(clients, monkeypatch):
    srv._note_clients()
    clients["clients"] = [KITTY, dict(LG, mapped=False)]
    stream = ScriptedStream([])
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    srv.wait_for("window_open", match="lg", timeout_s=1)
    assert stream.calls == 1


def test_acting_tools_refresh_the_known_windows(clients, monkeypatch):
    monkeypatch.setattr(srv.hyprctl, "dispatch", lambda *a: None)
    monkeypatch.setattr(srv.trust, "remember_seat", lambda: None)
    srv.hypr("workspace", workspace="3")
    assert srv.known_clients_state() == [srv.hyprctl.window_key(KITTY)]


def test_a_new_window_at_a_closed_ones_address_is_new(clients, monkeypatch):
    # V47/V50: Hyprland hands a freed window's address to the next window;
    # keyed on the address alone, the new one counted as already seen
    srv._note_clients()
    reborn = dict(LG, address=KITTY["address"], stableId="later")
    clients["clients"] = [reborn]
    _no_stream(monkeypatch)
    assert srv.wait_for("window_open", match="lg-remote")["already"] is True


def test_a_known_list_of_bare_addresses_means_none_known():
    # written by an older hypruse: it says nothing about the windows now
    srv.restore_known_clients(["0xa", "0xb"])
    assert srv.known_clients_state() is None
    srv.restore_known_clients(["0xa#1"])
    assert srv.known_clients_state() == ["0xa#1"]


# --- window_close: the event carries only the address -----------------------------


DIALOG = {"address": "0xD", "class": "chromium", "title": "Open File", "mapped": True,
          "workspace": {"id": 1, "name": "1"}}


def test_a_close_filtered_by_title_matches_the_windows_address(clients, monkeypatch):
    clients["clients"] = [KITTY, DIALOG]
    stream = ScriptedStream([("closewindow", {"address": "0xa"}),  # another window
                             ("closewindow", {"address": "0xd"})])
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    out = srv.wait_for("window_close", match="open file")
    assert out == {"event": "closewindow", "address": "0xd"}


def test_a_close_in_the_gap_before_subscribing_is_already(clients, monkeypatch):
    clients["clients"] = [KITTY, DIALOG]

    def subscribe():
        clients["clients"] = [KITTY]  # it closed while the socket connected
        return ScriptedStream([])

    monkeypatch.setattr(srv.events, "EventStream", subscribe)
    out = srv.wait_for("window_close", match="open file")
    assert out == {"event": "closewindow", "address": "0xd", "already": True}


def test_any_one_of_several_matches_closing_satisfies(clients, monkeypatch):
    clients["clients"] = [KITTY, DIALOG, dict(DIALOG, address="0xE")]
    stream = ScriptedStream([("closewindow", {"address": "0xe"})])
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    assert srv.wait_for("window_close", match="open file")["address"] == "0xe"


def test_a_window_that_maps_while_subscribing_is_already(clients, monkeypatch):
    srv._note_clients()

    def subscribe():
        clients["clients"] = [KITTY, LG]  # mapped after the pre-check's query
        return ScriptedStream([])

    monkeypatch.setattr(srv.events, "EventStream", subscribe)
    out = srv.wait_for("window_open", match="lg-remote")
    assert out["already"] is True and out["address"] == "0xB"


def test_an_opened_window_says_whether_the_agent_launched_it(clients, monkeypatch):
    # V61: a new window's class and title are the app's claim; the token
    # in its process is not
    srv._note_clients()
    clients["clients"] = [KITTY, dict(LG, pid=777)]
    monkeypatch.setattr(srv, "_launch_token_of", lambda pid: "tok" if pid == 777 else None)
    monkeypatch.setattr(srv.trust, "launch_known", lambda token: token == "tok")
    _no_stream(monkeypatch)
    assert srv.wait_for("window_open", match="lg-remote")["launched_by_agent"] is True
    stream = ScriptedStream([("openwindow", {"address": "0xC", "class": "lg-remote"})])
    monkeypatch.setattr(srv.events, "EventStream", lambda: stream)
    assert srv.wait_for("window_open", match="lg")["launched_by_agent"] is False
