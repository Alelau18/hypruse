"""The acting tools in background mode: what each does to a window the human
is not in, with every desktop and accessibility call stubbed."""

import pytest

from hypruse import server as srv

REAL_UI_READ = srv._ui_read  # bound before the fixture stubs it

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
    def ui_read(w="", name="", actionable=True, handles=False):
        item = {"role": s["role"], "name": "Save" if s["role"] != "text" else "Name",
                "x": 150, "y": 80, "clickable": True}
        if s["frame_sure"]:
            item["_svc"], item["_path"] = "svc", "/save" if s["role"] != "text" else "/name"
        else:
            item["_unsure"] = True
        return [item]

    s["frame_sure"] = True
    monkeypatch.setattr(srv, "_ui_read", ui_read)

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
    monkeypatch.setattr(srv.hyprctl, "layout_group", lambda: (0, 1))
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
    monkeypatch.setattr(srv.a11y, "window_frame_for",
                        lambda bus, svc, path, title, size, peers:
                        (svc, path) if s["frame_sure"] else None)
    monkeypatch.setattr(srv.a11y, "focused_text", lambda bus, svc, path: s["focused_text"])
    s["fields"] = []
    monkeypatch.setattr(srv.a11y, "editable_fields", lambda bus, svc, path: s["fields"])
    texts = {"field": ""}

    def insert(bus, svc, path, text):
        s["inserted"].append((svc, path, text))
        texts["field"] += text
        return True

    s["fail_after"] = None

    def shortcut(mods, key, addr):
        if s["fail_after"] is not None and len(s["shortcuts"]) >= s["fail_after"]:
            raise srv.hyprctl.HyprctlError("window gone")
        s["shortcuts"].append((mods, key, addr))

    monkeypatch.setattr(srv.hyprctl, "send_shortcut", shortcut)

    monkeypatch.setattr(srv.a11y, "insert_text", insert)
    monkeypatch.setattr(srv.a11y, "text_of", lambda bus, svc, path: texts["field"])
    monkeypatch.setattr(srv.trust, "_click_scene", lambda: (MONS, []))
    monkeypatch.setattr(srv.trust, "_read_layers", lambda: [])
    monkeypatch.setattr(srv.trust, "guard_pointer", lambda *a, **k: None)
    monkeypatch.setattr(srv.trust, "session_locked", lambda: None)
    monkeypatch.setattr(srv.hyprctl, "cursor_pos", lambda: (5, 5))
    s["at_points"] = []

    def other_at(x, y, active):
        s["at_points"].append((x, y))
        hit = s["window_at_point"]
        return None if hit in (None, active) else dict(CLIENT, address=hit)

    monkeypatch.setattr(srv.trust, "other_window_at", other_at)
    monkeypatch.setattr(srv.hinput, "move", lambda x, y: s["clicks"].append(("move", x, y)))
    monkeypatch.setattr(srv.hinput, "drag", lambda *a, **k: s["clicks"].append(("drag",) + a))
    monkeypatch.setattr(srv.hinput, "scroll", lambda **k: s["clicks"].append(("scroll", k)))
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
    assert "accessibility interface" in out and "holds the typed text (5 characters)" in out


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


# --- pointer, hypr, use_bind --------------------------------------------------


def test_pointer_over_another_window_is_refused_in_automatic_mode(wired_bg):
    wired_bg["window_at_point"] = "0xa"  # not the active window
    with pytest.raises(srv.trust.TrustError, match="background=false"):
        srv.pointer("click", x=100, y=100)
    assert wired_bg["clicks"] == []


def test_the_refusal_points_at_click_ui(wired_bg):
    wired_bg["window_at_point"] = "0xa"
    with pytest.raises(srv.trust.TrustError, match="click_ui"):
        srv.pointer("click", x=100, y=100)


def test_pointer_over_the_active_window_works(wired_bg):
    wired_bg["window_at_point"] = wired_bg["active"]
    srv.pointer("click", x=100, y=100)
    assert wired_bg["clicks"] == [(100, 100)]


