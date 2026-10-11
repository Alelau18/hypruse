# Background Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an agent act on windows on its own workspaces (99 down to 90 by default) without moving the human's cursor, focus or workspace, per `docs/superpowers/specs/2026-10-11-background-mode-design.md`.

**Architecture:** A new `background.py` owns the policy (off, automatic, guarantee), the agent workspace range, and nothing else. A new `keymap.py` maps characters to `sendshortcut` chords for the active XKB layout through libxkbcommon via ctypes. `a11y.py` gains AT-SPI text and focus primitives, `hyprctl.py` gains `send_shortcut`, and `server.py` routes `click_ui`, `keyboard`, `pointer`, `launch`, `hypr`, `use_bind` and `sequence` through the policy. Results state the method; the journal already records results.

**Tech Stack:** Python 3.11+, busctl (AT-SPI), hyprctl (hyprlang and Lua dispatch), libxkbcommon via ctypes, pytest, ruff.

**Conventions:** run everything from the worktree root. Tests: `uv run --group dev pytest -q`. Lint: `uv run --group dev ruff check .`. Every human-read text (docs, messages, commit bodies) follows the humanizer rules: plain, no em dashes. Commit messages carry no attribution trailers.

---

## File structure

| File | Responsibility |
|---|---|
| `src/hypruse/background.py` (new) | `mode()`, `decide()`, `refuse()`, agent workspace range, `next_workspace()`, `on_agent_workspace()` |
| `src/hypruse/keymap.py` (new) | `chord_for(char) -> (mods, key) or None` for the active layout |
| `src/hypruse/a11y.py` | `has_action`, `grab_focus`, `focused_text`, `insert_text`, `text_of` |
| `src/hypruse/hyprctl.py` | `send_shortcut(mods, key, address)`, Lua form `_lua_sendshortcut` |
| `src/hypruse/trust.py` | guarantee confinement scope `("agent", ...)` |
| `src/hypruse/server.py` | background paths and the `background` parameter |
| `src/hypruse/verbs.py` | `--background` / `--foreground` flags |
| `tests/test_background.py`, `tests/test_keymap.py`, `tests/test_background_tools.py` (new) | unit tests |
| `tests/test_e2e_background.py` (new) | live suite, marker `background` |

---

### Task 1: background policy

**Files:** Create `src/hypruse/background.py`; Test `tests/test_background.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run** `uv run --group dev pytest -q tests/test_background.py`. Expected: collection error, no module `hypruse.background`.

- [ ] **Step 3: Implement**

```python
"""Background mode: the agent works on its own workspaces while the human
keeps theirs. This module holds the policy (who decides that an action runs
in the background) and the agent's workspace range; the tools in server.py
ask it, and the background primitives live in a11y.py and hyprctl.py.

HYPRUSE_BACKGROUND unset: automatic. An action on the window the human is
in runs in the foreground; on any other window, in the background.
HYPRUSE_BACKGROUND=1: the guarantee. Everything runs in the background on
the agent's workspaces, and nothing may take the foreground.
HYPRUSE_BACKGROUND=0: off, exactly 0.12's behaviour.
"""

from __future__ import annotations

import os
from typing import Any, NoReturn

from hypruse import hyprctl, trust

OFF, AUTO, GUARANTEE = "off", "automatic", "guarantee"


def mode() -> str:
    raw = os.environ.get("HYPRUSE_BACKGROUND", "").strip().lower()
    if raw in ("", "auto", "automatic"):
        return AUTO
    if raw in ("0", "off", "false", "no"):
        return OFF
    if raw in ("1", "on", "true", "yes", "always"):
        return GUARANTEE
    raise trust.TrustError(
        f"HYPRUSE_BACKGROUND={raw!r} is malformed (use 1, 0, or leave it unset); "
        "refusing every action until it is fixed"
    )


def decide(target_is_active: bool, explicit: bool | None) -> bool:
    """True when the action runs in the background. The guarantee wins over
    an explicit request, which wins over the automatic rule."""
    m = mode()
    if m == GUARANTEE:
        if explicit is False:
            raise trust.TrustError(
                "background=false is refused: HYPRUSE_BACKGROUND is set, so nothing "
                "may take over the human's screen"
            )
        return True
    if explicit is not None:
        return explicit
    if m == OFF:
        return False
    return not target_is_active


