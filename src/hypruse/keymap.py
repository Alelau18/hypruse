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
_CONTROL = {"\n": "Return", "\r": "Return", "\t": "Tab", "\b": "BackSpace",
            "\x1b": "Escape", "\x7f": "Delete"}
CONTROL_CHARS = frozenset(_CONTROL)  # typed as keys, never inserted as text


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
    lib.xkb_keysym_from_name.restype = ctypes.c_uint32
    lib.xkb_keysym_from_name.argtypes = [ctypes.c_char_p, ctypes.c_int]
    return lib


class Keymap:
    def __init__(self, layout: str, variant: str = "", options: str = ""):
        self._table: dict[str, tuple[str, str]] = {}
        self._bases: set[str] = set()
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
                    self._bases.add(base)
                ch = lib.xkb_keysym_to_utf32(syms[0])
                if ch and base and chr(ch) not in self._table:
                    self._table[chr(ch)] = (_LEVEL_MODS[lvl], base)

    def chord(self, char: str) -> tuple[str, str] | None:
        """(mods, key) to send for `char`, or None when this layout has no key for it."""
        if char in _CONTROL:
            return ("", _CONTROL[char])
        return self._table.get(char)

    def chord_for_key(self, name: str) -> tuple[str, str] | None:
        """(mods, key) for a keysym NAME. A key's own base name goes as is;
        a name for a shifted symbol ("plus" is Shift and "equal" on a US
        layout) goes through its character, since Hyprland only finds base
        names. None when the name is neither."""
        if name in self._bases:
            return ("", name)
        lib = _lib()
        if lib is None or name.startswith("KP_"):  # a keypad key is not its main-row twin
            return None
        sym = lib.xkb_keysym_from_name(name.encode(), 0)
        ch = lib.xkb_keysym_to_utf32(sym) if sym else 0
        return self.chord(chr(ch)) if ch and chr(ch).isprintable() else None


@functools.cache
def for_layout(layout: str, variant: str = "", options: str = "") -> Keymap:
    return Keymap(layout, variant, options)
