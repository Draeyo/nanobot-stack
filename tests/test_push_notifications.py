"""tests/test_push_notifications.py — PWA and Push Notification tests."""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_client(env_overrides: dict | None = None) -> TestClient:
    """Import app fresh with given env overrides."""
    overrides = env_overrides or {}
    with patch.dict(os.environ, overrides, clear=False):
        import admin_ui as admin_ui_module
        importlib.reload(admin_ui_module)
        import app as app_module
        importlib.reload(app_module)
        return TestClient(app_module.app)


# ---------------------------------------------------------------------------
# Task 3: Manifest
# ---------------------------------------------------------------------------
class TestManifest:
    def test_manifest_served_correctly(self):
        """GET /static/manifest.json returns 200 with required PWA fields."""
        client = _make_client({"PWA_ENABLED": "true"})
        r = client.get("/static/manifest.json")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Nanobot"
        assert data["short_name"] == "Nanobot"
        assert data["display"] == "standalone"
        assert data["start_url"] == "/"
        assert len(data["icons"]) >= 2
        assert any(i["sizes"] == "192x192" for i in data["icons"])
        assert any(i["sizes"] == "512x512" for i in data["icons"])

    def test_manifest_content_type(self):
        """manifest.json is served with application/json content-type."""
        client = _make_client({"PWA_ENABLED": "true"})
        r = client.get("/static/manifest.json")
        assert r.status_code == 200
        assert "json" in r.headers.get("content-type", "")

    def test_sw_registration_in_html(self):
        """GET /admin HTML contains manifest link and SW registration when PWA_ENABLED=true."""
        client = _make_client({"PWA_ENABLED": "true"})
        r = client.get("/admin")
        assert r.status_code == 200
        html = r.text
        assert '<link rel="manifest" href="/static/manifest.json">' in html
        assert "navigator.serviceWorker.register('/static/sw.js'" in html

    def test_sw_absent_when_pwa_disabled(self):
        """When PWA_ENABLED=false, HTML has no manifest link or SW registration."""
        env = {"PWA_ENABLED": "false"}
        with patch.dict(os.environ, env, clear=False):
            import admin_ui as admin_ui_module
            importlib.reload(admin_ui_module)
            import app as app_module
            importlib.reload(app_module)
            client = TestClient(app_module.app)
            r = client.get("/admin")
        assert r.status_code == 200
        html = r.text
        assert '<link rel="manifest"' not in html
        assert "serviceWorker.register" not in html


# ---------------------------------------------------------------------------
# Task 4: Service Worker
# ---------------------------------------------------------------------------
class TestServiceWorker:
    def test_sw_js_served(self):
        """GET /static/sw.js returns 200."""
        client = _make_client({"PWA_ENABLED": "true"})
        r = client.get("/static/sw.js")
        assert r.status_code == 200
        assert "javascript" in r.headers.get("content-type", "") or \
               "text/plain" in r.headers.get("content-type", "")

    def test_sw_contains_cache_name(self):
        """sw.js defines a CACHE_NAME constant."""
        client = _make_client({"PWA_ENABLED": "true"})
        r = client.get("/static/sw.js")
        assert "nanobot-v1" in r.text

    def test_sw_registration_in_html(self):
        """admin HTML contains SW registration script when PWA_ENABLED=true."""
        client = _make_client({"PWA_ENABLED": "true"})
        r = client.get("/admin")
        assert r.status_code == 200
        assert "navigator.serviceWorker.register('/static/sw.js'" in r.text


# ---------------------------------------------------------------------------
# Task 5: PushNotificationManager core
# ---------------------------------------------------------------------------
class TestPushNotificationManager:
    def _db_path(self, tmp_path):
        return tmp_path / "scheduler.db"

    def _create_table(self, db_path):
        db = sqlite3.connect(str(db_path))
        db.execute("""
            CREATE TABLE push_subscriptions (
                id TEXT PRIMARY KEY, endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL, auth TEXT NOT NULL,
                created_at TEXT NOT NULL, last_used TEXT DEFAULT NULL
            )
        """)
        db.commit()
        db.close()

    def test_vapid_keys_auto_generated(self, tmp_path):
        """When PUSH_ENABLED=true and keys are empty, auto-generate a valid VAPID key pair."""
        db_path = self._db_path(tmp_path)
        self._create_table(db_path)
        env = {
            "PUSH_ENABLED": "true",
            "PUSH_VAPID_PUBLIC_KEY": "",
            "PUSH_VAPID_PRIVATE_KEY": "",
            "PUSH_VAPID_EMAIL": "test@nanobot.local",
            "RAG_STATE_DIR": str(tmp_path),
        }
        with patch.dict(os.environ, env):
            import push_notifications as pn_mod
            importlib.reload(pn_mod)
            mgr = pn_mod.PushNotificationManager()
            assert mgr.vapid_public_key
            assert len(mgr.vapid_public_key) > 10
            assert mgr.vapid_private_key
            assert len(mgr.vapid_private_key) > 10

    def test_vapid_public_key_from_env(self, tmp_path):
        """When keys are set in env, PushNotificationManager uses them directly."""
        db_path = self._db_path(tmp_path)
        self._create_table(db_path)
        from push_notifications import PushNotificationManager
        pub, priv = PushNotificationManager.generate_vapid_keys()
        env = {
            "PUSH_ENABLED": "true",
            "PUSH_VAPID_PUBLIC_KEY": pub,
            "PUSH_VAPID_PRIVATE_KEY": priv,
            "PUSH_VAPID_EMAIL": "test@nanobot.local",
            "RAG_STATE_DIR": str(tmp_path),
        }
        with patch.dict(os.environ, env):
            import push_notifications as pn_mod
            importlib.reload(pn_mod)
            mgr = pn_mod.PushNotificationManager()
            assert mgr.vapid_public_key == pub


