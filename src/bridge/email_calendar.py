"""EmailCalendarFetcher — IMAP email ingestion and CalDAV/ICS calendar ingestion."""
from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("rag-bridge.email_calendar")

STATE_DIR = Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))

EMAIL_INBOX_TTL = 7 * 24 * 3600    # 7 days in seconds
CALENDAR_TTL = 30 * 24 * 3600      # 30 days in seconds
SNIPPET_MAX = 500


class EmailCalendarFetcher:
    """Fetches emails via IMAP and calendar events via CalDAV or ICS file.

    All methods return empty lists when EMAIL_CALENDAR_ENABLED is not 'true'.
    Credentials are read from environment variables and never stored anywhere.
    """

    def __init__(self, settings: dict | None = None) -> None:
        self._enabled = os.getenv("EMAIL_CALENDAR_ENABLED", "false").lower() == "true"

        # IMAP config
        self._imap_host = os.getenv("EMAIL_IMAP_HOST", "")
        self._imap_port = int(os.getenv("EMAIL_IMAP_PORT", "993"))
        self._imap_user = os.getenv("EMAIL_IMAP_USER", "")
        self._imap_password = os.getenv("EMAIL_IMAP_PASSWORD", "")
        self._imap_folder = os.getenv("EMAIL_IMAP_FOLDER", "INBOX")
        self._max_fetch = int(os.getenv("EMAIL_MAX_FETCH", "20"))

        # CalDAV / ICS config
        self._caldav_url = os.getenv("CALENDAR_CALDAV_URL", "")
        self._calendar_user = os.getenv("CALENDAR_USERNAME", "")
        self._calendar_password = os.getenv("CALENDAR_PASSWORD", "")
        self._ics_path = os.getenv("CALENDAR_ICS_PATH", "")

        self._db_path = str(STATE_DIR / "scheduler.db")

    # ------------------------------------------------------------------
    # Public async interface
    # ------------------------------------------------------------------

    async def fetch_recent_emails(self, since_hours: int = 24) -> list[dict]:
        """Fetch recent emails via IMAP (TLS only). Returns list of email dicts."""
        if not self._enabled:
            return []
        if not self._imap_host or not self._imap_user or not self._imap_password:
            logger.warning("IMAP not fully configured — skipping email fetch")
            return []
        return await asyncio.to_thread(self._fetch_emails_sync, since_hours)

    async def fetch_today_agenda(self) -> list[dict]:
        """Fetch calendar events for the next 24 hours via CalDAV or ICS."""
        if not self._enabled:
            return []
        if self._caldav_url:
            return await asyncio.to_thread(self._fetch_caldav_sync)
        if self._ics_path:
            return await asyncio.to_thread(self._fetch_ics_sync, self._ics_path)
        logger.debug("No calendar source configured — skipping agenda fetch")
        return []

    async def sync_to_qdrant(self, qdrant_client: Any) -> dict:
        """Upsert emails and events to Qdrant. Returns {"emails": N, "events": M}."""
        if not self._enabled:
            return {"emails": 0, "events": 0}

        emails: list[dict] = []
        events: list[dict] = []
        email_status = "ok"
        cal_status = "ok"

        try:
            emails = await self.fetch_recent_emails(since_hours=24)
        except Exception as exc:
            logger.exception("IMAP fetch failed during sync")
            email_status = "error"

        try:
            events = await self.fetch_today_agenda()
        except Exception as exc:
            logger.exception("Calendar fetch failed during sync")
            cal_status = "error"

        email_count = await asyncio.to_thread(
            self._upsert_emails, qdrant_client, emails
        )
        event_count = await asyncio.to_thread(
            self._upsert_events, qdrant_client, events
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        self._update_sync_log("imap", now_iso, email_count, email_status)
        cal_account = "caldav" if self._caldav_url else "ics"
        self._update_sync_log(cal_account, now_iso, event_count, cal_status)

        return {"emails": email_count, "events": event_count}

    # ------------------------------------------------------------------
    # IMAP — synchronous internals (run in thread)
    # ------------------------------------------------------------------

    def _fetch_emails_sync(self, since_hours: int) -> list[dict]:
        """Connect via IMAP4_SSL (TLS required) and return filtered email list."""
        results: list[dict] = []

        # TLS only — IMAP4_SSL is the only permitted connection type
        conn = imaplib.IMAP4_SSL(self._imap_host, self._imap_port)
        try:
            conn.login(self._imap_user, self._imap_password)
            conn.select(self._imap_folder)

            since_date = (datetime.now(timezone.utc) - timedelta(hours=since_hours))
            since_str = since_date.strftime("%d-%b-%Y")

            # Search for unseen messages since the window date
            status, data = conn.search(None, f'(UNSEEN SINCE {since_str})')
            if status != "OK" or not data or not data[0]:
                return results

            msg_ids = data[0].split()
            # Take the most recent N
            msg_ids = msg_ids[-self._max_fetch:]

            # Fetch approved senders for the known_contact heuristic
            approved_senders = self._get_approved_senders()

            # Fetch flagged message IDs
            flagged_ids: set[bytes] = set()
            try:
                flag_status, flag_data = conn.search(None, "FLAGGED")
                if flag_status == "OK" and flag_data and flag_data[0]:
                    flagged_ids = set(flag_data[0].split())
            except Exception:
                pass

            seen_message_ids: set[str] = set()

            for mid in msg_ids:
                try:
                    _, msg_data = conn.fetch(mid, "(BODY[HEADER] BODY[TEXT])")
                    if not msg_data or not msg_data[0]:
                        continue

                    raw_header = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
                    raw_text = b""
                    if len(msg_data) > 2 and isinstance(msg_data[2], tuple):
                        raw_text = msg_data[2][1]
                    elif len(msg_data) > 1 and isinstance(msg_data[1], tuple):
                        raw_text = msg_data[1][1]

                    parsed = email.message_from_bytes(raw_header)

                    message_id = parsed.get("Message-ID", "").strip()
                    if not message_id:
                        message_id = f"no-id-{mid.decode()}"

                    # Dedup by message_id
                    if message_id in seen_message_ids:
                        continue
                    seen_message_ids.add(message_id)

                    subject = self._decode_header(parsed.get("Subject", ""))
                    sender = self._decode_header(parsed.get("From", ""))
                    date_str = parsed.get("Date", "")
                    in_reply_to = parsed.get("In-Reply-To", "") or parsed.get("References", "")

                    # Importance filter — at least one must match
                    is_flagged = mid in flagged_ids
                    has_thread = bool(in_reply_to.strip())
                    is_known = self._is_known_contact(sender, approved_senders)

                    if not (is_flagged or has_thread or is_known):
                        continue

                    tags: list[str] = ["unread"]
                    if is_flagged:
                        tags.append("flagged")
                    if has_thread:
                        tags.append("thread")
                    if is_known:
                        tags.append("known_contact")

                    # Extract snippet from body text
                    snippet = self._extract_snippet(raw_text)

                    results.append({
                        "message_id": message_id,
                        "subject": subject,
                        "sender": sender,
                        "date": date_str,
                        "snippet": snippet,
                        "tags": tags,
                    })
                except Exception:
                    logger.exception("Failed to process email message %s", mid)

        finally:
            try:
                conn.logout()
            except Exception:
                pass

        return results

    def _decode_header(self, value: str) -> str:
        if not value:
            return ""
        try:
            parts = email.header.decode_header(value)
            decoded_parts = []
            for part, charset in parts:
                if isinstance(part, bytes):
                    decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
                else:
                    decoded_parts.append(str(part))
            return " ".join(decoded_parts).strip()
        except Exception:
            return value

    def _extract_snippet(self, raw_text: bytes) -> str:
        if not raw_text:
            return ""
        try:
            text = raw_text.decode("utf-8", errors="replace")
        except Exception:
            text = ""
        # Strip excessive whitespace
        import re
        text = re.sub(r"\s+", " ", text).strip()
        return text[:SNIPPET_MAX]

    def _get_approved_senders(self) -> set[str]:
        try:
            from dm_pairing import list_approved_users  # type: ignore[import]
            users = list_approved_users()
            senders: set[str] = set()
            for u in users:
                if "email" in u:
                    senders.add(u["email"].lower())
            return senders
        except Exception:
            return set()

    def _is_known_contact(self, sender: str, approved: set[str]) -> bool:
        sender_lower = sender.lower()
        for addr in approved:
            if addr in sender_lower:
                return True
        return False

    # ------------------------------------------------------------------
    # CalDAV — synchronous internals (run in thread)
    # ------------------------------------------------------------------

    def _fetch_caldav_sync(self) -> list[dict]:
        try:
            import caldav  # type: ignore[import]
        except ImportError:
            logger.error("caldav package not installed — cannot fetch CalDAV events")
            return []

        results: list[dict] = []
        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=24)

        try:
            client = caldav.DAVClient(
                url=self._caldav_url,
                username=self._calendar_user or None,
                password=self._calendar_password or None,
            )
            principal = client.principal()
            calendars = principal.calendars()
            for cal in calendars:
                try:
                    vevent_list = cal.date_search(start=now, end=end, expand=True)
                    for vevent in vevent_list:
                        try:
                            event_dict = self._parse_caldav_event(vevent, source="caldav")
                            if event_dict:
                                results.append(event_dict)
                        except Exception:
                            logger.exception("Failed to parse CalDAV event")
                except Exception:
                    logger.exception("Failed to search calendar")
        except Exception:
            logger.exception("CalDAV connection failed")

        results.sort(key=lambda e: e.get("start_dt", ""))
        return results

    def _parse_caldav_event(self, vevent: Any, source: str) -> dict | None:
        try:
            from icalendar import Calendar  # type: ignore[import]
            cal_data = vevent.data if hasattr(vevent, "data") else str(vevent)
            if isinstance(cal_data, str):
                cal_data = cal_data.encode()
            cal = Calendar.from_ical(cal_data)
            for component in cal.walk():
                if component.name == "VEVENT":
                    return self._extract_event_fields(component, source)
        except Exception:
            logger.exception("Failed to parse vevent data")
        return None

    def _extract_event_fields(self, component: Any, source: str) -> dict:
        def to_iso(dt_val: Any) -> str:
            if dt_val is None:
                return ""
            if hasattr(dt_val, "dt"):
                dt_val = dt_val.dt
            if isinstance(dt_val, datetime):
                if dt_val.tzinfo is None:
                    dt_val = dt_val.replace(tzinfo=timezone.utc)
                return dt_val.isoformat()
            # date only
            return str(dt_val)

        uid = str(component.get("UID", ""))
        title = str(component.get("SUMMARY", ""))
        start_dt = to_iso(component.get("DTSTART"))
        end_dt = to_iso(component.get("DTEND"))
        location = str(component.get("LOCATION", ""))
        description = str(component.get("DESCRIPTION", ""))[:SNIPPET_MAX]

        return {
            "event_uid": uid,
            "title": title,
            "start_dt": start_dt,
            "end_dt": end_dt,
            "location": location,
            "description": description,
            "source": source,
        }

    # ------------------------------------------------------------------
    # ICS file — synchronous internals (run in thread)
    # ------------------------------------------------------------------

    def _fetch_ics_sync(self, ics_path: str) -> list[dict]:
        try:
            from icalendar import Calendar  # type: ignore[import]
        except ImportError:
            logger.error("icalendar package not installed — cannot parse ICS file")
            return []

        results: list[dict] = []
        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=24)

        try:
            with open(ics_path, "rb") as f:
                cal = Calendar.from_ical(f.read())
            for component in cal.walk():
                if component.name != "VEVENT":
                    continue
                try:
                    event_dict = self._extract_event_fields(component, source="ics")
                    # Filter to next-24h window
                    if self._event_in_window(event_dict, now, end):
                        results.append(event_dict)
                except Exception:
                    logger.exception("Failed to parse ICS component")
        except Exception:
            logger.exception("Failed to read ICS file: %s", ics_path)

        results.sort(key=lambda e: e.get("start_dt", ""))
        return results

    def _event_in_window(self, event: dict, start: datetime, end: datetime) -> bool:
        try:
            ev_start_str = event.get("start_dt", "")
            if not ev_start_str:
                return False
            ev_start = datetime.fromisoformat(ev_start_str)
            if ev_start.tzinfo is None:
                ev_start = ev_start.replace(tzinfo=timezone.utc)
            return start <= ev_start <= end
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Qdrant upsert — synchronous internals (run in thread)
    # ------------------------------------------------------------------

    def _make_vector(self, text: str) -> list[float]:
        """Generate a dense vector for the given text using sentence-transformers."""
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]
            model = SentenceTransformer("all-MiniLM-L6-v2")
            vec = model.encode(text, normalize_embeddings=True).tolist()
            return vec
        except Exception:
            logger.warning("sentence-transformers unavailable — using zero vector")
            return [0.0] * 384

    def _ensure_collection(self, qdrant_client: Any, name: str, vector_size: int = 384) -> None:
        try:
            from qdrant_client.models import Distance, VectorParams  # type: ignore[import]
            qdrant_client.get_collection(name)
        except Exception:
            try:
                from qdrant_client.models import Distance, VectorParams  # type: ignore[import]
                qdrant_client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
                )
            except Exception:
                logger.exception("Failed to create Qdrant collection: %s", name)

    def _upsert_emails(self, qdrant_client: Any, emails: list[dict]) -> int:
        if not emails:
            return 0
        try:
            from qdrant_client.models import PointStruct  # type: ignore[import]
        except ImportError:
            logger.error("qdrant_client not available")
            return 0

        self._ensure_collection(qdrant_client, "email_inbox")
        count = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        expires_at = time.time() + EMAIL_INBOX_TTL

        for em in emails:
            try:
                message_id = em["message_id"]
                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, message_id))
                text = f"{em.get('subject', '')} {em.get('snippet', '')}"
                vector = self._make_vector(text)

                payload = {
                    "message_id": message_id,
                    "subject": em.get("subject", ""),
                    "sender": em.get("sender", ""),
                    "date": em.get("date", ""),
                    "snippet": em.get("snippet", ""),
                    "tags": em.get("tags", []),
                    "source": "imap",
                    "created_at": now_iso,
                    "expires_at": expires_at,
                }

                qdrant_client.upsert(
                    collection_name="email_inbox",
                    points=[PointStruct(id=point_id, vector=vector, payload=payload)],
                )
                count += 1
            except Exception:
                logger.exception("Failed to upsert email %s", em.get("message_id", "?"))

        return count

    def _upsert_events(self, qdrant_client: Any, events: list[dict]) -> int:
        if not events:
            return 0
        try:
            from qdrant_client.models import PointStruct  # type: ignore[import]
        except ImportError:
            logger.error("qdrant_client not available")
            return 0

        self._ensure_collection(qdrant_client, "calendar_events")
        count = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        expires_at = time.time() + CALENDAR_TTL

        for ev in events:
            try:
                event_uid = ev.get("event_uid", "")
                if not event_uid:
                    event_uid = str(uuid.uuid4())
                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, event_uid))
                text = f"{ev.get('title', '')} {ev.get('description', '')}"
                vector = self._make_vector(text)

                payload = {
                    "event_uid": event_uid,
                    "title": ev.get("title", ""),
                    "start_dt": ev.get("start_dt", ""),
                    "end_dt": ev.get("end_dt", ""),
                    "location": ev.get("location", ""),
                    "description": ev.get("description", ""),
                    "source": ev.get("source", "ics"),
                    "created_at": now_iso,
                    "expires_at": expires_at,
                }

                qdrant_client.upsert(
                    collection_name="calendar_events",
                    points=[PointStruct(id=point_id, vector=vector, payload=payload)],
                )
                count += 1
            except Exception:
                logger.exception("Failed to upsert event %s", ev.get("event_uid", "?"))

        return count

    # ------------------------------------------------------------------
    # SQLite sync log
    # ------------------------------------------------------------------

    def _update_sync_log(self, account: str, last_synced: str,
                          items_synced: int, status: str) -> None:
        log_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sync-log-{account}"))
        try:
            db = sqlite3.connect(self._db_path)
            try:
                db.execute("""
                    INSERT OR REPLACE INTO email_sync_log
                        (id, account, last_synced, items_synced, status)
                    VALUES (?, ?, ?, ?, ?)
                """, (log_id, account, last_synced, items_synced, status))
                db.commit()
            finally:
                db.close()
        except Exception:
            logger.exception("Failed to update email_sync_log for account=%s", account)

    def get_sync_status(self) -> dict:
        """Return last sync status for all accounts from the DB."""
        result: dict = {}
        try:
            db = sqlite3.connect(self._db_path)
            try:
                rows = db.execute(
                    "SELECT account, last_synced, items_synced, status FROM email_sync_log"
                ).fetchall()
                for account, last_synced, items_synced, status in rows:
                    result[account] = {
                        "last_synced": last_synced,
                        "items_synced": items_synced,
                        "status": status,
                    }
            finally:
                db.close()
        except Exception:
            logger.exception("Failed to read email_sync_log")
        return result

    # ------------------------------------------------------------------
    # Formatting helpers (used by scheduler_executor)
    # ------------------------------------------------------------------

    @staticmethod
    def format_agenda(events: list[dict]) -> str:
        """Format events as 'HH:MM – Title (Location)' lines."""
        lines: list[str] = []
        for ev in events:
            start_str = ev.get("start_dt", "")
            title = ev.get("title", "")
            location = ev.get("location", "")
            try:
                dt = datetime.fromisoformat(start_str)
                time_label = dt.strftime("%H:%M")
            except Exception:
                time_label = start_str[:5] if len(start_str) >= 5 else start_str
            if location:
                line = f"{time_label} – {title} ({location})"
            else:
                line = f"{time_label} – {title}"
            lines.append(line)
        return "\n".join(lines) if lines else "Aucun événement aujourd'hui."
