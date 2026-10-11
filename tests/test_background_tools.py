"""The acting tools in background mode: what each does to a window the human
is not in, with every desktop and accessibility call stubbed."""

import pytest

from hypruse import server as srv

CLIENT = {"address": "0xa", "class": "gedit", "title": "doc", "at": [100, 50],
          "size": [800, 600], "pid": 42, "workspace": {"id": 99}, "monitor": 0,
          "floating": False}
MONS = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0, "width": 1920, "height": 1080,
         "scale": 1.0, "activeWorkspace": {"id": 1}, "specialWorkspace": {"id": 0}}]


@pytest.fixture
def wired_bg(monkeypatch):
    s = {"active": "0xhuman", "clicks": [], "dispatch": [], "actions": [], "grabbed": [],
         "has_action": True, "role": "push button", "values": [{}, {}], "gone": False,
         "inserted": [], "typed": [], "shortcuts": [], "focused_text": None,
         "xwayland": False, "window_at_point": "0xhuman"}
    monkeypatch.delenv("HYPRUSE_BACKGROUND", raising=False)
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv.time, "sleep", lambda s_: None)

    def resolve(w=""):
        if s["gone"] and w == "0xa":
            raise ValueError("window '0xa' not found")
        return dict(CLIENT, xwayland=s["xwayland"])

    monkeypatch.setattr(srv, "_resolve_window", resolve)
    monkeypatch.setattr(srv, "_ui_read", lambda w="", name="", actionable=True, handles=False: [
        {"role": s["role"], "name": "Save" if s["role"] != "text" else "Name",
         "x": 150, "y": 80, "clickable": True, "_svc": "svc",
         "_path": "/save" if s["role"] != "text" else "/name"}])

    monkeypatch.setattr(srv, "_active_address", lambda: s["active"])

    def query(what):
        if what == "activewindow":
            return {"address": s["active"]}
        if what == "clients":
            return [] if s["gone"] else [dict(CLIENT, xwayland=s["xwayland"])]
        return {}

    monkeypatch.setattr(srv.hyprctl, "query", query)
    monkeypatch.setattr(srv.hyprctl, "dispatch", lambda *a: s["dispatch"].append(a))
    monkeypatch.setattr(srv.hyprctl, "send_shortcut",
                        lambda mods, key, addr: s["shortcuts"].append((mods, key, addr)))
    monkeypatch.setattr(srv.hyprctl, "active_layout", lambda: ("us", "", ""))
    monkeypatch.setattr(srv.hinput, "click",
                        lambda x, y, button="left", double=False: s["clicks"].append((x, y)))
    monkeypatch.setattr(srv.hinput, "type_text", lambda t: s["typed"].append(t))
    monkeypatch.setattr(srv.a11y, "connect", lambda: object())
    monkeypatch.setattr(srv.a11y, "has_action", lambda bus, svc, path: s["has_action"])
    monkeypatch.setattr(srv.a11y, "do_action",
                        lambda bus, svc, path, index=0: s["actions"].append((svc, path)) or True)
    monkeypatch.setattr(srv.a11y, "grab_focus",
                        lambda bus, svc, path: s["grabbed"].append((svc, path)) or True)
    values = iter(s["values"])
    monkeypatch.setattr(srv.a11y, "current_value",
                        lambda bus, svc, path: next(values, {}))
    monkeypatch.setattr(srv.trust, "_click_scene", lambda: (MONS, []))
    monkeypatch.setattr(srv.trust, "_read_layers", lambda: [])
    return s


# --- click_ui ---------------------------------------------------------------


def test_click_ui_on_another_window_presses_its_action(wired_bg):
    out = srv.click_ui(name="Save", window="0xa")
    assert wired_bg["actions"] == [("svc", "/save")]
    assert wired_bg["clicks"] == [] and wired_bg["dispatch"] == []
    assert "accessibility action" in str(out) and "focus untouched" in str(out)


def test_click_ui_on_the_active_window_still_clicks(wired_bg):
    wired_bg["active"] = "0xa"
    srv.click_ui(name="Save", window="0xa")
    assert wired_bg["clicks"] and not wired_bg["actions"]


def test_a_control_without_an_action_is_refused_in_the_background(wired_bg):
    wired_bg["has_action"] = False
    with pytest.raises(srv.trust.TrustError, match="no accessibility action"):
        srv.click_ui(name="Save", window="0xa")
    assert wired_bg["clicks"] == [] and wired_bg["actions"] == []


def test_background_false_forces_the_old_path(wired_bg):
    srv.click_ui(name="Save", window="0xa", background=False)
    assert wired_bg["clicks"] and not wired_bg["actions"]


def test_a_text_field_gets_the_apps_own_focus(wired_bg):
    wired_bg["role"] = "text"
    srv.click_ui(name="Name", window="0xa")
    assert wired_bg["grabbed"] == [("svc", "/name")] and not wired_bg["actions"]


def test_the_press_is_read_back(wired_bg):
    wired_bg["role"] = "check box"
    wired_bg["values"][:] = [{"checked": False}, {"checked": True}]
    out = srv.click_ui(name="Save", window="0xa")
    assert "now: checked=true" in str(out)


def test_a_press_the_app_ignored_reads_as_no_change(wired_bg):
    wired_bg["role"] = "check box"
    wired_bg["values"][:] = [{"checked": False}, {"checked": False}]
    assert "no change seen" in str(srv.click_ui(name="Save", window="0xa"))


def test_a_dry_run_names_the_method_and_does_nothing(wired_bg, monkeypatch):
    monkeypatch.setattr(srv.journal, "dry_run", lambda: True)
    out = srv.click_ui(name="Save", window="0xa")
    assert "accessibility action" in str(out) and wired_bg["actions"] == []


def test_the_guarantee_never_clicks_even_the_active_window(wired_bg, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    wired_bg["active"] = "0xa"
    srv.click_ui(name="Save", window="0xa")
    assert wired_bg["actions"] and not wired_bg["clicks"]


def test_marks_carry_what_a_background_press_needs(wired_bg):
    srv._last_marks.clear()
    srv._last_marks.update({"window": "0xa", "class": "gedit", "ts": srv.time.time(),
                            "size": [800, 600],
                            "items": {1: {"dx": 50, "dy": 30, "label": "push button 'Save'",
                                          "svc": "svc", "path": "/save",
                                          "role": "push button"}}})
    srv.click_ui(mark=1)
    assert wired_bg["actions"] == [("svc", "/save")]
