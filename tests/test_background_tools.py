"""The acting tools in background mode: what each does to a window the human
is not in, with every desktop and accessibility call stubbed."""

import pytest

from hypruse import server as srv

REAL_UI_READ = srv._ui_read  # bound before the fixture stubs it
REAL_PROVEN_FRAME = srv._proven_frame
REAL_SEND_SHORTCUT = srv.hyprctl.send_shortcut
REAL_DISPATCH = srv.hyprctl.dispatch

CLIENT = {"address": "0xa", "class": "gedit", "title": "doc", "at": [100, 50],
          "size": [800, 600], "pid": 42, "workspace": {"id": 99}, "monitor": 0,
          "floating": False}
MONS = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0, "width": 1920, "height": 1080,
         "scale": 1.0, "activeWorkspace": {"id": 1}, "specialWorkspace": {"id": 0}}]


class _Bus:
    """The a11y bus as the background paths see it once a walk is stubbed:
    only who serves a bus name. A name not in `pids` is the window's app."""

    def __init__(self, pids):
        self.pids = pids

    def conn_pid(self, svc):
        return self.pids.get(svc, CLIENT["pid"])


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
        if handles and s["frame_sure"]:  # like the real one: handles only when asked
            item["_svc"], item["_path"] = "svc", "/save" if s["role"] != "text" else "/name"
        elif handles:
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
    monkeypatch.setattr(
        srv.hinput, "click",
        lambda x, y, button="left", double=False, hold_ms=0: s["clicks"].append((x, y)))
    monkeypatch.setattr(srv.hinput, "type_text", lambda t: s["typed"].append(t))
    # every service is the window's (pid 42) unless a test names one in pids
    s["pids"] = {}
    monkeypatch.setattr(srv.a11y, "connect", lambda: _Bus(s["pids"]))
    monkeypatch.setattr(srv.a11y, "same_app", lambda a, b: a is not None and a == b)
    monkeypatch.setattr(srv.a11y, "has_action", lambda bus, svc, path: s["has_action"])
    s["action_name"], s["focused_after"] = "click", False
    monkeypatch.setattr(srv.a11y, "action_name",
                        lambda bus, svc, path, index=0: s["action_name"])
    monkeypatch.setattr(srv.a11y, "is_focused", lambda bus, svc, path: s["focused_after"])
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
                        lambda bus, svc, path, title="", size=None, owner=None: (svc, path))
    monkeypatch.setattr(srv, "_proven_frame",
                        lambda bus, client, app: app if s["frame_sure"] else None)
    monkeypatch.setattr(srv.a11y, "focused_text",
                        lambda bus, svc, path, owner=None: s["focused_text"])
    s["fields"] = []
    s["fields_complete"] = True
    monkeypatch.setattr(srv.a11y, "editable_fields",
                        lambda bus, svc, path, owner=None: (s["fields"], s["fields_complete"]))
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
    # the seat as the background paths read it before and after acting
    s["seat_after"] = None
    seat_reads = {"n": 0}

    def batch(cmds):
        seat_reads["n"] += 1
        if cmds == ["activewindow", "activeworkspace"]:
            if s["seat_after"] is not None and seat_reads["n"] % 2 == 0:
                return list(s["seat_after"])
            return [{"address": s["active"]}, {"id": 1}]
        return [MONS, [dict(CLIENT, xwayland=s["xwayland"])]]

    monkeypatch.setattr(srv.hyprctl, "batch_query", batch)
    s["toolkit"] = ("gtk", "3.24.52")
    s["focus_on_activate"] = 0
    monkeypatch.setattr(srv.a11y, "toolkit", lambda bus, svc: s["toolkit"])
    monkeypatch.setattr(srv, "_focus_on_activate", lambda addr="": s["focus_on_activate"])
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


