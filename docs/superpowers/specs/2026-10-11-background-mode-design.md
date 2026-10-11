# Background mode

Status: approved by the owner on 2026-10-11. Target release: 0.13.0.

## Goal

An agent works in apps on workspaces the human is not looking at, while the
human keeps working on their own. On stock Hyprland, with no compositor
plugin, the agent never moves the human's cursor, never takes their keyboard
focus, and never switches their workspace.

## What the spike proved

Measured live on Hyprland 0.56.2 with a GTK test form on workspace 99, with
the human's active window, workspace and cursor identical before and after
every step:

| Capability on a hidden window | Result |
|---|---|
| open an app on another workspace (`launch --workspace 99`) | works, no focus change |
| read controls and values (AT-SPI tree) | works |
| press a control (AT-SPI `DoAction`) | works, about 15 ms |
| set text (AT-SPI `EditableText.SetTextContents`) | works, about 19 ms, any text |
| move the app's own focus to a field (AT-SPI `Component.GrabFocus`) | works |
| send keys (`hyprctl dispatch sendshortcut ", h, address:0x..."`) | works, about 45 ms per key |
| capture pixels (`grim -T`) | works, but stale: GTK stops drawing while hidden |

The accessibility tree is therefore the source of truth in the background.
Pixels of a hidden window are a rough guide only.

Hyprland's `sendshortcut` (`Actions::pass`, ConfigActions.cpp in 0.56.2)
moves keyboard focus to the target, sends the key and restores focus inside
one event-loop step, so the human's own keystrokes cannot interleave. Two
consequences: the human's app sees keyboard focus leave and return for each
key (most apps ignore it; some close an open menu), and when both the target
and the focused window are XWayland, Hyprland sends the key to the focused
window instead of the target.

## Policy: who decides

Three layers. The guarantee overrides everything; without it, a call's own
`background` argument wins over the automatic rule.

1. Session guarantee, `HYPRUSE_BACKGROUND=1`. Every acting tool works only
   on windows on the agent's workspaces, never focuses a window, never
   switches the human's workspace and never moves the cursor. An action that
   needs the foreground is refused, and the agent cannot override it. Input
   is confined to the agent's workspaces. Observation (screenshot, ui,
   desktop, marks, wait_for) is not restricted.
2. Per call, `background=true|false` on the acting tools. `true` forces the
   background and refuses what the background cannot do. `false` asks for
   the foreground behaviour of 0.12, and is refused under the guarantee.
3. Automatic, the default when `HYPRUSE_BACKGROUND` is unset. An action on
   the window the human is in runs in the foreground, as in 0.12. An action
   on any other window runs in the background. What the background cannot do
   is refused with the reason and the hint `background=false`.

`HYPRUSE_BACKGROUND=0` turns automatic mode off and restores 0.12 behaviour
exactly.

## The agent's workspaces

- `HYPRUSE_AGENT_WORKSPACES`, default `99-90`: the workspaces the agent may
  use, in order. `A-B` counts from A to B (downward when A is larger). A
  comma list (`99,98,50`) and named entries (`name:research`) are accepted.
- `HYPRUSE_AGENT_FILL`, default `1`: how many agent windows go on one agent
  workspace before the next one is used. With 1, every app has a workspace
  to itself, which keeps hidden windows at full size for capture and layout.
- A workspace in the range that holds a window the agent did not open is
  skipped, so the human's own windows are never mixed with the agent's.
- When every workspace in the range is full, the next app goes on the agent
  workspace with the fewest agent windows.
- Under the guarantee, the confinement scope is the windows on the agent's
  workspaces.

"Windows the agent opened" is the existing owned set that `launch` already
records for `HYPRUSE_CONFINE=launched` (persisted across CLI calls in
cli-state).

## Tools in the background

| Tool | Background behaviour |
|---|---|
| `click_ui` | Press the control with its AT-SPI action. No pointer, no focus. A text field gets the app's own focus through `GrabFocus`. A control with no action is refused, and so is a right or double click (the action is the control's primary one). Marks also store each control's AT-SPI service and path, so `click_ui(mark=N)` works in the background. |
| `keyboard`, type, with `window=` | Into a focused text field, or the window's only one (GTK reports no focus inside an inactive window): AT-SPI `EditableText.InsertText` at the caret, any text. Otherwise `sendshortcut` key by key, mapped on the first keyboard layout with SHIFT and AltGr levels; a character with no key (emoji, CJK) is refused on that path before any key is sent, and so is a keyboard switched away from its first layout. |
| `keyboard`, key combo, with `window=` | `sendshortcut` with the modifiers. |
| `keyboard` without `window=` | Types into the window the human is in, as in 0.12. Refused under the guarantee, which requires `window=` on an agent workspace. |
| `pointer` | Moves the real cursor, so it is foreground by nature. Automatic mode allows it where its input lands on the window the human is in or on no window, and refuses it over any other window; a move counts, since focus follows the mouse. Refused under the guarantee. |
| `launch` | Without a workspace, in automatic mode or under the guarantee: silently onto the next agent workspace. With a workspace: there, silently (under the guarantee, only an agent workspace). With `background=false`: on the human's workspace, as in 0.12. |
| `hypr` | Operations that do not move the human's view (close, move to a workspace, float) work on agent windows; under the guarantee `move_window` takes only agent workspaces. `workspace`, `focus_window` and `fullscreen` (which acts on the focused window, so it focuses first) change the human's view: allowed in automatic mode as an explicit request, refused under the guarantee. |
| `use_bind` | As in 0.12 in automatic mode. Refused under the guarantee, since a bind can do anything. |
| `sequence` | Each step behaves like its tool. A `background` field on the sequence applies to every step. |
| `screenshot`, `ui`, `marks`, `desktop`, `wait_for`, `zoom` | Unchanged, except that `ui` ends with a note for a window that is not on screen: its points are inside the window, and a pointer click at them would land on what the human sees. |

