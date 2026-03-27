# Sub-projet I — Progressive Web App Mobile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make nanobot-stack installable as a PWA with offline support, mobile chat UI, and Web Push notifications

**Architecture:** PWA manifest + Service Worker served as static files. PushNotificationManager handles VAPID key generation and pywebpush delivery. Mobile chat view is a responsive Alpine.js component activated at <768px. New BroadcastNotifier channel 'webpush'. SQLite table push_subscriptions stores browser endpoints.

**Tech Stack:** pywebpush>=2.0, Alpine.js (existing), Service Worker API, FastAPI static files (existing)

---

## Context

Key file locations and patterns observed:

- **HTML assembly:** `src/bridge/admin_ui.py` builds the full SPA in `build_admin_html()` (line 1525). The `<head>` is at line 1528–1533. The `</body>` closing is at line 1555. PWA tags go into the f-string at the `<head>` block; the SW registration script and `#mobile-chat-view` div go just before `</body></html>`.
- **Static files:** `src/bridge/static/` does **not yet exist** — must be created, then mounted in `app.py`.
- **Migration pattern:** `migrations/015_backup_log.py` — `VERSION` int constant, `check()` queries `sqlite_master`, `migrate()` runs DDL with `PRAGMA journal_mode=WAL`, commits, logs. DB path is `STATE_DIR / "scheduler.db"`.
- **Next migration number:** Highest existing is `015`. No `016` file exists yet. Use **`019_push_subscriptions.py`** (the spec draft referenced 017 before 016 was planned in this project).
- **BroadcastNotifier:** `src/bridge/broadcast_notifier.py` — `VALID_CHANNELS` frozenset, `broadcast(channels, message)` fans out via `asyncio.gather`. The new `webpush` channel needs a `_deliver_webpush()` async method and `webpush` added to `VALID_CHANNELS`.
- **app.py pattern:** Router imports at top, `include_router` calls in the app setup. StaticFiles mount goes via `app.mount("/static", StaticFiles(...), name="static")`.
- **Dep to add:** `pywebpush>=2.0` to `src/bridge/requirements.txt`.

---

## Task 1 — Migration: `push_subscriptions` table

**File:** `migrations/019_push_subscriptions.py`

### Test first

No automated pytest for migrations (consistent with existing migration tests pattern in the project). Manual verification step is included. Skip to implementation.

### Implementation

- [ ] Create `migrations/019_push_subscriptions.py`:

```python
"""019_push_subscriptions — push_subscriptions table for Web Push VAPID."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 19

logger = logging.getLogger("migration.v16")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def check(_ctx: dict) -> bool:
    db_path = STATE_DIR / "scheduler.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='push_subscriptions'"
        ).fetchall()
        return len(tables) > 0
    finally:
        db.close()


def migrate(_ctx: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = STATE_DIR / "scheduler.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id          TEXT PRIMARY KEY,
                endpoint    TEXT NOT NULL UNIQUE,
                p256dh      TEXT NOT NULL,
                auth        TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                last_used   TEXT DEFAULT NULL
            );
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_push_subscriptions_endpoint
            ON push_subscriptions (endpoint);
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_push_subscriptions_created_at
            ON push_subscriptions (created_at);
        """)
        db.commit()
        logger.info("Migration 016: push_subscriptions table created at %s", db_path)
    finally:
        db.close()
```

### Run & verify

- [ ] `python migrations/run_migrations.py` — confirm "Migration 016" log line appears
- [ ] `sqlite3 $RAG_STATE_DIR/scheduler.db ".tables"` — confirm `push_subscriptions` listed
- [ ] `sqlite3 $RAG_STATE_DIR/scheduler.db ".schema push_subscriptions"` — confirm columns

### Commit

```
git add migrations/019_push_subscriptions.py
git commit -m "feat(migration): add push_subscriptions table (migration 016)"
```

---

## Task 2 — Static files directory setup

**Files:** `src/bridge/static/` directory structure + FastAPI mount in `app.py`

### Context

`src/bridge/static/` does not exist yet. FastAPI's `StaticFiles` mount must be added to `app.py`. The import `from fastapi.staticfiles import StaticFiles` and `app.mount(...)` follow the standard FastAPI pattern. The static directory must exist before the app starts or `StaticFiles` raises a startup error.

### Implementation

- [ ] Create the static directory and placeholder to track it in git:

```bash
mkdir -p src/bridge/static/icons
touch src/bridge/static/.gitkeep
touch src/bridge/static/icons/.gitkeep
```

- [ ] In `src/bridge/app.py`, add the StaticFiles import at the top with other FastAPI imports:

```python
from fastapi.staticfiles import StaticFiles
```

- [ ] In `src/bridge/app.py`, add the mount after `app = FastAPI(...)` is defined (find the app instantiation block and add immediately after):

```python
# Static files for PWA assets (manifest, sw.js, mobile.css, icons)
_static_dir = pathlib.Path(__file__).parent / "static"
_static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
```

### Test

- [ ] Start the app: `uvicorn app:app --port 8765`
- [ ] `curl -I http://localhost:8765/static/` — expect 200 or 404 (not 500)

### Commit

```
git add src/bridge/static/.gitkeep src/bridge/static/icons/.gitkeep src/bridge/app.py
git commit -m "feat(pwa): create static/ directory and mount StaticFiles in app.py"
```

---

## Task 3 — PWA Manifest

**Files:** `src/bridge/static/manifest.json`, modification to `src/bridge/admin_ui.py`

### Test first

