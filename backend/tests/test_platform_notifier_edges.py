"""Failure paths and odd text for the host notifiers (termux, macOS, Windows, Linux).

The subprocess boundary is faked: no real notifier runs. What matters is what
the dispatcher sees (a RuntimeError with a useful message, a killed process on
timeout) and that a title or body never changes the command that runs: they
arrive as data, whatever quotes, unicode or leading dashes they contain.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend.services.notification_channels.platforms.linux import LinuxNotifier
from backend.services.notification_channels.platforms.macos import MacNotifier
from backend.services.notification_channels.platforms.termux import TermuxNotifier
from backend.services.notification_channels.platforms.windows import (
    BODY_VAR,
    TITLE_VAR,
    WindowsNotifier,
    toast_script,
)
from backend.services.notification_events import NotificationSeverity

Notifier = TermuxNotifier | MacNotifier | WindowsNotifier | LinuxNotifier

# (notifier, executable, its message for a failed run, its "not found" message)
NOTIFIERS = [
    pytest.param(TermuxNotifier, "termux-notification", "termux-notification failed",
                 "termux-notification binary not found", id="termux"),
    pytest.param(MacNotifier, "osascript", "osascript failed", "osascript binary not found", id="macos"),
    pytest.param(WindowsNotifier, "powershell.exe", "PowerShell toast failed", "powershell.exe not found",
                 id="windows"),
]

ODD_TEXTS = [
    'say "hi"',
    "it's Bob's file",
    "back\\slash \\\" mixed",
    "-starts with a dash",
    "--title injected",
    "Ünïcödé ✓ 同步 🚀",
    "two\nlines",
    "$(whoami) `id` ${HOME} $env:PATH",
    "; Remove-Item -Recurse C:\\",
]


class FakeProcess:
    """Stands in for asyncio.subprocess.Process."""

    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr

    def kill(self) -> None:
        self.killed = True


class Exec:
    """Records the argv of each create_subprocess_exec call; returns ``proc`` or raises ``error``."""

    def __init__(self, proc: FakeProcess | None = None, error: BaseException | None = None) -> None:
        self.proc = proc or FakeProcess()
        self.error = error
        self.argv: list[tuple[str, ...]] = []
        self.kwargs: list[dict[str, object]] = []

    async def __call__(self, *argv: str, **kwargs: object) -> FakeProcess:
        self.argv.append(argv)
        self.kwargs.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.proc


@pytest.fixture
def fake_exec(monkeypatch: pytest.MonkeyPatch):
    def install(proc: FakeProcess | None = None, error: BaseException | None = None) -> Exec:
        fake = Exec(proc, error)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
        return fake

    return install


async def _time_out(awaitable, timeout):
    """asyncio.wait_for that times out at once (closing the coroutine, so nothing is left unawaited)."""
    awaitable.close()
    raise asyncio.TimeoutError


# --- failures the dispatcher reports -----------------------------------------


@pytest.mark.parametrize("cls, exe, failed, not_found", NOTIFIERS)
async def test_missing_executable_is_a_clear_error(fake_exec, cls, exe, failed, not_found) -> None:
    """The tool not being installed is reported by name, not as a bare FileNotFoundError."""
    fake = fake_exec(error=FileNotFoundError(exe))

    with pytest.raises(RuntimeError) as raised:
        await cls().send("Title", "Body", NotificationSeverity.ERROR)

    assert str(raised.value) == not_found
    assert fake.argv[0][0] == exe


@pytest.mark.parametrize("cls, exe, failed, not_found", NOTIFIERS)
async def test_non_zero_exit_reports_code_and_stderr(
    fake_exec, caplog: pytest.LogCaptureFixture, cls, exe, failed, not_found,
) -> None:
    """A notifier that ran but failed says why (its stderr) and is logged as a warning."""
    fake_exec(FakeProcess(returncode=2, stderr=b"  module BurntToast not found\n"))

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError) as raised:
        await cls().send("Title", "Body", NotificationSeverity.WARNING)

    assert str(raised.value) == f"{failed} (rc=2): module BurntToast not found"
    assert str(raised.value) in caplog.text


@pytest.mark.parametrize("cls, exe, failed, not_found", NOTIFIERS)
async def test_hung_notifier_is_killed(fake_exec, monkeypatch: pytest.MonkeyPatch, cls, exe, failed, not_found) -> None:
    """A notifier that never returns is killed, so no process is left behind per event."""
    fake = fake_exec()
    monkeypatch.setattr(asyncio, "wait_for", _time_out)

    with pytest.raises(RuntimeError, match="timed out after 5s"):
        await cls().send("Title", "Body", NotificationSeverity.INFO)

    assert fake.proc.killed


@pytest.mark.xfail(strict=True, reason="stderr is decoded as strict UTF-8; PowerShell on a non-English "
                   "Windows writes it in the OEM code page, so the error becomes a UnicodeDecodeError")
async def test_non_utf8_stderr_still_gives_the_failure(fake_exec) -> None:
    """German Windows: PowerShell's error text in cp850 ('Ä' is 0x8e) must not hide the failure."""
    fake_exec(FakeProcess(returncode=1, stderr="Modul 'BurntToast' nicht gefunden: Ä".encode("cp850")))

    with pytest.raises(RuntimeError, match=r"rc=1"):
        await WindowsNotifier().send("Title", "Body", NotificationSeverity.ERROR)


# --- odd titles and bodies ----------------------------------------------------


