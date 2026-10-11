"""Set-of-Marks capture (marks) and click-by-name/mark (click_ui)."""

import json
import shutil
import subprocess

import pytest
from mcp.types import TextContent

from hypruse import server as srv


def make_client():
    return {
        "address": "0xa", "class": "gedit", "title": "doc",
        "at": [100, 50], "size": [800, 600], "pid": 42, "workspace": {"id": 1},
        "monitor": 0, "floating": False,
    }


MONS = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0, "width": 1920, "height": 1080,
         "scale": 1.0, "activeWorkspace": {"id": 1}, "specialWorkspace": {"id": 0}},
        {"id": 1, "name": "DP-1", "x": 1920, "y": 0, "width": 1920, "height": 1080,
         "scale": 1.0, "activeWorkspace": {"id": 5}, "specialWorkspace": {"id": 0}}]


ELEMENTS = [
    {"role": "push button", "name": "Save", "x": 150, "y": 80, "clickable": True},
    {"role": "push button", "name": "Save As", "x": 250, "y": 80, "clickable": True},
    {"role": "text", "name": "Body", "x": 400, "y": 300, "clickable": True, "value": "hi"},
]

META = {
    "target": "window", "geometry": [100, 50, 800, 600],
    "image": [800, 600], "format": "jpeg", "scale": 1.0,
}


@pytest.fixture
def wired(monkeypatch):
    calls = {"clicks": [], "dispatch": []}
    client = make_client()
    monkeypatch.setattr(srv.safety, "touch", lambda *a: None)
    monkeypatch.setattr(srv, "_resolve_window", lambda w="": client)
    monkeypatch.setattr(
        srv, "_ui_read",
        lambda w="", name="", actionable=True, handles=False: [
            e for e in ELEMENTS if name.lower() in e["name"].lower()
        ],
    )
    monkeypatch.setattr(srv.hyprctl, "dispatch", lambda *a: calls["dispatch"].append(a))
    monkeypatch.setattr(
        srv.hinput, "click",
        lambda x, y, button="left", double=False: calls["clicks"].append((x, y, button)),
    )
    monkeypatch.setattr(srv.time, "sleep", lambda s: None)
    # no layer surface covers anything unless a test says so (the real
    # check would query the live compositor)
    monkeypatch.setattr(srv.trust, "covering_layer", lambda x, y: None)
    monkeypatch.setattr(srv.trust, "_read_layers", lambda: [])
    # one monitor, and no other window over the point, unless a test says so
    monkeypatch.setattr(srv.trust, "_click_scene", lambda: (MONS, []))
    calls["client"] = client
    return calls


def test_click_ui_exact_name_wins_over_substring(wired):
    # 'Save' substring-matches both buttons, but exactly one exact match
    out = srv.click_ui(name="Save")
    assert wired["clicks"] == [(150, 80, "left")]
    assert ("focuswindow", "address:0xa") in wired["dispatch"]  # focused first
    assert "Save" in out


def test_click_ui_ambiguous_returns_candidates(wired):
    out = srv.click_ui(name="Sav")
    assert wired["clicks"] == []  # never guesses
    assert isinstance(out, list) and "ambiguous" in out[0].text
    names = [e["name"] for e in json.loads(out[1].text)]
    assert names == ["Save", "Save As"]


def test_click_ui_index_disambiguates(wired):
    out = srv.click_ui(name="Sav", index=1)
    assert wired["clicks"] == [(250, 80, "left")]
    assert "Save As" in out
    with pytest.raises(ValueError, match="out of range"):
        srv.click_ui(name="Sav", index=5)


def test_click_ui_requires_exactly_one_selector(wired):
    with pytest.raises(ValueError, match="exactly one"):
        srv.click_ui()
    with pytest.raises(ValueError, match="exactly one"):
        srv.click_ui(name="Save", mark=1)


def test_click_ui_no_tree_falls_back(wired, monkeypatch):
    monkeypatch.setattr(srv, "_ui_read", lambda *a, **k: "gedit exposes no tree")
    out = srv.click_ui(name="Save")
    assert out == "gedit exposes no tree"
    assert wired["clicks"] == []


