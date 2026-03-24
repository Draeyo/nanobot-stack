"""Tests for email_calendar.EmailCalendarFetcher."""
# pylint: disable=redefined-outer-name,protected-access,attribute-defined-outside-init
from __future__ import annotations

import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure src/bridge is on the import path
# ---------------------------------------------------------------------------
SRC_BRIDGE = Path(__file__).resolve().parent.parent / "src" / "bridge"
if str(SRC_BRIDGE) not in sys.path:
    sys.path.insert(0, str(SRC_BRIDGE))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_ICS = Path(__file__).resolve().parent / "fixtures" / "sample.ics"


def _make_raw_email(
    message_id: str,
    subject: str,
    sender: str,
    body: str = "Hello world",
    in_reply_to: str = "",
) -> bytes:
    """Build a minimal RFC 2822 email as bytes."""
    msg = MIMEText(body)
    msg["Message-ID"] = message_id
    msg["Subject"] = subject
    msg["From"] = sender
    msg["Date"] = "Mon, 24 Mar 2026 09:00:00 +0000"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    return msg.as_bytes()


def _make_fetcher(enabled: bool = True, db_path: Path | None = None, **env_overrides):
    """Helper: patch env vars and return an EmailCalendarFetcher."""
    env = {
        "EMAIL_CALENDAR_ENABLED": "true" if enabled else "false",
        "EMAIL_IMAP_HOST": "imap.example.com",
        "EMAIL_IMAP_PORT": "993",
        "EMAIL_IMAP_USER": "user@example.com",
        "EMAIL_IMAP_PASSWORD": "secret",
        "EMAIL_IMAP_FOLDER": "INBOX",
        "EMAIL_MAX_FETCH": "20",
        "CALENDAR_ICS_PATH": str(FIXTURE_ICS),
    }
    env.update(env_overrides)

    with patch.dict(os.environ, env, clear=False):
        from email_calendar import EmailCalendarFetcher
        fetcher = EmailCalendarFetcher()
        if db_path:
            fetcher._db_path = str(db_path)
        return fetcher


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    """Temporary SQLite DB with email_sync_log table."""
    db_path = tmp_path / "scheduler.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS email_sync_log (
            id          TEXT PRIMARY KEY,
            account     TEXT NOT NULL,
            last_synced TEXT NOT NULL,
            items_synced INTEGER DEFAULT 0,
            status      TEXT NOT NULL
        )
    """)
    db.commit()
    db.close()
    return db_path


# ---------------------------------------------------------------------------
# test_disabled_flag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disabled_flag():
    """EMAIL_CALENDAR_ENABLED=false → all methods return empty lists, no IMAP/CalDAV calls."""
    with patch("imaplib.IMAP4_SSL") as mock_ssl, \
         patch.dict(os.environ, {"EMAIL_CALENDAR_ENABLED": "false"}, clear=False):
        from email_calendar import EmailCalendarFetcher
        fetcher = EmailCalendarFetcher()

        emails = await fetcher.fetch_recent_emails()
        events = await fetcher.fetch_today_agenda()

        assert emails == []
        assert events == []
        mock_ssl.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_sync_to_qdrant():
    """sync_to_qdrant returns zeros when disabled."""
    with patch.dict(os.environ, {"EMAIL_CALENDAR_ENABLED": "false"}, clear=False):
        from email_calendar import EmailCalendarFetcher
        fetcher = EmailCalendarFetcher()
        result = await fetcher.sync_to_qdrant(MagicMock())
        assert result == {"emails": 0, "events": 0}


# ---------------------------------------------------------------------------
# test_tls_only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tls_only():
    """Verify IMAP4_SSL is used (not plain IMAP4) for email fetch."""
    raw = _make_raw_email("<tls-test@x>", "TLS test", "a@b.com", in_reply_to="<prev@x>")

    mock_imap = MagicMock()
    mock_imap.login.return_value = ("OK", [])
    mock_imap.select.return_value = ("OK", [b"1"])
    mock_imap.search.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1 (BODY[HEADER]", raw), b")", (b"1 (BODY[TEXT]", b"body text"), b")"])
    mock_imap.logout.return_value = ("BYE", [])

    with patch("imaplib.IMAP4_SSL", return_value=mock_imap) as mock_ssl_cls, \
         patch("imaplib.IMAP4") as mock_plain_cls:
        fetcher = _make_fetcher(enabled=True)
        await fetcher.fetch_recent_emails(since_hours=24)

        mock_ssl_cls.assert_called_once()
        mock_plain_cls.assert_not_called()


# ---------------------------------------------------------------------------
# test_imap_fetch_mock
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_imap_fetch_mock():
    """Mock IMAP4_SSL — fetch_recent_emails returns correct fields."""
    raw_header = _make_raw_email(
        "<abc123@mail.com>", "Bonjour", "alice@example.com", in_reply_to="<prev@x>"
    )

    mock_imap = MagicMock()
    mock_imap.login.return_value = ("OK", [])
    mock_imap.select.return_value = ("OK", [b"1"])
    # Return one unseen message id
    mock_imap.search.side_effect = [
        ("OK", [b"1"]),   # UNSEEN search
        ("OK", [b""]),    # FLAGGED search
    ]
    mock_imap.fetch.return_value = (
        "OK",
        [(b"1 (BODY[HEADER])", raw_header), b")", (b"1 (BODY[TEXT])", b"Email body here"), b")"],
    )
    mock_imap.logout.return_value = ("BYE", [])

    with patch("imaplib.IMAP4_SSL", return_value=mock_imap), \
         patch("dm_pairing.list_approved_users", return_value=[]):
        fetcher = _make_fetcher(enabled=True)
        emails = await fetcher.fetch_recent_emails(since_hours=24)

    # The email has In-Reply-To so it passes the importance filter
    assert len(emails) == 1
    em = emails[0]
    assert em["message_id"] == "<abc123@mail.com>"
    assert em["subject"] == "Bonjour"
    assert em["sender"] == "alice@example.com"
    assert "thread" in em["tags"]
    assert len(em["snippet"]) <= 500


# ---------------------------------------------------------------------------
# test_imap_dedup_by_message_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_imap_dedup_by_message_id():
    """Two emails with same message_id → only one kept."""
    raw = _make_raw_email("<dup@x>", "Dup", "x@y.com", in_reply_to="<prev@x>")

    mock_imap = MagicMock()
    mock_imap.login.return_value = ("OK", [])
    mock_imap.select.return_value = ("OK", [b"2"])
    # Return two message IDs that have the same Message-ID header
    mock_imap.search.side_effect = [
        ("OK", [b"1 2"]),
        ("OK", [b""]),
    ]
    mock_imap.fetch.return_value = (
        "OK",
        [(b"x (BODY[HEADER])", raw), b")", (b"x (BODY[TEXT])", b"body"), b")"],
    )
    mock_imap.logout.return_value = ("BYE", [])

    with patch("imaplib.IMAP4_SSL", return_value=mock_imap), \
         patch("dm_pairing.list_approved_users", return_value=[]):
        fetcher = _make_fetcher(enabled=True)
        emails = await fetcher.fetch_recent_emails(since_hours=24)

    message_ids = [e["message_id"] for e in emails]
    assert len(set(message_ids)) == len(message_ids), "Duplicate message_ids found"


# ---------------------------------------------------------------------------
# test_important_filter_heuristic
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_important_filter_heuristic():
    """Flagged email → included; plain unseen from unknown sender → excluded."""
    raw_flagged = _make_raw_email("<flagged@x>", "Important", "boss@corp.com")
    raw_plain = _make_raw_email("<plain@x>", "Newsletter", "newsletter@spam.com")

    mock_imap = MagicMock()
    mock_imap.login.return_value = ("OK", [])
    mock_imap.select.return_value = ("OK", [b"2"])
    mock_imap.search.side_effect = [
        ("OK", [b"1 2"]),   # UNSEEN returns ids 1 and 2
        ("OK", [b"1"]),     # FLAGGED returns id 1 only
    ]

    # fetch is called for each message id
    def fetch_side_effect(mid, _spec):
        if mid == b"1":
            return ("OK", [(b"1 (BODY[HEADER])", raw_flagged), b")", (b"1 (BODY[TEXT])", b"body"), b")"])
        return ("OK", [(b"2 (BODY[HEADER])", raw_plain), b")", (b"2 (BODY[TEXT])", b"body"), b")"])

    mock_imap.fetch.side_effect = fetch_side_effect
    mock_imap.logout.return_value = ("BYE", [])

    with patch("imaplib.IMAP4_SSL", return_value=mock_imap), \
         patch("dm_pairing.list_approved_users", return_value=[]):
        fetcher = _make_fetcher(enabled=True)
        emails = await fetcher.fetch_recent_emails(since_hours=24)

    subjects = [e["subject"] for e in emails]
    assert "Important" in subjects
    assert "Newsletter" not in subjects


# ---------------------------------------------------------------------------
# test_ics_fetch_local
# ---------------------------------------------------------------------------

def _make_mock_icalendar():
    """Build a minimal mock of the icalendar module for use in sys.modules."""
    import types
    mock_ical_mod = types.ModuleType("icalendar")

    class MockVDDDLists:
        def __init__(self, dt):
            self.dt = dt

    class MockCalendar:
        @staticmethod
        def from_ical(data: bytes):
            cal = MockCalendar()
            cal._data = data
            return cal

        def walk(self):
            # Parse dates from the fixture ICS — simple fixed return
            class FakeVEvent:
                name = "VEVENT"

                def get(self, key, default=""):
                    mapping = {
                        "UID": "test-event-001@nanobot",
                        "SUMMARY": "Réunion équipe",
                        "DTSTART": MockVDDDLists(datetime(2026, 3, 24, 9, 0, 0, tzinfo=timezone.utc)),
                        "DTEND": MockVDDDLists(datetime(2026, 3, 24, 10, 0, 0, tzinfo=timezone.utc)),
                        "LOCATION": "Salle A",
                        "DESCRIPTION": "Weekly team sync",
                    }
                    return mapping.get(key, default)

            class FakeVEvent2:
                name = "VEVENT"

                def get(self, key, default=""):
                    mapping = {
                        "UID": "test-event-002@nanobot",
                        "SUMMARY": "Dentiste",
                        "DTSTART": MockVDDDLists(datetime(2026, 3, 24, 14, 30, 0, tzinfo=timezone.utc)),
                        "DTEND": MockVDDDLists(datetime(2026, 3, 24, 15, 30, 0, tzinfo=timezone.utc)),
                        "LOCATION": "15 rue de la Paix",
                        "DESCRIPTION": "Rendez-vous annuel",
                    }
                    return mapping.get(key, default)

            class FakeCalComp:
                name = "VCALENDAR"

                def get(self, _key, default=""):
                    return default

            return [FakeCalComp(), FakeVEvent(), FakeVEvent2()]

    mock_ical_mod.Calendar = MockCalendar
    return mock_ical_mod


@pytest.mark.asyncio
async def test_ics_fetch_local():
    """Parse local ICS fixture file — events in next-24h window returned."""
    # Events are dated 2026-03-24T09:00Z and 14:30Z
    mock_ical = _make_mock_icalendar()

    with patch.dict(sys.modules, {"icalendar": mock_ical}), \
         patch.dict(os.environ, {
             "EMAIL_CALENDAR_ENABLED": "true",
             "CALENDAR_ICS_PATH": str(FIXTURE_ICS),
             "CALENDAR_CALDAV_URL": "",
         }, clear=False):
        from importlib import reload
        import email_calendar
        reload(email_calendar)
        fetcher = email_calendar.EmailCalendarFetcher()

        # Patch _event_in_window to always accept (window simulation)
        with patch.object(fetcher, "_event_in_window", return_value=True):
            events = await fetcher.fetch_today_agenda()

    assert len(events) >= 1
    titles = [e["title"] for e in events]
    assert "Réunion équipe" in titles or "Dentiste" in titles


@pytest.mark.asyncio
async def test_ics_fetch_fields():
    """ICS fixture events have the expected fields."""
    mock_ical = _make_mock_icalendar()

    with patch.dict(sys.modules, {"icalendar": mock_ical}), \
         patch.dict(os.environ, {
             "EMAIL_CALENDAR_ENABLED": "true",
             "CALENDAR_ICS_PATH": str(FIXTURE_ICS),
             "CALENDAR_CALDAV_URL": "",
         }, clear=False):
        from importlib import reload
        import email_calendar
        reload(email_calendar)
        fetcher = email_calendar.EmailCalendarFetcher()
        with patch.object(fetcher, "_event_in_window", return_value=True):
            events = await fetcher.fetch_today_agenda()

    required_keys = {"event_uid", "title", "start_dt", "end_dt", "location", "description", "source"}
    for ev in events:
        missing = required_keys - set(ev.keys())
        assert not missing, f"Event missing fields: {missing}"


# ---------------------------------------------------------------------------
# test_caldav_fetch_mock
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_caldav_fetch_mock():
    """Mock caldav.DAVClient — fetch_today_agenda returns filtered events."""
    ics_data = (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        b"UID:caldav-evt-001@test\r\nSUMMARY:CalDAV Meeting\r\n"
        b"DTSTART:20260324T100000Z\r\nDTEND:20260324T110000Z\r\n"
        b"LOCATION:Online\r\nDESCRIPTION:Remote meeting\r\n"
        b"END:VEVENT\r\nEND:VCALENDAR\r\n"
    )

    mock_event = MagicMock()
    mock_event.data = ics_data

    mock_cal = MagicMock()
    mock_cal.date_search.return_value = [mock_event]

    mock_principal = MagicMock()
    mock_principal.calendars.return_value = [mock_cal]

    mock_client_instance = MagicMock()
    mock_client_instance.principal.return_value = mock_principal

    # Build a mock caldav module so import inside _fetch_caldav_sync succeeds
    import types
    mock_caldav_mod = types.ModuleType("caldav")
    mock_caldav_mod.DAVClient = MagicMock(return_value=mock_client_instance)

    # Build a mock icalendar that parses the ics_data above
    mock_ical = _make_mock_icalendar()

    with patch.dict(sys.modules, {"caldav": mock_caldav_mod, "icalendar": mock_ical}), \
         patch.dict(os.environ, {
             "EMAIL_CALENDAR_ENABLED": "true",
             "CALENDAR_CALDAV_URL": "https://caldav.example.com/",
             "CALENDAR_USERNAME": "user",
             "CALENDAR_PASSWORD": "pass",
         }, clear=False):
        from importlib import reload
        import email_calendar
        reload(email_calendar)
        fetcher = email_calendar.EmailCalendarFetcher()
        events = await fetcher.fetch_today_agenda()

    assert len(events) >= 1
    assert events[0]["source"] == "caldav"


# ---------------------------------------------------------------------------
# test_agenda_formatting
# ---------------------------------------------------------------------------

def test_agenda_formatting():
    """format_agenda produces 'HH:MM – Title (Location)' lines."""
    from email_calendar import EmailCalendarFetcher
    events = [
        {"title": "Réunion équipe", "start_dt": "2026-03-24T09:00:00+00:00", "location": "Salle A",
         "end_dt": "", "description": "", "event_uid": "1"},
        {"title": "Dentiste", "start_dt": "2026-03-24T14:30:00+00:00", "location": "15 rue de la Paix",
         "end_dt": "", "description": "", "event_uid": "2"},
    ]
    result = EmailCalendarFetcher.format_agenda(events)
    assert "09:00" in result
    assert "Réunion équipe" in result
    assert "Salle A" in result
    assert "14:30" in result
    assert "Dentiste" in result


def test_agenda_formatting_no_location():
    """Events without location don't show empty parentheses."""
    from email_calendar import EmailCalendarFetcher
    events = [
        {"title": "Solo task", "start_dt": "2026-03-24T11:00:00+00:00", "location": "",
         "end_dt": "", "description": "", "event_uid": "3"},
    ]
    result = EmailCalendarFetcher.format_agenda(events)
    assert "Solo task" in result
    assert "()" not in result


