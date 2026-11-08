import json
import re

import pytest

from hypruse import hyprctl


@pytest.mark.parametrize(
    "mask,names",
    [
        (0, []),
        (64, ["SUPER"]),
        (5, ["CTRL", "SHIFT"]),
        (65, ["SUPER", "SHIFT"]),
        (72, ["SUPER", "ALT"]),
    ],
)
def test_modmask_decode(mask, names):
    assert hyprctl.modmask_to_names(mask) == names


RAW = [
    {
        "modmask": 64,
        "key": "Q",
        "dispatcher": "exec",
        "arg": "kitty",
        "has_description": True,
        "description": "open terminal",
        "submap": "",
        "mouse": False,
        "keycode": 0,
    },
    {
        "modmask": 64,
        "key": "mouse:272",
        "dispatcher": "movewindow",
        "arg": "",
        "submap": "",
        "mouse": True,
        "keycode": 0,
    },
    {
        "modmask": 65,
        "key": "",
        "dispatcher": "movetoworkspace",
        "arg": "2",
        "submap": "",
        "mouse": False,
        "keycode": 10,
    },
    {
        "modmask": 0,
        "key": "escape",
        "dispatcher": "submap",
        "arg": "reset",
        "submap": "resize",
        "mouse": False,
        "keycode": 0,
    },
]


def test_parse_binds_shapes():
    parsed = hyprctl.parse_binds(RAW)
    combos = [b["combo"] for b in parsed]
    assert combos == ["SUPER+Q", "SUPER+SHIFT+code:10", "escape"]
    assert parsed[0]["description"] == "open terminal"
    assert parsed[0]["action"] == "exec" and parsed[0]["arg"] == "kitty"
    assert "description" not in parsed[1]
    assert parsed[2]["submap"] == "resize"


def test_parse_binds_drops_mouse_binds():
    parsed = hyprctl.parse_binds(RAW)
    assert not any("mouse" in b["combo"] for b in parsed)


# A Lua config binds Lua values, not named dispatchers: Hyprland 0.56
# reports every one as `__lua` with a registry index, and never sets the
# `mouse` flag.
LUA_RAW = [
    {
        "modmask": 64,
        "key": "Q",
        "dispatcher": "__lua",
        "arg": "37",
        "has_description": True,
        "description": "open terminal",
        "submap": "",
        "mouse": False,
        "keycode": 0,
    },
    {
        "modmask": 64,
        "key": "mouse:272",
        "dispatcher": "__lua",
        "arg": "41",
        "submap": "",
        "mouse": False,  # the Lua manager never sets this
        "keycode": 0,
    },
    {
        "modmask": 0,
        "key": "switch:on:Lid",
        "dispatcher": "__lua",
        "arg": "42",
        "submap": "",
        "mouse": False,
        "keycode": 0,
    },
    {
        "modmask": 64,
        "key": "mouse_down",  # scroll, not a button: still a real bind
        "dispatcher": "__lua",
        "arg": "43",
        "submap": "",
        "mouse": False,
        "keycode": 0,
    },
]


def test_parse_binds_marks_lua_closures_and_drops_their_registry_index():
    parsed = hyprctl.parse_binds(LUA_RAW)
    assert parsed == [
        {"combo": "SUPER+Q", "action": "lua", "description": "open terminal"},
        {"combo": "SUPER+mouse_down", "action": "lua"},
    ]
    # the index is a pointer into the compositor's own Lua state: it means
    # nothing here, and offering it would invite an agent to send it back
    assert "arg" not in parsed[0]


def _lua57(key, handler, arg, **extra):
    return {"modmask": 64, "key": key, "dispatcher": handler, "arg": arg, "submap": "",
            "mouse": False, "keycode": 0, **extra}