def test_marks_carry_what_a_background_press_needs(wired_bg, monkeypatch):
    # the documented flow: marks() on a hidden window, then click_ui(mark=N)
    monkeypatch.setattr(srv, "_grab_env", lambda **kw: (b"IMG", {
        "geometry": [100, 50, 800, 600], "scale": 1.0, "image": [800, 600],
        "format": "jpeg"}))
    monkeypatch.setattr(srv, "_draw_marks", lambda *a: None)
    srv._last_marks.clear()
    srv.marks(window="0xa")
    srv.click_ui(mark=1)
    assert wired_bg["actions"] == [("svc", "/save")]


def test_a_mark_from_an_unproven_frame_is_refused_in_the_background(wired_bg, monkeypatch):
    monkeypatch.setattr(srv, "_grab_env", lambda **kw: (b"IMG", {
        "geometry": [100, 50, 800, 600], "scale": 1.0, "image": [800, 600],
        "format": "jpeg"}))
    monkeypatch.setattr(srv, "_draw_marks", lambda *a: None)
    wired_bg["frame_sure"] = False
    srv._last_marks.clear()
    srv.marks(window="0xa")
    with pytest.raises(srv.trust.TrustError, match="cannot tell"):
        srv.click_ui(mark=1)
    assert wired_bg["actions"] == []


def test_a_press_says_it_pressed_and_claims_no_focus(wired_bg):
    out = str(srv.click_ui(name="Save", window="0xa"))
    assert out.startswith("pressed ") and "focused" not in out.split(";")[0]


def test_an_action_that_only_focuses_says_so_and_reads_the_focus_back(wired_bg):
    # Qt WebEngine's list rows offer SetFocus and nothing else: the row is
    # focused, not opened, and keys sent next go to it only if it took focus
    wired_bg["action_name"] = "SetFocus"
    wired_bg["focused_after"] = True
    out = str(srv.click_ui(name="Save", window="0xa"))
    assert out.startswith("focused ") and "SetFocus" in out
    assert "the app now reports it focused" in out and "not pressed" in out
    assert wired_bg["actions"] == [("svc", "/save")]


def test_a_focus_the_app_does_not_confirm_is_reported(wired_bg):
    wired_bg["action_name"] = "SetFocus"
    out = str(srv.click_ui(name="Save", window="0xa"))
    assert "does not report it focused" in out


def test_a_dry_run_of_a_focus_action_says_focus(wired_bg, monkeypatch):
    monkeypatch.setattr(srv.journal, "dry_run", lambda: True)
    wired_bg["action_name"] = "SetFocus"
    out = str(srv.click_ui(name="Save", window="0xa"))
    assert "would focus" in out and wired_bg["actions"] == []


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


# F1/F10/F49: a key string that carries a window selector after a comma.
# Live (Lua manager), two windows logging their keystrokes: keys aimed at
# 0xa landed in the focused window or any window the regex named, after
# every guard had checked 0xa, and the result said the focus was untouched.
SELECTOR_KEYS = ["x, active", "Return, active", "z, title:WIN_B|", "x, class:^kitty$|\\",
                 "alt+F4, active", "Return, class:.*polkit.*|"]


@pytest.mark.parametrize("keys", SELECTOR_KEYS)
def test_a_selector_in_the_keys_is_refused_before_any_key(wired_bg, keys):
    with pytest.raises(srv.hinput.InputError, match="not a key name"):
        srv.keyboard("key", keys=keys, window="0xa")
    assert wired_bg["shortcuts"] == [] and wired_bg["dispatch"] == []


@pytest.mark.parametrize("prov", [srv.hyprctl.HYPRLANG, srv.hyprctl.LUA])
@pytest.mark.parametrize("keys", ["y", "ctrl+s", "Return", "ctrl+,", "shift+|", "F5", "plus",
                                  "alt+F4"])
