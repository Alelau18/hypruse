# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/).

## [Unreleased]

### Added
- **Window selectors.** Everywhere a tool takes a window address
  (`window=` on `screenshot`, `zoom`, `ui`, `marks`, `keyboard` and
  `click_ui`, `hypr` targets, `sequence` steps, the CLI's `--window` and
  `ADDR`), it also takes `class:REGEX`, `title:REGEX` (case-insensitive
  search) or `pid:N`, resolved once when the call starts by one resolver
  (`hyprctl.select_client`). One match is used; several are refused with
  the candidates' address, class, title and workspace; none says so and
  lists the windows of a similar class. Addresses die whenever a game
  restarts, which had agents re-finding windows with shell loops.
- **`sequence` sleep step.** `{"op": "sleep", "ms": N}` (1-60000) pauses
  between steps. It is checked before the first step runs, journaled with
  the sequence, skipped in a dry run, and stops the sequence rather than
  overrun its 30 s budget. Agents had been using unfiltered
  `wait_for title_change` steps as delays.
- **Holding a button.** `pointer` takes `action='down'` and `'up'`
  (optional x,y to move there first, `button` as for a click) and
  `hold_ms` on a click (press, wait, release; up to 10 s). `down` records
  the held button where the SIGTERM cleanup and `hypruse stop` find it,
  refuses a second hold, and `up` clears it; a move while a button is down
  is guarded like a drag. Both go through the click's guards (trust,
  confinement, the auth interlock, the session lock and the background
  policy: refused under `HYPRUSE_BACKGROUND=1`, judged by the point like a
  click in automatic mode), rehearse under dry run, and are journaled. An
  `up` with no x,y of the button hypruse holds is never refused, since it
  only ends a press that was allowed and refusing it would strand the
  button; an `up` elsewhere is guarded like a move.
  The CLI has `pointer down [X Y] [--button B]`, `pointer up [X Y]
  [--button B]` and `pointer click ... --hold-ms N`; a shell `down`
  carries over to the next verb, and `hypruse stop` releases it. The MCP
  server records its own `down` and `up` in that instance's CLI state when
  the file exists, so a shell verb run beside it sees the same hold. A
  `sequence` that stops before the step that releases a button it pressed
  releases it.

### Fixed
- **CLI state per compositor instance.** The shell verbs kept one
  `cli-state.json` per user, which a verb under another Hyprland instance
  (a nested test compositor) overwrote, discarding the other's marks,
  owned windows and seat baseline. It is now
  `cli-state-<instance signature>.json`.
- **Session discovery picks one live compositor.** With the session
  variables stripped, a crashed instance's leftover socket file counted as
  live, and `WAYLAND_DISPLAY` was picked on its own (by name, so
  `wayland-10` sorted before `wayland-2`), so the signature and the socket
  could belong to different compositors. Both now come from one record of
  `hyprctl -j instances` (newest first), or, when hyprctl cannot answer,
  from a runtime-dir scan that requires the pid in `hyprland.lock` to be
  alive. A set signature with no `WAYLAND_DISPLAY` gets its own instance's
  socket.
- **`HYPRUSE_STRICT` no longer stops background work when the human
  moves.** The seat guard ran before `keyboard` and `click_ui` decided on
  the background path, so every mouse move the human made refused the
  agent's work in a window they were not in. The mode is decided first and
  the guard runs on the foreground path only.
- **`then='screenshot'` after a background action shows that window.** It
  captured the focused monitor, which shows the human's own work, not the
  window acted on. A background `click_ui`, a background `keyboard`, and a
  `sequence` whose steps named a window now capture that window (`grim -T`,
  which marks a hidden window `visible: false` and possibly stale); other
  captures say in their metadata (`view`) that they show the human's
  focused monitor.
- **A background field focus could switch the human's workspace.** The
  GTK 2/3 refusal read `hyprctl getprop ... focus_on_activate`, which
  prints false whenever no window rule sets it, as the setting being off.
  With `misc:focus_on_activate = true`, a background `click_ui` on a GTK 3
  entry ran GrabFocus, GTK presented its window, and Hyprland switched the
  human's workspace and moved the cursor. The option is now read first and
  a window rule can only turn the value on.
- **Say how to get a tree from Chromium and Electron.** When `ui`,
  `click_ui` or `marks` find no tree, or no matching controls, in a window
  whose class looks like Chromium, Chrome, Helium, Brave, Electron or VS
  Code, the answer adds that the app exposes its controls only when
  started with `--force-renderer-accessibility`, else screenshot + zoom.
- **Sleeping monitors and window flags.** `desktop` marks windows that are
  `xwayland`, `fullscreen`, `pinned` or `hidden` (each only when true, to
  keep the snapshot small) and monitors in DPMS sleep (`dpms: off`). A
  capture of a monitor, a region or a screen crop touching a sleeping
  monitor is refused at once ("monitor X is asleep (DPMS off) ...")
  instead of a 10 s grim timeout.
- **Screenshot regions as agents write them.** `region` takes `x,y,W,H`
  besides `x,y,WxH` and `x,y WxH`, and `window` with `region` is a crop in
  the window's own coordinates instead of an error: clipped to the window,
  cut from the screen at its global position, with global `geometry` so
  `global = geometry[:2] + image_pixel / scale` still holds. A window that
  is not on screen is captured whole by `grim -T`, which hypruse cannot
  crop without an image library, so that case is refused with what to do.