# ---------------------------------------------------------------------------
# Task 6-9: Push API endpoints
# ---------------------------------------------------------------------------
class TestPushAPI:
    """Tests for /api/push/* endpoints."""

    def _client_with_push(self, tmp_path, extra_env: dict | None = None) -> TestClient:
        """Build a TestClient with PUSH_ENABLED=true and a temp DB."""
        db_path = tmp_path / "scheduler.db"
        db = sqlite3.connect(str(db_path))
        db.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id TEXT PRIMARY KEY, endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL, auth TEXT NOT NULL,
                created_at TEXT NOT NULL, last_used TEXT DEFAULT NULL
            )
        """)
        db.commit()
        db.close()

        from push_notifications import PushNotificationManager
        pub, priv = PushNotificationManager.generate_vapid_keys()

        env = {
            "PUSH_ENABLED": "true",
            "PUSH_VAPID_PUBLIC_KEY": pub,
            "PUSH_VAPID_PRIVATE_KEY": priv,
            "PUSH_VAPID_EMAIL": "test@nanobot.local",
            "RAG_STATE_DIR": str(tmp_path),
        }
        env.update(extra_env or {})
        with patch.dict(os.environ, env):
            import push_notifications
            importlib.reload(push_notifications)
            import push_api
            importlib.reload(push_api)
            import app as app_module
            importlib.reload(app_module)
            return TestClient(app_module.app)

    def test_vapid_public_key_endpoint(self, tmp_path):
        """GET /api/push/vapid-public-key returns 200 with non-empty key."""
        client = self._client_with_push(tmp_path)
        r = client.get("/api/push/vapid-public-key")
        assert r.status_code == 200
        data = r.json()
        assert "vapid_public_key" in data
        assert len(data["vapid_public_key"]) > 10

    def test_push_endpoints_disabled(self, tmp_path):
        """When PUSH_ENABLED=false, push endpoints return 503."""
        db_path = tmp_path / "scheduler.db"
        db = sqlite3.connect(str(db_path))
        db.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id TEXT PRIMARY KEY, endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL, auth TEXT NOT NULL,
                created_at TEXT NOT NULL, last_used TEXT DEFAULT NULL
            )
        """)
        db.commit()
        db.close()
        env = {
            "PUSH_ENABLED": "false",
            "RAG_STATE_DIR": str(tmp_path),
        }
        with patch.dict(os.environ, env):
            import push_api
            importlib.reload(push_api)
            import app as app_module
            importlib.reload(app_module)
            client = TestClient(app_module.app)
        r = client.get("/api/push/vapid-public-key")
        assert r.status_code == 503
        r = client.post("/api/push/subscribe", json={
            "endpoint": "https://x.example", "p256dh": "abc", "auth": "def"
        })
        assert r.status_code == 503
        r = client.post("/api/push/test")
        assert r.status_code == 503

    def test_subscribe_stores_in_sqlite(self, tmp_path):
        """POST /api/push/subscribe returns 201 and stores row in push_subscriptions."""
        client = self._client_with_push(tmp_path)
        payload = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/test-token",
            "p256dh": "BNT_test_p256dh_key_base64url",
            "auth": "test_auth_base64url",
        }
        r = client.post("/api/push/subscribe", json=payload)
        assert r.status_code == 201
        data = r.json()
        assert "id" in data
        # Verify DB row
        db = sqlite3.connect(str(tmp_path / "scheduler.db"))
        row = db.execute(
            "SELECT * FROM push_subscriptions WHERE endpoint=?",
            (payload["endpoint"],)
        ).fetchone()
        db.close()
        assert row is not None

    def test_subscribe_duplicate_endpoint(self, tmp_path):
        """Second POST with same endpoint returns 200 (upsert), no UNIQUE error."""
        client = self._client_with_push(tmp_path)
        payload = {
            "endpoint": "https://fcm.example/dup-endpoint",
            "p256dh": "key1",
            "auth": "auth1",
        }
        r1 = client.post("/api/push/subscribe", json=payload)
        assert r1.status_code == 201
        payload["p256dh"] = "key2_updated"
        r2 = client.post("/api/push/subscribe", json=payload)
        assert r2.status_code == 200  # update, not insert

    def test_unsubscribe_removes_row(self, tmp_path):
        """DELETE /api/push/unsubscribe removes the subscription row."""
        client = self._client_with_push(tmp_path)
        endpoint = "https://fcm.example/to-delete"
        # Subscribe first
        client.post("/api/push/subscribe", json={
            "endpoint": endpoint, "p256dh": "k", "auth": "a"
        })
        r = client.request("DELETE", "/api/push/unsubscribe",
                           json={"endpoint": endpoint})
        assert r.status_code == 200
        # Verify gone
        db = sqlite3.connect(str(tmp_path / "scheduler.db"))
        row = db.execute(
            "SELECT id FROM push_subscriptions WHERE endpoint=?", (endpoint,)
        ).fetchone()
        db.close()
        assert row is None

    def test_unsubscribe_unknown_endpoint(self, tmp_path):
        """DELETE /api/push/unsubscribe with unknown endpoint returns 404."""
        client = self._client_with_push(tmp_path)
        r = client.request("DELETE", "/api/push/unsubscribe",
                           json={"endpoint": "https://unknown.example/gone"})
        assert r.status_code == 404

    def test_push_test_endpoint(self, tmp_path):
        """POST /api/push/test calls send_to_all and returns sent count."""
        client = self._client_with_push(tmp_path)
        with patch("push_notifications.PushNotificationManager.send_to_all") as mock_sta:
            mock_sta.return_value = {"sent": 0, "failed": 0, "expired_cleaned": 0}
            r = client.post("/api/push/test")
        assert r.status_code == 200
        data = r.json()
        assert "sent" in data
        mock_sta.assert_called_once()
        call_kwargs = mock_sta.call_args
        assert call_kwargs is not None


