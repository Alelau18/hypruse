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
from collections.abc import Callable, Iterable
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


def refuse(what: str, instead: str = "") -> NoReturn:
    """Refuse an action the background cannot do, saying what to do instead
    (`instead`, a way that works in the background, comes first)."""
    if mode() == GUARANTEE:
        hint = "ask the human; HYPRUSE_BACKGROUND is set"
    else:
        hint = (
            "pass background=false to do it in the foreground (that takes over the "
            "human's screen), or ask the human"
        )
    if instead:
        hint = f"{instead}, or {hint}"
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
        if sep and _id(lo) and _id(hi) and abs(int(lo) - int(hi)) < _MAX_WORKSPACES:
            a, b = int(lo), int(hi)
            step = -1 if a > b else 1
            out.extend(str(i) for i in range(a, b + step, step))
        elif _id(part):
            out.append(str(int(part)))  # 099 is workspace 99
        else:
            out = []
            break
    if not out or len(out) > _MAX_WORKSPACES:
        raise trust.TrustError(
            f"HYPRUSE_AGENT_WORKSPACES={raw!r} is malformed (use 99-90, 99,98,50 or "
            f"name:x, at most {_MAX_WORKSPACES} workspaces); launch, and every action "
            "under HYPRUSE_BACKGROUND=1, is refused until it is fixed"
        )
    return out


_MAX_WORKSPACES = 1000
_MAX_ID = 2**31 - 1  # Hyprland's workspace ids are 32-bit


def _id(part: str) -> bool:
    return part.isdigit() and 0 < int(part) <= _MAX_ID


def fill() -> int:
    raw = os.environ.get("HYPRUSE_AGENT_FILL", "1").strip()
    if not raw.isdigit() or int(raw) < 1:
        raise trust.TrustError(
            f"HYPRUSE_AGENT_FILL={raw!r} must be a whole number of 1 or more; launch is "
            "refused until it is fixed"
        )
    return int(raw)


def matches(selector: str, ws: dict[str, Any]) -> bool:
    """Whether a workspace record is the one an agent-workspace selector
    (a number, or name:x) names."""
    if selector.startswith("name:"):
        return ws.get("name") == selector[5:]
    if not selector.isdigit():
        return False
    return hyprctl.workspace_ref(ws) == int(selector) or ws.get("name") == selector


_matches = matches


def _owned(client: dict[str, Any], owned: set[str]) -> bool:
    """The agent opened it: in this session's owned set, or tagged
    hypruse-owned by an earlier one under HYPRUSE_MARK."""
    return client.get("address") in owned or any(
        str(t).rstrip("*") == "hypruse-owned" for t in client.get("tags") or ()
    )


def is_agent_workspace(name: str) -> bool:
    """Whether a workspace argument (as hypr and launch take it) names one of
    the agent's workspaces. Relative ones (+1, e+1, previous) do not."""
    return name.strip() in agent_workspaces()


def require_agent_workspace(name: str, instead: str = "") -> None:
    """Under the guarantee, refuse a workspace that is not the agent's: its
    windows stay off the human's screen."""
    if mode() == GUARANTEE and not is_agent_workspace(name):
        refuse(
            f"workspace {name} is not one of the agent's workspaces "
            f"({', '.join(agent_workspaces())}), and the agent's windows stay on those",
            instead,
        )


def on_agent_workspace(client: dict[str, Any]) -> bool:
    ws = client.get("workspace") or {}
    return any(_matches(s, ws) for s in agent_workspaces())


def next_workspace(
    clients: list[dict[str, Any]],
    owned: set[str],
    shown: Iterable[dict[str, Any]] = (),
    allowed: Callable[[str], bool] | None = None,
) -> str:
    """Where the agent's next app goes: the first workspace in the range with
    fewer than fill() agent windows and none of the human's; when all are
    full, the one with the fewest agent windows. Never one on screen
    (`shown`, the monitors' workspace records): the human would see the app
    open, and Hyprland drops a silent rule for the workspace a window maps
    on, so it would take their focus too. `allowed` narrows the range."""
    shown = list(shown)
    usable: list[tuple[str, int]] = []
    on_screen = False
    for sel in agent_workspaces():
        if allowed is not None and not allowed(sel):
            continue
        here = [c for c in clients if matches(sel, c.get("workspace") or {})]
        if any(not _owned(c, owned) for c in here):
            continue  # holds a window the agent did not open
        if any(matches(sel, ws) for ws in shown):
            on_screen = True
            continue
        usable.append((sel, len(here)))
    if not usable:
        why = ("every free agent workspace is on screen" if on_screen
               else "every agent workspace holds a window the agent did not open")
        raise trust.TrustError(
            f"{why}, so an app cannot open out of the human's sight; free one or widen "
            "HYPRUSE_AGENT_WORKSPACES"
        )
    limit = fill()
    for sel, n in usable:
        if n < limit:
            return sel
    return min(usable, key=lambda u: u[1])[0]