def refuse(what: str) -> NoReturn:
    """Refuse an action the background cannot do, saying what to do instead."""
    if mode() == GUARANTEE:
        hint = "ask the human; HYPRUSE_BACKGROUND is set"
    else:
        hint = (
            "pass background=false to do it in the foreground (that takes over the "
            "human's screen), or ask the human"
        )
    raise trust.TrustError(f"{what}, so it cannot be done in the background: {hint}")
```

- [ ] **Step 4: Run** the tests. Expected: PASS. Run `uv run --group dev ruff check .`.
- [ ] **Step 5: Commit** `feat(background): the policy for who decides an action runs in the background`.

### Task 2: the agent's workspaces

**Files:** Modify `src/hypruse/background.py`; Test `tests/test_background.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run** them. Expected: FAIL, `agent_workspaces` missing.

- [ ] **Step 3: Implement** (append to `background.py`)

```python
def agent_workspaces() -> list[str]:
    """HYPRUSE_AGENT_WORKSPACES as an ordered list of workspace selectors:
    `A-B` counts from A to B, a comma list keeps its order, `name:x` names
    a workspace. Default 99-90. Malformed refuses, like HYPRUSE_CONFINE."""
    raw = os.environ.get("HYPRUSE_AGENT_WORKSPACES", "99-90").strip()
    out: list[str] = []
    for part in (p.strip() for p in raw.split(",")):
        if part.startswith("name:") and part[5:]:
            out.append(part)
            continue
        lo, sep, hi = part.partition("-")
        if sep and lo.isdigit() and hi.isdigit() and int(lo) > 0 and int(hi) > 0:
            a, b = int(lo), int(hi)
            step = -1 if a > b else 1
            out.extend(str(i) for i in range(a, b + step, step))
        elif part.isdigit() and int(part) > 0:
            out.append(part)
        else:
            out = []
            break
    if not out:
        raise trust.TrustError(
            f"HYPRUSE_AGENT_WORKSPACES={raw!r} is malformed (use 99-90, 99,98,50 or "
            "name:x); refusing every action until it is fixed"
        )
    return out


def fill() -> int:
    raw = os.environ.get("HYPRUSE_AGENT_FILL", "1").strip()
    if not raw.isdigit() or int(raw) < 1:
        raise trust.TrustError(f"HYPRUSE_AGENT_FILL={raw!r} must be a whole number of 1 or more")
    return int(raw)


def _matches(selector: str, ws: dict[str, Any]) -> bool:
    if selector.startswith("name:"):
        return ws.get("name") == selector[5:]
    return hyprctl.workspace_ref(ws) == int(selector) or ws.get("name") == selector


def on_agent_workspace(client: dict[str, Any]) -> bool:
    ws = client.get("workspace") or {}
    return any(_matches(s, ws) for s in agent_workspaces())


def next_workspace(clients: list[dict[str, Any]], owned: set[str]) -> str:
    """Where the agent's next app goes: the first workspace in the range with
    fewer than fill() agent windows and none of the human's; when all are
    full, the one with the fewest agent windows."""
    usable: list[tuple[str, int]] = []
    for sel in agent_workspaces():
        here = [c for c in clients if _matches(sel, c.get("workspace") or {})]
        if any(c.get("address") not in owned for c in here):
            continue  # holds a window the agent did not open
        usable.append((sel, len(here)))
    if not usable:
        raise trust.TrustError(
            "every agent workspace holds a window the agent did not open; free one or "
            "widen HYPRUSE_AGENT_WORKSPACES"
        )
    limit = fill()
    for sel, n in usable:
        if n < limit:
            return sel
    return min(usable, key=lambda u: u[1])[0]
```

- [ ] **Step 4: Run** tests and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `feat(background): the agent's workspace range, filled from the top`.

### Task 3: keymap chords for sendshortcut

**Files:** Create `src/hypruse/keymap.py`; Test `tests/test_keymap.py`

- [ ] **Step 1: Write the failing tests** (they need libxkbcommon, which every Wayland system has; skip when absent)

