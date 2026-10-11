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
from typing import NoReturn

from hypruse import trust

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