def test_marks_builds_legend_and_stores_relative_offsets(wired, monkeypatch):
    monkeypatch.setattr(srv, "_grab_env", lambda **kw: (b"IMG", dict(META)))
    drawn = {}

    def fake_draw(data, fmt, points):
        drawn["points"] = points
        return b"MARKED"

    monkeypatch.setattr(srv, "_draw_marks", fake_draw)
    monkeypatch.setattr(
        srv, "_package", lambda data, meta: [TextContent(type="text", text=f"pkg:{len(data)}")]
    )
    out = srv.marks()
    # mark pixels: (global - origin) * scale
    assert drawn["points"] == [(1, 50, 30), (2, 150, 30), (3, 300, 250)]
    legend = json.loads(out[-1].text)["legend"]
    assert [x["mark"] for x in legend] == [1, 2, 3]
    assert legend[2]["value"] == "hi"
    # stored offsets are window-relative, re-anchored at click time
    assert srv._last_marks["items"][1] == {"dx": 50, "dy": 30, "label": "push button 'Save'",
                                           "role": "push button"}
    assert out[0].text == "pkg:6"  # the MARKED image was packaged, not the original


def test_marks_pixel_math_honors_scale(wired, monkeypatch):
    meta = dict(META, scale=2.0)
    monkeypatch.setattr(srv, "_grab_env", lambda **kw: (b"IMG", meta))
    drawn = {}
    monkeypatch.setattr(
        srv, "_draw_marks", lambda d, f, p: drawn.update(points=p) or b"M"
    )
    monkeypatch.setattr(srv, "_package", lambda data, meta: [])
    srv.marks()
    assert drawn["points"][0] == (1, 100, 60)  # (150-100)*2, (80-50)*2


def test_marks_without_imagemagick_returns_legend(wired, monkeypatch):
    monkeypatch.setattr(srv, "_grab_env", lambda **kw: (b"IMG", dict(META)))
    monkeypatch.setattr(srv, "_draw_marks", lambda *a: None)
    out = srv.marks()
    assert "ImageMagick not found" in out[0].text
    assert json.loads(out[1].text)["legend"]  # coordinates still exact


def test_marks_no_tree_falls_back(wired, monkeypatch):
    monkeypatch.setattr(srv, "_ui_read", lambda *a, **k: "no tree")
    assert srv.marks() == "no tree"


def test_click_mark_reanchors_to_moved_window(wired, monkeypatch):
    monkeypatch.setattr(srv, "_grab_env", lambda **kw: (b"IMG", dict(META)))
    monkeypatch.setattr(srv, "_draw_marks", lambda *a: b"M")
    monkeypatch.setattr(srv, "_package", lambda data, meta: [])
    srv.marks()
    wired["client"]["at"] = [300, 200]  # the window moved since the capture
    out = srv.click_ui(mark=1)
    assert wired["clicks"] == [(350, 230, "left")]  # 300+50, 200+30
    assert "mark 1" in out


def test_click_unknown_mark_raises(wired):
    srv._last_marks = {}
    with pytest.raises(ValueError, match="no mark"):
        srv.click_ui(mark=7)