def test_the_key_goes_to_the_window_the_guards_checked(wired_bg, monkeypatch, prov, keys):
    # the recipient is the window field of the one string that reaches
    # hyprctl, so that field, and only it, must name the guarded window
    sent = []
    monkeypatch.setattr(srv.hyprctl, "send_shortcut", REAL_SEND_SHORTCUT)
    monkeypatch.setattr(srv.hyprctl, "dispatch", REAL_DISPATCH)
    monkeypatch.setattr(srv.hyprctl, "_provider", prov)
    monkeypatch.setattr(srv.hyprctl, "_run", lambda *a: sent.append(a) or "ok")
    srv.keyboard("key", keys=keys, window="0xa")
    ((verb, *request),) = sent
    assert verb == "dispatch"
    if prov == srv.hyprctl.HYPRLANG:
        name, arg = request
        assert name == "sendshortcut"
        fields = [f.strip() for f in arg.split(",")]
        assert len(fields) == 3 and fields[2] == "address:0xa"
    else:
        (expr,) = request
        assert expr.count("window =") == 1 and expr.endswith('window = "address:0xa" })')


def test_a_selector_in_the_keys_never_reaches_hyprland_from_a_sequence(wired_bg, monkeypatch):
    sent = []
    monkeypatch.setattr(srv.hyprctl, "send_shortcut", REAL_SEND_SHORTCUT)
    monkeypatch.setattr(srv.hyprctl, "dispatch", REAL_DISPATCH)
    monkeypatch.setattr(srv.hyprctl, "_provider", srv.hyprctl.LUA)
    monkeypatch.setattr(srv.hyprctl, "_run", lambda *a: sent.append(a) or "ok")
    out = str(srv.sequence([{"op": "keyboard", "action": "key", "keys": "x, active",
                             "window": "0xa"}]))
    assert "not a key name" in out
    assert sent == []


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


