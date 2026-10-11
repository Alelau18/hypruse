"""Shared test wiring.

The one rule here: a unit test must never read live system state. The
0.9.3 audit found a guard that was dead code against the real
compositor while its tests passed happily on fabricated fixtures, so
anything that touches the machine gets neutralized by default and a
test that wants the interesting state opts in explicitly.
"""

import pytest

from hypruse import hyprctl, journal, server, trust


@pytest.fixture(autouse=True)
def no_ambient_journal(monkeypatch):
    """No recording, no dry run, unless a test asks for it.

    Same rule as below, pointed at the developer's environment rather
    than the machine's: a suite run with HYPRUSE_JOURNAL exported would
    append hundreds of fabricated entries to a real audit trail, and one
    with HYPRUSE_DRYRUN set would pass while every acting tool did
    nothing.
    """
    for var in ("HYPRUSE_JOURNAL", "HYPRUSE_JOURNAL_TEXT", "HYPRUSE_DRYRUN"):
        monkeypatch.delenv(var, raising=False)
    journal._broken = False
    # replay marks its records `by: replay` and the CLI verbs stamp theirs
    # with a source; a test that ran either must not leave the mark on every
    # record the next test writes
    monkeypatch.setattr(journal, "_origin", "")
    monkeypatch.setattr(journal, "_source", "")
    # and a verb switches the content blocks to the plain kind for the rest
    # of the process; the MCP-facing tests must keep seeing the real types
    monkeypatch.setattr(server, "_plain_blocks", False)


@pytest.fixture(autouse=True)
def hyprlang_provider(monkeypatch):
    """The config manager is hyprlang, unless a test says otherwise.

    Same rule again: hyprctl.provider() shells out to the compositor and
    then caches the answer in a module global, so without this the suite
    would probe the developer's own session and whichever test got there
    first would decide the wire format for every test after it.
    """
    monkeypatch.setattr(hyprctl, "_provider", hyprctl.HYPRLANG)


@pytest.fixture(autouse=True)
def unlocked_session(monkeypatch):
    """No session locker, unless a test says otherwise.

    trust.session_locked() scans /proc, so without this the suite's
    result would depend on whether the machine running it happens to be
    locked: green on a developer's desk, red in a locked session.
    """
    monkeypatch.setattr(trust, "session_locked", lambda: None)


@pytest.fixture(autouse=True)
def compositor_lock_unknown(monkeypatch):
    """The compositor cannot be asked about the lock, unless a test says
    otherwise, so trust.session_locked() falls back to its /proc scan.

    Asking would run `hyprctl locked` against whatever Hyprland happens
    to be running the suite.
    """
    monkeypatch.setattr(trust, "_compositor_locked", lambda: None)


@pytest.fixture(autouse=True)
def foreground_by_default(monkeypatch):
    """HYPRUSE_BACKGROUND=0 (0.12 behaviour) unless a test says otherwise.

    The tests written before background mode assert foreground behaviour;
    background tests unset it to get the automatic default.
    """
    monkeypatch.setenv("HYPRUSE_BACKGROUND", "0")


@pytest.fixture(autouse=True)
def no_live_batch_query(monkeypatch):
    """hyprctl.batch_query and hyprctl.query fail unless a test stubs them.

    Same rule: it shells out to whatever Hyprland is running the suite, and
    a test that forgot to stub it would pass or fail on the developer's own
    desktop. Code that degrades without the compositor gets to degrade.
    """
    def unreachable(cmds):
        raise hyprctl.HyprctlError("no compositor in unit tests")

    monkeypatch.setattr(hyprctl, "batch_query", unreachable)
    monkeypatch.setattr(hyprctl, "query", lambda what: unreachable([what]))
