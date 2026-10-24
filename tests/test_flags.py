"""Safety switches fail toward safety (V72): HYPRUSE_READONLY, HYPRUSE_DRYRUN
and HYPRUSE_STRICT read a value that is neither on nor off as on."""

import pytest

from hypruse import flags, journal, trust


@pytest.mark.parametrize("value", ["1", "true", "YES", " on ", "ture", "y", "= 1", "enabled"])
def test_a_safety_flag_is_on_unless_it_says_off(monkeypatch, value):
    monkeypatch.setenv("HYPRUSE_DRYRUN", value)
    assert flags.safety_flag("HYPRUSE_DRYRUN") and journal.dry_run()
    monkeypatch.setenv("HYPRUSE_STRICT", value)
    assert trust._strict_on()


@pytest.mark.parametrize("value", ["", "0", "false", "No", " off "])
def test_a_safety_flag_is_off_only_when_it_says_so(monkeypatch, value):
    monkeypatch.setenv("HYPRUSE_READONLY", value)
    assert not flags.safety_flag("HYPRUSE_READONLY")


def test_a_near_miss_is_said_out_loud(monkeypatch, capsys):
    monkeypatch.setattr(flags, "_warned", set())
    monkeypatch.setenv("HYPRUSE_READONLY", "ture")
    flags.safety_flag("HYPRUSE_READONLY")
    assert "treating it as on" in capsys.readouterr().err


def test_a_danger_flag_keeps_the_strict_reading(monkeypatch):
    # HYPRUSE_JOURNAL_TEXT=maybe must not write keystrokes to disk
    monkeypatch.setenv("HYPRUSE_JOURNAL_TEXT", "maybe")
    assert not journal.text_recorded()


def test_an_empty_runtime_dir_is_not_the_working_directory(monkeypatch, tmp_path):
    # V46: set but empty, it made state paths relative to the cwd
    from hypruse import cli_state, safety, session

    monkeypatch.setenv("XDG_RUNTIME_DIR", "")
    monkeypatch.chdir(tmp_path)
    assert not str(cli_state.path()).startswith("hypruse")
    session.ensure_session_env()
    import os

    assert os.environ["XDG_RUNTIME_DIR"].startswith("/")
    assert safety.state_path().is_absolute()
