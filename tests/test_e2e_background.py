"""Background mode, live. Run explicitly with:  pytest -m background

Opens yad forms on the agent's workspaces, drives them in the background
and reads the results back from the accessibility tree. After every step
the human's active window, active workspace and cursor must be exactly
what they were before the suite started: that is the promise background
mode makes, checked against the real compositor.
"""

import contextlib
import os
import shutil
import time

import pytest

from hypruse import hyprctl, trust
from hypruse import server as srv

# bound at import time, so conftest's autouse stubs do not hide them
from hypruse.trust import _compositor_locked as real_compositor_locked  # noqa: E402
from hypruse.trust import session_locked as real_session_locked  # noqa: E402

pytestmark = [
    pytest.mark.background,
    pytest.mark.skipif(
        not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or not shutil.which("yad"),
        reason="needs a live Hyprland session and yad",
    ),
]


def _seat():
    return (
        (hyprctl.query("activewindow") or {}).get("address"),
        (hyprctl.query("activeworkspace") or {}).get("id"),
        hyprctl.cursor_pos(),
    )


@pytest.fixture
def live(monkeypatch):
    """The real compositor and lock checks, background mode as the
    environment leaves it unless a test sets it, and every window this
    test opened closed afterwards."""
    monkeypatch.setattr(hyprctl, "_provider", None)
    monkeypatch.setattr(trust, "session_locked", real_session_locked)
    monkeypatch.setattr(trust, "_compositor_locked", real_compositor_locked)
    monkeypatch.delenv("HYPRUSE_BACKGROUND", raising=False)
    monkeypatch.delenv("HYPRUSE_CONFINE", raising=False)
    opened: list[str] = []
    before = _seat()

    def open_form(*fields: str, title: str) -> str:
        args = " ".join(f"--field={f}" for f in fields)
        out = srv.launch(f"yad --form --title={title} {args} --button=OK:0", wait_s=15)
        assert isinstance(out, dict), out
        opened.append(out["address"])
        time.sleep(0.5)  # let the accessibility tree fill in
        return out["address"]

    yield {"open": open_form, "before": before}
    for addr in opened:
        with contextlib.suppress(Exception):
            hyprctl.dispatch("closewindow", f"address:{addr}")
    assert _seat() == before, "the human's seat moved"


def _control(addr: str, name: str) -> dict:
    found = [c for c in srv._ui_read(addr, actionable=False) if c.get("name") == name
             and c.get("role") != "label"]
    assert found, f"no control named {name!r}"
    return found[0]


def test_an_app_opens_on_an_agent_workspace_and_is_driven_unseen(live):
    addr = live["open"]("Agree:CHK", "Name", title="hypruse-bg-one")
    client = srv._resolve_window(addr)
    from hypruse import background as bg

    assert bg.on_agent_workspace(client), client.get("workspace")
    assert _seat() == live["before"]

    out = str(srv.click_ui(name="Agree", window=addr))
    assert "accessibility action" in out and "checked=true" in out, out
    assert _control(addr, "Agree").get("checked") is True
    assert _seat() == live["before"]

    out = str(srv.keyboard("type", text="Bonjour ✓ 1@", window=addr))
    assert "accessibility interface" in out, out
    assert _control(addr, "Name").get("value") == "Bonjour ✓ 1@"
    assert _seat() == live["before"]


def test_keys_reach_a_hidden_window_through_sendshortcut(live):
    # two text fields and no focus report from the hidden window: typing
    # goes key by key to whatever field the app itself has focused
    addr = live["open"]("Name", "City", title="hypruse-bg-two")
    srv.click_ui(name="City", window=addr)
    out = str(srv.keyboard("type", text="Paris 1@", window=addr))
    assert _control(addr, "City").get("value") == "Paris 1@", out
    assert _seat() == live["before"]

    out = str(srv.keyboard("key", keys="ctrl+a", window=addr))
    assert "sendshortcut" in out, out
    srv.keyboard("key", keys="BackSpace", window=addr)
    assert _control(addr, "City").get("value") == ""
    assert _seat() == live["before"]


def test_the_guarantee_holds_live(live, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    addr = live["open"]("Agree:CHK", title="hypruse-bg-guarantee")
    srv.click_ui(name="Agree", window=addr)
    assert _control(addr, "Agree").get("checked") is True
    x, y = hyprctl.cursor_pos()
    with pytest.raises(trust.TrustError, match="HYPRUSE_BACKGROUND"):
        srv.pointer("click", x=x, y=y)
    with pytest.raises(trust.TrustError, match="HYPRUSE_BACKGROUND"):
        srv.hypr("focus_window", target=addr)
    with pytest.raises(trust.TrustError, match="window="):
        srv.keyboard("type", text="x")
    assert _seat() == live["before"]