# ---------------------------------------------------------------------------
# Task 10: BroadcastNotifier webpush channel
# ---------------------------------------------------------------------------
class TestBroadcastNotifierWebpush:
    @pytest.mark.asyncio
    async def test_broadcast_notifier_webpush_channel(self, tmp_path):
        """BroadcastNotifier.broadcast(channels=['webpush']) calls send_to_all once."""
        from push_notifications import PushNotificationManager
        pub, priv = PushNotificationManager.generate_vapid_keys()
        env = {
            "PUSH_ENABLED": "true",
            "PUSH_VAPID_PUBLIC_KEY": pub,
            "PUSH_VAPID_PRIVATE_KEY": priv,
            "PUSH_VAPID_EMAIL": "test@nanobot.local",
            "RAG_STATE_DIR": str(tmp_path),
        }
        with patch.dict(os.environ, env):
            import push_notifications
            importlib.reload(push_notifications)
            import broadcast_notifier
            importlib.reload(broadcast_notifier)
            from broadcast_notifier import BroadcastNotifier

            mock_channel_manager = MagicMock()
            notifier = BroadcastNotifier(mock_channel_manager)

            with patch.object(
                push_notifications.PushNotificationManager,
                "send_to_all",
                return_value={"sent": 1, "failed": 0, "expired_cleaned": 0},
            ) as mock_send:
                result = await notifier.broadcast(
                    channels=["webpush"], message="Test push message"
                )

        assert "webpush" in result
        assert result["webpush"] is True
        mock_send.assert_called_once_with(
            title="Nanobot",
            body="Test push message",
            url="/",
        )

    @pytest.mark.asyncio
    async def test_broadcast_notifier_webpush_disabled(self):
        """When PUSH_ENABLED=false, webpush channel returns False."""
        env = {"PUSH_ENABLED": "false"}
        with patch.dict(os.environ, env):
            import broadcast_notifier
            importlib.reload(broadcast_notifier)
            from broadcast_notifier import BroadcastNotifier

            notifier = BroadcastNotifier(MagicMock())
            result = await notifier.broadcast(
                channels=["webpush"], message="Test"
            )
        assert result.get("webpush") is False