def test_agenda_empty():
    """Empty events list returns placeholder message."""
    from email_calendar import EmailCalendarFetcher
    result = EmailCalendarFetcher.format_agenda([])
    assert "Aucun événement" in result


# ---------------------------------------------------------------------------
# test_sync_to_qdrant_upsert_count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_to_qdrant_upsert_count(tmp_db):
    """sync_to_qdrant returns {"emails": N, "events": M} with correct counts."""
    mock_qdrant = MagicMock()
    mock_qdrant.get_collection.return_value = MagicMock()
    mock_qdrant.upsert.return_value = MagicMock()

    sample_emails = [
        {"message_id": f"<msg{i}@x>", "subject": f"Sub {i}", "sender": f"s{i}@x.com",
         "date": "Mon, 24 Mar 2026 09:00:00 +0000", "snippet": "body", "tags": ["unread"]}
        for i in range(3)
    ]
    sample_events = [
        {"event_uid": f"uid-{i}", "title": f"Event {i}", "start_dt": "2026-03-24T10:00:00Z",
         "end_dt": "2026-03-24T11:00:00Z", "location": "", "description": "", "source": "ics"}
        for i in range(2)
    ]

    with patch.dict(os.environ, {"EMAIL_CALENDAR_ENABLED": "true"}, clear=False):
        from importlib import reload
        import email_calendar
        reload(email_calendar)
        fetcher = email_calendar.EmailCalendarFetcher()
        fetcher._db_path = str(tmp_db)

    with patch.object(fetcher, "fetch_recent_emails", new_callable=AsyncMock) as mock_emails, \
         patch.object(fetcher, "fetch_today_agenda", new_callable=AsyncMock) as mock_events, \
         patch("email_calendar.EmailCalendarFetcher._make_vector", return_value=[0.0] * 384):
        mock_emails.return_value = sample_emails
        mock_events.return_value = sample_events

        result = await fetcher.sync_to_qdrant(mock_qdrant)

    assert result["emails"] == 3
    assert result["events"] == 2
    assert mock_qdrant.upsert.call_count == 5  # 3 emails + 2 events