```python
import ctypes.util

import pytest

from hypruse import keymap

pytestmark = pytest.mark.skipif(ctypes.util.find_library("xkbcommon") is None,
                                reason="libxkbcommon not installed")


def test_us_layout():
    m = keymap.Keymap("us")
    assert m.chord("a") == ("", "a")
    assert m.chord("A") == ("SHIFT", "a")
    assert m.chord("1") == ("", "1")
    assert m.chord("!") == ("SHIFT", "1")
    assert m.chord(" ") == ("", "space")


def test_french_azerty():
    m = keymap.Keymap("fr")
    assert m.chord("1") == ("SHIFT", "ampersand")  # digits are shifted on AZERTY
    assert m.chord("&") == ("", "ampersand")
    assert m.chord("@") == ("MOD5", "agrave")  # AltGr
    assert m.chord("é") == ("", "eacute")


def test_a_character_the_layout_cannot_type_is_none():
    assert keymap.Keymap("us").chord("上") is None


def test_control_characters_map_to_their_keys():
    m = keymap.Keymap("us")
    assert m.chord("\n") == ("", "Return")
    assert m.chord("\t") == ("", "Tab")
```

- [ ] **Step 2: Run.** Expected: FAIL, no module `hypruse.keymap`.

- [ ] **Step 3: Implement**

```python
"""Characters to `sendshortcut` chords for an XKB layout.

Hyprland's sendshortcut resolves a key NAME by matching it against each
key's unshifted symbol, so on AZERTY "1" is not found: it is Shift plus the
key whose base symbol is "ampersand". This module builds the layout's
keymap with libxkbcommon (through ctypes, no new dependency; every Wayland
desktop has the library) and answers, for each character, the modifiers
and the base key name to send."""

from __future__ import annotations

import ctypes
import ctypes.util
import functools

# level 0 plain, 1 Shift, 2 AltGr (ISO_Level3_Shift, Mod5 in Hyprland's
# names), 3 Shift+AltGr
_LEVEL_MODS = ("", "SHIFT", "MOD5", "SHIFT MOD5")
_CONTROL = {"\n": "Return", "\r": "Return", "\t": "Tab", "\b": "BackSpace"}


class _RuleNames(ctypes.Structure):
    _fields_ = [(n, ctypes.c_char_p) for n in ("rules", "model", "layout", "variant", "options")]


@functools.cache
def _lib() -> ctypes.CDLL | None:
    path = ctypes.util.find_library("xkbcommon")
    if path is None:
        return None
    lib = ctypes.CDLL(path)
    lib.xkb_context_new.restype = ctypes.c_void_p
    lib.xkb_context_new.argtypes = [ctypes.c_int]
    lib.xkb_keymap_new_from_names.restype = ctypes.c_void_p
    lib.xkb_keymap_new_from_names.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_RuleNames), ctypes.c_int]
    lib.xkb_keymap_min_keycode.argtypes = [ctypes.c_void_p]
    lib.xkb_keymap_max_keycode.argtypes = [ctypes.c_void_p]
    lib.xkb_keymap_num_levels_for_key.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
    lib.xkb_keymap_key_get_syms_by_level.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint32))]
    lib.xkb_keysym_to_utf32.restype = ctypes.c_uint32
    lib.xkb_keysym_to_utf32.argtypes = [ctypes.c_uint32]
    lib.xkb_keysym_get_name.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
    return lib


class Keymap:
    def __init__(self, layout: str, variant: str = "", options: str = ""):
        self._table: dict[str, tuple[str, str]] = {}
        lib = _lib()
        if lib is None:
            return
        ctx = lib.xkb_context_new(0)
        names = _RuleNames(b"evdev", b"pc105", layout.encode(), variant.encode(),
                           options.encode() or None)
        km = lib.xkb_keymap_new_from_names(ctx, ctypes.byref(names), 0)
        if not km:
            return
        for kc in range(lib.xkb_keymap_min_keycode(km), lib.xkb_keymap_max_keycode(km) + 1):
            base = None
            for lvl in range(min(lib.xkb_keymap_num_levels_for_key(km, kc, 0), 4)):
                syms = ctypes.POINTER(ctypes.c_uint32)()
                if lib.xkb_keymap_key_get_syms_by_level(km, kc, 0, lvl, ctypes.byref(syms)) < 1:
                    continue
                buf = ctypes.create_string_buffer(64)
                lib.xkb_keysym_get_name(syms[0], buf, 64)
                if lvl == 0:
                    base = buf.value.decode()
                ch = lib.xkb_keysym_to_utf32(syms[0])
                if ch and base and chr(ch) not in self._table:
                    self._table[chr(ch)] = (_LEVEL_MODS[lvl], base)

    def chord(self, char: str) -> tuple[str, str] | None:
        """(mods, key) to send for `char`, or None when this layout has no key for it."""
        if char in _CONTROL:
            return ("", _CONTROL[char])
        return self._table.get(char)


@functools.cache
def for_layout(layout: str, variant: str = "", options: str = "") -> Keymap:
    return Keymap(layout, variant, options)
```