# 0.57 reports the bound value as Lua prints it (hyprwm/Hyprland#15722)
LUA57_RAW = [
    _lua57("L", "HL.Dispatcher(close)", "10", description="close window"),
    _lua57("J", "function: 0x558a32c398b0", "7"),
    _lua57("K", "HL.Dispatcher(exec_cmd)", "6"),
    _lua57("P", "function: builtin: 0x7f00aa", "12"),
    _lua57("mouse:272", "HL.Dispatcher(drag)", "13"),
]


def test_parse_binds_reads_hyprland_057_lua_binds():
    assert hyprctl.parse_binds(LUA57_RAW) == [
        {"combo": "SUPER+L", "action": "lua", "dispatcher": "close", "description": "close window"},
        {"combo": "SUPER+J", "action": "lua"},
        {"combo": "SUPER+K", "action": "lua", "dispatcher": "exec_cmd"},
        {"combo": "SUPER+P", "action": "lua"},
    ]


@pytest.mark.parametrize("handler,kind", [
    ("__lua", (True, "")),
    ("HL.Dispatcher(close)", (True, "close")),
    ("HL.Dispatcher(INVALID)", (True, "")),
    ("function: 0x55d0", (True, "")),
    ("exec", (False, "")),
    ("function", (False, "")),
    ("HL.Dispatcher", (False, "")),
])
def test_lua_bind_kind(handler, kind):
    assert hyprctl.lua_bind_kind(handler) == kind


class FakeSocket:
    """hyprctl._run as the compositor answers it: a batch gets the eval's
    `ok` and then the binds JSON, an eval gets `ok` or raises as _request
    does on an `error:` reply."""

    def __init__(self, raw, eval_reply="ok", batch_reply="ok"):
        self.raw, self.eval_reply, self.batch_reply = raw, eval_reply, batch_reply
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "--batch":
            return f"{self.batch_reply}\n\n\n" + json.dumps(self.raw, indent=4)
        assert args[0] == "eval"
        if self.eval_reply.startswith("error:"):
            raise hyprctl.HyprctlError(f"hyprctl {' '.join(args)}: {self.eval_reply}")
        return self.eval_reply


def _probe_token(batch_spec):
    m = re.fullmatch(r'eval __hypruse_bind_probe = "([0-9a-f]{16})" ; j/binds', batch_spec)
    assert m, batch_spec
    return m.group(1)


def test_run_lua_bind_reads_the_index_with_a_token_and_checks_it(monkeypatch):
    fake = FakeSocket(LUA57_RAW)
    monkeypatch.setattr(hyprctl, "_run", fake)
    assert hyprctl.run_lua_bind("super + l") == "HL.Dispatcher(close)"
    (batch, spec), (verb, code) = fake.calls
    token = _probe_token(spec)
    # the token is checked before anything runs, then cleared
    assert code.startswith(f'if __hypruse_bind_probe ~= "{token}" then error(')
    assert "__hypruse_bind_probe = nil" in code
    assert "local f = debug.getregistry()[10]" in code
    assert code.endswith(
        "local ok, err = pcall(hl.dispatch, f) if not ok then error(tostring(err), 0) end")
    # a dispatcher's function prints as no address `binds` showed: type only
    assert "tostring(f)" not in code
    assert ";" not in spec.split(" ; ")[0]  # a batch splits on it


def test_run_lua_bind_checks_a_function_by_its_address(monkeypatch):
    fake = FakeSocket(LUA57_RAW)
    monkeypatch.setattr(hyprctl, "_run", fake)
    hyprctl.run_lua_bind("SUPER+J")
    code = fake.calls[1][1]
    assert "debug.getregistry()[7]" in code
    assert 'tostring(f) ~= "function: 0x558a32c398b0"' in code


def test_run_lua_bind_on_hyprland_056(monkeypatch):
    fake = FakeSocket(LUA_RAW)
    monkeypatch.setattr(hyprctl, "_run", fake)
    hyprctl.run_lua_bind("SUPER+Q")
    assert "debug.getregistry()[37]" in fake.calls[1][1]