# ---------------------------------------------------------------------------
# test_email_digest_window_calculation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_email_digest_window_last_synced_3h_ago(tmp_db):
    """last_synced 3h ago → email window = ~3h."""
    now = datetime.now(timezone.utc)
    last_synced = (now - timedelta(hours=3)).isoformat()
    log_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "sync-log-imap"))

    db = sqlite3.connect(str(tmp_db))
    db.execute(
        "INSERT INTO email_sync_log (id, account, last_synced, items_synced, status) VALUES (?,?,?,?,?)",
        (log_id, "imap", last_synced, 5, "ok")
    )
    db.commit()
    db.close()

    from scheduler_executor import JobExecutor
    executor = JobExecutor(db_path=str(tmp_db), notifier=MagicMock())
    # Sub-daily cron (every 2h)
    window = executor._email_window_hours("0 */2 * * *", None)
    assert 3 <= window <= 5  # roughly 3-4h


@pytest.mark.asyncio
async def test_email_digest_window_null_last_synced(tmp_db):
    """No last_synced record → email window defaults to 24h."""
    from scheduler_executor import JobExecutor
    executor = JobExecutor(db_path=str(tmp_db), notifier=MagicMock())
    window = executor._email_window_hours("0 */2 * * *", None)
    assert window == 24