def test_one_field_in_a_walk_cut_short_is_not_the_only_field(wired_bg):
    # a large window may hold more fields past the elements read, so the
    # one found first is not provably where the text belongs
    wired_bg["fields"] = [("svc", "/name", 61)]
    wired_bg["fields_complete"] = False
    srv.keyboard("type", text="hi", window="0xa")
    assert wired_bg["inserted"] == [] and len(wired_bg["shortcuts"]) == 2
    with pytest.raises(srv.trust.TrustError, match="more elements than hypruse reads"):
        srv.keyboard("type", text="上", window="0xa")


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
    monkeypatch.setattr(srv.a11y, "find_elements",
                        lambda bus, s_, p, name="", actionable=True, owner=None: (
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
    with pytest.raises(srv.hyprctl.HyprctlError, match="sent 2 of 4 keys") as err:
        srv.keyboard("type", text="abcd", window="0xa")
    # Hyprland's own error names the key, a character of the typed text
    assert "window gone" not in str(err.value)


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


# --- review fixes: the seat, focus, dry runs, sequence --------------------------


def test_a_press_that_moved_the_humans_focus_says_so(wired_bg):
    wired_bg["seat_after"] = ({"address": "0xa"}, {"id": 99})
    out = str(srv.click_ui(name="Save", window="0xa"))
    assert "focus untouched" not in out and "WARNING" in out and "0xa" in out


def test_keys_that_moved_the_humans_focus_say_so(wired_bg):
    wired_bg["seat_after"] = ({"address": "0xa"}, {"id": 99})
    out = str(srv.keyboard("key", keys="ctrl+n", window="0xa"))
    assert "focus untouched" not in out and "WARNING" in out


def test_focusing_a_gtk3_field_is_refused_when_hyprland_focuses_on_activate(wired_bg):
    # GTK 3 presents its window when a widget takes focus over AT-SPI, and
    # misc:focus_on_activate turns that into a switch of the human's view
    wired_bg["role"] = "text"
    wired_bg["focus_on_activate"] = 1
    with pytest.raises(srv.trust.TrustError, match="focus_on_activate"):
        srv.click_ui(name="Name", window="0xa")
    assert wired_bg["grabbed"] == []
    wired_bg["toolkit"] = ("Qt", "6.11.1")  # Qt does not present its window
    srv.click_ui(name="Name", window="0xa")
    assert wired_bg["grabbed"] == [("svc", "/name")]


def test_a_field_is_told_by_its_flag_not_its_role_name(wired_bg, monkeypatch):
    # GTK4 calls a text field "text box"; pressing its action would activate it
    monkeypatch.setattr(srv, "_ui_read", lambda w="", name="", actionable=True, handles=False: [
        {"role": "text box", "name": "Name", "x": 150, "y": 80, "clickable": True,
         "_svc": "svc", "_path": "/name", "_field": True}])
    srv.click_ui(name="Name", window="0xa")
    assert wired_bg["grabbed"] == [("svc", "/name")] and wired_bg["actions"] == []


def test_a_dry_run_refuses_a_control_without_an_action_like_the_real_run(wired_bg, monkeypatch):
    monkeypatch.setattr(srv.journal, "dry_run", lambda: True)
    wired_bg["has_action"] = False
    with pytest.raises(srv.trust.TrustError, match="no accessibility action"):
        srv.click_ui(name="Save", window="0xa")


def test_a_dry_run_types_nothing(wired_bg, monkeypatch):
    monkeypatch.setattr(srv.journal, "dry_run", lambda: True)
    wired_bg["fields"] = [("svc", "/name", 61)]
    assert "DRY" in str(srv.keyboard("type", text="hi", window="0xa"))
    wired_bg["fields"] = []
    assert "DRY" in str(srv.keyboard("type", text="hi", window="0xa"))
    assert wired_bg["inserted"] == [] and wired_bg["shortcuts"] == []


def test_then_ui_after_a_background_press_that_closed_its_window_shows_no_other(
    wired_bg, monkeypatch
):
    # the focused window is the human's, not the closed window's parent
    def gone(w="", name="", actionable=True, handles=False):
        if w == "0xa" and not handles:
            raise ValueError("window '0xa' not found")
        return [{"role": "push button", "name": "Save", "x": 1, "y": 1, "clickable": True,
                 "_svc": "svc", "_path": "/save", "_field": False}]

    monkeypatch.setattr(srv, "_ui_read", gone)
    out = srv.click_ui(name="Save", window="0xa", then="ui")
    assert "0xa" in out[-1].text and "Save" not in out[-1].text


def test_the_keyboard_on_the_humans_own_window_stays_in_the_foreground(wired_bg):
    wired_bg["active"] = "0xa"
    srv.keyboard("type", text="x", window="0xa")
    assert wired_bg["typed"] == ["x"] and wired_bg["shortcuts"] == []
    assert ("focuswindow", "address:0xa") in wired_bg["dispatch"]


def test_the_guarantee_keeps_click_and_keys_off_the_humans_windows(wired_bg, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    monkeypatch.setattr(srv, "_resolve_window", lambda w="": dict(CLIENT, workspace={"id": 1}))
    with pytest.raises(srv.trust.TrustError, match="agent's workspaces"):
        srv.click_ui(name="Save", window="0xa")
    with pytest.raises(srv.trust.TrustError, match="agent's workspaces"):
        srv.keyboard("type", text="x", window="0xa")
    assert not (wired_bg["actions"] or wired_bg["grabbed"] or wired_bg["inserted"]
                or wired_bg["shortcuts"])


def test_confinement_still_applies_to_background_input(wired_bg, monkeypatch):
    monkeypatch.setenv("HYPRUSE_CONFINE", "class:kitty")
    with pytest.raises(srv.trust.TrustError, match="confinement"):
        srv.click_ui(name="Save", window="0xa")
    with pytest.raises(srv.trust.TrustError, match="confinement"):
        srv.keyboard("type", text="x", window="0xa")


def test_an_auth_dialog_and_a_locked_session_refuse_background_input(wired_bg, monkeypatch):
    monkeypatch.setattr(srv, "_resolve_window",
                        lambda w="": dict(CLIENT, **{"class": "hyprpolkitagent"}))
    with pytest.raises(srv.trust.TrustError, match="authentication"):
        srv.click_ui(name="Save", window="0xa")
    with pytest.raises(srv.trust.TrustError, match="authentication"):
        srv.keyboard("type", text="x", window="0xa")
    monkeypatch.setattr(srv, "_resolve_window", lambda w="": dict(CLIENT))
    monkeypatch.setattr(srv.trust, "session_locked", lambda: "hyprlock")
    with pytest.raises(srv.trust.TrustError, match="locked"):
        srv.click_ui(name="Save", window="0xa")
    with pytest.raises(srv.trust.TrustError, match="locked"):
        srv.keyboard("key", keys="ctrl+s", window="0xa")


def test_a_shifted_keysym_name_goes_through_the_layout(wired_bg):
    srv.keyboard("key", keys="ctrl+plus", window="0xa")
    assert wired_bg["shortcuts"] == [("CTRL SHIFT", "equal", "0xa")]  # us layout


def test_sequence_background_refuses_steps_that_take_the_humans_view(wired_bg):
    out = srv.sequence([{"op": "hypr", "action": "workspace", "workspace": "3"}],
                       background=True, stop_on_change=False, then="none")
    assert "ERROR" in str(out) and wired_bg["dispatch"] == []


def test_the_guarantee_refuses_toggle_floating_without_a_target(wired_bg, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    with pytest.raises(srv.trust.TrustError, match="HYPRUSE_BACKGROUND"):
        srv.hypr("toggle_floating")
    assert wired_bg["dispatch"] == []


def test_an_unproven_window_with_a_password_field_anywhere_refuses_typing(wired_bg):
    wired_bg["frame_sure"] = False
    wired_bg["fields"] = [("svc", "/user", 79), ("svc", "/pw", 40)]
    with pytest.raises(srv.trust.TrustError, match="password"):
        srv.keyboard("type", text="hunter2", window="0xa")
    assert wired_bg["shortcuts"] == []


def test_ui_of_an_unproven_window_says_its_controls_may_be_anothers(wired_bg, monkeypatch):
    monkeypatch.setattr(srv.a11y, "find_elements",
                        lambda bus, s_, p, name="", actionable=True, owner=None: (
        [{"role": "push button", "name": "Save", "extent": (10, 10, 20, 20),
          "clickable": True, "svc": s_, "path": p + "/save"}], False))
    wired_bg["frame_sure"] = False
    assert "another window" in REAL_UI_READ("0xa")[-1]["note"]
    wired_bg["frame_sure"] = True
    assert all("note" not in e for e in REAL_UI_READ("0xa"))


def test_a_root_from_another_app_instance_proves_nothing(monkeypatch):
    # app_for_pid matches a multi-process app by title across every process
    class Bus:
        def conn_pid(self, svc):
            return 777

    monkeypatch.setattr(srv.a11y, "same_app", lambda a, b: False)
    client = dict(CLIENT)
    assert REAL_PROVEN_FRAME(Bus(), client, ("other", "/root")) is None


def test_a_gtk4_field_cannot_take_focus_and_the_rehearsal_says_so(wired_bg, monkeypatch):
    # GTK 4 answers GrabFocus with NOT_SUPPORTED
    wired_bg["role"] = "text"
    wired_bg["toolkit"] = ("GTK", "4.22.4")
    with pytest.raises(srv.trust.TrustError, match="GTK 4"):
        srv.click_ui(name="Name", window="0xa")
    monkeypatch.setattr(srv.journal, "dry_run", lambda: True)
    with pytest.raises(srv.trust.TrustError, match="GTK 4"):
        srv.click_ui(name="Name", window="0xa")
    assert wired_bg["grabbed"] == []


@pytest.mark.parametrize("toolkit", [("gtk", "3.24.52"), ("GAIL", "2.24"), ("", "")])
def test_focusing_is_refused_where_the_window_could_come_forward(wired_bg, toolkit):
    # GTK 3 and 2 present their window; an unreadable toolkit fails closed
    wired_bg["role"] = "text"
    wired_bg["toolkit"] = toolkit
    wired_bg["focus_on_activate"] = 1
    with pytest.raises(srv.trust.TrustError, match="focus_on_activate"):
        srv.click_ui(name="Name", window="0xa")


def _foa(monkeypatch, prop, option):
    def query(what):
        if what.startswith("getprop address:0xa"):
            if prop is None:
                raise srv.hyprctl.HyprctlError("no such window")
            return {"focus_on_activate": prop}
        if option is None:
            raise srv.hyprctl.HyprctlError("unreadable")
        return {"option": "misc:focus_on_activate", "int": option}

    monkeypatch.setattr(srv.hyprctl, "query", query)
    return srv.__dict__["_focus_on_activate"]


def test_a_window_rule_turning_focus_on_activate_on_is_read(monkeypatch):
    foa = _foa(monkeypatch, True, 0)
    assert foa("0xa") == 1
    assert foa() == 0


def test_getprop_false_means_no_rule_so_the_option_decides(monkeypatch):
    # the bug: getprop prints false whenever no rule sets it, and reading
    # that as "off" let a GTK 3 field focus switch the human's workspace
    assert _foa(monkeypatch, False, 1)("0xa") == 1
    assert _foa(monkeypatch, False, 0)("0xa") == 0


def test_an_unreadable_option_stays_unknown_unless_a_rule_says_on(monkeypatch):
    assert _foa(monkeypatch, False, None)("0xa") is None  # callers refuse on None
    assert _foa(monkeypatch, True, None)("0xa") == 1
    assert _foa(monkeypatch, None, 0)("0xa") == 0


def test_a_rehearsed_press_reads_plainly(wired_bg, monkeypatch):
    monkeypatch.setattr(srv.journal, "dry_run", lambda: True)
    out = str(srv.click_ui(name="Save", window="0xa"))
    assert "would press push button 'Save' in gedit through its accessibility action" in out


def test_escape_and_delete_are_keys_too(wired_bg):
    wired_bg["focused_text"] = ("svc", "/name", 79)
    srv.keyboard("type", text="a\x1b", window="0xa")
    assert wired_bg["inserted"] == [] and wired_bg["shortcuts"][-1] == ("", "Escape", "0xa")


def test_typing_a_password_with_consent_is_not_read_back(wired_bg):
    wired_bg["focused_text"] = ("svc", "/pw", 40)
    out = str(srv.keyboard("type", text="hunter2", window="0xa", allow_auth=True))
    assert "password field" in out and "not read back" in out


def test_then_ui_says_gone_only_when_the_window_is(wired_bg, monkeypatch):
    def fails(w="", name="", actionable=True, handles=False):
        if not handles:
            raise ValueError("busctl timed out")
        return [{"role": "push button", "name": "Save", "x": 1, "y": 1, "clickable": True,
                 "_svc": "svc", "_path": "/save", "_field": False}]

    monkeypatch.setattr(srv, "_ui_read", fails)
    out = srv.click_ui(name="Save", window="0xa", then="ui")
    assert "timed out" in out[-1].text and "gone" not in out[-1].text


def test_sequence_then_ui_reads_the_window_its_steps_named(wired_bg, monkeypatch):
    seen = []
    monkeypatch.setattr(srv, "_ui_read", lambda w="", name="", actionable=True, handles=False:
                        seen.append(w) or [{"role": "push button", "name": "Save", "x": 1,
                                            "y": 1, "clickable": True, "_svc": "svc",
                                            "_path": "/save", "_field": False}])
    srv.sequence([{"op": "click_ui", "name": "Save", "window": "0xa"}],
                 stop_on_change=False, then="ui")
    assert seen[-1] == "0xa"


# --- launch, the guarantee and hypr, second pass --------------------------------


def test_a_focus_change_that_was_not_the_new_window_stays_the_humans(launch_fake):
    # under HYPRUSE_STRICT, re-baselining here would absorb the human's change
    launch_fake["active"][:] = ["0xhuman", "0xother"]
    srv.launch("foot")
    assert launch_fake["seat"] == 0


def test_a_launched_window_that_took_focus_says_so(launch_fake):
    launch_fake["active"][:] = ["0xhuman", "0xnew"]
    out = srv.launch("foot", workspace="3")
    assert "took the human's keyboard focus" in out["note"] and launch_fake["seat"] == 1


def test_under_the_guarantee_a_window_that_took_focus_is_a_warning(launch_fake, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    launch_fake["active"][:] = ["0xhuman", "0xnew"]
    assert "HYPRUSE_BACKGROUND" in srv.launch("foot")["warning"]


def test_the_guarantee_note_does_not_offer_background_false(launch_fake, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    assert "background=false" not in srv.launch("foot")["note"]


def test_a_workspace_with_spaces_is_still_checked(launch_fake, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    launch_fake["monitors"] = [dict(MONS[0], activeWorkspace={"id": 98, "name": "98"})]
    with pytest.raises(srv.trust.TrustError, match="on screen"):
        srv.launch("foot", workspace=" 98 ")


def test_background_true_refuses_an_explicit_workspace_on_screen(launch_fake):
    launch_fake["monitors"] = [dict(MONS[0], activeWorkspace={"id": 3, "name": "3"})]
    with pytest.raises(srv.trust.TrustError, match="on screen"):
        srv.launch("foot", workspace="3", background=True)
    srv.launch("foot", workspace="3")  # an explicit request without it, as before


def test_a_disjoint_confinement_under_the_guarantee_says_why(launch_fake, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    monkeypatch.setenv("HYPRUSE_CONFINE", "workspace:3")
    with pytest.raises(srv.trust.TrustError, match="HYPRUSE_CONFINE"):
        srv.launch("foot")


def test_the_guarantee_moves_windows_only_to_agent_workspaces_off_screen(wired_bg, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    monkeypatch.setattr(srv.hyprctl, "query", lambda what: (
        [dict(MONS[0], activeWorkspace={"id": 98, "name": "98"})] if what == "monitors"
        else [dict(CLIENT)]))
    with pytest.raises(srv.trust.TrustError, match="on screen"):
        srv.hypr("move_window", target="0xa", workspace="98")


def test_sequence_background_refuses_toggle_floating_without_a_target(wired_bg):
    out = srv.sequence([{"op": "hypr", "action": "toggle_floating"}],
                       background=True, stop_on_change=False, then="none")
    assert "ERROR" in str(out) and wired_bg["dispatch"] == []


def test_a_wrapped_command_is_recognised_by_the_program_it_runs():
    for command in ("uwsm app -- firefox", "setsid -f firefox", "flatpak run org.mozilla.firefox",
                    "sh -c 'firefox --new-window'", "gtk-launch firefox"):
        assert srv._launched_window("2", command)({"workspace": "1", "class": "firefox"}), command


def test_a_landing_is_recognised_by_the_workspace_id_too(monkeypatch):
    # a workspace with a default name reports that name in the event, not 99
    monkeypatch.setattr(srv, "_client_by_address",
                        lambda a: {"address": a, "workspace": {"id": 99, "name": "agent"}})
    accept = srv._launched_window("99", "foot")
    assert accept({"address": "0xf", "workspace": "agent", "class": "zz"})


# --- HYPRUSE_STRICT guards the human's seat, which background work never touches ----


def _seat_moved(monkeypatch):
    def moved():
        raise srv.trust.TrustError("seat moved since the last action (the human?)")

    monkeypatch.setattr(srv.trust, "guard_seat", moved)


def test_strict_does_not_refuse_background_keys_when_the_human_moves(wired_bg, monkeypatch):
    _seat_moved(monkeypatch)
    out = str(srv.keyboard("type", text="hello", window="0xa"))
    assert "sent 5 keys to 0xa" in out


def test_strict_does_not_refuse_a_background_press(wired_bg, monkeypatch):
    _seat_moved(monkeypatch)
    out = str(srv.click_ui(name="Save", window="0xa"))
    assert "accessibility action" in out and wired_bg["actions"] == [("svc", "/save")]


def test_strict_still_guards_foreground_input(wired_bg, monkeypatch):
    _seat_moved(monkeypatch)
    wired_bg["active"] = "0xa"  # the human is in it: foreground
    with pytest.raises(srv.trust.TrustError, match="seat moved"):
        srv.keyboard("type", text="hello", window="0xa")
    with pytest.raises(srv.trust.TrustError, match="seat moved"):
        srv.click_ui(name="Save", window="0xa")
    assert wired_bg["typed"] == [] and wired_bg["clicks"] == []


# C3 (F2, F11, V3, V6): a control or field whose bus name another process
# serves is refused at the sink, whatever handed it over: the walk drops such
# references, and this also covers a (service, path) restored from marks.
# `_ui_read` is stubbed here, so the walk's own filter is not what refuses.


def test_a_control_another_process_serves_is_never_pressed(wired_bg):
    wired_bg["pids"]["svc"] = 7777  # the control's bus name is not pid 42's
    with pytest.raises(srv.trust.TrustError, match="another process"):
        srv.click_ui(name="Save", window="0xa")
    assert wired_bg["actions"] == [] and wired_bg["grabbed"] == []


def test_a_field_another_process_serves_never_takes_focus(wired_bg):
    wired_bg["role"] = "text"
    wired_bg["pids"]["svc"] = 7777
    with pytest.raises(srv.trust.TrustError, match="another process"):
        srv.click_ui(name="Name", window="0xa")
    assert wired_bg["grabbed"] == []


def test_a_rehearsal_refuses_a_foreign_control_like_the_real_run(wired_bg, monkeypatch):
    monkeypatch.setenv("HYPRUSE_DRYRUN", "1")
    wired_bg["pids"]["svc"] = 7777
    with pytest.raises(srv.trust.TrustError, match="another process"):
        srv.click_ui(name="Save", window="0xa")


def test_a_mark_whose_control_another_process_serves_is_refused(wired_bg, monkeypatch):
    monkeypatch.setattr(srv, "_grab_env", lambda **kw: (b"IMG", {
        "geometry": [100, 50, 800, 600], "scale": 1.0, "image": [800, 600],
        "format": "jpeg"}))
    monkeypatch.setattr(srv, "_draw_marks", lambda *a: None)
    srv._last_marks.clear()
    srv.marks(window="0xa")
    wired_bg["pids"]["svc"] = 7777  # checked when the mark is used, not only when drawn
    with pytest.raises(srv.trust.TrustError, match="another process"):
        srv.click_ui(mark=1)
    assert wired_bg["actions"] == []


def test_text_never_goes_into_a_field_another_process_serves(wired_bg):
    wired_bg["focused_text"] = ("other", "/entry", 79)
    wired_bg["pids"]["other"] = 7777
    with pytest.raises(srv.trust.TrustError, match="another process"):
        srv.keyboard("type", text="hi", window="0xa")
    assert wired_bg["inserted"] == [] and wired_bg["shortcuts"] == []


def test_a_field_of_the_windows_own_web_process_takes_text(wired_bg, monkeypatch):
    # WebKitGTK: the page's field is on the web process's own connection
    wired_bg["focused_text"] = ("web", "/entry", 79)
    wired_bg["pids"]["web"] = 4343
    monkeypatch.setattr(srv.a11y, "same_app", lambda a, b: a == b or (a, b) == (4343, 42))
    srv.keyboard("type", text="hi", window="0xa")
    assert wired_bg["inserted"] == [("web", "/entry", "hi")]