- [ ] **Step 4: Run** tests and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `feat(keymap): characters to sendshortcut chords for the active layout`.

### Task 4: hyprctl.send_shortcut and the active layout

**Files:** Modify `src/hypruse/hyprctl.py`; Test `tests/test_hyprctl.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_send_shortcut_legacy(monkeypatch):
    sent = []
    monkeypatch.setattr(hyprctl, "_provider", hyprctl.HYPRLANG)
    monkeypatch.setattr(hyprctl, "_run", lambda *a: sent.append(a) or "ok")
    hyprctl.send_shortcut("SHIFT", "ampersand", "0xabc")
    assert sent == [("dispatch", "sendshortcut", "SHIFT, ampersand, address:0xabc")]


def test_send_shortcut_lua():
    expr = hyprctl.lua_dispatch("sendshortcut", ("SHIFT, ampersand, address:0xabc",))
    assert expr == ('hl.dsp.send_shortcut({ mods = "SHIFT", key = "ampersand", '
                    'window = "address:0xabc" })')


def test_active_layout(monkeypatch):
    monkeypatch.setattr(hyprctl, "query", lambda what: {"keyboards": [
        {"main": False, "layout": "us"}, {"main": True, "layout": "fr", "variant": "", "options": ""}]})
    assert hyprctl.active_layout() == ("fr", "", "")
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement**

```python
def _lua_sendshortcut(arg: str) -> str:
    mods, key, window = (p.strip() for p in arg.split(",", 2))
    return (f"hl.dsp.send_shortcut({{ mods = {lua_str(mods)}, key = {lua_str(key)}, "
            f"window = {lua_str(window)} }})")
```

Register `"sendshortcut": _lua_sendshortcut` in `_LUA_DISPATCH`, then:

```python
def send_shortcut(mods: str, key: str, address: str) -> None:
    """Send one key, with modifiers, to a window without focusing it.
    Hyprland moves keyboard focus to the window, sends the key and restores
    focus inside one event-loop step (Actions::pass), so the human's own
    keys cannot interleave. Refuse an XWayland target before calling this:
    between two XWayland windows Hyprland delivers to the focused one."""
    dispatch("sendshortcut", f"{mods}, {key}, address:{address}")


def active_layout() -> tuple[str, str, str]:
    """(layout, variant, options) of the main keyboard; ("us", "", "") if unknown."""
    with contextlib.suppress(Exception):
        kb = next(k for k in query("devices")["keyboards"] if k.get("main"))
        layout = (kb.get("layout") or "us").split(",")[0]
        variant = (kb.get("variant") or "").split(",")[0]
        return layout, variant, kb.get("options") or ""
    return "us", "", ""
```

- [ ] **Step 4: Run** tests and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `feat(hyprctl): send_shortcut in both config dialects, and the active layout`.

### Task 5: AT-SPI text and focus primitives

**Files:** Modify `src/hypruse/a11y.py`; Test `tests/test_a11y.py` (extend `FakeBus` with `GetNActions`, `GrabFocus`, `GetText`, `GetCaretOffset`, `InsertText`)

- [ ] **Step 1: Write the failing tests**

```python
def test_has_action_and_text_primitives():
    entry = ("app", "/entry")
    nodes = {entry: {"role": 79, "name": "", "children": [], "text": "ab", "caret": 1,
                     "n_actions": 0, "states": {_FOCUSED_BIT}}}
    bus = FakeBus(nodes)
    assert a11y.has_action(bus, *entry) is False
    assert a11y.grab_focus(bus, *entry) is True
    assert a11y.insert_text(bus, *entry, "XY") is True
    assert a11y.text_of(bus, *entry) == "aXYb"  # inserted at the caret