- [ ] Write the test in `tests/test_push_notifications.py` — add this test class first (file will be extended in later tasks):

```python
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
        """manifest.json is served with application/manifest+json content-type."""
        client = _make_client({"PWA_ENABLED": "true"})
        r = client.get("/static/manifest.json")
        assert r.status_code == 200
        # FastAPI StaticFiles sets content-type from file extension;
        # accept application/json as fallback since StaticFiles uses mimetypes
        assert "json" in r.headers.get("content-type", "")
```

- [ ] Run (expect failure — file not yet created):

```bash
cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestManifest -v
```

### Implementation

- [ ] Create `src/bridge/static/manifest.json`:

```json
{
  "name": "Nanobot",
  "short_name": "Nanobot",
  "description": "Assistant IA personnel self-hosted",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "orientation": "portrait-primary",
  "theme_color": "#1a1a2e",
  "background_color": "#0f0f1a",
  "lang": "fr-FR",
  "icons": [
    {
      "src": "/static/icons/icon-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any maskable"
    },
    {
      "src": "/static/icons/icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any maskable"
    }
  ],
  "categories": ["productivity", "utilities"],
  "prefer_related_applications": false
}
```

- [ ] In `src/bridge/admin_ui.py`, locate the `build_admin_html()` function. The `<head>` block currently ends with:

```python
<style>{ADMIN_CSS}</style>
```

Add a new constant `PWA_ENABLED = os.getenv("PWA_ENABLED", "true").lower() == "true"` near the top of the file (after `ADMIN_ENABLED`). Then modify `build_admin_html()` to inject PWA head tags conditionally. The f-string head section becomes:

```python
def build_admin_html() -> str:
    """Assemble the full admin SPA HTML."""
    pwa_head = ""
    if PWA_ENABLED:
        pwa_head = """
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#1a1a2e">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Nanobot">
<link rel="apple-touch-icon" href="/static/icons/icon-192.png">"""
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>nanobot admin</title>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
{pwa_head}
<style>{ADMIN_CSS}</style>
</head>
...
```

**Note:** edit only the `<head>` interpolation part. The `{ADMIN_CSS}` and existing `<script>` tags must remain. The `...` above represents all existing sections unchanged.

### Test for `<link rel="manifest">` in HTML

- [ ] Add to `TestManifest` class in `tests/test_push_notifications.py`:

```python
    def test_sw_registration_in_html(self):
        """GET / HTML contains manifest link and SW registration when PWA_ENABLED=true."""
        client = _make_client({"PWA_ENABLED": "true"})
        r = client.get("/admin")
        assert r.status_code == 200
        html = r.text
        assert '<link rel="manifest" href="/static/manifest.json">' in html
        assert "navigator.serviceWorker.register('/static/sw.js'" in html

    def test_sw_absent_when_pwa_disabled(self):
        """When PWA_ENABLED=false, HTML has no manifest link or SW registration."""
        client = _make_client({"PWA_ENABLED": "false"})
        r = client.get("/admin")
        assert r.status_code == 200
        html = r.text
        assert '<link rel="manifest"' not in html
        assert "serviceWorker.register" not in html
```

- [ ] Run tests (manifest test passes; SW tests fail until Task 4):

```bash
cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestManifest::test_manifest_served_correctly -v
```

### Commit

```
git add src/bridge/static/manifest.json src/bridge/admin_ui.py tests/test_push_notifications.py
git commit -m "feat(pwa): add PWA manifest and <link rel=manifest> in admin_ui head"
```

---

## Task 4 — Service Worker (`sw.js`)

**Files:** `src/bridge/static/sw.js`, modification to `src/bridge/admin_ui.py`

### Test first

- [ ] Add to `tests/test_push_notifications.py`:

```python
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
```

- [ ] Run (expect failure):

```bash
cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestServiceWorker -v
```

### Implementation

- [ ] Create `src/bridge/static/sw.js`:

```javascript
// Service Worker — nanobot-stack PWA
// Cache strategy: cache-first for /static/*, network-first for /api/*, offline fallback for navigation

const CACHE_NAME = "nanobot-v1";

const PRECACHE_URLS = [
  "/",
  "/static/manifest.json",
  "/static/mobile.css",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nanobot — Hors ligne</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    background:#0f0f1a;color:#e0e0e0;display:flex;flex-direction:column;
    align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
  h1{font-size:1.5rem;color:#3b82f6;margin-bottom:1rem}
  p{color:#888;margin-bottom:2rem}
  button{background:#3b82f6;color:#fff;border:none;padding:10px 24px;
    border-radius:6px;font-size:1rem;cursor:pointer}
  button:hover{opacity:.85}
</style></head>
<body>
  <h1>Hors ligne</h1>
  <p>Reconnectez-vous au serveur Nanobot.</p>
  <button onclick="location.reload()">Réessayer</button>
</body></html>`;

const OFFLINE_JSON = JSON.stringify({ error: "offline" });

// ---------------------------------------------------------------------------
// install — precache critical assets
// ---------------------------------------------------------------------------
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      // Cache what we can; ignore failures for assets not yet generated (icons)
      return Promise.allSettled(
        PRECACHE_URLS.map((url) =>
          cache.add(url).catch(() => {
            console.warn("[SW] Precache miss:", url);
          })
        )
      );
    }).then(() => self.skipWaiting())
  );
});

// ---------------------------------------------------------------------------
// activate — clean old caches, claim clients
// ---------------------------------------------------------------------------
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

