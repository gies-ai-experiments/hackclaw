"""Tests for the application reminder poller."""
from __future__ import annotations

from nanobot.onboard.reminder_poller import build_applied_set


def _app_row(*members: tuple[str, str, str, str]) -> list[str]:
    """Build a 26-col application row with up to 4 members.

    Matches the live Google Sheet shape: Timestamp at col 0, then the
    25 columns parser.py originally expected from the CSV export.
    ``build_applied_set`` strips col 0 before parsing.
    """
    row = ["2026-04-17T00:00:00", "TeamX", str(len(members)), "m", "m", "m", "m"]
    for i in range(4):
        if i < len(members):
            row.extend(members[i])
        else:
            row.extend(["", "", "", ""])
    row.extend(["focus", "comfort", "yes"])
    return row


def test_build_applied_set_one_team() -> None:
    rows = [
        _app_row(
            ("Alice", "ALICE@illinois.edu", "Finance", "Junior"),
            ("Bob", "bob@ilinois.edu", "Accounting", "Senior"),  # typo'd domain
        ),
    ]
    applied = build_applied_set(rows)
    assert applied == {"alice@illinois.edu", "bob@illinois.edu"}


def test_build_applied_set_drops_non_illinois() -> None:
    rows = [_app_row(("Carol", "carol@gmail.com", "Marketing", "Senior"))]
    assert build_applied_set(rows) == set()


def test_build_applied_set_skips_blank_rows() -> None:
    rows = [["", "", "", "", "", ""]]  # too short, no members
    assert build_applied_set(rows) == set()


from nanobot.onboard.reminder_poller import Action, InterestRow, classify


def _ir(
    *,
    email: str = "alice@illinois.edu",
    program: str = "Finance",
    reminder_count: int = 0,
    not_eligible_sent: str = "",
) -> InterestRow:
    return InterestRow(
        row_index=2,
        name="Alice",
        raw_email=email,
        program=program,
        reminder_count=reminder_count,
        not_eligible_sent_at=not_eligible_sent,
    )


def test_classify_skip_when_already_applied() -> None:
    row = _ir()
    assert classify(row, applied={"alice@illinois.edu"}, max_reminders=3) == Action.SKIP


def test_classify_skip_when_non_illinois_email() -> None:
    row = _ir(email="alice@gmail.com")
    assert classify(row, applied=set(), max_reminders=3) == Action.SKIP


def test_classify_reminder_when_gies_and_not_applied() -> None:
    row = _ir(program="Finance", reminder_count=0)
    assert classify(row, applied=set(), max_reminders=3) == Action.REMINDER


def test_classify_skip_when_reminder_cap_reached() -> None:
    row = _ir(program="Finance", reminder_count=3)
    assert classify(row, applied=set(), max_reminders=3) == Action.SKIP


def test_classify_not_eligible_when_non_gies_program() -> None:
    row = _ir(program="Computer Science", reminder_count=0)
    assert classify(row, applied=set(), max_reminders=3) == Action.NOT_ELIGIBLE


def test_classify_skip_when_not_eligible_already_sent() -> None:
    row = _ir(program="Computer Science", not_eligible_sent="2026-04-17T10:00:00")
    assert classify(row, applied=set(), max_reminders=3) == Action.SKIP


from nanobot.onboard.reminder_poller import parse_interest_row


def _interest(*, name="A", email="a@illinois.edu", program="Finance",
              count="", last_at="", neligible_at="") -> list[str]:
    return [
        "2026-04-17T00:00:00", name, email, "Junior", "Yes", program,
        "x", "x", "x", "x", "x",
        count, last_at, neligible_at,
    ]


def test_parse_interest_row_basic() -> None:
    raw = _interest()
    row = parse_interest_row(2, raw)
    assert row.row_index == 2
    assert row.name == "A"
    assert row.raw_email == "a@illinois.edu"
    assert row.program == "Finance"
    assert row.reminder_count == 0
    assert row.not_eligible_sent_at == ""


def test_parse_interest_row_reads_dedup_state() -> None:
    raw = _interest(count="2", last_at="2026-04-17T01:00:00", neligible_at="")
    row = parse_interest_row(7, raw)
    assert row.row_index == 7
    assert row.reminder_count == 2


def test_parse_interest_row_short_row_defaults_dedup() -> None:
    raw = _interest()[:6]  # only timestamp..program
    row = parse_interest_row(3, raw)
    assert row.reminder_count == 0
    assert row.not_eligible_sent_at == ""


def test_parse_interest_row_garbage_count_defaults_zero() -> None:
    raw = _interest(count="not-a-number")
    row = parse_interest_row(4, raw)
    assert row.reminder_count == 0


from unittest.mock import MagicMock, patch

from nanobot.onboard.reminder_poller import SMTPSettings, send_email
from nanobot.onboard.templates import RenderedEmail