@pytest.mark.skipif(shutil.which("magick") is None, reason="needs imagemagick")
def test_draw_marks_real_imagemagick(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    src = tmp_path / "src.jpg"
    subprocess.run(["magick", "-size", "60x40", "xc:white", str(src)], check=True)
    from hypruse import screenshot as shot

    out = srv._draw_marks(src.read_bytes(), "jpeg", [(1, 15, 15), (12, 45, 25)])
    assert out is not None and out != src.read_bytes()
    assert shot.image_size(out) == (60, 40)  # dims unchanged, marks drawn


def test_click_ui_then_ui_observes_the_clicked_window(wired, monkeypatch):
    # the click may hand focus to a dialog it spawned; then='ui' must show
    # the window the agent clicked, not whatever holds focus afterwards
    reads = []

    def ui_read(w="", name="", actionable=True, handles=False):
        reads.append(w)
        return [e for e in ELEMENTS if name.lower() in e["name"].lower()]

    monkeypatch.setattr(srv, "_ui_read", ui_read)
    srv.click_ui(name="Body", then="ui")
    assert reads == ["0xa", "0xa"]  # resolution, then the fused observation


def test_click_ui_refuses_under_a_focus_stealing_layer(wired, monkeypatch):
    # a launcher over the point would swallow the click while the result
    # claimed success; the refusal must come before any side effect
    # a 1x1 launcher exactly at the control's click point: refused only if
    # the guard checks the ACTUAL point
    rofi = {"namespace": "rofi", "kind": "launcher", "level": "overlay",
            "geometry": [150, 80, 1, 1]}
    monkeypatch.setattr(srv.trust, "_read_layers", lambda: [rofi])
    with pytest.raises(srv.trust.TrustError, match="rofi"):
        srv.click_ui(name="Save")
    assert wired["clicks"] == []
    assert wired["dispatch"] == []  # not even focused


def test_click_ui_refuses_while_the_session_is_locked(wired, monkeypatch):
    # click_ui is one of the three tools the session-lock guard protects;
    # without this the guard is dead code at this call site (suite green)
    monkeypatch.setattr(srv.trust, "session_locked", lambda: "hyprlock")
    with pytest.raises(srv.trust.TrustError, match="session is locked"):
        srv.click_ui(name="Save")
    assert wired["clicks"] == []
    assert wired["dispatch"] == []  # refused before focusing


def test_click_ui_refuses_under_lock_even_with_allow_auth(wired, monkeypatch):
    # click_ui always targets a specific window control, so under a lock
    # the click cannot reach it: allow_auth (drive the prompt) does not
    # apply, since there is no "click a control by name" on a lock screen
    monkeypatch.setattr(srv.trust, "session_locked", lambda: "hyprlock")
    with pytest.raises(srv.trust.TrustError, match="cannot reach the requested window"):
        srv.click_ui(name="Save", allow_auth=True)
    assert wired["clicks"] == []
    assert wired["dispatch"] == []


def test_click_ui_confinement_wiring_refuses_out_of_scope(wired, monkeypatch):
    # integration: guard_client is real here; the 'gedit' target is out of
    # a kitty-only scope, so the click is refused before any side effect
    monkeypatch.setenv("HYPRUSE_CONFINE", "class:kitty")
    with pytest.raises(srv.trust.TrustError, match="confinement scope"):
        srv.click_ui(name="Save")
    assert wired["clicks"] == []
    assert wired["dispatch"] == []


def test_click_ui_auth_wiring_refuses_polkit(wired, monkeypatch):
    wired["client"]["class"] = "hyprpolkitagent"
    with pytest.raises(srv.trust.TrustError, match="authentication dialog"):
        srv.click_ui(name="Save")
    assert wired["clicks"] == []


# --- click_ui checks the point it will click (0.12.1 review rounds) -------

AUTH_DIALOG = {"address": "0xp", "class": "hyprpolkitagent", "floating": True,
               "at": [100, 50], "size": [400, 200], "workspace": {"id": 1}, "monitor": 0}


def _scene(monkeypatch, clients, fallthrough=0):
    monkeypatch.setattr(srv.trust, "_click_scene", lambda: (MONS, clients))
    monkeypatch.setattr(srv.trust, "_special_fallthrough", lambda: fallthrough)


def test_click_ui_refuses_a_floating_auth_dialog_over_the_control(wired, monkeypatch):
    # Hyprland's hit test puts a floating window above any tiled one
    _scene(monkeypatch, [AUTH_DIALOG])
    with pytest.raises(srv.trust.TrustError, match="authentication dialog"):
        srv.click_ui(name="Save")
    assert wired["clicks"] == [] and wired["dispatch"] == []
    monkeypatch.setattr(srv.journal, "dry_run", lambda: True)
    with pytest.raises(srv.trust.TrustError, match="authentication dialog"):
        srv.click_ui(name="Save")  # the dry run rehearses the refusal too


def test_click_ui_through_an_auth_dialog_needs_allow_auth(wired, monkeypatch):
    _scene(monkeypatch, [AUTH_DIALOG])
    srv.click_ui(name="Save", allow_auth=True)
    assert wired["clicks"] == [(150, 80, "left")]


def _check(client, x=150, y=80, **kw):
    return srv.trust.check_click_point(client, x, y, **kw)


def test_a_floating_target_does_not_hide_a_floating_auth_dialog(monkeypatch):
    # focusing a floating window does not raise it on Hyprland 0.56
    target = dict(make_client(), floating=True)
    _scene(monkeypatch, [AUTH_DIALOG])
    with pytest.raises(srv.trust.TrustError, match="authentication dialog"):
        _check(target)


def test_a_pinned_window_over_the_point_counts(monkeypatch):
    monkeypatch.setenv("HYPRUSE_CONFINE", "class:gedit")
    pip = {"address": "0xq", "class": "mpv", "floating": True, "pinned": True,
           "at": [120, 60], "size": [200, 120], "workspace": {"id": 5}, "monitor": 1}
    _scene(monkeypatch, [pip])
    with pytest.raises(srv.trust.TrustError, match="confinement"):
        _check(dict(make_client(), floating=True))


def test_a_floating_window_from_another_monitor_counts(monkeypatch):
    # a floating window can hang across a monitor edge
    stray = dict(AUTH_DIALOG, address="0xs", workspace={"id": 5}, monitor=1)
    _scene(monkeypatch, [stray])
    with pytest.raises(srv.trust.TrustError, match="authentication dialog"):
        _check(make_client())


def test_windows_that_cannot_cover_the_target_do_not_count(monkeypatch):
    tiled = dict(AUTH_DIALOG, address="0xt", floating=False)  # tiled: no overlap
    tab = dict(AUTH_DIALOG, address="0xg", visible=False)  # an inactive tab
    replaced = dict(AUTH_DIALOG, address="0xe", workspace={"id": 9}, monitor=0)
    other_tiled = dict(AUTH_DIALOG, address="0xo", floating=False,
                       workspace={"id": 5}, monitor=1)
    _scene(monkeypatch, [tiled, tab, replaced, other_tiled])
    _check(make_client())


def test_a_scratchpad_focusing_will_hide_does_not_count_before_focus(monkeypatch):
    # with input:special_fallthrough 0 (the default), focusing a window on a
    # regular workspace hides the monitor's scratchpad
    monkeypatch.setenv("HYPRUSE_CONFINE", "class:gedit")
    pad = {"address": "0xk", "class": "kitty", "floating": False, "at": [0, 0],
           "size": [1920, 1080], "workspace": {"id": -98, "name": "special:term"},
           "monitor": 0}
    mons = [dict(MONS[0], specialWorkspace={"id": -98, "name": "special:term"}), MONS[1]]
    monkeypatch.setattr(srv.trust, "_click_scene", lambda: (mons, [pad]))
    monkeypatch.setattr(srv.trust, "_special_fallthrough", lambda: 0)
    _check(make_client())  # before focus: it will be hidden
    with pytest.raises(srv.trust.TrustError, match="confinement"):
        _check(make_client(), focused=True)  # still up after focusing: it is over
    monkeypatch.setattr(srv.trust, "_special_fallthrough", lambda: None)
    with pytest.raises(srv.trust.TrustError, match="confinement"):
        _check(make_client())  # cannot tell: fail closed


def test_click_ui_refuses_a_point_off_every_monitor(monkeypatch):
    # Hyprland would move the click to the nearest monitor edge
    off = dict(make_client(), at=[-500, 50])
    _scene(monkeypatch, [])
    with pytest.raises(srv.trust.TrustError, match="off every monitor"):
        _check(off, x=-300, y=80)


def test_click_ui_refuses_a_point_outside_its_window(monkeypatch):
    _scene(monkeypatch, [])
    with pytest.raises(srv.trust.TrustError, match="outside the window"):
        _check(make_client(), x=1500, y=80)


def test_a_mark_on_a_resized_window_must_be_taken_again(wired, monkeypatch):
    srv._last_marks.clear()
    srv._last_marks.update({"window": "0xa", "class": "gedit", "ts": srv.time.time(),
                            "size": [1200, 600],
                            "items": {1: {"dx": 50, "dy": 30, "label": "push button 'Save'"}}})
    with pytest.raises(ValueError, match="resized"):
        srv.click_ui(mark=1)
    assert wired["clicks"] == []


def test_a_moved_window_tells_a_mark_caller_to_take_marks_again(wired, monkeypatch):
    srv._last_marks.clear()
    srv._last_marks.update({"window": "0xa", "class": "gedit", "ts": srv.time.time(),
                            "size": [800, 600],
                            "items": {1: {"dx": 50, "dy": 30, "label": "push button 'Save'"}}})
    moved = dict(wired["client"], at=[300, 200])
    seen = iter([wired["client"], moved])
    monkeypatch.setattr(srv, "_resolve_window", lambda w="": next(seen))
    with pytest.raises(srv.trust.TrustError, match="marks"):
        srv.click_ui(mark=1)
    assert wired["clicks"] == []


def test_click_ui_refuses_when_focusing_moved_the_window(wired, monkeypatch):
    moved = dict(wired["client"], at=[300, 200])
    seen = iter([wired["client"], moved])
    monkeypatch.setattr(srv, "_resolve_window", lambda w="": next(seen))
    with pytest.raises(srv.trust.TrustError, match="moved"):
        srv.click_ui(name="Save")
    assert wired["clicks"] == []


# A control can hang over onto another monitor. With a scratchpad up there,
# Hyprland's hit test on that monitor considers only the scratchpad (and
# pinned windows) at special_fallthrough 0, so the click cannot reach the
# target; at 1 the scratchpad's windows come first.
def _overhang(monkeypatch, fallthrough, pad_covers=True):
    monkeypatch.setenv("HYPRUSE_CONFINE", "class:gedit")
    target = dict(make_client(), floating=True, at=[1700, 200], size=[800, 500],
                  workspace={"id": 5}, monitor=1)
    mons = [dict(MONS[0], specialWorkspace={"id": -98, "name": "special:term"}), MONS[1]]
    pad = {"address": "0xk", "class": "kitty", "floating": False,
           "at": [0, 0] if pad_covers else [0, 600], "size": [1920, 1080 if pad_covers else 480],
           "workspace": {"id": -98, "name": "special:term"}, "monitor": 0}
    monkeypatch.setattr(srv.trust, "_click_scene", lambda: (mons, [pad]))
    monkeypatch.setattr(srv.trust, "_special_fallthrough", lambda: fallthrough)
    return target


@pytest.mark.parametrize("fallthrough", [0, None])
def test_a_scratchpad_on_the_points_monitor_refuses_at_fallthrough_0(monkeypatch, fallthrough):
    target = _overhang(monkeypatch, fallthrough)
    for focused in (False, True):
        with pytest.raises(srv.trust.TrustError, match="scratchpad"):
            _check(target, x=1800, y=300, focused=focused)


def test_at_fallthrough_1_the_scratchpads_own_windows_count(monkeypatch):
    target = _overhang(monkeypatch, 1)
    with pytest.raises(srv.trust.TrustError, match="confinement"):
        _check(target, x=1800, y=300)
    target = _overhang(monkeypatch, 1, pad_covers=False)
    _check(target, x=1800, y=300)  # the scratchpad window is not at the point


@pytest.mark.parametrize("reply, expect", [
    ({"option": "input:special_fallthrough", "int": 0, "set": False}, 0),  # hyprland.conf
    ({"option": "input:special_fallthrough", "bool": True, "set": True}, 1),  # hyprland.lua
    ({"option": "input:special_fallthrough"}, None),
])
def test_special_fallthrough_reads_both_config_managers(monkeypatch, reply, expect):
    from hypruse.trust import _special_fallthrough as real

    monkeypatch.setattr(srv.trust.hyprctl, "query", lambda what: reply)
    assert real() == expect


# Hyprland's hit test on the monitor under the point: pinned windows, then
# that monitor's scratchpad, then floating windows (no monitor test), then
# only that monitor's own tiled windows.
def test_a_tiled_target_takes_no_clicks_on_another_monitor(monkeypatch):
    target = dict(make_client(), at=[1500, 50], size=[960, 1000], monitor=0)
    _scene(monkeypatch, [])
    for focused in (False, True):
        with pytest.raises(srv.trust.TrustError, match="own monitor"):
            _check(target, x=2000, y=300, focused=focused)  # (2000, 300) is on DP-1


def test_a_pinned_target_reaches_past_another_monitors_scratchpad(monkeypatch):
    target = _overhang(monkeypatch, 0)
    target["pinned"] = True
    _check(target, x=1800, y=300)


def test_a_target_on_its_own_open_scratchpad_reaches_past_another(monkeypatch):
    target = _overhang(monkeypatch, 0)
    notes = {"id": -97, "name": "special:notes"}
    target["workspace"] = notes
    mons = [dict(MONS[0], specialWorkspace={"id": -98, "name": "special:term"}),
            dict(MONS[1], specialWorkspace=notes)]
    pad = {"address": "0xk", "class": "kitty", "floating": False, "at": [0, 600],
           "size": [1920, 480], "workspace": {"id": -98, "name": "special:term"}, "monitor": 0}
    monkeypatch.setattr(srv.trust, "_click_scene", lambda: (mons, [pad]))
    _check(target, x=1800, y=300)


def test_another_monitors_scrolled_out_scratchpad_does_not_cover_this_one(monkeypatch):
    # its rect lies over monitor 0, but Hyprland's scratchpad pass on monitor
    # 0 takes only monitor 0's scratchpad
    monkeypatch.setenv("HYPRUSE_CONFINE", "class:gedit")
    column = {"address": "0xc", "class": "kitty", "floating": False, "at": [1000, 0],
              "size": [1200, 1080], "workspace": {"id": -98, "name": "special:term"},
              "monitor": 1}
    mons = [MONS[0], dict(MONS[1], specialWorkspace={"id": -98, "name": "special:term"})]
    monkeypatch.setattr(srv.trust, "_click_scene", lambda: (mons, [column]))
    monkeypatch.setattr(srv.trust, "_special_fallthrough", lambda: 1)
    wide = dict(make_client(), size=[1700, 600])  # reaches x 1800 on monitor 0
    for focused in (False, True):
        _check(wide, x=1500, y=300, focused=focused)  # inside both rects, on monitor 0


# Hyprland's own permission prompt (hyprland-dialog, Deny / Allow once /
# Allow and remember) has priority focus: the pointer goes to it before
# even the pinned pass. A click there can grant a permission.
PERMISSION = {"address": "0xh", "class": "hyprland-dialog", "floating": True,
              "at": [100, 50], "size": [500, 300], "workspace": {"id": 1}, "monitor": 0}


@pytest.mark.parametrize("focused", [False, True])
def test_the_permission_prompt_counts_even_over_a_pinned_target(monkeypatch, focused):
    _scene(monkeypatch, [PERMISSION])
    pinned = dict(make_client(), floating=True, pinned=True)
    with pytest.raises(srv.trust.TrustError, match="Hyprland's own dialog"):
        _check(pinned, focused=focused)
    monkeypatch.setenv("HYPRUSE_AUTH_GUARD", "0")
    monkeypatch.setenv("HYPRUSE_CONFINE", "class:gedit")
    with pytest.raises(srv.trust.TrustError, match="confinement"):
        _check(pinned, focused=focused)


def test_click_ui_clicks_nothing_under_the_permission_prompt(wired, monkeypatch):
    wired["client"]["pinned"] = wired["client"]["floating"] = True
    _scene(monkeypatch, [PERMISSION])
    with pytest.raises(srv.trust.TrustError):
        srv.click_ui(name="Save")
    assert wired["clicks"] == [] and wired["dispatch"] == []


def test_click_ui_refuses_a_tiled_target_on_another_monitor(wired, monkeypatch):
    wide = dict(wired["client"], at=[1500, 50], size=[960, 1000])
    monkeypatch.setattr(srv, "_resolve_window", lambda w="": wide)
    monkeypatch.setattr(srv, "_ui_read", lambda w="", name="", actionable=True, handles=False: [
        {"role": "push button", "name": "Save", "x": 2000, "y": 300, "clickable": True}])
    _scene(monkeypatch, [])
    with pytest.raises(srv.trust.TrustError, match="own monitor"):
        srv.click_ui(name="Save")
    assert wired["clicks"] == [] and wired["dispatch"] == []



@pytest.mark.parametrize("target", [
    {"floating": True, "pinned": True}, {"floating": True}, {"fullscreen": 1}])
def test_a_tiled_permission_prompt_still_takes_the_click(monkeypatch, target):
    # its priority is keyed on the dialog process, not on floating: a user
    # rule (or an agent toggling it) can tile it and it still comes first
    _scene(monkeypatch, [dict(PERMISSION, floating=False)])
    with pytest.raises(srv.trust.TrustError, match="Hyprland's own dialog"):
        _check(dict(make_client(), **target))