// ---------------------------------------------------------------------------
// fetch — routing strategy
// ---------------------------------------------------------------------------
self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // /api/* — network-first, offline JSON fallback
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(request).catch(() =>
        new Response(OFFLINE_JSON, {
          status: 503,
          headers: { "Content-Type": "application/json" },
        })
      )
    );
    return;
  }

  // /static/* — cache-first
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // Navigation requests — network-first, offline HTML fallback
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match("/").then((cached) =>
          cached ||
          new Response(OFFLINE_HTML, {
            status: 200,
            headers: { "Content-Type": "text/html; charset=utf-8" },
          })
        )
      )
    );
    return;
  }
});

// ---------------------------------------------------------------------------
// push — show notification
// ---------------------------------------------------------------------------
self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  const title = data.title || "Nanobot";
  const options = {
    body: data.body || "",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    data: { url: data.url || "/" },
    vibrate: [200, 100, 200],
    requireInteraction: false,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// ---------------------------------------------------------------------------
// notificationclick — open or focus window
// ---------------------------------------------------------------------------
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = event.notification.data.url || "/";
  event.waitUntil(
    clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clientList) => {
        for (const client of clientList) {
          if (client.url === targetUrl && "focus" in client) return client.focus();
        }
        if (clients.openWindow) return clients.openWindow(targetUrl);
      })
  );
});
```

- [ ] In `src/bridge/admin_ui.py`, add the SW registration script. In `build_admin_html()`, add a `pwa_script` variable alongside `pwa_head`:

```python
    pwa_script = ""
    if PWA_ENABLED:
        pwa_script = """
<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', async () => {
    try {
      const reg = await navigator.serviceWorker.register('/static/sw.js', { scope: '/' });
      console.log('[Nanobot PWA] Service Worker enregistré', reg.scope);
      window._swRegistration = reg;
    } catch (err) {
      console.warn('[Nanobot PWA] Enregistrement SW échoué', err);
    }
  });
}
</script>"""
```

Then in the return f-string, insert `{pwa_script}` just before `</body></html>`:

```python
    ...
    <script>{ADMIN_JS}</script>
    {pwa_script}
    </body></html>"""
```

### Run tests

- [ ] `cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestServiceWorker -v` — all pass
- [ ] `cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestManifest -v` — all pass

### Commit

```
git add src/bridge/static/sw.js src/bridge/admin_ui.py tests/test_push_notifications.py
git commit -m "feat(pwa): add Service Worker with cache strategy and SW registration in HTML"
```

---

## Task 5 — `PushNotificationManager` class

**File:** `src/bridge/push_notifications.py`

### Test first

- [ ] Add to `tests/test_push_notifications.py`:

```python
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
            from push_notifications import PushNotificationManager
            importlib.reload(importlib.import_module("push_notifications"))
            from push_notifications import PushNotificationManager as PNM
            mgr = PNM()
            assert mgr.vapid_public_key
            assert len(mgr.vapid_public_key) > 10
            assert mgr.vapid_private_key
            assert len(mgr.vapid_private_key) > 10

    def test_vapid_public_key_from_env(self, tmp_path):
        """When keys are set in env, PushNotificationManager uses them directly."""
        db_path = self._db_path(tmp_path)
        self._create_table(db_path)
        # Use a real generated key pair for realistic test
        from py_vapid import Vapid
        v = Vapid()
        v.generate_keys()
        pub = v.public_key_urlsafe.decode()
        priv = v.private_key_urlsafe.decode()
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
```

- [ ] Run (expect failure — module doesn't exist):

```bash
cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestPushNotificationManager -v
```

### Implementation

- [ ] Add `pywebpush>=2.0` to `src/bridge/requirements.txt`:

```
pywebpush>=2.0
```

- [ ] `pip install pywebpush>=2.0`

- [ ] Create `src/bridge/push_notifications.py`:

```python
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
        from py_vapid import Vapid
        v = Vapid()
        v.generate_keys()
        return v.public_key_urlsafe.decode(), v.private_key_urlsafe.decode()

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
                from pywebpush import webpush, WebPushException
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
```

### Run tests

- [ ] `cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestPushNotificationManager -v`

### Commit

```
git add src/bridge/push_notifications.py src/bridge/requirements.txt tests/test_push_notifications.py
git commit -m "feat(push): add PushNotificationManager with VAPID key management and send_to_all"
```

---

## Task 6 — `GET /api/push/vapid-public-key`

**File:** `src/bridge/push_api.py` (create)

### Test first

- [ ] Add to `tests/test_push_notifications.py`:

```python
# ---------------------------------------------------------------------------
# Task 6–9: Push API endpoints
# ---------------------------------------------------------------------------
class TestPushAPI:
    """Tests for /api/push/* endpoints."""

    def _client_with_push(self, tmp_path, extra_env: dict | None = None) -> TestClient:
        """Build a TestClient with PUSH_ENABLED=true and a temp DB."""
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
        db.close()

        from py_vapid import Vapid
        v = Vapid()
        v.generate_keys()
        pub = v.public_key_urlsafe.decode()
        priv = v.private_key_urlsafe.decode()

        env = {
            "PUSH_ENABLED": "true",
            "PUSH_VAPID_PUBLIC_KEY": pub,
            "PUSH_VAPID_PRIVATE_KEY": priv,
            "PUSH_VAPID_EMAIL": "test@nanobot.local",
            "RAG_STATE_DIR": str(tmp_path),
        }
        env.update(extra_env or {})
        with patch.dict(os.environ, env):
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
            CREATE TABLE push_subscriptions (
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
```

- [ ] Run (expect failure):

```bash
cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestPushAPI::test_vapid_public_key_endpoint -v
```

### Implementation

- [ ] Create `src/bridge/push_api.py`:

```python
"""push_api — REST endpoints for Web Push subscription management."""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.push_api")

PUSH_ENABLED = os.getenv("PUSH_ENABLED", "false").lower() == "true"

router = APIRouter(prefix="/api/push", tags=["push"])

# Lazy singleton — initialized in app.py startup
_push_manager = None


def init_push_api(push_manager) -> None:
    """Called from app.py startup to inject the PushNotificationManager instance."""
    global _push_manager
    _push_manager = push_manager


def _require_push() -> "PushNotificationManager":  # noqa: F821
    if not PUSH_ENABLED or _push_manager is None:
        raise HTTPException(status_code=503, detail="Push notifications not enabled")
    return _push_manager


# ---------------------------------------------------------------------------
# Task 6: GET /api/push/vapid-public-key
# ---------------------------------------------------------------------------
@router.get("/vapid-public-key")
async def get_vapid_public_key():
    """Return the VAPID public key for client-side push subscription."""
    mgr = _require_push()
    return {"vapid_public_key": mgr.get_vapid_public_key()}
```

### Run test

- [ ] `cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestPushAPI::test_vapid_public_key_endpoint -v`

### Commit

```
git add src/bridge/push_api.py tests/test_push_notifications.py
git commit -m "feat(push): add GET /api/push/vapid-public-key endpoint"
```

---

## Task 7 — `POST /api/push/subscribe`

**File:** `src/bridge/push_api.py` (extend)

### Test first

- [ ] Add to `TestPushAPI` in `tests/test_push_notifications.py`:

```python
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
```

- [ ] Run (expect failure):

```bash
cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestPushAPI::test_subscribe_stores_in_sqlite ../../tests/test_push_notifications.py::TestPushAPI::test_subscribe_duplicate_endpoint -v
```

### Implementation

- [ ] Extend `src/bridge/push_api.py` — add the Pydantic model and subscribe endpoint:

```python
# After the existing router and _require_push() definition:

class SubscribeRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


# ---------------------------------------------------------------------------
# Task 7: POST /api/push/subscribe
# ---------------------------------------------------------------------------
@router.post("/subscribe", status_code=201)
async def subscribe_push(body: SubscribeRequest):
    """Store a browser push subscription. Upserts on duplicate endpoint."""
    mgr = _require_push()
    # Check if endpoint already exists — if so, treat as update (200)
    existing_subs = mgr.get_all_subscriptions()
    is_update = any(s["endpoint"] == body.endpoint for s in existing_subs)

    sub_id = mgr.subscribe(body.endpoint, body.p256dh, body.auth)

    if is_update:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=200,
            content={"id": sub_id, "message": "Souscription mise à jour"},
        )
    return {"id": sub_id, "message": "Souscription enregistrée"}
```

### Run tests

- [ ] `cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestPushAPI::test_subscribe_stores_in_sqlite ../../tests/test_push_notifications.py::TestPushAPI::test_subscribe_duplicate_endpoint -v`

### Commit

```
git add src/bridge/push_api.py tests/test_push_notifications.py
git commit -m "feat(push): add POST /api/push/subscribe with upsert logic"
```

---

## Task 8 — `DELETE /api/push/unsubscribe`

**File:** `src/bridge/push_api.py` (extend)

### Test first

- [ ] Add to `TestPushAPI`:

```python
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
```

- [ ] Run (expect failure):

```bash
cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestPushAPI::test_unsubscribe_removes_row ../../tests/test_push_notifications.py::TestPushAPI::test_unsubscribe_unknown_endpoint -v
```

### Implementation

- [ ] Extend `src/bridge/push_api.py`:

```python
class UnsubscribeRequest(BaseModel):
    endpoint: str


# ---------------------------------------------------------------------------
# Task 8: DELETE /api/push/unsubscribe
# ---------------------------------------------------------------------------
@router.delete("/unsubscribe")
async def unsubscribe_push(body: UnsubscribeRequest):
    """Remove a browser push subscription by endpoint."""
    mgr = _require_push()
    removed = mgr.unsubscribe(body.endpoint)
    if not removed:
        raise HTTPException(status_code=404, detail="Souscription introuvable")
    return {"message": "Souscription supprimée"}
```

### Run tests

- [ ] `cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestPushAPI::test_unsubscribe_removes_row ../../tests/test_push_notifications.py::TestPushAPI::test_unsubscribe_unknown_endpoint -v`

### Commit

```
git add src/bridge/push_api.py tests/test_push_notifications.py
git commit -m "feat(push): add DELETE /api/push/unsubscribe endpoint"
```

---

## Task 9 — `POST /api/push/test`

**File:** `src/bridge/push_api.py` (extend)

### Test first

- [ ] Add to `TestPushAPI`:

```python
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
```

- [ ] Run (expect failure):

```bash
cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestPushAPI::test_push_test_endpoint -v
```

### Implementation

- [ ] Extend `src/bridge/push_api.py`:

```python
# ---------------------------------------------------------------------------
# Task 9: POST /api/push/test
# ---------------------------------------------------------------------------
@router.post("/test")
async def test_push():
    """Send a test push notification to all subscriptions."""
    mgr = _require_push()
    result = mgr.send_to_all(
        title="Nanobot — Test",
        body="Notification de test depuis le serveur Nanobot.",
        url="/",
    )
    return result
```

### Run tests

- [ ] `cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestPushAPI -v` — all pass

### Commit

```
git add src/bridge/push_api.py tests/test_push_notifications.py
git commit -m "feat(push): add POST /api/push/test endpoint"
```

---

## Task 10 — BroadcastNotifier `webpush` channel

**File:** `src/bridge/broadcast_notifier.py`

### Test first

- [ ] Add to `tests/test_push_notifications.py`:

```python
# ---------------------------------------------------------------------------
# Task 10: BroadcastNotifier webpush channel
# ---------------------------------------------------------------------------
class TestBroadcastNotifierWebpush:
    @pytest.mark.asyncio
    async def test_broadcast_notifier_webpush_channel(self, tmp_path):
        """BroadcastNotifier.broadcast(channels=['webpush']) calls send_to_all once."""
        from py_vapid import Vapid
        v = Vapid()
        v.generate_keys()
        pub = v.public_key_urlsafe.decode()
        priv = v.private_key_urlsafe.decode()
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
```

- [ ] Run (expect failure):

```bash
cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestBroadcastNotifierWebpush -v
```

### Implementation

- [ ] Edit `src/bridge/broadcast_notifier.py`:

**Change 1** — update `VALID_CHANNELS`:

```python
# Before:
VALID_CHANNELS = frozenset({"ntfy", "telegram", "discord", "whatsapp"})

# After:
VALID_CHANNELS = frozenset({"ntfy", "telegram", "discord", "whatsapp", "webpush"})
```

**Change 2** — add `_deliver_webpush()` and route it in `_deliver()`:

In `_deliver()`, add the webpush branch:

```python
    async def _deliver(self, channel: str, message: str) -> bool:
        if channel == "ntfy":
            return await self._deliver_ntfy(message)
        if channel == "webpush":
            return await self._deliver_webpush(message)
        return await self._deliver_adapter(channel, message)
```

Add `_deliver_webpush()` method after `_deliver_ntfy()`:

```python
    async def _deliver_webpush(self, message: str) -> bool:
        push_enabled = os.getenv("PUSH_ENABLED", "false").lower() == "true"
        if not push_enabled:
            logger.debug("webpush channel skipped: PUSH_ENABLED=false")
            return False
        try:
            from push_notifications import PushNotificationManager
            mgr = PushNotificationManager()
            result = mgr.send_to_all(title="Nanobot", body=message, url="/")
            return result.get("sent", 0) > 0 or result.get("failed", 0) == 0
        except Exception:
            logger.exception("webpush delivery failed")
            return False
```

Also add `import os` at the top of `broadcast_notifier.py` if not already present.

### Run tests

- [ ] `cd src/bridge && python -m pytest ../../tests/test_push_notifications.py::TestBroadcastNotifierWebpush -v`

### Commit

```
git add src/bridge/broadcast_notifier.py tests/test_push_notifications.py
git commit -m "feat(push): add webpush channel to BroadcastNotifier"
```

---

## Task 11 — Mobile Chat UI (visual, no automated test)

**File:** `src/bridge/admin_ui.py`

This task has no automated test — it is a purely visual change. Manual verification on a mobile browser is the acceptance criterion.

### Implementation

- [ ] Add `MOBILE_CSS` constant in `admin_ui.py` (after `ADMIN_CSS`):

```python
MOBILE_CSS = """
/* ===== Mobile PWA View (< 768px) ===== */
@media (max-width: 767px) {
  .topnav { display: none !important; }
  main { display: none !important; }
  #mobile-chat-view { display: flex !important; }
}
@media (min-width: 768px) {
  #mobile-chat-view { display: none !important; }
}