@patch("nanobot.onboard.reminder_poller.smtplib.SMTP")
def test_send_email_starttls(mock_smtp_cls: MagicMock) -> None:
    mock_conn = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_conn

    send_email(
        to_email="alice@illinois.edu",
        rendered=RenderedEmail(subject="Hi", body="Body\n"),
        smtp=SMTPSettings(
            host="smtp.gmail.com",
            port=587,
            username="bot@gmail.com",
            password="apppass",
            from_address="bot@gmail.com",
            use_tls=True,
            use_ssl=False,
        ),
    )

    mock_conn.starttls.assert_called_once()
    mock_conn.login.assert_called_once_with("bot@gmail.com", "apppass")
    mock_conn.send_message.assert_called_once()
    sent = mock_conn.send_message.call_args[0][0]
    assert sent["To"] == "alice@illinois.edu"
    assert sent["From"] == "bot@gmail.com"
    assert sent["Subject"] == "Hi"
    assert sent.get_content().rstrip("\n") == "Body"


from datetime import datetime

from nanobot.onboard.reminder_poller import run_once
from nanobot.onboard.sheet_io import SheetWriter


def test_run_once_sends_reminder_and_stamps() -> None:
    interest_rows = [_interest(name="Alice", email="alice@illinois.edu", program="Finance")]
    sent: list[tuple[str, str]] = []
    ws = MagicMock()
    writer = SheetWriter(ws)
    now = datetime(2026, 4, 17, 12, 0, 0)

    run_once(
        interest_rows=interest_rows,
        application_rows=[],
        writer=writer,
        send_fn=lambda *, to_email, rendered: sent.append((to_email, rendered.subject)),
        max_reminders=3,
        now=now,
    )

    assert sent == [("alice@illinois.edu", "Finish your Gies AI for Impact Challenge application")]
    # row_index 1 (first data row), new_count = 1. _sheet_row = row_index + 1 = 2.
    ws.update_cell.assert_any_call(2, 12, 1)
    ws.update_cell.assert_any_call(2, 13, "2026-04-17T12:00:00")


def test_run_once_sends_not_eligible_for_non_gies() -> None:
    interest_rows = [_interest(email="dan@illinois.edu", program="Computer Science")]
    sent: list[tuple[str, str]] = []
    ws = MagicMock()
    writer = SheetWriter(ws)
    now = datetime(2026, 4, 17, 12, 0, 0)

    run_once(
        interest_rows=interest_rows,
        application_rows=[],
        writer=writer,
        send_fn=lambda *, to_email, rendered: sent.append((to_email, rendered.subject)),
        max_reminders=3,
        now=now,
    )

    assert sent[0][0] == "dan@illinois.edu"
    assert "Gies AI for Impact Challenge" in sent[0][1]
    ws.update_cell.assert_called_once_with(2, 14, "2026-04-17T12:00:00")


def test_run_once_skips_applied() -> None:
    interest_rows = [_interest(email="bob@illinois.edu", program="Finance")]
    # Live sheet layout: Timestamp at col 0, then the 25 CSV-export columns.
    app_row = ["2026-04-17T00:00:00", "TeamX", "1", "m", "m", "m", "m"]
    app_row += ["Bob", "BOB@illinois.edu", "Finance", "Senior"]
    app_row += ["", "", "", ""] * 3
    app_row += ["focus", "comfort", "yes"]

    sent: list[tuple[str, str]] = []
    ws = MagicMock()
    writer = SheetWriter(ws)

    run_once(
        interest_rows=interest_rows,
        application_rows=[app_row],
        writer=writer,
        send_fn=lambda *, to_email, rendered: sent.append((to_email, rendered.subject)),
        max_reminders=3,
        now=datetime(2026, 4, 17, 12, 0, 0),
    )

    assert sent == []
    ws.update_cell.assert_not_called()


def test_run_once_send_failure_does_not_increment_count() -> None:
    interest_rows = [_interest(email="alice@illinois.edu", program="Finance")]
    ws = MagicMock()
    writer = SheetWriter(ws)

    def boom(*, to_email: str, rendered) -> None:
        raise ConnectionError("smtp down")

    run_once(
        interest_rows=interest_rows,
        application_rows=[],
        writer=writer,
        send_fn=boom,
        max_reminders=3,
        now=datetime(2026, 4, 17, 12, 0, 0),
    )

    ws.update_cell.assert_not_called()


from nanobot.onboard.reminder_poller import smtp_settings_from_config


def test_smtp_settings_from_config_extracts_email_channel() -> None:
    fake_config = MagicMock()
    fake_config.channels.email.smtp_host = "smtp.gmail.com"
    fake_config.channels.email.smtp_port = 587
    fake_config.channels.email.smtp_username = "bot@gmail.com"
    fake_config.channels.email.smtp_password = "apppass"
    fake_config.channels.email.smtp_use_tls = True
    fake_config.channels.email.smtp_use_ssl = False
    fake_config.channels.email.from_address = "bot@gmail.com"

    settings = smtp_settings_from_config(fake_config)
    assert settings.host == "smtp.gmail.com"
    assert settings.port == 587
    assert settings.username == "bot@gmail.com"
    assert settings.password == "apppass"
    assert settings.from_address == "bot@gmail.com"
    assert settings.use_tls is True
    assert settings.use_ssl is False
