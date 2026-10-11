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
