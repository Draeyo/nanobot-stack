"""PushNotificationManager — VAPID key management and Web Push delivery."""
from __future__ import annotations

import json
import logging
import os
import pathlib
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("rag-bridge.push_notifications")

STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))
PUSH_ENABLED = os.getenv("PUSH_ENABLED", "false").lower() == "true"


class PushNotificationManager:
    """Manages VAPID keys, push subscriptions, and Web Push delivery."""

    def __init__(self) -> None:
        self.push_enabled = PUSH_ENABLED
        pub = os.getenv("PUSH_VAPID_PUBLIC_KEY", "")
        priv = os.getenv("PUSH_VAPID_PRIVATE_KEY", "")
        self.vapid_email = os.getenv("PUSH_VAPID_EMAIL", "admin@nanobot.local")

        if self.push_enabled and (not pub or not priv):
            pub, priv = self.generate_vapid_keys()
            logger.warning(
                "VAPID keys auto-generated. Persist these in stack.env to avoid "
                "invalidating existing push subscriptions on restart:\n"
                "  PUSH_VAPID_PUBLIC_KEY=%s\n  PUSH_VAPID_PRIVATE_KEY=%s",
                pub,
                priv,
            )

        self.vapid_public_key: str = pub
        self.vapid_private_key: str = priv

    # ------------------------------------------------------------------
    # VAPID key management
    # ------------------------------------------------------------------

    @staticmethod
    def generate_vapid_keys() -> tuple[str, str]:
        """Generate a new VAPID key pair. Returns (public_key_b64url, private_key_b64url)."""
        import base64  # pylint: disable=import-outside-toplevel
        from cryptography.hazmat.primitives.serialization import (  # pylint: disable=import-outside-toplevel
            Encoding, PublicFormat,
        )
        from py_vapid import Vapid  # pylint: disable=import-outside-toplevel
        v = Vapid()
        v.generate_keys()
        # Extract raw key bytes and encode as base64url (no padding)
        priv_bytes = v.private_key.private_numbers().private_value.to_bytes(32, "big")
        pub_bytes = v.public_key.public_bytes(
            encoding=Encoding.X962,
            format=PublicFormat.UncompressedPoint,
        )
        pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
        priv_b64 = base64.urlsafe_b64encode(priv_bytes).rstrip(b"=").decode()
        return pub_b64, priv_b64

    def get_vapid_public_key(self) -> str:
        """Return the VAPID public key (base64url encoded)."""
        return self.vapid_public_key

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def _get_db(self) -> sqlite3.Connection:
        db_path = STATE_DIR / "scheduler.db"
        db = sqlite3.connect(str(db_path))
        db.row_factory = sqlite3.Row
        return db

    def subscribe(self, endpoint: str, p256dh: str, auth: str) -> str:
        """INSERT OR REPLACE subscription. Returns the subscription id."""
        now = datetime.now(timezone.utc).isoformat()
        sub_id = str(uuid.uuid4())
        db = self._get_db()
        try:
            # Check if endpoint already exists — if so, return existing id
            existing = db.execute(
                "SELECT id FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
            ).fetchone()
            if existing:
                sub_id = existing["id"]
                db.execute(
                    "UPDATE push_subscriptions SET p256dh=?, auth=? WHERE endpoint=?",
                    (p256dh, auth, endpoint),
                )
            else:
                db.execute(
                    "INSERT INTO push_subscriptions (id, endpoint, p256dh, auth, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (sub_id, endpoint, p256dh, auth, now),
                )
            db.commit()
            logger.info("Push subscription stored: id=%s", sub_id)
            return sub_id
        finally:
            db.close()

    def unsubscribe(self, endpoint: str) -> bool:
        """DELETE subscription by endpoint. Returns True if a row was deleted."""
        db = self._get_db()
        try:
            cursor = db.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
            )
            db.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info("Push subscription removed for endpoint: %s", endpoint[:40])
            return deleted
        finally:
            db.close()

    def get_all_subscriptions(self) -> list[dict[str, Any]]:
        """Return all active push subscriptions."""
        db = self._get_db()
        try:
            rows = db.execute("SELECT * FROM push_subscriptions").fetchall()
            return [dict(r) for r in rows]
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Push delivery
    # ------------------------------------------------------------------

    def send(self, subscription_id: str, title: str, body: str, url: str = "/") -> bool:
        """Send a push notification to a single subscription. Returns True on success."""
        db = self._get_db()
        try:
            row = db.execute(
                "SELECT * FROM push_subscriptions WHERE id = ?", (subscription_id,)
            ).fetchone()
            if not row:
                logger.warning("send(): subscription not found: %s", subscription_id)
                return False

            payload = json.dumps({
                "title": title,
                "body": body,
                "url": url,
                "icon": "/static/icons/icon-192.png",
            })
            subscription_info = {
                "endpoint": row["endpoint"],
                "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
            }
            vapid_claims = {"sub": f"mailto:{self.vapid_email}"}

            try:
                from pywebpush import webpush  # pylint: disable=import-outside-toplevel
                webpush(
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=self.vapid_private_key,
                    vapid_claims=vapid_claims,
                )
                now = datetime.now(timezone.utc).isoformat()
                db.execute(
                    "UPDATE push_subscriptions SET last_used=? WHERE id=?",
                    (now, subscription_id),
                )
                db.commit()
                return True
            except Exception as exc:
                # Check for HTTP 410 Gone — subscription expired
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code == 410:
                    logger.info(
                        "Push subscription expired (410), removing: %s", subscription_id
                    )
                    db.execute(
                        "DELETE FROM push_subscriptions WHERE id=?", (subscription_id,)
                    )
                    db.commit()
                else:
                    logger.warning("Push delivery failed for %s: %s", subscription_id, exc)
                return False
        finally:
            db.close()

    def send_to_all(self, title: str, body: str, url: str = "/") -> dict[str, int]:
        """Send push notification to all subscriptions.

        Returns {"sent": N, "failed": M, "expired_cleaned": K}.
        """
        subscriptions = self.get_all_subscriptions()
        sent = 0
        failed = 0
        expired_cleaned = 0

        for sub in subscriptions:
            result = self.send(sub["id"], title, body, url)
            if result:
                sent += 1
            else:
                # Distinguish expired (row deleted) from other failures
                db = self._get_db()
                try:
                    still_exists = db.execute(
                        "SELECT id FROM push_subscriptions WHERE id=?", (sub["id"],)
                    ).fetchone()
                finally:
                    db.close()
                if not still_exists:
                    expired_cleaned += 1
                else:
                    failed += 1

        logger.info(
            "send_to_all: sent=%d failed=%d expired_cleaned=%d", sent, failed, expired_cleaned
        )
        return {"sent": sent, "failed": failed, "expired_cleaned": expired_cleaned}

    def _cleanup_expired(self, expired_ids: list[str]) -> int:
        """Delete a list of expired subscription ids. Returns count deleted."""
        if not expired_ids:
            return 0
        db = self._get_db()
        try:
            placeholders = ",".join("?" * len(expired_ids))
            cursor = db.execute(
                f"DELETE FROM push_subscriptions WHERE id IN ({placeholders})", expired_ids
            )
            db.commit()
            return cursor.rowcount
        finally:
            db.close()