- **A failed `then=` observation no longer hides a successful action.**
  Only `then='ui'` failures were caught: a grim timeout after a successful
  `hypr focus_window` surfaced as a tool error. Every `then=` value now
  returns the action's result with a note, `(then='screenshot' failed:
  <reason>; the action itself went through)`, and an unknown `then` is
  refused before acting instead of after.
- **`wait_for(window_open)` no longer misses a window that mapped before
  it subscribed.** The server keeps the set of windows the agent has had a
  chance to see, refreshed at the start of every acting tool call, by every
  `desktop`, and at startup (the CLI carries it between verbs). A filtered
  wait finding a matching window outside that set answers at once with
  `already: true` and the window's address, class and title, and reports
  each window once. Agents lost about 185 s to such timeouts.
- **`wait_for(window_close)` filtered by class or title can succeed.**
  Hyprland's `closewindow` event carries only the address, so a title
  filter never matched it. The filter is resolved to the matching open
  windows' addresses when the wait starts, any one of them closing ends
  it, and one that closed while the wait was subscribing is reported as
  `already`. `sequence`'s wait steps behave the same.
- **Pointer motion apps can see.** A cursor move was a compositor warp,
  which XWayland, Wine and GameMaker clients never receive as motion, so
  hover did nothing in games and agents fell back to `ydotool mousemove`.
  Every warp is now followed by a net-zero one-pixel motion on the virtual
  pointer (+1 then -1, in two frames), so the client under the cursor sees
  the mouse arrive (-1 first on the layout's right edge, where the +1
  would be clamped). `HYPRUSE_POINTER_NUDGE=0` turns it off.
- **Drags that games see as drags.** A drag was 12 warps with the button
  held, so a game saw a press and a release with no motion between. Every
  step is now a warp plus real motion, the path ends exactly on the target,
  and `pointer(action='drag')` takes `duration_ms` (0-5000, default 200)
  and `steps` (1-100, default 12); the CLI takes `--duration-ms` and
  `--steps`.

### Security
- **A key combo cannot name a different window.** In a background
  `keyboard(action='key')`, text after a comma in `keys` (`'x, active'`,
  `'Return, class:REGEX|'`) reached `sendshortcut`'s window field, so the
  key went to the human's focused window or any window the regex matched,
  after every guard had checked the named one, and the result said the
  human's focus was untouched. Reproduced under the Lua config manager;
  the hyprlang manager refused the extra field only because hyprutils'
  `CVarList2` ignores its field limit. A key is now one character or an
  XKB keysym name (letters, digits, underscores), checked when the combo
  is parsed, and `hyprctl.send_shortcut` refuses any modifier, key or
  address outside that grammar before Hyprland is asked; the Lua builder
  wants exactly three fields.
- **`sequence` steps and `replay` get the tools' own argument types.**
  Both rebuilt a tool call from JSON and called the function on it raw,
  so a step's `"allow_auth": "false"` was a non-empty string that every
  guard read as an override, while the journal and the replay plan
  printed `allow_auth=false`. A step is now validated against its tool's
  signature (the coercion a direct MCP call gets: `"false"` is `False`),
  every step before the first one runs; replay validates each entry
  strictly and refuses one whose flag is not a boolean, and prints and
  runs the checked arguments. The guards take only `True` as an
  override, and `hypruse journal` marks a non-boolean flag
  `<not a boolean: ...>`.
- **The accessibility walk stays inside the window's own app.** Every
  object below an app's root comes from that app's own replies, and an
  app could name another app's object as a child, a label's target or
  its window frame, the registry root included, which lists every app.
  hypruse then read it for `ui`, and pressed it (`click_ui`) or typed
  into it (`keyboard`) in the background, as a deputy with the a11y-bus
  reach a Flatpak app's own proxy denies it, while every guard had
  checked the app's window. A reference is now followed only when it is
  served on the root's own connection or on one whose process is the
  window's app (`a11y.Owner`, one check per bus name, so WebKitGTK's
  web process still counts), for children, label relations and frames
  alike; the background press and the accessibility typing check the
  object's connection again just before acting, which also covers a
  control restored from `marks`.
- **The password-field refusal is about the field, not the verb.** It ran
  for `keyboard(action='type')` only, so `action='key'` entered the same
  password one key per call (or pasted it with ctrl+v); it checked only
  the field focused when the call started, so `user<Tab>secret` put the
  secret into the next field; it took "no password field among the
  fields read" as "none" when the walk stopped at its budget; and the
  strict foreground check asked only for the focused element, which GTK
  does not report in an inactive window, walking a root it had not
  proved. One decision now serves typed text, a key that types a
  character or pastes, both paths: it refuses a focused password field,
  one among several when focus is not reported, a field census cut
  short, text with a Tab or a line break into a window with a password
  field, and a Tab into one whose fields could not all be read. The foreground still checks only under
  `HYPRUSE_AUTH_GUARD=strict`; the keyboard tool's description no longer
  says it is the default. A `keys` combo that types a character is
  journaled as a digest, like typed text, and a result names it "a
  character key"; replay refuses such an entry as it refuses typed text.
- **Requests go to Hyprland's socket, not through the hyprctl binary.**
  The binary sends any request that merely contains `/--batch` as a
  batch, which Hyprland splits on `;`: with `HYPRUSE_MARK` on, a launched
  app whose window class carried `x;dispatch exec ...;/--batch` ran a
  host command through hypruse's notice (reproduced). And each background
  key was one `hyprctl dispatch sendshortcut` child process whose argv,
  the typed character included, any local user can read from /proc.
  hypruse now writes the request the binary would build (a fixed command
  word before any argument) to `.socket.sock` itself; only `batch_query`
  builds a batch. A reply that is not valid UTF-8 (a hostile title) is
  read with replacement characters instead of failing. The `hyprctl`
  binary is still used for `hyprctl -j instances`.
- **Windows are told apart by more than their address.** An address is
  the heap pointer of Hyprland's window object, which it hands to a new
  window once the old one is freed. `launched` confinement, launch
  placement on the agent's workspaces, the `HYPRUSE_STRICT` seat baseline,
  the windows `wait_for(window_open)` counts as already seen, and `marks`
  all compared the address alone, so the human's next window could
  inherit the agent's trust, a focus change could go unnoticed, and a
  new window could be skipped as already known. They now use
  `hyprctl.window_key`: the address with Hyprland's `stableId`, which is
  never reused (pid and initial class on a Hyprland without one). The
  CLI state drops a bare address an older hypruse wrote, and prunes
  closed windows from the owned-set whatever `HYPRUSE_CONFINE` says.
- **`launch` owns only the window it opened.** With no placement rule
  (`background=false`, `HYPRUSE_BACKGROUND=0`), it waited for the next
  window anyone opened, so the human's next window became the agent's:
  in `launched` scope, tagged `hypruse-owned`. And a class sharing three
  letters with any word of the command counted as the program's. A
  launch now exports a random `HYPRUSE_LAUNCH` token to its command and
  waits for a window on its rule's workspace, one whose process carries
  the token, or one of the program's class; only one carrying the token,
  or one on an agent workspace off screen, is recorded as the agent's (on
  a workspace the human can see, they may open a window there too). A
  command that hands off to another process (`xdg-open`, D-Bus
  activation, a systemd service) carries no token to its window. A single-instance app's window, which opens
  from the already-running process, is still found and moved, and the
  result says it is not counted as the agent's.
- **The replay plan is the plan that runs.** It cut every value at 120
  characters, so the tail of a long `launch` command ran unseen; it let
  newlines and bidi overrides through, so a value could draw plan lines
  of its own or read backwards; it folded every `False`, so
  `launch(background=False)` (the human's screen, no placement rule)
  read like the default; and `--execute` re-read the journal, which a
  live server keeps appending to. The plan now prints values whole and
  escapes every character in Unicode categories Cc, Cf, Zl and Zp (and
  the backslash), folds an argument only when it is the tool's own
  default, marks dry entries, and ends with a digest that `--execute
  --plan DIGEST` must match. `hypruse journal` escapes the same way, and
  the shell verbs' output escapes bidi and separator characters.
- **`sequence` sees the changes around its waits.** A `wait_for` step
  read and dropped every event it did not match from the stream the
  change check drains, so a launcher or a window that opened during the
  wait never stopped the run; before a wait step, every event of the
  awaited type was let through whatever the step's `match`; and after a
  focusing step, any workspace switch counted as the step's own, the
  human's included. The wait now leaves the events it skips for the next
  check, only the one event the wait's own filter accepts is let through
  (resolved before the step ahead of it, so a `window_close` filter can
  still name its window), and a switch counts as the step's only when it
  is to the workspace the step's window (or a `hypr workspace` step's
  absolute target) is on. An app that opens a second window during a
  wait step (a splash, a helper) now stops the sequence after it.
- **A root registered by a process inside the window proves nothing.**
  A terminal has no accessibility tree, so the title fallback could find
  a root that a process started in it registered, with a frame named
  after the terminal's title; `same_app` accepts a descendant of the
  window's process, so background typing went into that process's field
  instead of the terminal, and one that had detached (`setsid`, a tmux
  server) but kept the terminal's app scope passed the same way. A root
  on another process now proves the window only when its process started
  the window's, or both run in one Flatpak instance.
- **The journal's text digest is keyed.** It was an unsalted SHA-256
  prefix beside the exact length, so a four-digit PIN came straight back
  from it. It is now an HMAC-SHA256 prefix (`hmac`, not `sha256`) under a
  random key stored 0600 beside the journal.
- **`HYPRUSE_STRICT` guards every action that takes the human's view:**
  `hypr workspace` (relative switches depend on the seat), `hypr
  focus_window`, and a `launch` that opens on the human's workspace. A
  target-less `hypr fullscreen`/`toggle_floating` fails closed when the
  focused window cannot be read. Under confinement a bare `pointer move`
  is guarded like a click: it hovers, and with follow_mouse focuses, the
  window under it. A digit workspace selector (`HYPRUSE_AGENT_WORKSPACES`,
  `HYPRUSE_CONFINE=workspace:3`) matches the workspace with that number
  only, as Hyprland resolves it, never one named "3"; `name:3` names it.
- **What the guards checked is what gets the input.** Foreground
  `keyboard(window=...)` and `hypr fullscreen` with a target now confirm
  the target holds the focus after the focus dispatch and send nothing
  otherwise (a dialog or the human could take it in between); `pointer`
  rounds its coordinates before the guards, which used to check a point
  0.4 px outside a dialog the cursor then landed inside; background
  key-by-key typing stops if the keyboard switches away from its first
  layout partway; and a window capture that finishes after the session
  locked is discarded.
- **A malformed accessibility reply reads as "not there".** An app
  chooses its replies' types and values, and a string where an integer
  belongs, an empty reply or an infinite double raised an exception that
  escaped every `except A11yError`, so one hostile app broke `ui`,
  `click_ui` and background typing for every window whose tree reached
  it. Every reader now takes such a reply as "not there", as it does a
  refused call, and a background press that landed reports a failed
  read-back in its result instead of failing.
- **Safety switches fail toward safety.** `HYPRUSE_READONLY`,
  `HYPRUSE_DRYRUN` and `HYPRUSE_STRICT` read a near-miss value (`ture`,
  `y`, `= 1`) as off, so read-only registered every acting tool and a
  rehearsal delivered real input. A value that is neither on nor off is
  now on, with a warning (`hypruse.flags`). Opt-in danger switches
  (`HYPRUSE_CLIPBOARD`, `HYPRUSE_JOURNAL_TEXT`) keep the strict reading.
- **Non-finite numbers are refused before acting.** `launch(wait_s=nan)`
  ran the app and then failed, and the clamps kept NaN; `pointer`,
  `wait_for`, `launch` and sequence wait steps refuse NaN and infinities.
- **`hypruse stop` signals only the hypruse that wrote the beacon.** The
  beacon records its writer's start time, and a stale beacon whose pid
  the kernel handed to another process (or one pointed at another
  process) is cleared without signalling it; the pid in the verb lock
  must be a hypruse. A beacon from an older hypruse needs a hypruse at
  its pid.
- An empty `XDG_RUNTIME_DIR` no longer makes hypruse's state paths
  relative to the working directory, and a capture-notice time restored
  from the CLI state that cannot be one (in the future) means "notify
  now" rather than silencing every notice.
- **`keyboard type --allow-auth TEXT` warns** that the text is on the
  verb's own command line, readable from /proc; the skill says to pass it
  on stdin (`keyboard type - --allow-auth`).

### Not changed
- `launch` still only reports where the human's focus went when an
  already-running app's window opens on their screen and is moved away
  ("the focused window is now X"). Giving focus back was tried and
  dropped: Hyprland's `focuswindow` also raises the window and warps the
  cursor to its middle, so it would move the human's cursor, and it could
  refocus a window the human had already left during the wait.

## [0.13.0] - 2026-10-18

Background mode: an agent works in apps on workspaces the human is not
looking at while the human keeps working on their own, on stock Hyprland
with no compositor plugin.

### Added
- **Background mode, on by default.** An action on the window the human
  is in runs on their screen as before. An action on any other window
  runs without their cursor, keyboard focus or workspace. `click_ui`
  presses the control through its accessibility action (a text field gets
  the app's own focus). `keyboard` with `window=` types into the window's
  focused or only text field through the accessibility interface, or sends
  the keys to that window alone with Hyprland's `sendshortcut`, mapped to
  the keyboard layout (AZERTY and AltGr characters included).
- **Results that say what happened.** Each background result names its
  method and reads the human's focused window and workspace before and
  after (with a short settle for an app that reacts a beat later): it says
  their focus was untouched only when it was, and ends with a `WARNING`
  when it moved. A check box, slider or field is read
  back from the accessibility tree; typed results never repeat the text.
  A refusal gives the reason and what to do instead.
- **The agent's workspaces.** `launch` with no workspace opens the app
  silently on the next of the agent's own workspaces:
  `HYPRUSE_AGENT_WORKSPACES`, 99 down to 90 by default (a range in either
  direction, a comma list, or `name:` workspaces), `HYPRUSE_AGENT_FILL`
  apps to a workspace (default 1). It skips one that holds the human's
  windows or is on screen, counts a window an earlier session tagged
  `hypruse-owned` as the agent's, and stays inside
  `HYPRUSE_CONFINE=workspace:...`. It waits for its own window (one on its
  workspace or of the launched program's class, past wrappers such as
  `uwsm app --` or `flatpak run`), and says when the window took the
  human's focus.
- **The guarantee, `HYPRUSE_BACKGROUND=1`.** Input is confined to the
  windows on the agent's workspaces, and to `HYPRUSE_CONFINE`'s scope too
  when both are set. The pointer, workspace switches, `focus_window`,
  `fullscreen`, `toggle_floating` with no target and `use_bind` are
  refused, and `move_window` and `launch` take only agent workspaces that
  are off screen. It bounds hypruse's own input, not what a launched
  command does.
- **`background=true|false`** on `click_ui`, `keyboard`, `pointer`,
  `launch` and `sequence` (which passes it to its pointer, keyboard and
  `click_ui` steps and, when true, refuses a `hypr` step that takes the
  human's view), and `--background` / `--foreground` on the CLI verbs.
- **Background refusals.** A right or double click; a control with no
  accessibility action; a control in a window the accessibility tree
  cannot tell apart from the app's other windows (by title and size, and
  by the process its accessibility connection belongs to: the same
  process, a parent or child, one Flatpak instance or one app scope);
  focusing a GTK 4 field (GTK 4 does not allow it), or a GTK 2 or 3 field
  while `focus_on_activate` is on for that window; a password field the
  tree shows, without `allow_auth`; and, on the `sendshortcut` path, a
  character the layout has no key for, an XWayland window, or a keyboard
  switched away from its first layout.
- When no listed control matches a name, `click_ui` and `ui` find a
  named element of any role that has an accessibility action, such as a
  row in a web app's list, and `ui` shows the action's name. Some actions
  only focus: Qt WebEngine's rows offer `SetFocus` and nothing else. In
  the background such a press reads as focused, not pressed, and says
  whether the app reports the element focused. That does not open the
  row, and Enter sent to a focused Qt WebEngine row did not open it
  either.
- A live suite, `pytest -m background`, that drives yad forms on the
  agent's workspaces and checks the human's active window, workspace and
  cursor after every step, and the compositor's focus and workspace events.

### Changed
- In automatic mode, the default, some calls behave differently from
  0.12. `launch` without a workspace opens on an agent workspace, not the
  human's. `click_ui` and `keyboard(window=...)` on a window the human is
  not in no longer focus it. `pointer` is refused where its input would
  land on a window other than the active one (a launcher or popup over the
  point counts as the human's), and a move counts, since focus follows the
  mouse. `HYPRUSE_BACKGROUND=0` keeps 0.12's behaviour for the session,
  and `background=false` does so for one call.
- `ui` lists editable text fields among the actionable controls, and an
  unnamed field takes the name of its label, so `click_ui Name` finds a
  GTK form's field by the label next to it. A control is listed once even
  when its label also points at it.
- `ui` and `then='ui'` end with a note when the window is not on screen:
  its points are inside the window, and a `pointer` click at them would
  land on whatever the human sees there.
- `launch` with a workspace now waits for a window on that workspace or of
  the launched program's class, instead of the first window that opens
  anywhere, and re-baselines the `HYPRUSE_STRICT` seat guard only when it
  moved the seat (always with `HYPRUSE_BACKGROUND=0`).
- Accessibility reads go over one D-Bus connection that stays open,
  instead of starting a `busctl` process for every call. That is about
  nine times faster: reading ZapZap's 904-element tree took 41 s, and a
  name search over all of it now takes 3.3 s. A walk reads up to 2000
  elements instead of 400. `busctl` still finds the bus, and takes over
  for an address the connection cannot use.

### Fixed
- A walk read an element twice when the toolkit lists it under two
  parents, as Qt WebEngine does, and spent its budget on the copies. It
  now reads each once.
- A walk cut short says so: `ui` adds a note even when it found
  something, and stops suggesting a name filter, which reads the same
  elements. The background keyboard path no longer treats the one field
  it found as the window's only one when the walk did not finish.
- A call the app never answered used to read as an element with no name
  or no children. It now fails the read, while a call the app refuses
  (an element that is gone) still reads as "not there".

## [0.12.1] - 2026-10-11

Fixes from a review of 0.12.0.

### Fixed
- **Screenshots.** `visible` is false for an inactive tab in a group and
  for a window behind a fullscreen sibling, not just for a window on a
  hidden workspace; `zoom` refuses those, and refusals say which case it
  is. Window captures report `source`: `"window"` when grim captured the
  window itself, `"screen"` when it fell back to cropping the screen, with
  a note that anything over the window shows in its place. An XWayland app
  with its own menu, combo list or tooltip open is cropped from the
  screen, since a window capture would leave the menu out. The hint for a
  hidden window says to capture again after bringing it forward, instead
  of promising the old coordinates still hold, and in read-only mode it
  says to ask the human rather than naming the `hypr` tool. A missing grim
  or the byte budget is reported as itself. A hidden window caught
  mid-animation is retried for up to about a second. A window whose
  workspace is shown but which is scrolled out of view of its monitor, or
  floating off every monitor, counts as not shown. `stable=true` keeps its
  last good frame through a single failed capture, raises when captures
  keep failing, and with `then="screenshot"` a failed capture no longer
  hides that the action itself went through.
- **Trust guards.** `click_ui` checks the point it clicks, not only its
  target. It refuses a point off every monitor or outside the target, a
  point on another monitor for a tiled target (Hyprland gives a tiled
  window clicks only on its own monitor), a point on another monitor
  whose scratchpad is up, and a mark taken before the window was resized.
  Hyprland's own permission prompt (`hyprland-dialog`: Deny / Allow once
  / Allow and remember) takes the pointer before any other window, even a
  pinned one, floating or tiled, so it counts over every target, and
  clicking or typing into it needs `allow_auth`. Its app-not-responding
  and safe-mode notices share its window class and title, so they need
  `allow_auth` too. It refuses when an
  authentication dialog will cover the control once the target is
  focused, or under confinement an out-of-scope window: Hyprland gives
  the click to pinned and floating windows first and focusing does not
  raise a window, so a floating dialog over the control takes it, and
  hyprpolkitagent opens floating. It reads the target again after
  focusing it and refuses if focusing moved it (Omarchy fullscreens a
  window focused behind a fullscreen one, and a scrolling layout scrolls
  its column into view).
  `pointer` refuses a point off every monitor while a guard is on, since
  Hyprland would move the click to the nearest screen edge, onto a window
  nothing checked. A layer whose namespace contains "polkit" counts as an
  authentication prompt, so the prompts end-4's dots (`quickshell:polkit`)
  draws are covered as well as Omarchy 4's. When the
  layer list cannot be read, `pointer`, `click_ui` and `keyboard` refuse
  instead of letting input through to a prompt they cannot rule out,
  unless `allow_auth` is passed or `HYPRUSE_AUTH_GUARD=0`. That setting now
  lifts the typing refusal for a polkit layer too, as the README says. A
  click is refused when any polkit layer covers the point, even one under
  an on-screen keyboard. Omarchy's lock preview is no longer treated as a
  lock screen (refusals say a click dismisses it), and its reminders,
  Wi-Fi QR and speed test overlays count as launchers. A process name the
  kernel cut mid-character no longer crashes the lock check.
- **Hyprland 0.57 workspaces.** Events name a renamed workspace by its
  number, so `wait_for(match="web")` never matched it and a `sequence`
  stopped on its own switch there. Both now know the display name. CLI
  output quotes workspace names.
- **Qt apps with a native GTK dialog open.** `ui`, `marks` and `click_ui`
  on the main window read the main window, not the dialog.
- The live lock test checks the real `hyprctl -j locked` reply.

### Added
- `hypruse doctor` says whether a window screenshot can capture the window
  itself, which takes grim 1.5+ (`-T`) on Hyprland 0.54+ (window ids).

### Changed
- Requires `mcp` 1.21.1 or newer; older versions fail at startup with
  current pydantic.

## [0.12.0] - 2026-10-08

### Fixed
- **Hyprland 0.57 workspaces.** Hyprland 0.57 drops `id` from special and
  named workspaces (hyprwm/Hyprland#16269). `desktop` sorted workspaces by
  id and would have crashed for anyone with a scratchpad up, and the
  visibility checks would have stopped seeing a pulled-up scratchpad, so
  the confinement guard would have failed open over it. A workspace is now
  named by its number when it has one and by its name otherwise
  (`"special:vault"`), in `desktop` and in every check. On 0.56 too, a
  pulled-up special workspace now reads `visible: true` in `desktop`.
- **The lock guard asks the compositor.** It used to look for a hyprlock
  or swaylock process. It now asks Hyprland (`hyprctl -j locked`, 0.41+),
  which also catches Omarchy 4's lock, built into its Quickshell shell,
  and a locker that crashed, which leaves the session locked. The process
  scan still names the locker in the refusal, and decides alone only when
  Hyprland cannot be asked.
- **Omarchy 4's polkit prompt.** Omarchy 4 draws its polkit prompt as a
  layer surface, not a window, so the auth interlock never saw it. A new
  `auth` layer kind refuses typing and clicks into it unless `allow_auth`,
  and stops `click_ui` and a running `sequence`. Omarchy's menu, pickers,
  bar popups, bar and notifications are classified too, instead of
  `unknown`.
- **Screenshots of hidden or covered windows** (#4). `screenshot(window=)`
  cropped the window's rectangle out of the screen, so a window on a hidden
  workspace or under another window came back as some other window's
  pixels, labelled as this one. It now captures the window itself with
  `grim -T` (grim 1.5+), scaled by the window's own monitor, and the
  metadata gains `visible`. Many apps stop drawing while hidden, so a
  hidden window's capture carries a note that it may show how the window
  last looked, and `stable=true` reports `stable: null` for it rather than
  vouching for a frozen frame. Window captures are refused while the
  session is locked, since Hyprland would hand over the window behind the
  lock. Where `grim -T` is not available, a window on screen falls back to
  the crop and a hidden one is refused, with how to bring it forward.
  `zoom(window=)` refuses a hidden window.
- **Qt apps with GTK theme integration** (#6, thanks @Pillumz). A Qt app
  run with `QT_QPA_PLATFORMTHEME=gtk3` registers two accessibility roots
  under one PID, the empty GTK bridge first, so `ui` found no controls. The
  first of them that holds a window now wins.
- A stable-capture test could pass by accident on a fast machine (#5,
  thanks @Pillumz).
- `hypruse pointer scroll DY` without DX exited with a usage error on
  Python 3.11 and on 3.12 before 3.12.7, an argparse bug that also kept CI
  red since 0.11.0.

### Added
- Every MCP tool declares a title and annotations: the seven observation
  tools are read-only, the acting ones destructive.

### Changed
- `launch`'s description opens with "Open an app".
- Removed the `Environment :: X11 Applications` classifier.

## [0.11.0] - 2026-09-13

### Added
- **Shell verbs**: every tool is now a command, for agents that run shell
  commands instead of MCP (Pi, Codex, Hermes, OpenClaw, or a Claude Code
  session that prefers a skill to a tool list). The verbs are the tool
  names, a tool's `action` is a positional sub-verb (`hypruse pointer click
  800 60`, `hypruse keyboard type hello --window 0x..`, `hypruse hypr
  workspace 3`, `hypruse clipboard read`), and the calls go through the
  same functions the MCP server registers, so every trust guard, the
  journal and the activity beacon apply unchanged. The contract is built
  for a program reading the output: compact plain text, one line per fact
  (`--json` for the raw result as one line); a capture prints its file path
  and coordinate metadata, never bytes (`--out PATH` moves it); errors are
  one line on stderr; exit 0 delivered, 1 error, 2 usage, 3 refused by a
  trust layer or by read-only mode, 4 ran but found nothing to act on (a
  `wait_for` timeout, no accessibility tree, an ambiguous name, a sequence
  that stopped). `--then` and `--dry-run` work as on the tools, `--allow-auth`
  where the tool has it, `-` reads text from stdin, and `launch` takes the
  app's own flags after `--`. `hypruse --help` lists every verb.
- **Agent skill** (`skills/hypruse`, shipped inside the package): a
  SKILL.md following the Agent Skills specification that teaches an agent
  the desktop-first workflow, the coordinate contract, the exit codes and
  the safety rules, with `references/verbs.md` and `references/recipes.md`
  one level down. `hypruse skill install` copies it to
  `~/.agents/skills/hypruse` and links it into the skills directory of each
  agent present on the machine (`--agent NAME` for one, created if absent;
  `--copy` instead of symlinks); `hypruse skill uninstall` removes exactly
  what it made and nothing that is not hypruse's. `hypruse init` offers the
  install and `doctor` reports it. The repo layout also works with
  `npx skills add IlyasKhallouki/hypruse`.
- **Cross-process state** for the verbs, in
  `$XDG_RUNTIME_DIR/hypruse/cli-state.json`, keyed by compositor instance:
  the `marks` numbering (so `click_ui --mark N` resolves), the `launched`
  confinement set (which would otherwise be empty again after the launch),
  and the `HYPRUSE_STRICT` seat baseline, without which the seat guard is a
  no-op in every fresh process and strict mode would silently fail open.
  Under `launched` confinement the set is pruned against the live window
  list before it is trusted, since an address can be reused.
- `hypruse serve` runs the MCP server explicitly (bare `hypruse` still does).
- Journal records written through the verbs carry `source: "cli"`, and
  `hypruse journal` shows it; they are still the agent's own actions, so
  `replay` re-issues them. The session header is written once per run of
  identical flags rather than once per process.

### Changed
- `hypruse.server` no longer imports the MCP stack at import time: the
  tools ask for content blocks through two small factories, and the FastMCP
  app is built on first use (`server.app()`, still reachable as
  `server.mcp`). A shell verb therefore never loads `mcp`, and starts in
  a few hundred milliseconds instead of several seconds.
- `safety.arm()` installs the SIGTERM and atexit cleanup without raising the
  beacon; `init()` calls it. A verb running beside a live server leaves the
  server's beacon alone but is still safe under `pkill -f hypruse` mid-drag.
- The file-mode capture metadata now carries `path`, so a reader takes the
  location from the record rather than from the sentence around it.
- The CLI's default usage is a complete verb list, and the interactive
  notice the server prints on a terminal points at it.

### Fixed
- `hypruse --help` and `-h` started the MCP server (silently, when stdin
  was a pipe, which it always is under an agent's shell): the entry point
  routed every leading dash to the server. Help and `--version` now print
  and exit without importing it.

## [0.10.0] - 2026-08-31

### Added
- **Action journal** (`HYPRUSE_JOURNAL`): an append-only NDJSON record of
  every tool call, one JSON object per line, in
  `$XDG_STATE_HOME/hypruse/journal.ndjson` or a path you name. Each entry
  carries the tool, the arguments as called, how long it took, and the
  outcome, and a session opens with the trust flags it ran under, so a
  refusal three hours in can be read next to the configuration that
  produced it. Refusals are recorded with the guard's own message: the
  confinement, auth, and seat layers previously decided and forgot, and
  this is the only place that history exists. `kind` separates input
  delivered to the desktop from the agent merely looking, which is the
  split a privacy audit needs; observation payloads are never recorded,
  only that they happened, so the journal cannot become a second copy of
  everything the agent saw. Typed and copied text is stored as a length
  plus a short digest unless `HYPRUSE_JOURNAL_TEXT=1` says otherwise,
  because keystrokes are passwords; a digest is not anonymity, though, so
  the file is written 0600 in a 0700 directory and rotates at
  `HYPRUSE_JOURNAL_MAX_BYTES` (8 MiB, one generation kept, 0 to never
  rotate). Unlike a guard, the recorder fails toward the ACTION: an
  unwritable journal, an unusable path, or a path that is not a regular
  file warns once on stderr and gets out of the way rather than failing a
  desktop over a log line.
- **Dry run** (`HYPRUSE_DRYRUN`): every acting tool validates its
  arguments and runs every trust guard, then reports the plan it was
  about to execute and delivers nothing. A rehearsal whose refusals
  differed from the real run would be worth nothing, so the guards stay
  exactly where they are; what changes is that no click, keystroke,
  window op, or clipboard write leaves the process, not even the window
  focus that normally precedes typing. Enforced twice: at each tool, and
  at the effect boundary itself (`input`'s six delivery functions,
  `hyprctl dispatch`, `clipboard write`), so a path nobody thought of
  fails loudly with nothing delivered instead of quietly acting during
  what the caller was told was a simulation. The server instructions tell
  the agent dry run is on, so it reports a plan rather than retrying an
  action that "did not work".
- `hypruse journal` reads the record back: one line per call, a second
  line for anything a trust layer refused, and a summary of actions,
  observations, refusals, and errors. `--acts`, `--refused`, `-n N`,
  `-v`.
- `hypruse replay` re-issues a journal's actions through the same tool
  functions, so the same guards apply to the replay. It prints the plan
  and stops there unless `--execute`. Everything it refuses, it refuses
  before taking the seat, because a refusal that lands halfway leaves the
  desktop part-way through someone else's plan: an action recorded by a
  newer hypruse (skipping it and running the rest would report success
  for a different sequence of events than the one recorded), a recorded
  window that no longer exists (`--skip-missing` runs the rest), text
  recorded as a digest, a `click_ui(mark=N)` whose numbering died with
  the session that drew it, a clipboard write without the same
  `HYPRUSE_CLIPBOARD` opt-in the server needs, and `HYPRUSE_READONLY` or
  `HYPRUSE_DRYRUN`. It paces itself from the recorded think time, capped
  by `--max-gap` and scaled by `--speed`. Its own actions are journaled
  and marked, so replaying a file twice runs the recorded plan rather
  than the plan plus the replay of it, and it raises the activity beacon
  only when no live hypruse already holds it, so a running server keeps
  its own `hypruse stop` and Waybar indicator.

### Fixed
- **Hyprland's Lua config manager** (0.56+). Every window operation failed
  on a desktop configured with a `hyprland.lua`: `launch`, all six `hypr`
  actions, `click_ui`, `keyboard` with a `window=` target, `use_bind`, and,
  without a named seat, every `pointer` and `sequence` move. 0.56 did not
  remove the string dispatchers, as the reports read; it added a second
  config manager and gave it a different IPC. Under that one `hyprctl
  dispatch X` evaluates the Lua expression `hl.dispatch(X)`, so
  `movecursor 100 100` is a syntax error, and `hyprctl keyword` is refused
  outright. Which manager runs is decided by the config file's extension,
  and `hyprland.lua` is looked for first, so a fresh 0.56 install is a Lua
  desktop while a 0.56 install with the old config is untouched: this is
  not something a version check can answer. hypruse now probes the manager
  once (`hyprctl -j status`, a JSON line, not the two-second `systeminfo`),
  speaks whichever dialect came back, and re-probes if a call fails, since
  `hyprctl reload full-reset` can swap managers under a running server.
  Callers are unchanged: a window op is still described once, in the
  legacy shape, and `hyprctl.py` translates. Thanks to @Sokoshy for the
  report, the `hl.dsp` enumeration, and the verified migration examples
  ([#1](https://github.com/IlyasKhallouki/hypruse/issues/1)).
- Ownership marking (`HYPRUSE_MARK`) was silently dead on a Lua config:
  the border rule could not install and no window was ever tagged, so even
  the documented workaround of putting the rule in your own config matched
  nothing, and the only surviving signal was the toast. The rule now goes
  out as `hl.window_rule` there, and a failure to install it says so on
  stderr instead of leaving a visibility layer quietly not running.
- `use_bind` on a Lua config now explains itself. Hyprland stores a Lua
  bind as an anonymous closure and exposes no IPC route to call it, so the
  tool refused with a Lua parser error about `__lua` plus an upstream note
  suggesting the caller had sent bad syntax, and a dry run cheerfully
  planned an action that could never run. `binds` reports those as action
  `lua` and drops the registry index, which is meaningless outside the
  compositor; the combo and description still say what the workflow is,
  and `use_bind` names the cause and points at `hypr` and `launch`.
- `binds` no longer lists pointer binds as if they were pressable. The
  `mouse` flag it filtered on is the hyprlang manager's, and a Lua config
  never sets it, so `SUPER+mouse:272` came through as a keyboard combo.
- `hypr` and `launch` now reject a `workspace` argument containing the
  punctuation a workspace identifier never has. On a Lua desktop the
  dispatcher argument is evaluated inside the compositor's own interpreter
  with the standard library open, and `workspace` was the one agent-supplied
  dispatcher argument with no structural validation, on deliberately the
  one `hypr` action that skips the confinement guard. Everything hypruse
  sends on the Lua path is now built as an escaped literal rather than
  interpolated.
- `hypruse doctor` reports which config manager the session runs.

### Changed
- `hypr` now validates its action, its `workspace` argument, and its
  target address before any guard runs and before anything is
  dispatched, matching what `keyboard` already did. A malformed address
  under confinement now says so, instead of reporting the window as
  missing.
- `keyboard(action="key")` parses the combo before it focuses the target
  window. An unknown modifier used to surface from `wtype` after the
  focus had already moved, which is exactly the seat change a refused
  call must not leave behind.
- `pointer` checks the button name, the x/y pairing, and a non-zero
  scroll itself rather than leaving all three to the input layer, so the
  same call is rejected whether or not it is going to be delivered.

## [0.9.4] - 2026-07-26

### Added
- hypruse is listed in the official [MCP registry](https://registry.modelcontextprotocol.io)
  as `io.github.IlyasKhallouki/hypruse`, so MCP clients that browse the
  registry can discover and install it. The entry ships as `server.json`
  and carries the env flags (`HYPRUSE_SCREENSHOT_MODE`, `HYPRUSE_READONLY`,
  `HYPRUSE_CONFINE`, and the rest of the trust layer) so clients can
  surface them at install time. The `release` workflow publishes the entry
  on a version tag, right after PyPI.

No code changes: the registry verifies package ownership by finding a
`mcp-name:` token in the README PyPI serves as the package description, and
a published version's description cannot be edited, so the token needed a
release of its own.

## [0.9.3] - 2026-07-22

### Fixed
- `HYPRUSE_STRICT` no longer locks up permanently after a human touches
  the seat. The guard's own error says "re-read desktop()/screenshot()
  and retry", but no read ever re-baselined the seat, so once tripped it
  refused every action for the rest of the session. Every observation of
  current state (`desktop`, the `screenshot`/`zoom` capture path, `ui`,
  `marks`) now re-arms the guard, making the documented recovery real.
  The guard also fails closed when the seat cannot be read at all,
  matching the trust layers' fail-toward-less-action invariant.
- `hypr close_window` with a `then=` observation no longer snapshots
  before the destroy lands (the observation used to still list the
  window it had just closed). It subscribes to the event socket before
  dispatching and waits, up to 1s, for the compositor to report the
  close; a window that will not close (an unsaved-changes prompt) is
  reported honestly instead of claimed closed, and a socket dying
  mid-confirmation degrades to a note rather than a tool error.
- `wait_for('layer_open')` gained a level-triggered pre-check: a
  launcher that mapped before the wait subscribed (the
  use_bind-then-wait pattern) satisfies the wait immediately instead of
  timing out forever. Filtered waits only, like the workspace pre-check.
  `layer_close` deliberately gets no pre-check: "nothing matching is
  mapped" also describes a layer that has not opened yet, and a wrong
  "already closed" is worse than an honest timeout.
- Input aimed under a focus-stealing layer surface (launcher, lock
  screen, on-screen keyboard) stops being reported as success the
  target never saw. `click_ui` refuses when such a layer covers the
  click point; `pointer` appends a warning naming the layer (clicking
  INTO a launcher is the legitimate way to drive one); `keyboard`
  refuses a `window=`-targeted type while any such layer holds the
  keyboard grab (the keys provably cannot reach that window, and
  `allow_auth` does not apply, since intending credential entry
  contradicts naming another target), refuses typing into a layer
  entirely while `HYPRUSE_CONFINE` is set (a layer surface is not a
  window and a launcher runs what it is given, the same escape
  `use_bind` is refused for), refuses typing under a lock screen unless
  `allow_auth=true`, and otherwise annotates window-less typing with
  where the keys really went.
- `click_ui` with `then='ui'` observes the clicked window, not whatever
  window holds focus after the click (a spawned dialog could steal it).
- `wait_for('title_change')` waits on `windowtitlev2` only: the
  address-only `windowtitle` event could never satisfy a title match,
  and an unfiltered wait could return a payload with no title in it.
  This requires a Hyprland new enough to emit `windowtitlev2`.
- `marks` now flashes the same `HYPRUSE_MARK` capture notice as
  `screenshot`/`zoom` (it takes a real capture and was silent).
- Input is refused while the session is LOCKED. This gap was found by
  audit: the lock-screen protections above were built on the `layers`
  list, but modern lockers (hyprlock, swaylock >= 1.7) are
  `ext-session-lock-v1` clients, not layer-shell clients, so they never
  appear there and every lock branch was unreachable against the locker
  people actually run. Hyprland exposes no lock state over IPC either,
  so detection now uses the locker process, which the protocol
  guarantees exits on unlock. `pointer`, `keyboard`, and `click_ui`
  refuse while it is up; `allow_auth=true` downgrades that to a note
  for a human who wants the agent to unlock. The docs claiming lock
  screens appear under `layers` were corrected in the same pass.
- `click_ui` with `then='ui'` no longer answers "window not found" when
  the click dismissed its own window (Close, OK, Discard). It falls
  back to the focused window, which is normally the parent.
- `keyboard` validates `action`/`text`/`keys` before focusing the
  target window, so a malformed call no longer moves the human's focus
  on its way to raising.
- A layer refusal that names the covering surface now names the TOPMOST
  one (highest level, most recently mapped) rather than the first in
  list order, and its "close it first" advice is written per kind: a
  lock screen does not yield to esc, and an on-screen keyboard is
  dismissed rather than escaped.
- `click_ui` now surfaces the session-lock note on the `allow_auth` path
  (it was discarded, so a click into a locked session reported a control
  it never reached), and refuses under a lock like `pointer`/`keyboard`
  do; both were untested at that call site.
- Two `_LOCKER_COMMS` entries could never match a real process:
  `swaylock-effects` is 16 characters and the kernel caps
  `/proc/PID/comm` at 15 (now stored truncated), and `gtygra` was a
  fabricated name with no basis in any packaging (removed). An
  import-time assertion now rejects any over-length entry.
- Under a lock, the keyboard-grab note no longer also fires and
  contradicts the lock note about where the keys went.
- The session-lock guard now refuses (not just annotates) a
  `window=`-targeted or confined input even with `allow_auth`, closing a
  regression where `keyboard(type, window=X, allow_auth=true)` under a
  lock would type X's secret into the credential prompt: naming a target
  window, or a confinement scope, contradicts intending to drive the
  prompt, so it is refused the same way a keyboard-grabbing layer is.
  `click_ui` refuses under a lock outright (it always targets a window
  control that the input cannot reach). `pointer` emits a single note,
  not two contradictory ones, when a lock and a covering layer coincide.
- A windowless `keyboard` type now fails closed under `HYPRUSE_CONFINE`
  when the active window cannot be resolved because hyprctl is
  unreadable, instead of skipping the confinement check (the virtual
  keyboard delivers keys even when hyprctl is down). "Nothing focused"
  and "the compositor is unreadable" are now distinguished.
- The strict-mode password-field refusal runs before the `focuswindow`
  dispatch, so a refused type no longer moves the human's focus first.
- `swaylock-effects` is packaged as a drop-in that ships
  `/usr/bin/swaylock` (comm `swaylock`, already covered), so the
  fabricated `swaylock-effect` locker entry was removed rather than
  modeling a `/proc` name no real packaging produces.

### Changed
- Read-only mode ships its own server instructions and observation-tool
  descriptions covering only the observation workflow, instead of the
  full-surface text that kept advertising the seven stripped acting
  tools (`click_ui`, `sequence`, `use_bind`, ...).

## [0.9.2] - 2026-07-19

### Fixed
- `sequence`: a `keyboard` or `click_ui` step with `window=X` no longer
  aborts the run when X lives on another workspace. Focusing X switched
  to its workspace, and the sequence mistook its own focus-induced switch
  for a human takeover, dropping the remaining steps (the documented
  type-then-enter pattern lost its enter). Any focus-capable step now
  excuses the workspace it actually landed on.
- `hypr fullscreen` / `toggle_floating` with no target act on the active
  window, but skipped the `HYPRUSE_STRICT` seat guard, so in strict mode
  they could fullscreen a window the human had just refocused. The
  target-less branch now checks the seat guard (address-targeted actions
  still skip it, since they name their window).

### Changed
- Documentation reorganized for the larger 0.8.0/0.9.x feature set: a new
  README "Features" section groups the tools into five capabilities
  (semantic control, click-by-name, the zoom loop, round-trip fusion,
  trust) ordered cheapest-first, each with a "use it well" tip; the
  roadmap no longer lists shipped work (`sequence`, `then=` fusion, the
  a11y tools) as future and gains "Deeper reading", "Trust", and
  "Measurement" sections; the "How it works" diagram shows the AT-SPI
  (`busctl`) path the `ui`/`marks`/`click_ui` tools use; the security
  model documents `pointer`'s `allow_auth` and that `use_bind` is refused
  under confinement; ARCHITECTURE gains a trust-layer section and notes
  `hypruse stop`. Several small factual fixes (the auth-guard override
  covers `pointer` too, a stale desktop-snapshot timing figure, the
  `super+enter` keyboard example that belonged to `use_bind`).

## [0.9.1] - 2026-07-19

### Fixed
- `HYPRUSE_MARK` ownership marking was a no-op on modern Hyprland. The
  0.9.0 code used `hyprctl keyword windowrulev2 "bordercolor ..."`, but
  `windowrulev2` is deprecated (a no-op), the field was renamed to
  `border_color`, it takes a single 6-char color, and the tag matcher
  dropped its colon (`tag NAME`), so the rule was silently rejected and
  swallowed. Marking now: reliably tags each agent-owned window
  `hypruse-owned` and flashes an on-screen notice when the agent opens a
  window (both verified working), and best-effort installs the corrected
  `border_color` windowrule (current and legacy matcher forms). A runtime
  window rule does not render on every Hyprland version/config, so the
  README documents the one-line config rule for a guaranteed outline;
  hypruse's tagging matches it.

## [0.9.0] - 2026-07-19

### Added
- Optional confinement and trust layers (`src/hypruse/trust.py`), each an
  opt-in env flag that fails toward less action and composes with the
  approval/beacon layers:
  - `HYPRUSE_CONFINE` restricts input to a scope of windows: `launched`
    (only windows hypruse opened this session, seeded from `launch`),
    `class:a,b`, or `workspace:1,2`. Keyboard, `click_ui`, and `hypr`
    window ops are refused outside scope; a `pointer` click is refused
    when any window under the point is out of scope (Hyprland's client
    list is not z-ordered, so it fails closed rather than guess the top
    window). A malformed value refuses every action.
  - `HYPRUSE_AUTH_GUARD` (default on) refuses to click or type into a
    known authentication dialog (polkit agents, keyring prompt);
    `=strict` also refuses typing into a password field detected in the
    accessibility tree. A per-call `allow_auth=true` on `keyboard` /
    `click_ui` overrides it and, changing the argument shape, surfaces in
    the approval prompt.
  - `HYPRUSE_STRICT` refuses to act when the cursor or focused window
    moved since hypruse's last action (the seat was taken); the agent
    must re-observe and retry.
  - `HYPRUSE_MARK` borders agent-owned windows (a runtime `windowrulev2`
    on a tag, torn down on exit) and flashes a rate-limited on-screen
    notice on capture.
- `hypruse stop` subcommand: an emergency stop that signals the running
  server to shut down gracefully (releasing any held pointer button and
  clearing the beacon), cleaner than `pkill` and safe to bind:
  `bind = SUPER SHIFT, BackSpace, exec, hypruse stop`.

## [0.8.0] - 2026-07-19

### Added
- `desktop` reports layer-shell surfaces: launchers (wofi/rofi), bars,
  notification popups, and lock screens are not windows and were
  previously invisible to the semantic snapshot. They appear under
  `layers` with namespace, a best-effort `kind` (prefix heuristic that
  degrades to `unknown`), level, monitor, and global geometry, from the
  same single batched hyprctl call.
- `wait_for` gains `layer_open` / `layer_close` (match on the layer
  namespace, e.g. waiting for a launcher to appear), `urgent` (a window
  demands attention), and `screencast` (screen sharing started/stopped),
  parsed from the matching socket2 events.
- `sequence` treats a keyboard-grabbing layer surface (launcher, lock
  screen, on-screen keyboard) appearing mid-run as a structural change
  and stops; notification popups and bars are explicitly noise and do
  not abort the run.
- `then='ui'`: a fourth act-and-observe mode that appends the focused
  window's accessible elements with their CURRENT values to the acting
  call's own result. Reading the effect of typing or toggling costs a
  few hundred exact tokens instead of a screenshot, and degrades to a
  note when the app exposes no tree.
- `marks` tool: Set-of-Marks capture, the window screenshot with every
  accessible control drawn as a numbered mark plus a JSON legend (role,
  name, current value, exact global click point per number). Drawing
  shells out to ImageMagick (optional, same idiom as grim/wtype/busctl);
  without it the exact legend still returns. Grounded in the set-of-mark
  visual-prompting and OSWorld a11y+screenshot results already cited in
  the README Research section.
- `click_ui` tool: click a control by accessible NAME or by a `marks`
  number in ONE call. The coordinate is resolved from the accessibility
  tree, the window is focused first, and the click goes through the real
  pointer path so it stays visible and inherits every safety guarantee
  (beacon, panic kill, seat serialization); AT-SPI DoAction was
  evaluated and rejected because invisible synthetic input would bypass
  that layer. Exact name match beats substring, an ambiguous name
  returns the candidate list instead of guessing, and mark offsets are
  window-relative so a moved window stays clickable. Also available as a
  `sequence` op.

## [0.7.1] - 2026-07-19

### Fixed
- The pixel-to-global coordinate contract on scaled monitors, two root
  causes found by an adversarial audit. grim's `-s` flag is an ABSOLUTE
  logical-to-pixel factor, not a multiplier of native pixels: every
  capture that emitted `-s` (the image-mode edge cap, an explicit
  `scale`, or a byte-budget rung) came back smaller than intended on any
  monitor with scale other than 1.0 while the metadata claimed otherwise,
  so mapped clicks landed off by the monitor scale (halved on a 2x
  display). And a capture rect straddling monitors with different scales
  is rendered by grim at the GREATEST intersected scale, not the scale
  under the rect's top-left corner. Both paths now model grim's real
  semantics, with HiDPI and cross-seam regression tests.
- `sequence`: a `wait_for` step now runs against the sequence's own event
  stream. The between-step settle drain used to consume the very event
  the wait then waited for on a fresh socket, so click-then-wait (the
  documented pattern) falsely timed out for any app that mapped its
  window within the 0.2s settle.
- `sequence`: a workspace step with a relative or alias target (`+1`,
  `e+1`, `previous`) no longer aborts the rest of the sequence. The
  compositor reports the resolved workspace name, which never equaled
  the literal argument, so the sequence mistook its own switch for a
  human takeover.
- `wait_for('workspace')` with no `match` filter now actually waits for
  the next switch instead of returning the current workspace instantly
  as already-satisfied. A filtered workspace wait still pre-checks.
- The Waybar module builds its JSON with `jq` and the beacon whitelists
  `last_action` down to a plain token: a crafted tool argument could
  previously inject JSON through the hand-built module output and blank
  the robot indicator while the agent still had hands on the desktop.
- The SIGTERM kill switch arriving mid-`drag` now releases the held
  button before the process dies, via a registered cleanup; a drag holds
  a button across ~200 ms of cursor moves, the one window the
  one-call press/release guarantee did not cover.
- Concurrent tool calls can no longer interleave input on the shared
  seat: pointer and keyboard operations are serialized by a reentrant
  lock (MCP hosts may issue tool calls in parallel and sync tools run on
  worker threads). The kill-switch cleanup stays lock-free.

### Changed
- CI tests all four advertised Python versions (3.11-3.14); the `mcp`
  dependency gets an upper bound (`<2`); both PKGBUILDs declare the
  `hyprland` runtime dependency; RELEASING.md documents bumping both
  version sources and a test enforces their agreement.
- README/ARCHITECTURE drift reconciled: 13 tools, `ui` listed in
  read-only mode, `sequence` listed among `then=` takers, lossless-PNG
  timing re-measured (~800 ms, not ~440 ms), `HYPRUSE_MAX_IMAGE_EDGE`
  documented, and a new security-model layer names prompt injection via
  on-screen text as untrusted input with approval as the backstop.

## [0.7.0] - 2026-07-18

### Added
- `ui` tool: read a window's AT-SPI accessibility tree and return its
  clickable elements by accessible name with exact global coordinates and
  no screenshot, for GTK/Qt apps that expose one. Read through the `busctl`
  CLI (no new Python dependencies), correlating the a11y app to the window
  by connection PID (frame-title fallback for multi-process apps) and
  mapping window-relative extents to global through the window's hyprctl
  position (AT-SPI screen coordinates are unreliable on Wayland). It is an
  observation tool (available in read-only mode) and returns a
  fall-back-to-vision message when an app exposes nothing (terminals,
  Electron/Chrome without `--force-renderer-accessibility`). Grounded in
  the GUI-agent literature (OSWorld, UFO, Agent-S); see the README
  Research section.
- `ui` also reports a control's CURRENT VALUE, not just its label: `value`
  for text typed into an entry and for a slider or spinner number,
  `percent` for a slider's position within its range, `checked` for a box
  or toggle. Reads are gated on role, so finding a button costs no extra
  calls, and a password field's contents are never read. Many dropdowns
  expose no value at all (GTK's newer combo boxes publish neither text nor
  selection), so a screenshot is still the way to read a rendered value.

## [0.6.0] - 2026-07-17

### Added
- `sequence` tool: run an ordered list of actions
  (pointer/keyboard/hypr/wait_for) in one MCP call, so a click/type/enter
  micro-sequence costs one model round-trip instead of several (LLM calls
  dominate task latency). With `stop_on_change` (default), the run stops,
  best-effort, when it notices a structural change between steps that the
  step did not intend (a window opening, closing, or moving, or a switch
  to a different workspace than the step asked for), so later steps do not
  act on stale state; it returns what ran plus the changed desktop.
  Matching is payload-aware, so the human doing the same kind of action to
  a different target still stops the run. Bare focus changes are not
  watched (a click focuses a window), so give a keyboard step a `window=`
  address to type into a specific window reliably. Bounded to 20 steps and
  ~30s total wall-clock. `then` observes the final state.

### Changed
- `desktop` assembles its snapshot from a single `hyprctl --batch` call
  instead of five separate queries, cutting the per-command fork overhead
  (~4x fewer forks; measured snapshot ~28 ms to ~20 ms). Output unchanged.

## [0.5.0] - 2026-07-17

### Added
- `keyboard` takes an optional `window` address that focuses the target
  window before typing, so keystrokes land in the intended app rather than
  whatever currently holds focus (focus-follows-mouse can retarget a shared
  seat between calls). Composes with `then`.
- Act-and-observe fusion: `pointer`, `keyboard`, `hypr`, and `use_bind`
  take an optional `then` argument that appends a fresh view of the result
  to the same tool call, so the agent sees an action's effect without a
  second round-trip (LLM calls dominate task latency). `then='desktop'`
  appends a semantic snapshot (~25 ms, best for window/focus changes),
  `then='screenshot'` a stable capture (best for visual changes),
  `then='none'` (default) nothing.

### Changed
- Screenshots default to JPEG q90 instead of PNG. On a 1080p frame this is
  roughly 13x faster to encode (measured ~640 ms to ~50 ms full monitor)
  and about 3x smaller, while full-res q90 reads UI text well. Pass
  `lossless=true` on `screenshot`/`zoom` for exact pixels (PNG). Because
  `capture_stable` inherits the format, its poll speeds up by the same
  factor. The byte-budget fit ladder now degrades JPEG quality before
  resolution, since grim's downscale filter is slower than a full-res
  capture; resolution is a last resort.

## [0.4.1] - 2026-07-17

### Fixed
- Multi-monitor and fractional-scaling hardening: `desktop` now reports
  monitor `geometry` in global logical coordinates (the same space window
  `at`/`size` and `pointer` use) instead of physical mode pixels, so on a
  fractionally-scaled or rotated monitor the reported size matches the
  layout. Rotated monitors surface a `transform` field, and their logical
  footprint has the axes swapped. Monitor-containment logic (used by
  `zoom` and screenshot scale lookup) shares one transform-aware
  `logical_rect` source of truth in `hyprctl`, fixing wrong-monitor
  selection and wrong `scale` stamps near fractional-scale seams.

## [0.4.0] - 2026-07-17

### Added
- `clipboard` tool, opt-in via `HYPRUSE_CLIPBOARD=1` (and never in
  read-only mode): read or write the text clipboard through wl-clipboard.
  Default installs keep the documented no-clipboard-access posture.
- `stable` parameter on `screenshot` and `zoom`: capture repeatedly (up
  to 2s) until two consecutive frames are byte-identical, so a capture
  right after an action does not land mid-animation; the metadata
  reports `stable` true/false.

### Changed
- Whole-notch scrolls now go out as discrete axis events (axis_discrete
  on the virtual-pointer wire), so applications that step per wheel
  click see real notches; fractional deltas keep the continuous path.

## [0.3.0] - 2026-07-17

### Added
- `zoom` tool: native-resolution re-capture around an estimated global
  point (`x,y`, optional `size` "WxH" and window clamp), returning the
  same pixel-to-global mapping metadata as `screenshot` plus the echoed
  `point`. This promotes the coarse-to-fine precision loop from a usage
  convention in the server instructions to a first-class primitive, the
  mechanism vendor computer-use implementations and the GUI-agents
  literature converged on (see the README Research section).

### Changed
- Roadmap: OCR click-by-text is dropped in favor of the zoom loop; the
  README gains a Research section with the sources behind the decision.

### Fixed
- Screenshot metadata could stamp the wrong `scale` near monitor seams on
  multi-monitor layouts with fractional scaling: monitor containment
  treated hyprctl's physical mode width/height as logical bounds. Monitor
  rects are now derived logically (size divided by scale, axes swapped
  for 90/270-degree transforms); `zoom` uses the same logical rects for
  clamping and monitor selection.

## [0.2.1] - 2026-07-16

### Fixed
- Keybinds are now run through the new `use_bind` tool (execute a bind's
  action by combo) instead of synthetic keypresses: Hyprland does not route
  virtual-keyboard input through its keybind matcher, so `keyboard` combos
  reach the focused app but never trigger compositor binds like `super+f`.
  The `binds`/`keyboard` docs and server instructions are corrected to
  match.
- `wait_for` pre-checks current state so it does not miss an event that
  fired before it could subscribe: `window_close` returns immediately when
  nothing matches, `workspace` when the target is already active.

## [0.2.0] - 2026-07-16

### Added
- `hypruse init`: registers the server in detected MCP clients (per-client
  confirmation, timestamped config backup, never overwrites an existing
  entry), then runs doctor. `hypruse doctor`: one-command diagnostics for
  dependencies, session discovery, the event socket, a virtual-pointer
  handshake, and a live capture.
- `binds` tool: the user's keybinds decoded to combos with descriptions,
  so agents drive the desktop through its owner's own shortcuts.
- `wait_for` tool: block on real compositor events (window open/close,
  workspace change, title change) with a match filter and timeout.
- Read-only mode: `HYPRUSE_READONLY=1` exposes only observation tools.

### Changed
- `launch` now subscribes to the Hyprland event socket before dispatching
  and blocks on the actual openwindow event (polling remains as fallback).
- Console entry point moved to `hypruse.cli:main`; bare `hypruse` still
  runs the stdio server, so existing client registrations keep working.

## [0.1.2] - 2026-07-16

### Changed
- Docs, docstrings, and package metadata use plain ASCII punctuation; em
  and en dashes removed throughout.

## [0.1.1] - 2026-07-16

### Fixed
- Ctrl+C no longer hangs with a "could not acquire lock for stdin at
  interpreter shutdown" fatal: the beacon's SIGINT handler was fighting the
  MCP/anyio runtime's own shutdown. SIGINT is now left to the runtime (the
  beacon is still cleaned up via `atexit`); only SIGTERM is handled.

### Added
- Running `hypruse` directly in a terminal now prints what it is and how to
  register it, then exits, instead of silently blocking as a stdio server
  waiting for a client that will never connect.

## [0.1.0] - 2026-07-16

Initial release.

### Added
- MCP stdio server with six tools: `desktop`, `screenshot`, `pointer`,
  `keyboard`, `hypr`, `launch`, plus cross-tool guidance in the server
  `instructions` and terse per-tool schemas for eager client loading.
- Semantic desktop snapshot over hyprctl IPC (token-lean, fixture-tested).
- Screenshots via grim: focused monitor, exact window crop, region zoom,
  with true output dimensions and scale for exact pixel→global mapping.
  Default returns a saved PNG path (works with hosts that mangle inline
  image blocks); `HYPRUSE_SCREENSHOT_MODE=image` returns wire-level image
  content auto-fit to a transport byte budget and long-edge cap
  (`HYPRUSE_MAX_IMAGE_BYTES`, `HYPRUSE_MAX_IMAGE_EDGE`) by degrading format
  before resolution, so the API never silently downscales under the model.
  Coarse-to-fine clicking guidance to counter pixel-estimation error.
- Pointer input over a raw `zwlr_virtual_pointer_v1` wire client, no
  ydotool, no uinput, no daemon; positioning via `hyprctl movecursor`.
- Keyboard input via wtype (XKB keymap upload; unicode/layout-correct),
  with combo parsing (`ctrl+shift+t`, `super+enter`, bare-mod taps).
- Workspace/window dispatchers and app launch with new-window detection,
  including single-instance apps (browsers) whose window is relocated to
  the requested workspace.
- Session discovery: finds `HYPRLAND_INSTANCE_SIGNATURE` / `WAYLAND_DISPLAY`
  from runtime sockets when the host launches the server with a stripped
  environment (dbus/systemd-activated desktop apps).
- Activity beacon + Waybar kill-switch indicator module.
- Unit suite, seat-safe live e2e, MCP stdio round-trip tests, supervised
  input verification script, CI.