```

with `FakeBus.call` handling `GetNActions` (`[node.get("n_actions", 1)]`), `GrabFocus` (`[True]`), `GetCaretOffset` (`[node.get("caret", 0)]`), and `InsertText` (`sig = ("is", pos, text, length)`: splice into `node["text"]`, return `[True]`); `GetText` already exists.

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement**

```python
_EDITABLE = "org.a11y.atspi.EditableText"


def has_action(bus: Bus, svc: str, path: str) -> bool:
    try:
        return int(bus.call(svc, path, _ACTION, "GetNActions")[0]) > 0
    except (A11yError, IndexError, ValueError):
        return False


def grab_focus(bus: Bus, svc: str, path: str) -> bool:
    """Move the app's OWN keyboard focus to this element. Not the
    compositor's: the human's focus is untouched."""
    try:
        return bool(bus.call(svc, path, _COMPONENT, "GrabFocus")[0])
    except (A11yError, IndexError):
        return False


def text_of(bus: Bus, svc: str, path: str) -> str:
    try:
        n = int(bus.prop(svc, path, _TEXT, "CharacterCount"))
        return str(bus.call(svc, path, _TEXT, "GetText", "ii", "0", str(n))[0])
    except (A11yError, IndexError, ValueError):
        return ""


def insert_text(bus: Bus, svc: str, path: str, text: str) -> bool:
    """Insert `text` at the caret, as typing would."""
    try:
        caret = int(bus.call(svc, path, _TEXT, "GetCaretOffset")[0])
        return bool(bus.call(svc, path, _EDITABLE, "InsertText", "isi",
                             str(max(caret, 0)), text, str(len(text)))[0])
    except (A11yError, IndexError, ValueError):
        return False


def focused_text(bus: Bus, app_svc: str, app_path: str) -> tuple[str, str, int] | None:
    """(svc, path, role) of the focused text field in this window, or None:
    where background typing goes when the app has an accessibility tree."""
    stack = [(app_svc, app_path)]
    visited = 0
    while stack and visited < 200:
        svc, path = stack.pop()
        visited += 1
        states = _states(bus, svc, path)
        if _STATE_FOCUSED in states:
            role = _role_num(bus, svc, path)
            return (svc, path, role) if role in _TEXT_ROLES | {PASSWORD_ROLE} else None
        if _STATE_SHOWING in states or (svc, path) == (app_svc, app_path):
            stack.extend(reversed(_children(bus, svc, path)))
    return None
```

Note: a hidden window may not report SHOWING on its subtree; if the live suite (Task 13) shows `focused_text` returning None on hidden windows, walk every child regardless of SHOWING, bounded by the 200-node budget.

- [ ] **Step 4: Run** tests and ruff. **Step 5: Commit** `feat(a11y): text, caret and focus primitives for background input`.

### Task 6: the guarantee confines input to the agent's workspaces

**Files:** Modify `src/hypruse/trust.py`; Test `tests/test_trust.py`

- [ ] **Step 1: Write the failing test**

```python
def test_the_guarantee_confines_to_the_agent_workspaces(monkeypatch):
    monkeypatch.delenv("HYPRUSE_CONFINE", raising=False)
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    monkeypatch.setenv("HYPRUSE_AGENT_WORKSPACES", "99-98")
    trust.guard_client(client(addr="0xa", ws=98))
    with pytest.raises(trust.TrustError, match="agent's workspaces"):
        trust.guard_client(client(addr="0xb", ws=5))
```

- [ ] **Step 2: Run.** Expected: FAIL (no confinement without HYPRUSE_CONFINE).
- [ ] **Step 3: Implement.** In `_confine_scope`, before reading HYPRUSE_CONFINE:

```python
    from hypruse import background  # background imports trust

    if background.mode() == background.GUARANTEE and not os.environ.get("HYPRUSE_CONFINE", "").strip():
        return ("agent", tuple(background.agent_workspaces()))
```

In `_client_in_scope`: `if kind == "agent": from hypruse import background; return background.on_agent_workspace(client)`. In `_describe`: `"the agent's workspaces"` for kind `agent`.

- [ ] **Step 4: Run** the whole suite (confinement is everywhere) and ruff. **Step 5: Commit** `feat(trust): the background guarantee confines input to the agent's workspaces`.

### Task 7: click_ui in the background

