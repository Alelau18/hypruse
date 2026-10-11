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