/* Mobile chat layout */
#mobile-chat-view {
  display: none;
  flex-direction: column;
  height: 100dvh;
  height: 100vh;
  background: var(--bg);
  position: fixed;
  inset: 0;
  z-index: 200;
}

/* Mobile header */
.mobile-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: var(--card);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.mobile-header .brand { font-weight: 700; font-size: 16px; color: var(--cyan); }
.mobile-header-actions { display: flex; gap: 8px; }

/* Message list */
.mobile-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  -webkit-overflow-scrolling: touch;
}

/* Message bubbles */
.message-bubble {
  max-width: 80%;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.5;
  word-wrap: break-word;
}
.message-bubble.user {
  align-self: flex-end;
  background: #1a1a2e;
  border: 1px solid var(--blue);
  border-radius: 18px 18px 4px 18px;
  color: var(--text);
  margin-left: auto;
}
.message-bubble.assistant {
  align-self: flex-start;
  background: #16213e;
  border: 1px solid var(--border);
  border-radius: 18px 18px 18px 4px;
  color: var(--text);
}

/* Typing indicator */
.typing-indicator { display: flex; gap: 4px; align-items: center; padding: 4px 0; }
.typing-indicator span {
  width: 6px; height: 6px; background: var(--muted);
  border-radius: 50%; animation: typing-bounce 1.2s infinite;
}
.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }
@keyframes typing-bounce {
  0%, 60%, 100% { transform: translateY(0); }
  30% { transform: translateY(-6px); }
}

