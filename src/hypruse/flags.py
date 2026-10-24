"""Reading the HYPRUSE_* switches that turn a SAFETY layer on.

An opt-in DANGER flag (HYPRUSE_CLIPBOARD, HYPRUSE_JOURNAL_TEXT) is on only
for an affirmative value, and anything else reads as off: a typo must not
write keystrokes to disk. An opt-in SAFETY flag needs the opposite reading.
HYPRUSE_READONLY=ture, =y or "= 1" used to read as off, so the human
believed the agent could only look while every acting tool was
registered, and HYPRUSE_DRYRUN delivered real input during what the human
took for a rehearsal. Here a value that is neither on nor off is on, with a
warning, as HYPRUSE_CONFINE refuses a value it cannot read.
"""

from __future__ import annotations

import os
import sys

_ON = ("1", "true", "yes", "on")
_OFF = ("", "0", "false", "no", "off")
_warned: set[str] = set()


def safety_flag(name: str) -> bool:
    """Whether a safety flag is on: an affirmative value, or one that is
    neither affirmative nor negative."""
    value = os.environ.get(name, "").strip().lower()
    if value in _ON:
        return True
    if value in _OFF:
        return False
    if name not in _warned:
        _warned.add(name)
        print(f"hypruse: {name}={os.environ.get(name)!r} is neither on (1, true, yes, on) nor "
              "off (0, false, no, off, empty); treating it as on", file=sys.stderr)
    return True