@pytest.mark.parametrize("raw,combo,match", [
    (LUA57_RAW, "SUPER+X", "the bind is gone"),
    ([_lua57("L", "killactive", "")], "SUPER+L", "not a Lua bind"),
    ([_lua57("L", "HL.Dispatcher(close)", "10; os.exit()")], "SUPER+L", "not a Lua bind"),
])
def test_run_lua_bind_refuses_what_it_cannot_resolve(monkeypatch, raw, combo, match):
    fake = FakeSocket(raw)
    monkeypatch.setattr(hyprctl, "_run", fake)
    with pytest.raises(hyprctl.HyprctlError, match=match):
        hyprctl.run_lua_bind(combo)
    assert [c[0] for c in fake.calls] == ["--batch"]  # nothing evaluated


def test_run_lua_bind_on_a_hyprlang_session(monkeypatch):
    fake = FakeSocket(LUA57_RAW, batch_reply="eval is only supported with the lua config manager")
    monkeypatch.setattr(hyprctl, "_run", fake)
    with pytest.raises(hyprctl.HyprctlError, match="only supported with the lua"):
        hyprctl.run_lua_bind("SUPER+L")
    assert len(fake.calls) == 1


def test_run_lua_bind_reports_the_compositor_s_words_not_the_snippet(monkeypatch):
    fake = FakeSocket(LUA57_RAW, eval_reply="error: hl.dispatch: boom")
    monkeypatch.setattr(hyprctl, "_run", fake)
    with pytest.raises(hyprctl.HyprctlError) as exc:
        hyprctl.run_lua_bind("SUPER+J")
    assert str(exc.value) == "use_bind SUPER+J: hl.dispatch: boom"


@pytest.mark.parametrize("reply,moved", [
    ("error: hypruse: the Lua config was reloaded while use_bind ran", True),
    ("error: hypruse: the bind changed while use_bind ran", True),
    # a failing bind's message names its chunk by source, which can hold
    # the guard's text: only a reply that STARTS with it is the guard
    ('error: hl.dispatch: [string "if x then error(\"hypruse: the bind changed"]:1: boom', False),
])
def test_run_lua_bind_tells_a_moved_bind_from_a_failing_one(monkeypatch, reply, moved):
    monkeypatch.setattr(hyprctl, "_run", FakeSocket(LUA57_RAW, eval_reply=reply))
    with pytest.raises(hyprctl.HyprctlError) as exc:
        hyprctl.run_lua_bind("SUPER+J")
    assert isinstance(exc.value, hyprctl.LuaBindMoved) is moved


def test_run_lua_bind_is_a_dry_run_barrier(monkeypatch):
    fake = FakeSocket(LUA57_RAW)
    monkeypatch.setattr(hyprctl, "_run", fake)
    monkeypatch.setenv("HYPRUSE_DRYRUN", "1")
    with pytest.raises(Exception, match="dry"):
        hyprctl.run_lua_bind("SUPER+L")
    assert fake.calls == []


def test_parse_binds_drops_pointer_and_switch_binds_a_lua_config_did_not_flag():
    combos = [b["combo"] for b in hyprctl.parse_binds(LUA_RAW)]
    assert "SUPER+mouse:272" not in combos and "switch:on:Lid" not in combos


def test_parse_binds_keeps_scroll_binds():
    # mouse_up/mouse_down are scroll, not buttons: on a hyprlang config they
    # carry a real dispatcher and use_bind runs them, so dropping them by a
    # bare "mouse" prefix would take a working bind away
    scroll = [
        {"modmask": 64, "key": "mouse_down", "dispatcher": "workspace", "arg": "e+1",
         "submap": "", "mouse": False, "keycode": 0}
    ]
    assert hyprctl.parse_binds(scroll) == [
        {"combo": "SUPER+mouse_down", "action": "workspace", "arg": "e+1"}
    ]