def test_pointer_over_no_window_works(wired_bg):
    wired_bg["window_at_point"] = None  # the bar, a launcher, the wallpaper
    srv.pointer("click", x=100, y=100)
    assert wired_bg["clicks"] == [(100, 100)]


def test_a_click_in_place_checks_the_cursor(wired_bg):
    wired_bg["window_at_point"] = "0xa"
    with pytest.raises(srv.trust.TrustError):
        srv.pointer("click")
    assert wired_bg["at_points"] == [(5, 5)]


def test_a_move_onto_another_window_is_refused(wired_bg):
    # focus follows the mouse by default, so a move alone can take focus
    wired_bg["window_at_point"] = "0xa"
    with pytest.raises(srv.trust.TrustError):
        srv.pointer("move", x=100, y=100)


def test_a_drag_checks_both_ends(wired_bg):
    srv.pointer("drag", x=1, y=2, to_x=3, to_y=4)
    assert wired_bg["at_points"] == [(1, 2), (3, 4)]


def test_pointer_background_false_skips_the_check(wired_bg):
    wired_bg["window_at_point"] = "0xa"
    srv.pointer("click", x=100, y=100, background=False)
    assert wired_bg["clicks"] == [(100, 100)] and wired_bg["at_points"] == []


def test_pointer_background_true_is_refused(wired_bg):
    with pytest.raises(srv.trust.TrustError, match="cursor"):
        srv.pointer("click", x=100, y=100, background=True)


def test_pointer_with_background_off_is_unchanged(wired_bg, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "0")
    wired_bg["window_at_point"] = "0xa"
    srv.pointer("click", x=100, y=100)
    assert wired_bg["clicks"] == [(100, 100)] and wired_bg["at_points"] == []


def test_the_guarantee_refuses_pointer_workspace_focus_fullscreen_and_binds(
    wired_bg, monkeypatch
):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    for call in (lambda: srv.pointer("click", x=1, y=1),
                 lambda: srv.pointer("move", x=1, y=1),
                 lambda: srv.hypr("workspace", workspace="3"),
                 lambda: srv.hypr("focus_window", target="0xa"),
                 lambda: srv.hypr("fullscreen", target="0xa"),
                 lambda: srv.use_bind("SUPER+F")):
        with pytest.raises(srv.trust.TrustError, match="HYPRUSE_BACKGROUND"):
            call()
    assert wired_bg["clicks"] == [] and wired_bg["dispatch"] == []


def test_the_guarantee_keeps_agent_windows_on_agent_workspaces(wired_bg, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    with pytest.raises(srv.trust.TrustError, match="agent's workspaces"):
        srv.hypr("move_window", target="0xa", workspace="3")
    srv.hypr("move_window", target="0xa", workspace="98")
    assert wired_bg["dispatch"] == [("movetoworkspacesilent", "98,address:0xa")]


def test_automatic_mode_still_switches_workspaces_on_request(wired_bg):
    srv.hypr("workspace", workspace="3")
    assert wired_bg["dispatch"] == [("workspace", "3")]


# --- launch -------------------------------------------------------------------


@pytest.fixture
def launch_fake(monkeypatch):
    s = {"rule": None, "clients": [], "seat": 0, "active": ["0xhuman"],
         "lands": None, "dispatch": [], "monitors": MONS}
    monkeypatch.delenv("HYPRUSE_BACKGROUND", raising=False)
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)

    def launch_and_wait(rule_command, wait_s, accept=None):
        s["rule"] = rule_command.removesuffix("foot").strip()
        ws = s["lands"] or (rule_command.split()[1] if rule_command.startswith("[") else "1")
        name = ws.removeprefix("name:")
        return {"address": "0xnew", "class": "foot", "title": "~",
                "workspace": {"id": int(ws) if ws.isdigit() else -1337, "name": name}}

    monkeypatch.setattr(srv, "_launch_and_wait", launch_and_wait)
    monkeypatch.setattr(srv.hyprctl, "query",
                        lambda what: s["monitors"] if what == "monitors" else s["clients"])
    monkeypatch.setattr(srv.hyprctl, "dispatch", lambda *a: s["dispatch"].append(a))
    monkeypatch.setattr(srv, "_active_address",
                        lambda: s["active"].pop(0) if len(s["active"]) > 1 else s["active"][0])
    monkeypatch.setattr(srv.trust, "note_launched", lambda *a: None)
    monkeypatch.setattr(srv.trust, "owned", lambda: {"0xmine"})

    def seat():
        s["seat"] += 1

    monkeypatch.setattr(srv.trust, "remember_seat", seat)
    return s