# ---------------------------------------------------------------------------
# Task 13: send() and send_to_all() detailed tests
# ---------------------------------------------------------------------------
class TestPushSend:
    def _setup_db(self, tmp_path) -> sqlite3.Connection:
        db_path = tmp_path / "scheduler.db"
        db = sqlite3.connect(str(db_path))
        db.execute("""
            CREATE TABLE push_subscriptions (
                id TEXT PRIMARY KEY, endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL, auth TEXT NOT NULL,
                created_at TEXT NOT NULL, last_used TEXT DEFAULT NULL
            )
        """)
        db.commit()
        return db

    def _make_manager(self, tmp_path):
        from push_notifications import PushNotificationManager
        pub, priv = PushNotificationManager.generate_vapid_keys()
        env = {
            "PUSH_ENABLED": "true",
            "PUSH_VAPID_PUBLIC_KEY": pub,
            "PUSH_VAPID_PRIVATE_KEY": priv,
            "PUSH_VAPID_EMAIL": "test@nanobot.local",
            "RAG_STATE_DIR": str(tmp_path),
        }
        with patch.dict(os.environ, env):
            import push_notifications
            importlib.reload(push_notifications)
            return push_notifications.PushNotificationManager()

    def _insert_sub(self, db, sub_id, endpoint):
        from datetime import datetime, timezone
        db.execute(
            "INSERT INTO push_subscriptions (id, endpoint, p256dh, auth, created_at) VALUES (?,?,?,?,?)",
            (sub_id, endpoint, "p256dh_test", "auth_test", datetime.now(timezone.utc).isoformat())
        )
        db.commit()

    def test_send_push_success(self, tmp_path):
        """send() returns True and updates last_used when webpush returns 201."""
        db = self._setup_db(tmp_path)
        sub_id = "sub-001"
        self._insert_sub(db, sub_id, "https://fcm.example/success")
        db.close()
        mgr = self._make_manager(tmp_path)

        with patch("pywebpush.webpush") as mock_wp:
            mock_wp.return_value = MagicMock(status_code=201)
            result = mgr.send(sub_id, "Test", "Test body", "/")

        assert result is True
        # Verify last_used updated
        db2 = sqlite3.connect(str(tmp_path / "scheduler.db"))
        row = db2.execute("SELECT last_used FROM push_subscriptions WHERE id=?", (sub_id,)).fetchone()
        db2.close()
        assert row[0] is not None

    def test_send_push_410_cleanup(self, tmp_path):
        """send() returns False and deletes subscription on HTTP 410 Gone."""
        db = self._setup_db(tmp_path)
        sub_id = "sub-410"
        self._insert_sub(db, sub_id, "https://fcm.example/expired")
        db.close()
        mgr = self._make_manager(tmp_path)

        # Simulate WebPushException with 410
        from pywebpush import WebPushException
        mock_response = MagicMock()
        mock_response.status_code = 410
        exc = WebPushException("Subscription gone", response=mock_response)

        with patch("pywebpush.webpush", side_effect=exc):
            result = mgr.send(sub_id, "Test", "Test body", "/")

        assert result is False
        db2 = sqlite3.connect(str(tmp_path / "scheduler.db"))
        row = db2.execute("SELECT id FROM push_subscriptions WHERE id=?", (sub_id,)).fetchone()
        db2.close()
        assert row is None  # cleaned up

    def test_send_to_all_multiple_subscriptions(self, tmp_path):
        """send_to_all returns sent=3, failed=0, expired_cleaned=0 for 3 successes."""
        db = self._setup_db(tmp_path)
        for i in range(3):
            self._insert_sub(db, f"sub-{i}", f"https://fcm.example/{i}")
        db.close()
        mgr = self._make_manager(tmp_path)

        with patch("pywebpush.webpush") as mock_wp:
            mock_wp.return_value = MagicMock(status_code=201)
            result = mgr.send_to_all("Title", "Body", "/")

        assert result["sent"] == 3
        assert result["failed"] == 0
        assert result["expired_cleaned"] == 0

    def test_send_to_all_partial_failure(self, tmp_path):
        """send_to_all with 1 success + 1 HTTP 410 returns sent=1, expired_cleaned=1."""
        db = self._setup_db(tmp_path)
        self._insert_sub(db, "sub-ok", "https://fcm.example/ok")
        self._insert_sub(db, "sub-gone", "https://fcm.example/gone")
        db.close()
        mgr = self._make_manager(tmp_path)

        from pywebpush import WebPushException
        mock_410_response = MagicMock()
        mock_410_response.status_code = 410
        exc_410 = WebPushException("Gone", response=mock_410_response)

        call_count = [0]
        def side_effect(**kwargs):
            call_count[0] += 1
            if "gone" in kwargs.get("subscription_info", {}).get("endpoint", ""):
                raise exc_410
            return MagicMock(status_code=201)

        with patch("pywebpush.webpush", side_effect=side_effect):
            result = mgr.send_to_all("Title", "Body", "/")

        assert result["sent"] == 1
        assert result["expired_cleaned"] == 1
        assert result["failed"] == 0
