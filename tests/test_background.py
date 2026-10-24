import pytest

from hypruse import background as bg
from hypruse import hyprctl, trust


@pytest.mark.parametrize("raw, mode", [
    (None, bg.AUTO), ("", bg.AUTO), ("auto", bg.AUTO), ("0", bg.OFF), ("off", bg.OFF),
    ("1", bg.GUARANTEE), ("on", bg.GUARANTEE)])
def test_mode(monkeypatch, raw, mode):
    if raw is None:
        monkeypatch.delenv("HYPRUSE_BACKGROUND", raising=False)
    else:
        monkeypatch.setenv("HYPRUSE_BACKGROUND", raw)
    assert bg.mode() == mode


def test_a_malformed_mode_refuses_everything(monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "maybe")
    with pytest.raises(trust.TrustError, match="malformed"):
        bg.mode()


@pytest.mark.parametrize("env, active, explicit, expect", [
    (None, True, None, False),   # automatic: the human's own window, foreground
    (None, False, None, True),   # automatic: any other window, background
    (None, False, False, False),  # the agent asks for the foreground
    (None, True, True, True),    # the agent asks for the background
    ("0", False, None, False),   # off: 0.12 behaviour
    ("0", False, True, True),    # off still honours an explicit request
    ("1", True, None, True),     # guarantee: always background
])
def test_decide(monkeypatch, env, active, explicit, expect):
    if env is None:
        monkeypatch.delenv("HYPRUSE_BACKGROUND", raising=False)
    else:
        monkeypatch.setenv("HYPRUSE_BACKGROUND", env)
    assert bg.decide(active, explicit) is expect


