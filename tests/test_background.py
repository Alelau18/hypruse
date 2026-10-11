import pytest

from hypruse import background as bg
from hypruse import trust


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
    return {"address": addr, "workspace": _ws(ws)}


def test_next_workspace_fills_from_the_top(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-97")
    monkeypatch.setenv("HYPRUSE_AGENT_FILL", "1")
    owned = {"0xa"}
    assert bg.next_workspace([], owned) == "99"
    assert bg.next_workspace([_c("0xa", 99)], owned) == "98"


def test_next_workspace_skips_a_workspace_holding_the_humans_window(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-97")
    assert bg.next_workspace([_c("0xhuman", 99)], set()) == "98"


def test_fill_stacks_windows_before_moving_down(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-97")
    monkeypatch.setenv("HYPRUSE_AGENT_FILL", "2")
    owned = {"0xa", "0xb"}
    assert bg.next_workspace([_c("0xa", 99)], owned) == "99"
    assert bg.next_workspace([_c("0xa", 99), _c("0xb", 99)], owned) == "98"


def test_a_full_range_overflows_onto_the_emptiest(monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-98")
    owned = {"0xa", "0xb", "0xc"}
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