`sendshortcut` is refused for an XWayland target, because Hyprland would
deliver the key to the focused window when that window is XWayland too.
It also finds the keycode on the layout in use but sends it with layout
group 0, so keys are mapped on the first layout and refused while the
keyboard is on another. AT-SPI text input has neither problem. Its
`InsertText` length is UTF-8 bytes for GTK and the other ATK toolkits and
UTF-16 units for Qt, so it is computed per toolkit.

## Honest results

- Every background action says how it was done, for example "pressed
  button 'Save' through its accessibility action (no pointer, your focus
  untouched)", "typed 12 characters into the text field through the
  accessibility interface", or "sent 5 keys to 0x... with sendshortcut".
- After a background action, hypruse reads the control back from the AT-SPI
  tree (a check box's state, a field's text, whether the window still
  exists) and reports what it found. A press the app ignored reads as "no
  change". Screenshots are not used to confirm, because hidden apps can
  show stale pixels.
- Refusals give the reason and what to do instead. Outside the guarantee:
  "pass background=false to use the real pointer (that takes over the
  human's screen), or ask the human". Under the guarantee: "ask the human;
  HYPRUSE_BACKGROUND is set".
- The journal records each action's method (accessibility action,
  accessibility text, sendshortcut, foreground), and a dry run reports the
  method it would use.

## Changes from the review before release

A 175-agent review of the first build found that the design's main
promise needed more than the policy. These rules were added:

- A background action acts on an AT-SPI object directly, so the object
  must provably belong to the target window: a frame matched by a title
  or size that another window of the same app shares is not used (the
  click refuses, the keys go by sendshortcut, aimed by address).
- Results read the human's active window and workspace before and after,
  claim "focus untouched" only when they match, and warn when an app
  moved them. Typed results never repeat the text (the journal stores
  result lines).
- `launch` waits for its own window (on its workspace, or of the launched
  program's class) and never picks an agent workspace that is on screen.
- The guarantee bounds hypruse's own input; a launched command runs as
  given, and an already running app opens its window on the human's
  screen first, which the result reports as a warning.
- GTK 3 fields are not focused over AT-SPI while `misc:focus_on_activate`
  is on, a password field among several refuses without `allow_auth`, and
  newlines and tabs are always keys.

## Safety

- Every existing guard applies to the background paths. Confinement checks
  the target window. The auth interlock refuses a background press or key
  for an authentication dialog, Hyprland's own dialog included.
- A password field (AT-SPI role PASSWORD_TEXT) refuses background text
  input unless `allow_auth`. The background path already holds the
  control's role, so this check is always on there.
- While the session is locked, background actions are refused, as all input
  is in 0.12. Working while locked may become an explicit opt-in later.
- The human cannot see background work. The journal records every action,
  `HYPRUSE_MARK` notices fire on launches and captures, and the README
  recommends the guarantee (which includes confinement) for unattended
  work.

## Components

- `background.py`, new. The policy (`mode()`: off, automatic or
  guarantee, and `decide(target, explicit)`), the agent workspace range
  (parse `HYPRUSE_AGENT_WORKSPACES`, `next_workspace(clients, owned)`), and
  the key mapping for `sendshortcut` (character to modifiers and keysym).
- `a11y.py` gains `insert_text`, `set_text`, `grab_focus` and an action
  check (`actions(bus, svc, path)`), next to the existing `do_action`.
- `hyprctl.py` gains `send_shortcut(mods, key, address)`, with the Lua and
  hyprlang dialects like the other dispatchers.
- `trust.py` gains the guarantee's confinement scope and
  `guard_background(...)` refusals.
- `server.py` routes `click_ui`, `keyboard`, `pointer`, `launch`, `hypr`,
  `use_bind` and `sequence` through the policy, and adds the `background`
  parameter to the acting tools.
- `verbs.py` adds `--background` and `--foreground` flags.

## Testing

- Unit tests first for the policy, the workspace range and filling, the key
  mapping, every background path, every refusal and its message, and the
  journal and dry-run method field.
- A live suite behind an opt-in marker that opens a GTK form on the agent's
  workspace, drives it in the background, reads results back from the
  AT-SPI tree, and asserts the human's active window, workspace and cursor
  are identical before and after every step.
- The verification workflow used for 0.12.x before release.

## Out of scope

- The browser bridge (web pages in the background). It lands in a follow-up
  release.
- Clicking arbitrary pixels in a hidden window. That needs a compositor
  plugin or an input seat of its own, which this design avoids.
- Working while the session is locked.
