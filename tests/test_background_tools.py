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
    monkeypatch.setattr(srv.a11y, "app_for_pid",
                        lambda bus, pid, title="", size=None: ("app", "/root"))
    monkeypatch.setattr(srv.a11y, "window_frame",
                        lambda bus, svc, path, title="", size=None: (svc, path))
    monkeypatch.setattr(srv.a11y, "focused_text", lambda bus, svc, path: s["focused_text"])
    s["fields"] = []
    monkeypatch.setattr(srv.a11y, "editable_fields", lambda bus, svc, path: s["fields"])
    texts = {"field": ""}

    def insert(bus, svc, path, text):
        s["inserted"].append((svc, path, text))
        texts["field"] += text
        return True

    monkeypatch.setattr(srv.a11y, "insert_text", insert)
    monkeypatch.setattr(srv.a11y, "text_of", lambda bus, svc, path: texts["field"])
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


def test_focusing_a_text_field_does_not_claim_no_change(wired_bg):
    # focus is not a value: an unchanged empty field is the expected outcome
    wired_bg["role"] = "text"
    wired_bg["values"][:] = [{"value": ""}, {"value": ""}]
    out = str(srv.click_ui(name="Name", window="0xa"))
    assert "focused" in out and "no change" not in out



# --- keyboard -----------------------------------------------------------------


def test_typing_into_a_hidden_text_field_uses_accessibility_text(wired_bg):
    wired_bg["focused_text"] = ("svc", "/name", 79)
    out = str(srv.keyboard("type", text="hello", window="0xa"))
    assert wired_bg["inserted"] == [("svc", "/name", "hello")]
    assert wired_bg["typed"] == [] and wired_bg["dispatch"] == [] and wired_bg["shortcuts"] == []
    assert "accessibility interface" in out and "'hello'" in out


def test_typing_without_a_text_field_sends_chords(wired_bg):
    srv.keyboard("type", text="A1", window="0xa")
    assert wired_bg["shortcuts"] == [("SHIFT", "a", "0xa"), ("", "1", "0xa")]  # us layout


def test_typing_follows_the_layout(wired_bg, monkeypatch):
    monkeypatch.setattr(srv.hyprctl, "active_layout", lambda: ("fr", "", ""))
    srv.keyboard("type", text="1@", window="0xa")
    assert wired_bg["shortcuts"] == [("SHIFT", "ampersand", "0xa"), ("MOD5", "agrave", "0xa")]


def test_a_character_the_layout_lacks_is_refused_before_any_key(wired_bg):
    with pytest.raises(srv.trust.TrustError, match="no key"):
        srv.keyboard("type", text="ok \u4e0a", window="0xa")
    assert wired_bg["shortcuts"] == []  # nothing half-typed


def test_a_key_combo_goes_through_sendshortcut(wired_bg):
    out = str(srv.keyboard("key", keys="ctrl+s", window="0xa"))
    assert wired_bg["shortcuts"] == [("CTRL", "s", "0xa")]
    assert "sendshortcut" in out


def test_keys_to_an_xwayland_window_are_refused(wired_bg):
    wired_bg["xwayland"] = True
    with pytest.raises(srv.trust.TrustError, match="XWayland"):
        srv.keyboard("key", keys="ctrl+s", window="0xa")


def test_a_password_field_needs_allow_auth(wired_bg):
    wired_bg["focused_text"] = ("svc", "/pw", 40)
    with pytest.raises(srv.trust.TrustError, match="password"):
        srv.keyboard("type", text="hunter2", window="0xa")
    assert wired_bg["inserted"] == []


def test_keyboard_without_a_window_types_into_the_humans_window_in_automatic_mode(wired_bg):
    srv.keyboard("type", text="x")
    assert wired_bg["typed"] == ["x"] and wired_bg["shortcuts"] == []


def test_the_guarantee_requires_a_window(wired_bg, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    with pytest.raises(srv.trust.TrustError, match="window="):
        srv.keyboard("type", text="x")



def test_a_hidden_window_with_one_field_gets_accessibility_text(wired_bg):
    # GTK reports no focus in a window that is not active, so the one
    # editable field is where the text goes
    wired_bg["fields"] = [("svc", "/name", 61)]
    srv.keyboard("type", text="hello", window="0xa")
    assert wired_bg["inserted"] == [("svc", "/name", "hello")] and wired_bg["shortcuts"] == []


def test_several_fields_and_no_focus_report_fall_back_to_keys(wired_bg):
    wired_bg["fields"] = [("svc", "/a", 61), ("svc", "/b", 61)]
    srv.keyboard("type", text="hi", window="0xa")
    assert wired_bg["inserted"] == [] and len(wired_bg["shortcuts"]) == 2
