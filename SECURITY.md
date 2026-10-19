# Security policy

## Supported versions

Only the latest release gets security fixes. Fixes land on `main` and go
out in the next release on PyPI and the AUR.

## Reporting a vulnerability

Report it privately: open the repository's **Security** tab and choose
**Report a vulnerability**. Please don't open a public issue for it.

Say what an agent or an attacker can do with it and how to reproduce it,
with your Hyprland and hypruse versions.

## What counts

hypruse gives an agent your mouse, keyboard and screen on purpose, so an
agent clicking, typing or taking screenshots is not a vulnerability on its
own. These are vulnerabilities:

- an agent acting while `HYPRUSE_READONLY` is set, or outside the scope of
  `HYPRUSE_CONFINE` or `HYPRUSE_BACKGROUND` (a program started with
  `launch` is outside hypruse's reach once it runs)
- an agent clicking or typing into an authentication dialog that
  `HYPRUSE_AUTH_GUARD` recognises, without `allow_auth=true`
- typed text reaching the journal when it should be redacted, or anything
  written to disk or sent over a network beyond what the
  [security model](README.md#security-model) lists

The security model also lists known limits, such as prompt injection
through window titles and the lock check failing open when the system
state can't be read. Those are documented trade-offs, but a cheap way to
close one is still welcome as a report or a pull request.