def test_launch_without_a_workspace_goes_to_the_next_agent_workspace(launch_fake):
    out = srv.launch("foot")
    assert launch_fake["rule"] == "[workspace 99 silent]"
    assert "agent" in out["note"] and "background=false" in out["note"]


def test_the_next_launch_takes_the_next_workspace(launch_fake):
    launch_fake["clients"] = [{"address": "0xmine", "workspace": {"id": 99, "name": "99"}}]
    srv.launch("foot")
    assert launch_fake["rule"] == "[workspace 98 silent]"


def test_launch_background_false_opens_on_the_humans_workspace(launch_fake):
    out = srv.launch("foot", background=False)
    assert launch_fake["rule"] == "" and "note" not in out


def test_launch_with_a_workspace_keeps_it(launch_fake):
    srv.launch("foot", workspace="3")
    assert launch_fake["rule"] == "[workspace 3 silent]"


def test_launch_with_background_off_is_unchanged(launch_fake, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "0")
    srv.launch("foot")
    assert launch_fake["rule"] == ""


def test_the_guarantee_launches_only_onto_agent_workspaces(launch_fake, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    with pytest.raises(srv.trust.TrustError, match="agent's workspaces"):
        srv.launch("foot", workspace="3")
    with pytest.raises(srv.trust.TrustError, match="HYPRUSE_BACKGROUND"):
        srv.launch("foot", background=False)
    assert launch_fake["rule"] is None
    srv.launch("foot", workspace="98")
    assert launch_fake["rule"] == "[workspace 98 silent]"


def test_a_silent_launch_leaves_the_seat_baseline_alone(launch_fake):
    # the human's seat did not change, so a change since the last action is
    # still theirs for the strict seat guard to see
    srv.launch("foot")
    assert launch_fake["seat"] == 0
    srv.launch("foot", background=False)
    assert launch_fake["seat"] == 1


def test_an_app_that_opened_elsewhere_says_whether_focus_moved(launch_fake):
    # a single-instance app opens from its running process, on the human's
    # workspace, and is moved after
    launch_fake["lands"] = "1"
    launch_fake["active"][:] = ["0xhuman", "0xother"]
    out = srv.launch("foot")
    assert ("movetoworkspacesilent", "99,address:0xnew") in launch_fake["dispatch"]
    assert "focus" in out["note"] and "0xother" in out["note"] and "agent" in out["note"]
    assert launch_fake["seat"] == 1  # our launch moved the seat


def test_a_dry_run_names_the_agent_workspace(launch_fake, monkeypatch):
    monkeypatch.setattr(srv.journal, "dry_run", lambda: True)
    out = srv.launch("foot")
    assert "[workspace 99 silent]" in out and launch_fake["rule"] is None


def test_a_keyless_character_with_several_fields_says_why(wired_bg):
    # the hidden app does not say which field has its focus, so the text
    # cannot go through accessibility, and the layout has no key for it
    wired_bg["fields"] = [("svc", "/a", 61), ("svc", "/b", 61)]
    with pytest.raises(srv.trust.TrustError, match="which of its 2 text fields"):
        srv.keyboard("type", text="上", window="0xa")


def test_a_keyless_character_with_no_field_says_so(wired_bg):
    with pytest.raises(srv.trust.TrustError, match="no accessibility text field"):
        srv.keyboard("type", text="上", window="0xa")


def test_keys_are_refused_while_the_keyboard_is_on_another_layout(wired_bg, monkeypatch):
    # Hyprland finds a key on the layout in use but the app reads it on the
    # first one, so on us,fr switched to fr an "a" would arrive as "q"
    monkeypatch.setattr(srv.hyprctl, "layout_group", lambda: (1, 2))
    with pytest.raises(srv.trust.TrustError, match="layout 2 of 2"):
        srv.keyboard("type", text="a", window="0xa")
    with pytest.raises(srv.trust.TrustError, match="layout 2 of 2"):
        srv.keyboard("key", keys="ctrl+s", window="0xa")
    assert wired_bg["shortcuts"] == []


def test_accessibility_text_does_not_care_about_the_layout(wired_bg, monkeypatch):
    monkeypatch.setattr(srv.hyprctl, "layout_group", lambda: (1, 2))
    wired_bg["fields"] = [("svc", "/name", 61)]
    srv.keyboard("type", text="a", window="0xa")
    assert wired_bg["inserted"] == [("svc", "/name", "a")]


def test_a_right_or_double_click_is_refused_in_the_background(wired_bg):
    # a control's accessibility action is its primary one: running it for a
    # right-click would do something the caller did not ask for
    with pytest.raises(srv.trust.TrustError, match="right click"):
        srv.click_ui(name="Save", window="0xa", button="right")
    with pytest.raises(srv.trust.TrustError, match="double click"):
        srv.click_ui(name="Save", window="0xa", double=True)
    assert wired_bg["actions"] == [] and wired_bg["clicks"] == []


# --- ui of a hidden window ----------------------------------------------------


def test_ui_of_a_hidden_window_says_its_points_are_not_on_screen(wired_bg, monkeypatch):
    # a pointer click at those points would land on whatever the human sees
    monkeypatch.setattr(srv.shot, "_hidden_reason",
                        lambda c, m, cl=None: "its workspace is not on any monitor")
    monkeypatch.setattr(srv.hyprctl, "batch_query", lambda cmds: [MONS, [CLIENT]])
    out = srv.ui(window="0xa")
    note = out[-1]["note"]
    assert note.startswith("0xa is not on screen (its workspace is not on any monitor)")
    assert "click_ui" in note and "pointer" in note


def test_ui_of_a_shown_window_has_no_note(wired_bg, monkeypatch):
    monkeypatch.setattr(srv.shot, "_hidden_reason", lambda c, m, cl=None: None)
    monkeypatch.setattr(srv.hyprctl, "batch_query", lambda cmds: [MONS, [CLIENT]])
    assert all("note" not in e for e in srv.ui(window="0xa"))


def test_then_ui_after_a_background_press_carries_the_note(wired_bg, monkeypatch):
    monkeypatch.setattr(srv.shot, "_hidden_reason",
                        lambda c, m, cl=None: "its workspace is not on any monitor")
    monkeypatch.setattr(srv.hyprctl, "batch_query", lambda cmds: [MONS, [CLIENT]])
    out = srv.click_ui(name="Save", window="0xa", then="ui")
    assert "not on screen" in out[-1].text


# --- review fixes: identity, passwords, the journal ---------------------------


def test_typing_in_a_window_its_tree_cannot_tell_apart_goes_by_keys(wired_bg):
    # another window of the same app shares its title or size: the field
    # found could be the human's, and sendshortcut is aimed by address
    wired_bg["frame_sure"] = False
    wired_bg["focused_text"] = ("svc", "/name", 79)
    srv.keyboard("type", text="hi", window="0xa")
    assert wired_bg["inserted"] == [] and len(wired_bg["shortcuts"]) == 2


def test_a_press_in_a_window_its_tree_cannot_tell_apart_is_refused(wired_bg):
    wired_bg["frame_sure"] = False
    with pytest.raises(srv.trust.TrustError, match="cannot tell"):
        srv.click_ui(name="Save", window="0xa")
    assert wired_bg["actions"] == []


def test_ui_read_hands_out_controls_only_from_a_proven_frame(wired_bg, monkeypatch):
    monkeypatch.setattr(srv.a11y, "find_elements", lambda bus, s_, p, name="", actionable=True: (
        [{"role": "push button", "name": "Save", "extent": (10, 10, 20, 20),
          "clickable": True, "svc": s_, "path": p + "/save"}], False))
    sure = REAL_UI_READ("0xa", handles=True)
    assert sure[0]["_svc"] == "app" and "_unsure" not in sure[0]
    wired_bg["frame_sure"] = False
    unsure = REAL_UI_READ("0xa", handles=True)
    assert "_svc" not in unsure[0] and unsure[0]["_unsure"]
    assert "_unsure" not in REAL_UI_READ("0xa")[0]  # coordinates only: nothing to prove


def test_a_password_field_among_several_is_refused_when_focus_is_unknown(wired_bg):
    wired_bg["fields"] = [("svc", "/user", 79), ("svc", "/pw", 40)]
    with pytest.raises(srv.trust.TrustError, match="password"):
        srv.keyboard("type", text="hunter2", window="0xa")
    assert wired_bg["shortcuts"] == [] and wired_bg["inserted"] == []
    srv.keyboard("type", text="hunter2", window="0xa", allow_auth=True)
    assert len(wired_bg["shortcuts"]) == 7


def test_the_result_never_echoes_the_field(wired_bg):
    # the journal stores every act's result line, and typed text is only
    # ever recorded as a length plus a digest
    wired_bg["focused_text"] = ("svc", "/name", 79)
    out = str(srv.keyboard("type", text="sk-live-SECRET", window="0xa"))
    assert "SECRET" not in out and "holds the typed text" in out


def test_a_keyless_character_is_named_by_position_not_echoed(wired_bg):
    with pytest.raises(srv.trust.TrustError) as err:
        srv.keyboard("type", text="ab\u4e0a", window="0xa")
    assert "character 3" in str(err.value) and "\u4e0a" not in str(err.value)


def test_typing_without_an_accessibility_bus_falls_back_to_keys(wired_bg, monkeypatch):
    def no_bus():
        raise srv.a11y.A11yError("accessibility bus reported no address")

    monkeypatch.setattr(srv.a11y, "connect", no_bus)
    srv.keyboard("type", text="ls", window="0xa")
    assert len(wired_bg["shortcuts"]) == 2


def test_newlines_and_tabs_are_keys_even_with_a_text_field(wired_bg):
    # wtype and sendshortcut press Return for a newline; inserting a raw
    # newline into an entry would submit nothing
    wired_bg["focused_text"] = ("svc", "/name", 79)
    srv.keyboard("type", text="q\n", window="0xa")
    assert wired_bg["inserted"] == []
    assert wired_bg["shortcuts"] == [("", "q", "0xa"), ("", "Return", "0xa")]


def test_a_key_that_fails_partway_says_how_many_landed(wired_bg):
    wired_bg["fail_after"] = 2
    with pytest.raises(srv.hyprctl.HyprctlError, match="sent 2 of 4 keys"):
        srv.keyboard("type", text="abcd", window="0xa")


def test_launch_never_opens_on_an_agent_workspace_the_human_is_viewing(launch_fake):
    launch_fake["monitors"] = [dict(MONS[0], activeWorkspace={"id": 99, "name": "99"})]
    out = srv.launch("foot")
    assert launch_fake["rule"] == "[workspace 98 silent]" and "98" in out["note"]


def test_the_guarantee_refuses_a_workspace_on_screen(launch_fake, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    launch_fake["monitors"] = [dict(MONS[0], activeWorkspace={"id": 98, "name": "98"})]
    with pytest.raises(srv.trust.TrustError, match="on screen"):
        srv.launch("foot", workspace="98")
    assert launch_fake["rule"] is None


def test_a_named_agent_workspace_is_not_mistaken_for_a_move(launch_fake, monkeypatch):
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "name:research")
    out = srv.launch("foot")
    assert launch_fake["rule"] == "[workspace name:research silent]"
    assert launch_fake["dispatch"] == [] and "elsewhere" not in out["note"]


def test_with_background_off_launch_re_baselines_the_seat_as_before(launch_fake, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "0")
    srv.launch("foot", workspace="3")
    assert launch_fake["seat"] == 1


def test_a_launch_that_took_focus_re_baselines_the_seat(launch_fake):
    # Hyprland drops a silent rule for the workspace a window maps on
    launch_fake["active"][:] = ["0xhuman", "0xnew"]
    srv.launch("foot", workspace="3")
    assert launch_fake["seat"] == 1


def test_confinement_to_workspaces_narrows_where_apps_open(launch_fake, monkeypatch):
    monkeypatch.setenv("HYPRUSE_CONFINE", "workspace:3,97")
    srv.launch("foot")
    assert launch_fake["rule"] == "[workspace 97 silent]"


def test_confinement_without_an_agent_workspace_opens_as_before(launch_fake, monkeypatch):
    # the agent could not use, move or close an app outside its scope
    monkeypatch.setenv("HYPRUSE_CONFINE", "workspace:3")
    out = srv.launch("foot")
    assert launch_fake["rule"] == "" and "HYPRUSE_CONFINE" in out["note"]


def test_an_app_that_opened_on_screen_says_it_took_focus(launch_fake):
    launch_fake["lands"] = "1"  # the human's workspace, on screen
    out = srv.launch("foot")
    assert "workspace 1" in out["note"] and "took their keyboard focus" in out["note"]
    assert "warning" not in out


def test_under_the_guarantee_an_app_that_opened_on_screen_is_a_warning(launch_fake, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    launch_fake["lands"] = "1"
    out = srv.launch("foot")
    assert "HYPRUSE_BACKGROUND" in out["warning"] and "separate" in out["warning"]


# --- which new window is the launched one -------------------------------------


class _Stream:
    """An event stream that honours the matcher, like the real one."""

    def __init__(self, events_):
        self.events = events_

    def wait_for(self, names, matcher, timeout):
        return next(((n, p) for n, p in self.events if matcher is None or matcher(n, p)), None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def test_launch_waits_for_its_own_window_not_one_the_human_opens(monkeypatch):
    human = ("openwindow", {"address": "0xkitty", "workspace": "1", "class": "kitty"})
    ours = ("openwindow", {"address": "0xfoot", "workspace": "99", "class": "foot"})
    monkeypatch.setattr(srv.events, "EventStream", lambda: _Stream([human, ours]))
    monkeypatch.setattr(srv.hyprctl, "dispatch", lambda *a: None)
    monkeypatch.setattr(srv, "_client_by_address", lambda a: {"address": a})
    accept = srv._launched_window("99", "foot")
    assert srv._launch_and_wait("[workspace 99 silent] foot", 5, accept)["address"] == "0xfoot"


def test_a_single_instance_app_is_recognised_by_its_class(monkeypatch):
    accept = srv._launched_window("99", "firefox --new-window https://example.org")
    assert accept({"address": "0xff", "workspace": "1", "class": "firefox"})
    assert accept({"address": "0xt", "workspace": "1", "class": "org.gnome.TextEditor"}) is False
    assert srv._launched_window("2", "gnome-text-editor")(
        {"workspace": "1", "class": "org.gnome.TextEditor"})
    assert srv._launched_window("2", "env GDK_BACKEND=wayland foot")(
        {"workspace": "1", "class": "foot"})