@pytest.mark.parametrize("text", ODD_TEXTS)
async def test_termux_passes_text_as_option_values(fake_exec, text: str) -> None:
    """termux-notification gets the text as argv values (no shell): unchanged, right after its option."""
    fake = fake_exec()

    await TermuxNotifier().send(text, text + " body", NotificationSeverity.ERROR)

    argv = list(fake.argv[0])
    assert argv[argv.index("--title") + 1] == text
    assert argv[argv.index("--content") + 1] == text + " body"
    assert argv[argv.index("--priority") + 1] == "high"
    assert len(argv) == 7  # nothing in the text became an argument of its own


def _applescript_literals(script: str) -> list[str]:
    """The string literals of an AppleScript source, unescaped (\\" and \\\\)."""
    literals: list[str] = []
    i = 0
    while i < len(script):
        if script[i] != '"':
            i += 1
            continue
        i += 1
        value = []
        while script[i] != '"':
            if script[i] == "\\":
                i += 1
            value.append(script[i])
            i += 1
        literals.append("".join(value))
        i += 1
    return literals


@pytest.mark.parametrize("text", ODD_TEXTS)
async def test_macos_script_keeps_text_inside_its_literals(fake_exec, text: str) -> None:
    """osascript runs one fixed statement; the title and body are only ever string literals in it."""
    fake = fake_exec()

    await MacNotifier().send(text, "body: " + text, NotificationSeverity.ERROR)

    exe, flag, script = fake.argv[0]
    assert (exe, flag) == ("osascript", "-e")
    assert _applescript_literals(script) == ["body: " + text, text, "Funk"]
    assert script.startswith('display notification "')
    assert script.endswith(' sound name "Funk"')


# PowerShell treats these four as single quotes too (language spec, verbatim strings).
_PS_QUOTES = "'\u2018\u2019\u201a\u201b"


def _powershell_verbatim_literals(script: str) -> list[str]:
    """The single-quoted string literals of a PowerShell command, as PowerShell reads them."""
    literals: list[str] = []
    i = 0
    while i < len(script):
        if script[i] not in _PS_QUOTES:
            i += 1
            continue
        i += 1
        value = []
        while i < len(script):
            if script[i] in _PS_QUOTES:
                if i + 1 < len(script) and script[i + 1] in _PS_QUOTES:
                    value.append(script[i])
                    i += 2
                    continue
                break
            value.append(script[i])
            i += 1
        literals.append("".join(value))
        i += 1
    return literals


def _windows_call(fake: Exec) -> tuple[str, str, str]:
    """The script PowerShell ran, and the title and body it got through its environment."""
    argv = fake.argv[0]
    assert argv[:4] == ("powershell.exe", "-NoProfile", "-NonInteractive", "-Command")
    assert len(argv) == 5
    env = fake.kwargs[0]["env"]
    assert isinstance(env, dict)
    return argv[4], env[TITLE_VAR], env[BODY_VAR]


@pytest.mark.parametrize("text", ODD_TEXTS)
async def test_windows_text_never_becomes_script(fake_exec, text: str) -> None:
    """The toast command is the same whatever the text: the title and body reach
    PowerShell as environment variables, so there is no literal for them to end."""
    fake = fake_exec()

    await WindowsNotifier().send(text, "body: " + text, NotificationSeverity.ERROR)

    script, title, body = _windows_call(fake)
    assert script == toast_script(NotificationSeverity.ERROR)
    assert _powershell_verbatim_literals(script) == ["Alarm"]
    assert (title, body) == (text, "body: " + text)


@pytest.mark.parametrize("text", [
    "Bob\u2019s report.docx",
    "\u2019; Start-Process calc; \u2019",
])
async def test_windows_typographic_quotes_stay_inside_the_literal(fake_exec, text: str) -> None:
    """A file named with a typographic apostrophe (common in documents) must not end a
    literal early: that broke the toast, and let a crafted name run PowerShell code."""
    fake = fake_exec()

    await WindowsNotifier().send("Sync conflict", text, NotificationSeverity.ERROR)

    script, title, body = _windows_call(fake)
    assert _powershell_verbatim_literals(script) == ["Alarm"]
    assert (title, body) == ("Sync conflict", text)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(title=st.text(), body=st.text())
async def test_windows_any_text_is_carried_verbatim(fake_exec, title: str, body: str) -> None:
    """Property: for any title and body the script is fixed and the text arrives unchanged
    (bar NUL, which no environment variable can hold)."""
    fake = fake_exec()

    await WindowsNotifier().send(title, body, NotificationSeverity.WARNING)

    script, got_title, got_body = _windows_call(fake)
    assert script == toast_script(NotificationSeverity.WARNING)
    assert (got_title, got_body) == (title.replace("\0", ""), body.replace("\0", ""))


@pytest.mark.xfail(strict=True, reason="notify-send gets the title and body as positional arguments with "
                   "no '--' before them, so text starting with '-' is parsed as an option")
@pytest.mark.parametrize("title, body", [("-draft.txt could not be synced", "Body"), ("Title", "--help")])
async def test_linux_text_starting_with_a_dash_is_not_an_option(fake_exec, title: str, body: str) -> None:
    """notify-send (GOption) rejects unknown options, so a title like '-draft.txt ...'
    fails the notification unless the arguments end with '--' first."""
    fake = fake_exec()

    await LinuxNotifier().send(title, body, NotificationSeverity.ERROR)

    argv = list(fake.argv[0])
    assert argv[-2:] == [title, body]
    assert "--" in argv[:-2]