**Files:** Modify `src/hypruse/server.py` (`click_ui`, `marks`, `restore_marks`, `_ui_read`); Test `tests/test_background_tools.py`

- [ ] **Step 1: Write the failing tests** (wired like `tests/test_marks.py`; `_ui_read` stubbed to return elements carrying `svc`/`path`; `a11y.connect`, `a11y.has_action`, `a11y.do_action`, `a11y.grab_focus` stubbed to record calls)

```python
def test_click_ui_on_another_window_presses_its_action(wired_bg):
    out = srv.click_ui(name="Save", window="0xa")  # 0xa is not the active window
    assert wired_bg["actions"] == [("svc", "/save")]
    assert wired_bg["clicks"] == [] and wired_bg["dispatch"] == []
    assert "accessibility action" in str(out)


def test_click_ui_on_the_active_window_still_clicks(wired_bg):
    wired_bg["active"] = "0xa"
    srv.click_ui(name="Save", window="0xa")
    assert wired_bg["clicks"] and not wired_bg["actions"]


def test_a_control_without_an_action_is_refused_in_the_background(wired_bg):
    wired_bg["has_action"] = False
    with pytest.raises(srv.trust.TrustError, match="no accessibility action"):
        srv.click_ui(name="Save", window="0xa")


def test_background_false_forces_the_old_path(wired_bg):
    srv.click_ui(name="Save", window="0xa", background=False)
    assert wired_bg["clicks"] and not wired_bg["actions"]


def test_a_text_field_gets_the_apps_own_focus(wired_bg):
    wired_bg["role"] = "text"
    srv.click_ui(name="Name", window="0xa")
    assert wired_bg["grabbed"] == [("svc", "/name")]
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Keep `svc` and `path` on `_ui_read` items under private keys (`_svc`, `_path`) and strip them from what `ui` returns to the agent. In `click_ui`, after the target `client` and element `e` are resolved and before the foreground guards:

```python
    active = (hyprctl.query("activewindow") or {}).get("address")
    if background.decide(client["address"] == active, background_):
        return _click_in_background(client, e, desc, then, allow_auth)
```

(the parameter is named `background` in the signature; inside, bind it to `background_` to avoid shadowing the module). `_click_in_background` runs `trust.guard_client`, `trust.guard_auth_client`, `trust.guard_session_lock(True, allow_auth)`, refuses an element without `_svc` via `background.refuse("this control was not resolved through the accessibility tree")`, then: text roles get `a11y.grab_focus`, others need `a11y.has_action` (else `background.refuse("this control has no accessibility action")`) and `a11y.do_action`. A dry run returns `f"{_DRY} press {desc} through its accessibility action in {cls}"`. Result: `f"pressed {desc} through its accessibility action (no pointer, your focus untouched) in {cls}"`, followed by a read-back: re-read the element with `a11y.element_value` (and whether the window still exists) and append `"; now: checked=true"` / `"; now: value='...'"` / `"; the window closed"`, or `"; no change seen"` when nothing it reports changed. Add a test that a press the app ignored reports "no change seen". Marks store `svc` and `path` per item, and `restore_marks` keeps them when they are strings.

- [ ] **Step 4: Run** the suite and ruff. **Step 5: Commit** `feat(click_ui): press controls through their accessibility action in the background`.

### Task 8: keyboard in the background

**Files:** Modify `src/hypruse/server.py` (`keyboard`); Test `tests/test_background_tools.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_typing_into_a_hidden_text_field_uses_accessibility_text(wired_bg):
    wired_bg["focused_text"] = ("svc", "/name", 79)
    out = srv.keyboard("type", text="hello", window="0xa")
    assert wired_bg["inserted"] == [("svc", "/name", "hello")]
    assert wired_bg["typed"] == [] and wired_bg["dispatch"] == []
    assert "accessibility interface" in str(out)


def test_typing_without_a_text_field_sends_chords(wired_bg):
    wired_bg["focused_text"] = None
    srv.keyboard("type", text="A1", window="0xa")
    assert wired_bg["shortcuts"] == [("SHIFT", "a", "0xa"), ("", "1", "0xa")]  # us layout


def test_a_character_the_layout_lacks_is_refused(wired_bg):
    wired_bg["focused_text"] = None
    with pytest.raises(srv.trust.TrustError, match="no key"):
        srv.keyboard("type", text="上", window="0xa")