# ---------------------------------------------------------------------------
# test_email_digest_cron_validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_email_digest_cron_too_frequent_raises(tmp_db):
    """email_digest with cron < 2h interval raises ValueError."""
    from scheduler_executor import JobExecutor
    executor = JobExecutor(db_path=str(tmp_db), notifier=MagicMock())

    with pytest.raises(ValueError, match="email_digest"):
        await executor.collect_sections(
            sections=["email_digest"],
            cron="*/30 * * * *",   # every 30 minutes
            last_run=None,
            prompt="",
            job_name="test",
        )


@pytest.mark.asyncio
async def test_email_digest_cron_ok_does_not_raise(tmp_db):
    """email_digest with cron >= 2h does not raise ValueError."""
    from scheduler_executor import JobExecutor
    executor = JobExecutor(db_path=str(tmp_db), notifier=MagicMock())

    # Patch out the actual fetch so the test completes without credentials
    with patch("email_calendar.EmailCalendarFetcher.fetch_recent_emails",
               new_callable=AsyncMock, return_value=[]):
        try:
            await executor.collect_sections(
                sections=["email_digest"],
                cron="0 */3 * * *",   # every 3 hours — should be fine
                last_run=None,
                prompt="",
                job_name="test",
            )
        except ValueError as e:
            pytest.fail(f"ValueError raised unexpectedly: {e}")


# ---------------------------------------------------------------------------
# test_sync_log_written
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_log_written(tmp_db):
    """sync_to_qdrant writes a row to email_sync_log."""
    mock_qdrant = MagicMock()
    mock_qdrant.get_collection.return_value = MagicMock()
    mock_qdrant.upsert.return_value = MagicMock()

    with patch.dict(os.environ, {"EMAIL_CALENDAR_ENABLED": "true"}, clear=False):
        from importlib import reload
        import email_calendar
        reload(email_calendar)
        fetcher = email_calendar.EmailCalendarFetcher()
        fetcher._db_path = str(tmp_db)

    with patch.object(fetcher, "fetch_recent_emails", new_callable=AsyncMock, return_value=[]), \
         patch.object(fetcher, "fetch_today_agenda", new_callable=AsyncMock, return_value=[]):
        await fetcher.sync_to_qdrant(mock_qdrant)

    db = sqlite3.connect(str(tmp_db))
    rows = db.execute("SELECT account, status FROM email_sync_log").fetchall()
    db.close()

    accounts = {r[0] for r in rows}
    assert "imap" in accounts