/* Quick actions */
.mobile-quick-actions {
  display: flex;
  gap: 8px;
  padding: 8px 16px;
  overflow-x: auto;
  flex-shrink: 0;
  border-top: 1px solid var(--border);
}
.mobile-quick-actions button {
  white-space: nowrap;
  flex-shrink: 0;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 16px;
  color: var(--text);
  padding: 6px 14px;
  font-size: 12px;
  cursor: pointer;
}

/* Input bar */
.mobile-input-bar {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 10px 16px;
  background: var(--card);
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}
.mobile-input-bar textarea {
  flex: 1;
  min-height: 40px;
  max-height: 120px;
  resize: none;
  border-radius: 20px;
  padding: 10px 14px;
  font-size: 14px;
  overflow-y: auto;
}
.mobile-input-bar .btn-send {
  height: 40px;
  width: 40px;
  border-radius: 50%;
  background: var(--blue);
  border: none;
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.mobile-input-bar .btn-mic {
  height: 40px;
  width: 40px;
  border-radius: 50%;
  background: var(--border);
  border: none;
  color: var(--text);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.mobile-input-bar .btn-mic.recording {
  background: var(--red);
  animation: recording-pulse 1s infinite;
}
@keyframes recording-pulse {
  0%, 100% { opacity: 1; } 50% { opacity: 0.5; }
}

/* Install banner */
.install-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: var(--card);
  border-bottom: 1px solid var(--blue);
  font-size: 13px;
  flex-shrink: 0;
}
.install-banner span { flex: 1; }
.install-banner button {
  background: var(--blue);
  color: #fff;
  border: none;
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}
.install-banner .btn-dismiss {
  background: transparent;
  color: var(--muted);
  font-size: 16px;
}
"""
```

- [ ] Add `MOBILE_CHAT_HTML` constant (after `MOBILE_CSS`):

```python
MOBILE_CHAT_HTML = """
<div id="mobile-chat-view"
     x-data="mobileChatApp()"
     x-init="init()">

  <!-- Install banner (Chrome Android A2HS) -->
  <div x-show="showInstallBanner" class="install-banner">
    <span>Installer Nanobot sur l'écran d'accueil</span>
    <button @click="installApp()">Installer</button>
    <button class="btn-dismiss" @click="showInstallBanner = false">✕</button>
  </div>

  <!-- Header -->
  <div class="mobile-header">
    <span class="brand">Nanobot</span>
    <div class="mobile-header-actions">
      <button class="btn btn-muted btn-sm" @click="newConversation()">⊕ Nouveau</button>
      <template x-if="pushEnabled">
        <button class="btn btn-sm"
                :class="pushSubscribed ? 'btn-blue' : 'btn-muted'"
                @click="pushSubscribed ? unsubscribePush() : subscribePush()"
                :title="pushSubscribed ? 'Désactiver les notifications' : 'Activer les notifications'">
          🔔
        </button>
      </template>
    </div>
  </div>

  <!-- Messages -->
  <div class="mobile-messages" x-ref="messagesContainer">
    <template x-for="msg in messages" :key="msg.ts">
      <div class="message-bubble" :class="msg.role">
        <template x-if="msg.streaming && msg.content === ''">
          <div class="typing-indicator">
            <span></span><span></span><span></span>
          </div>
        </template>
        <template x-if="!(msg.streaming && msg.content === '')">
          <div x-html="renderMarkdown(msg.content)"></div>
        </template>
      </div>
    </template>
  </div>

  <!-- Quick actions -->
  <div class="mobile-quick-actions">
    <button @click="triggerBriefing()">📋 Briefing maintenant</button>
  </div>

  <!-- Input bar -->
  <div class="mobile-input-bar">
    <textarea x-model="inputText"
              placeholder="Message..."
              rows="1"
              @keydown.enter.prevent="if(!$event.shiftKey) sendMessage()"
              @input="autoResize($event.target)"></textarea>
    <template x-if="voiceEnabled">
      <button class="btn-mic" :class="{ recording: isRecording }"
              @click="startVoiceInput()"
              :title="isRecording ? 'Arrêter' : 'Microphone'">
        🎤
      </button>
    </template>
    <button class="btn-send"
            @click="sendMessage(); navigator.vibrate && navigator.vibrate(10)"
            :disabled="isStreaming || !inputText.trim()"
            title="Envoyer">
      ➤
    </button>
  </div>
</div>
"""
```

- [ ] Add `MOBILE_CHAT_JS` constant:

```python
MOBILE_CHAT_JS = """
function mobileChatApp() {
  return {
    messages: [],
    inputText: '',
    isStreaming: false,
    isRecording: false,
    showInstallBanner: false,
    deferredPrompt: null,
    pushEnabled: false,
    pushSubscribed: false,
    voiceEnabled: false,

    async init() {
      await this.loadHistory();
      this.checkPushAvailability();
      this.checkVoiceAvailability();
      window.addEventListener('beforeinstallprompt', (e) => {
        e.preventDefault();
        this.deferredPrompt = e;
        this.showInstallBanner = true;
      });
    },

    async loadHistory() {
      try {
        const r = await fetch('/api/chat/history?limit=20');
        if (r.ok) {
          const data = await r.json();
          this.messages = (data.messages || []).map(m => ({
            role: m.role, content: m.content,
            ts: m.ts || Date.now(), streaming: false
          }));
          this.$nextTick(() => this.scrollToBottom());
        }
      } catch(e) { console.warn('[Mobile] loadHistory failed', e); }
    },

    async checkPushAvailability() {
      try {
        const r = await fetch('/api/push/vapid-public-key');
        if (r.ok) {
          this.pushEnabled = true;
          await this.checkPushSubscription();
        }
      } catch(e) {}
    },

    async checkVoiceAvailability() {
      try {
        const r = await fetch('/api/voice/status');
        if (r.ok) { const d = await r.json(); this.voiceEnabled = d.enabled === true; }
      } catch(e) {}
    },

    async sendMessage() {
      const text = this.inputText.trim();
      if (!text || this.isStreaming) return;
      this.inputText = '';
      this.messages.push({ role: 'user', content: text, ts: Date.now(), streaming: false });
      const assistantMsg = { role: 'assistant', content: '', ts: Date.now(), streaming: true };
      this.messages.push(assistantMsg);
      this.isStreaming = true;
      this.$nextTick(() => this.scrollToBottom());
      try {
        const es = new EventSource('/api/chat/stream?message=' + encodeURIComponent(text));
        es.onmessage = (e) => {
          if (e.data === '[DONE]') { assistantMsg.streaming = false; es.close(); this.isStreaming = false; return; }
          try { const d = JSON.parse(e.data); assistantMsg.content += d.content || ''; } catch(_) {}
          this.$nextTick(() => this.scrollToBottom());
        };
        es.onerror = () => { es.close(); this.isStreaming = false; assistantMsg.streaming = false; };
      } catch(e) { this.isStreaming = false; assistantMsg.streaming = false; }
    },

    async newConversation() {
      try { await fetch('/api/chat/reset', { method: 'POST' }); } catch(e) {}
      this.messages = [];
    },

    async triggerBriefing() {
      try {
        await fetch('/api/scheduler/trigger-briefing', { method: 'POST' });
        this.messages.push({ role: 'assistant', content: 'Briefing déclenché.', ts: Date.now(), streaming: false });
      } catch(e) {}
    },

    async installApp() {
      if (!this.deferredPrompt) return;
      this.deferredPrompt.prompt();
      await this.deferredPrompt.userChoice;
      this.showInstallBanner = false;
      this.deferredPrompt = null;
    },

    async checkPushSubscription() {
      try {
        if (!window._swRegistration) return;
        const sub = await window._swRegistration.pushManager.getSubscription();
        this.pushSubscribed = sub !== null;
      } catch(e) {}
    },

    async subscribePush() {
      try {
        const r = await fetch('/api/push/vapid-public-key');
        const { vapid_public_key } = await r.json();
        const appKey = this._urlBase64ToUint8Array(vapid_public_key);
        const sub = await window._swRegistration.pushManager.subscribe({
          userVisibleOnly: true, applicationServerKey: appKey
        });
        const keys = sub.toJSON().keys;
        await fetch('/api/push/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endpoint: sub.endpoint, p256dh: keys.p256dh, auth: keys.auth })
        });
        this.pushSubscribed = true;
      } catch(e) { console.warn('[Mobile] subscribePush failed', e); }
    },

    async unsubscribePush() {
      try {
        const sub = await window._swRegistration.pushManager.getSubscription();
        if (sub) {
          await fetch('/api/push/unsubscribe', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ endpoint: sub.endpoint })
          });
          await sub.unsubscribe();
        }
        this.pushSubscribed = false;
      } catch(e) { console.warn('[Mobile] unsubscribePush failed', e); }
    },

    async startVoiceInput() {
      if (this.isRecording) return;
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const recorder = new MediaRecorder(stream);
        const chunks = [];
        this.isRecording = true;
        recorder.ondataavailable = e => chunks.push(e.data);
        recorder.onstop = async () => {
          this.isRecording = false;
          stream.getTracks().forEach(t => t.stop());
          const blob = new Blob(chunks, { type: 'audio/webm' });
          const fd = new FormData(); fd.append('audio', blob, 'voice.webm');
          try {
            const r = await fetch('/api/voice/chat', { method: 'POST', body: fd });
            if (r.ok) {
              const d = await r.json();
              if (d.transcription) this.messages.push({ role: 'user', content: d.transcription, ts: Date.now(), streaming: false });
              if (d.response) this.messages.push({ role: 'assistant', content: d.response, ts: Date.now(), streaming: false });
              this.$nextTick(() => this.scrollToBottom());
            }
          } catch(e) { console.warn('[Mobile] voice upload failed', e); }
        };
        recorder.start();
        setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); }, 30000);
        document.querySelector('.btn-mic').addEventListener('click', () => {
          if (recorder.state === 'recording') recorder.stop();
        }, { once: true });
      } catch(e) { this.isRecording = false; console.warn('[Mobile] getUserMedia failed', e); }
    },

    renderMarkdown(text) {
      if (typeof marked !== 'undefined') return marked.parse(text || '');
      return (text || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\\n/g,'<br>');
    },

    scrollToBottom() {
      const el = this.$refs.messagesContainer;
      if (el) el.scrollTop = el.scrollHeight;
    },

    autoResize(el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 120) + 'px';
    },

    _urlBase64ToUint8Array(base64String) {
      const padding = '='.repeat((4 - base64String.length % 4) % 4);
      const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
      const rawData = atob(base64);
      return new Uint8Array([...rawData].map(c => c.charCodeAt(0)));
    },
  };
}
"""
```

- [ ] In `build_admin_html()`, inject `MOBILE_CSS` into the `<style>` block and add `MOBILE_CHAT_HTML` + `MOBILE_CHAT_JS` to the body. The `<style>` tag becomes:

```python
<style>{ADMIN_CSS}{MOBILE_CSS}</style>
```

Before `</main>` (after `{SECTION_SCHEDULER}`), add nothing — the mobile view goes outside `<main>`. In the body, after `</main>` and before `<script>{ADMIN_JS}</script>`:

```python
</main>
{MOBILE_CHAT_HTML}
<script>{ADMIN_JS}</script>
<script>{MOBILE_CHAT_JS}</script>
{pwa_script}
</body></html>
```

### Manual verification

- [ ] Open the admin UI in Chrome DevTools with "Responsive" mode set to 375px width — the tab nav and main content should be hidden, `#mobile-chat-view` should be visible
- [ ] Open on a real Android device (Chrome) — verify chat bubbles, send button, quick actions render correctly
- [ ] Verify no JavaScript console errors on load

### Commit

```
git add src/bridge/admin_ui.py
git commit -m "feat(pwa): add responsive mobile chat UI with Alpine.js mobileChatApp component"
```

---

## Task 12 — Mount `push_api` router in `app.py`

**File:** `src/bridge/app.py`

### Context

The pattern in `app.py` (lines 49–51) is:
```python
from broadcast_notifier import BroadcastNotifier
from scheduler import SchedulerManager
from scheduler_api import router as scheduler_router, init_scheduler_api
```

`include_router` is called for `scheduler_router`. The push router follows the same pattern, guarded by `PUSH_ENABLED`.

### Test first

- [ ] The `TestPushAPI.test_vapid_public_key_endpoint` test (Task 6) already validates router mounting. Run it again as a sanity check after this task.

### Implementation

- [ ] In `src/bridge/app.py`, add the push import near the top (after scheduler imports):

```python
from push_api import router as push_router, init_push_api
```

- [ ] In the app startup logic (find the `@app.on_event("startup")` handler or lifespan block), add conditional push initialization:

```python
# Push notifications (conditional on PUSH_ENABLED)
_push_enabled = os.getenv("PUSH_ENABLED", "false").lower() == "true"
if _push_enabled:
    from push_notifications import PushNotificationManager
    _push_mgr = PushNotificationManager()
    init_push_api(_push_mgr)
    logger.info("Push notifications enabled (VAPID public key: %s...)", _push_mgr.vapid_public_key[:16])
else:
    init_push_api(None)
```

- [ ] Mount the push router (add alongside `app.include_router(scheduler_router)`):

```python
app.include_router(push_router)
```

### Run tests

- [ ] `cd src/bridge && python -m pytest ../../tests/test_push_notifications.py -v` — full suite passes

### Commit

```
git add src/bridge/app.py
git commit -m "feat(push): mount push_api router and init PushNotificationManager at startup"
```

---

## Task 13 — Complete test suite in `tests/test_push_notifications.py`

**File:** `tests/test_push_notifications.py`

### Implementation

The test file has been built incrementally across Tasks 3–10. Add the remaining send tests from the spec that are not yet covered:

- [ ] Add to `tests/test_push_notifications.py` (inside a new `TestPushSend` class):

```python
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
        from py_vapid import Vapid
        v = Vapid()
        v.generate_keys()
        env = {
            "PUSH_ENABLED": "true",
            "PUSH_VAPID_PUBLIC_KEY": v.public_key_urlsafe.decode(),
            "PUSH_VAPID_PRIVATE_KEY": v.private_key_urlsafe.decode(),
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
```

### Run full test suite

- [ ] `cd src/bridge && python -m pytest ../../tests/test_push_notifications.py -v`

Expected output: all tests pass.

### Commit

```
git add tests/test_push_notifications.py
git commit -m "test(push): complete TDD test suite for push_notifications, push_api, and webpush channel"
```

---

## Final integration checklist

- [ ] `pip install pywebpush>=2.0` confirmed in the bridge container/venv
- [ ] `python migrations/run_migrations.py` — migration 016 applied
- [ ] `cd src/bridge && python -m pytest ../../tests/test_push_notifications.py -v` — all tests green
- [ ] App starts without error: `uvicorn app:app --port 8765`
- [ ] `curl http://localhost:8765/static/manifest.json` — 200, JSON with `name: "Nanobot"`
- [ ] `curl http://localhost:8765/static/sw.js` — 200, contains `nanobot-v1`
- [ ] `curl http://localhost:8765/api/push/vapid-public-key` — 503 (PUSH_ENABLED=false by default)
- [ ] Set `PUSH_ENABLED=true` in `stack.env`, restart — auto-generated VAPID keys logged; copy to `stack.env`
- [ ] `curl http://localhost:8765/api/push/vapid-public-key` — 200, non-empty key
- [ ] Open admin on Android Chrome (375px) — mobile chat view renders, tabs hidden
- [ ] Chrome DevTools → Application → Manifest — "Nanobot" manifest parsed, installable
- [ ] Chrome DevTools → Application → Service Workers — sw.js registered, status "activated"

---

## PWA icon note

`src/bridge/static/icons/icon-192.png` and `icon-512.png` are required by the manifest but are binary assets that must be created manually or generated from an existing logo. Options:

1. Use a script to generate minimal colored PNG icons:

```python
# generate_icons.py — run once to create placeholder icons
from PIL import Image, ImageDraw
import os

os.makedirs("src/bridge/static/icons", exist_ok=True)
for size in [192, 512]:
    img = Image.new("RGB", (size, size), color="#1a1a2e")
    d = ImageDraw.Draw(img)
    margin = size // 8
    d.ellipse([margin, margin, size - margin, size - margin], fill="#3b82f6")
    img.save(f"src/bridge/static/icons/icon-{size}.png")
print("Icons generated.")
```

2. Or copy existing project logo assets into `src/bridge/static/icons/`.

The Service Worker handles missing icons gracefully (logs a warning, does not abort install). The PWA is functional without icons; they are only required for the A2HS install prompt and notification badge.