def test_keys_to_an_xwayland_window_are_refused(wired_bg):
    wired_bg["xwayland"] = True
    with pytest.raises(srv.trust.TrustError, match="XWayland"):
        srv.keyboard("key", keys="ctrl+s", window="0xa")


def test_a_password_field_needs_allow_auth(wired_bg):
    wired_bg["focused_text"] = ("svc", "/pw", 40)
    with pytest.raises(srv.trust.TrustError, match="password"):
        srv.keyboard("type", text="hunter2", window="0xa")


def test_the_guarantee_requires_a_window(wired_bg, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    with pytest.raises(srv.trust.TrustError, match="window="):
        srv.keyboard("type", text="x")
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** In `keyboard`, after argument validation: `target_win = window or active address`; `bg_on = background.decide(target_win == active, background_)`; under the guarantee with no `window=`, refuse (`"keyboard needs window= on an agent workspace"`). When `bg_on`: guard confinement, auth and session lock as for click_ui; refuse an XWayland target for the sendshortcut paths (`client.get("xwayland")`); for `type`, find `a11y.focused_text`; role 40 without `allow_auth` refuses as a password field; a text role uses `a11y.insert_text`, else map every character with `keymap.for_layout(*hyprctl.active_layout()).chord(ch)` (all characters first, refusing on the first None with "has no key on this keyboard layout", so nothing is half-typed), then `hyprctl.send_shortcut` each. For `key`, convert the combo with `hinput.parse_combo` (modifiers to `SHIFT CTRL ALT SUPER`, the key to its keysym name) and send one `send_shortcut`. Results name the method, as in the spec. After accessibility text input, read the field back with `a11y.text_of` and report it (truncated to 80 characters); a field that did not change reports "no change seen". Add that test.

- [ ] **Step 4: Run** the suite and ruff. **Step 5: Commit** `feat(keyboard): type and press keys in hidden windows`.

### Task 9: pointer, hypr and use_bind under the policy

**Files:** Modify `src/hypruse/server.py`; Test `tests/test_background_tools.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_pointer_over_another_window_is_refused_in_automatic_mode(wired_bg):
    wired_bg["window_at_point"] = "0xa"  # not the active window
    with pytest.raises(srv.trust.TrustError, match="background=false"):
        srv.pointer("click", x=100, y=100)


def test_pointer_over_the_active_window_works(wired_bg):
    wired_bg["window_at_point"] = wired_bg["active"]
    srv.pointer("click", x=100, y=100)


def test_the_guarantee_refuses_pointer_workspace_focus_and_binds(wired_bg, monkeypatch):
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "1")
    for call in (lambda: srv.pointer("click", x=1, y=1),
                 lambda: srv.hypr("workspace", workspace="3"),
                 lambda: srv.hypr("focus_window", target="0xa"),
                 lambda: srv.use_bind("SUPER+F")):
        with pytest.raises(srv.trust.TrustError, match="HYPRUSE_BACKGROUND"):
            call()
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** `pointer`: resolve the topmost window at the point with `trust._windows_under` (floating first, then any); `background.decide(addr == active, background_)`; when it says background, `background.refuse("pointer moves the human's cursor")`. `hypr`: for `workspace` and `focus_window` under the guarantee, refuse with the guarantee message; other actions pass through (confinement already limits them to agent windows under the guarantee). `use_bind`: refuse under the guarantee.

- [ ] **Step 4: Run** the suite and ruff. **Step 5: Commit** `feat(server): pointer, hypr and use_bind follow the background policy`.

### Task 10: launch onto the agent's workspaces

**Files:** Modify `src/hypruse/server.py` (`launch`); Test `tests/test_background_tools.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_launch_without_a_workspace_goes_to_the_next_agent_workspace(launch_fake):
    out = srv.launch("foot")
    assert launch_fake["rule"].startswith("[workspace 99 silent]")
    assert "agent" in out.get("note", "")


def test_launch_background_false_opens_on_the_humans_workspace(launch_fake):
    srv.launch("foot", background=False)
    assert launch_fake["rule"] == ""


def test_launch_with_a_workspace_keeps_it(launch_fake):
    srv.launch("foot", workspace="3")
    assert launch_fake["rule"].startswith("[workspace 3 silent]")
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** In `launch`, when `workspace` is empty and `background.decide(False, background_)` is true, set `workspace = background.next_workspace(hyprctl.query("clients"), trust.owned())` and add `note: "opened on workspace N, one of the agent's (HYPRUSE_AGENT_WORKSPACES); pass background=false to open it on the human's"`. Only skip `remember_seat()` when launched silently: the human's seat did not change.

- [ ] **Step 4: Run** the suite and ruff. **Step 5: Commit** `feat(launch): open apps on the agent's workspaces by default`.

### Task 11: sequence and the CLI

**Files:** Modify `src/hypruse/server.py` (`sequence`), `src/hypruse/verbs.py`; Tests `tests/test_sequence.py`, `tests/test_verbs.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_sequence_background_applies_to_every_step(seq_wired):
    srv.sequence([{"op": "click_ui", "name": "Save", "window": "0xa"}], background=True)
    assert seq_wired["calls"][0]["background"] is True


def test_cli_background_flags():
    assert kwargs_of("click_ui", "--name", "Save", "--background")["background"] is True
    assert kwargs_of("pointer", "click", "1", "2", "--foreground")["background"] is False
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** `sequence(..., background: bool | None = None)` passes `background` into each step's tool call when the step does not set its own. In `verbs.acting`, add a mutually exclusive pair `--background` (store_true, dest background) and `--foreground` (store_false, dest background), default SUPPRESS, for `click_ui`, `keyboard`, `pointer`, `launch`, `sequence`.

- [ ] **Step 4: Run** the suite and ruff. **Step 5: Commit** `feat: background for sequence steps and the CLI`.

### Task 12: tool descriptions and server instructions

**Files:** Modify `src/hypruse/server.py` (docstrings of the acting tools, `_instructions`, `READONLY_INSTRUCTIONS` untouched); Test `tests/test_readonly.py`

- [ ] **Step 1: Write the failing test** asserting the `click_ui`, `keyboard`, `pointer` and `launch` descriptions mention `background`, and the instructions mention the agent's workspaces.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** One or two sentences per tool, humanizer rules: what happens in the background, and that `ui` is the truth for hidden windows because their pixels can be stale.
- [ ] **Step 4: Run.** **Step 5: Commit** `docs(server): tool descriptions explain background mode`.

### Task 13: the live suite

**Files:** Create `tests/test_e2e_background.py`; Modify `pyproject.toml` (register marker `background`, excluded by default like `e2e`)

- [ ] **Step 1: Write the suite.** Launch `yad --form --title=hypruse-bg --field=Agree:CHK --field=Name --button=OK:0` with `server.launch` (lands on an agent workspace), record the seat (active window, active workspace, cursor), then: `click_ui(name="Agree")`, `click_ui(name="Name")` and `keyboard("type", text="Bonjour 123 @", window=addr)`, and read back with `_ui_read`: the check box flipped and the field holds the text. Assert the seat is identical after every step. Finally close the window with `hypr close_window`. Skip unless `HYPRLAND_INSTANCE_SIGNATURE` is set and `yad` exists.
- [ ] **Step 2: Run** `uv run --group dev pytest -q -m background`. Expected: PASS, seat unchanged. If `focused_text` misses on the hidden window, apply the Task 5 note and rerun.
- [ ] **Step 3: Commit** `test: a live background suite that proves the seat never moves`.

### Task 14: docs and changelog

**Files:** Modify `README.md` (a "Background mode" section with the three layers, the workspace range and fill, the limits, and the guarantee for unattended work), `ARCHITECTURE.md` (the background paths), `CHANGELOG.md` (`[Unreleased]` entry), `skills/hypruse/SKILL.md` and `skills/hypruse/references/verbs.md` (`--background`/`--foreground`, workspace range). Run the humanizer rules on every paragraph.

- [ ] **Step 1: Write the docs.** **Step 2:** `grep -c "—\|–"` returns 0 on every changed doc. **Step 3: Commit** `docs: background mode`.

### Task 15: verification and release prep

- [ ] Run the full suite with and without a compositor, the e2e and background suites, ruff, and a wheel/sdist build.
- [ ] Run the verification workflow over `origin/main..HEAD` (review areas: policy, workspaces, keymap, a11y paths, each tool, docs; mutation testing; 3-lens verify), fix what it confirms, and ask the owner before releasing 0.13.0.