def test_the_guarantee_refuses_an_explicit_foreground(monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    with pytest.raises(trust.TrustError, match="HYPRUSE_BACKGROUND"):
        bg.decide(False, False)


def test_refuse_hints_depend_on_the_mode(monkeypatch):
    monkeypatch.delenv("HYPRUSE_BACKGROUND", raising=False)
    with pytest.raises(trust.TrustError, match="background=false") as e:
        bg.refuse("this control has no accessibility action")
    assert "in the background" in str(e.value)
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    with pytest.raises(trust.TrustError, match="ask the human") as e:
        bg.refuse("x")
    assert "background=false" not in str(e.value)


def _ws(i):
    return {"id": i, "name": str(i)}


def test_the_default_range_counts_down_from_99(monkeypatch):
    monkeypatch.delenv("HYPRUSE_AGENT_WORKSPACES", raising=False)
    assert bg.agent_workspaces() == [str(i) for i in range(99, 89, -1)]


@pytest.mark.parametrize("raw, expect", [
    ("99-97", ["99", "98", "97"]), ("90-92", ["90", "91", "92"]),
    ("99,98,50", ["99", "98", "50"]), ("name:research,99", ["name:research", "99"])])
def test_ranges_and_lists(monkeypatch, raw, expect):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", raw)
    assert bg.agent_workspaces() == expect


@pytest.mark.parametrize("raw", ["", "abc", "99-", "0", "-3"])
def test_a_malformed_range_refuses(monkeypatch, raw):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", raw)
    with pytest.raises(trust.TrustError, match="HYPRUSE_AGENT_WORKSPACES"):
        bg.agent_workspaces()


def _c(addr, ws):
    return {"address": addr, "stableId": addr[2:], "workspace": _ws(ws)}


def _k(*addrs):
    """The owned-set for these windows: hyprctl.window_key, as launch records."""
    return {hyprctl.window_key(_c(a, 0)) for a in addrs}


def test_next_workspace_fills_from_the_top(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-97")
    monkeypatch.setenv("HYPRUSE_AGENT_FILL", "1")
    owned = _k("0xa")
    assert bg.next_workspace([], owned) == "99"
    assert bg.next_workspace([_c("0xa", 99)], owned) == "98"


def test_next_workspace_skips_a_workspace_holding_the_humans_window(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-97")
    assert bg.next_workspace([_c("0xhuman", 99)], set()) == "98"


def test_fill_stacks_windows_before_moving_down(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-97")
    monkeypatch.setenv("HYPRUSE_AGENT_FILL", "2")
    owned = _k("0xa", "0xb")
    assert bg.next_workspace([_c("0xa", 99)], owned) == "99"
    assert bg.next_workspace([_c("0xa", 99), _c("0xb", 99)], owned) == "98"


def test_a_full_range_overflows_onto_the_emptiest(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-98")
    owned = _k("0xa", "0xb", "0xc")
    cl = [_c("0xa", 99), _c("0xb", 99), _c("0xc", 98)]
    assert bg.next_workspace(cl, owned) == "98"


def test_no_usable_workspace_refuses(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99")
    with pytest.raises(trust.TrustError, match="not open"):
        bg.next_workspace([_c("0xhuman", 99)], set())


def test_on_agent_workspace(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-98,name:research")
    assert bg.on_agent_workspace(_c("0xa", 98))
    assert bg.on_agent_workspace({"workspace": {"name": "research"}})
    assert not bg.on_agent_workspace(_c("0xa", 5))


def test_a_workspace_name_is_an_agent_workspace_only_when_listed(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-97,name:research")
    assert bg.is_agent_workspace("98") and bg.is_agent_workspace("name:research")
    assert not bg.is_agent_workspace("3") and not bg.is_agent_workspace("+1")
    assert not bg.is_agent_workspace("special:magic")


# --- review fixes -------------------------------------------------------------


def test_a_workspace_on_screen_is_never_picked(monkeypatch):
    # the human looking at 99 would see the app open, and Hyprland drops a
    # silent rule for the workspace a window maps on anyway
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-97")
    assert bg.next_workspace([], set(), shown=[_ws(99)]) == "98"
    monkeypatch.setenv("HYPRUSE_AGENT_FILL", "2")
    owned = _k("0xa")
    assert bg.next_workspace([_c("0xa", 99)], owned, shown=[_ws(99)]) == "98"


def test_the_overflow_skips_a_workspace_on_screen(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-98")
    owned = _k("0xa", "0xb", "0xc")
    cl = [_c("0xa", 99), _c("0xb", 98), _c("0xc", 98)]
    assert bg.next_workspace(cl, owned, shown=[_ws(99)]) == "98"


def test_every_free_workspace_on_screen_refuses(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99")
    with pytest.raises(trust.TrustError, match="on screen"):
        bg.next_workspace([], set(), shown=[_ws(99)])


def test_a_named_workspace_on_screen_is_skipped(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "name:research,99")
    assert bg.next_workspace([], set(), shown=[{"id": -1337, "name": "research"}]) == "99"


def test_a_window_tagged_hypruse_owned_counts_as_the_agents(monkeypatch):
    # a window an earlier session opened, under HYPRUSE_MARK
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-98")
    monkeypatch.setenv("HYPRUSE_AGENT_FILL", "2")
    left = dict(_c("0xold", 99), tags=["hypruse-owned*"])
    assert bg.next_workspace([left], set()) == "99"


def test_allowed_narrows_the_range(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-97")
    assert bg.next_workspace([], set(), allowed=lambda sel: sel == "97") == "97"
    with pytest.raises(trust.TrustError):
        bg.next_workspace([], set(), allowed=lambda sel: False)


def test_numbers_are_normalized(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "099,98")
    assert bg.agent_workspaces() == ["99", "98"]


@pytest.mark.parametrize("raw", ["1-100000", "99-2000000000", "3000000000"])
def test_a_huge_range_refuses(monkeypatch, raw):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", raw)
    with pytest.raises(trust.TrustError, match="HYPRUSE_AGENT_WORKSPACES"):
        bg.agent_workspaces()


def test_the_malformed_message_says_what_it_refuses(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "abc")
    with pytest.raises(trust.TrustError) as err:
        bg.agent_workspaces()
    assert "refusing every action until" not in str(err.value)
    assert "placing an app" in str(err.value)


def test_matches_a_name_selector_and_numbers():
    assert bg.matches("name:research", {"id": -1337, "name": "research"})
    assert bg.matches("99", {"id": 99, "name": "99"})
    assert not bg.matches("99", {"id": 9, "name": "9"})


def test_a_human_window_at_an_agent_windows_old_address_is_not_the_agents(monkeypatch):
    # V51: launch placement read the owned-set by address
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99")
    human = dict(_c("0xa", 99), stableId="later")
    with pytest.raises(trust.TrustError, match="not open"):
        bg.next_workspace([human], _k("0xa"))


def test_a_workspace_named_like_a_number_is_not_that_number():
    # V31: Hyprland resolves "99" to workspace id 99 only
    assert bg.matches("99", {"id": 99, "name": "99"})
    assert not bg.matches("99", {"id": -1337, "name": "99"})  # 0.56: a named one
    assert not bg.matches("99", {"name": "99"})  # 0.57: named ones carry no id
    assert bg.matches("name:99", {"id": -1337, "name": "99"})


def test_a_launched_apps_dialog_does_not_make_its_workspace_the_humans(monkeypatch):
    # V29: placement and the guarantee's scope use one test of "the agent's
    # window", which counts what a launched process opens later
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-97")
    monkeypatch.setenv("HYPRUSE_AGENT_FILL", "2")
    monkeypatch.setattr(trust, "_launches", {})
    trust.note_launch("tok")
    monkeypatch.setattr(trust, "launch_token_of", lambda pid: "tok" if pid == 77 else None)
    dialog = dict(_c("0xd", 99), pid=77)
    assert bg.next_workspace([dialog], set()) == "99"
    assert bg.next_workspace([dict(dialog, pid=78)], set()) == "98"  # someone else's
