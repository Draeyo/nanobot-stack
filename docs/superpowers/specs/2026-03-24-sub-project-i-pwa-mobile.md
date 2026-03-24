# Spec : Progressive Web App Mobile — Sous-projet I

**Date :** 2026-03-24
**Statut :** Spécifié
**Projet :** nanobot-stack
**Scope :** Transformation de l'interface web existante en Progressive Web App installable, interface chat optimisée mobile (< 768px), notifications Push via Web Push API avec clés VAPID, Service Worker avec stratégie de cache et page hors-ligne, sans ajout d'onglet dans l'Admin UI

---

## 1. Contexte & Objectifs

Nanobot-stack dispose d'une Admin UI Alpine.js riche (~11 onglets) servie par FastAPI, d'un pipeline SSE de streaming déjà fonctionnel (`streaming.py`), et de plusieurs canaux de notification (ntfy, Telegram, Discord). Sur mobile, l'interface complète est difficile à utiliser : les onglets ne sont pas adaptés aux petits écrans, et l'application doit être ouverte dans le navigateur plutôt qu'installée nativement.

Ce sous-projet ajoute trois capacités complémentaires :

1. **Manifest PWA** — permet l'installation sur l'écran d'accueil (A2HS) et l'ouverture en mode standalone, sans chrome de navigateur, sur Android (Chrome) et iOS (Safari 16.4+)
2. **Service Worker** — met en cache les assets statiques pour un chargement instantané, fournit une page hors-ligne propre, et sert de base pour les Push Notifications
3. **Interface chat mobile** — sur les écrans < 768px, les onglets Admin UI sont masqués et remplacés par une vue chat plein écran adaptée aux interactions tactiles, avec bulles de messages, actions rapides et bouton microphone (lien avec le Sous-projet G)

Les notifications Push Web remplacent le besoin de garder le navigateur ouvert : le bridge peut envoyer des messages push via `BroadcastNotifier` vers le navigateur inscrit, en complément des canaux Telegram/Discord/ntfy existants.

**Objectifs :**
- Permettre l'installation de Nanobot sur l'écran d'accueil Android et iOS sans développement natif
- Offrir une expérience chat mobile fluide : bulles, streaming SSE, actions rapides, microphone opt-in
- Envoyer des notifications push au navigateur mobile même quand l'onglet est fermé (via VAPID/Web Push)
- Fonctionner hors-ligne avec une page de repli claire plutôt qu'une erreur navigateur
- Opt-in total : `PWA_ENABLED=true` ajoute seulement le lien manifest ; `PUSH_ENABLED=true` active le pipeline VAPID complet

---

## 2. Architecture

### Nouveaux modules et fichiers statiques

```
PushNotificationManager (src/bridge/push_notifications.py)
  ├── generate_vapid_keys() → tuple[str, str]
  │     └── py_vapid : génère paire de clés ECDH P-256 (public, private), encodées base64url
  ├── get_vapid_public_key() → str
  │     └── Lit PUSH_VAPID_PUBLIC_KEY depuis l'environnement (pré-généré au startup)
  ├── subscribe(endpoint, p256dh, auth) → str
  │     └── INSERT OR REPLACE INTO push_subscriptions → retourne l'id de la souscription
  ├── unsubscribe(endpoint) → bool
  │     └── DELETE FROM push_subscriptions WHERE endpoint = ?
  ├── send(subscription_id, title, body, url) → bool
  │     └── webpush() via pywebpush avec clés VAPID → POST vers endpoint du navigateur
  ├── send_to_all(title, body, url) → dict
  │     └── Itère sur toutes les souscriptions actives, appelle send() pour chacune
  │         Marque last_used, supprime les souscriptions expirées (HTTP 410 Gone)
  └── _cleanup_expired() → int
        └── DELETE FROM push_subscriptions WHERE id IN (liste des 410 reçus)
```

### Intégration avec BroadcastNotifier existant

```
BroadcastNotifier (src/bridge/notifier.py) — modifié
  └── Nouveau canal "webpush"
        └── Si PUSH_ENABLED=true et souscriptions actives :
              PushNotificationManager.send_to_all(title, body, url)
```

### Service Worker et assets statiques

```
src/bridge/static/
  ├── manifest.json     [nouveau] — Manifest PWA (nom, icônes, display, start_url)
  ├── sw.js             [nouveau] — Service Worker (cache, offline, push events)
  ├── mobile.css        [nouveau] — Styles spécifiques mobile (< 768px)
  └── icons/
        ├── icon-192.png  [nouveau] — Icône PWA 192x192
        └── icon-512.png  [nouveau] — Icône PWA 512x512
```

### Fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `src/bridge/push_notifications.py` | Créer | `PushNotificationManager` — VAPID, souscriptions, envoi push |
| `src/bridge/static/manifest.json` | Créer | Manifest PWA avec icônes, couleurs, display standalone |
| `src/bridge/static/sw.js` | Créer | Service Worker — cache-first statiques, network-first API, offline fallback, push handler |
| `src/bridge/static/mobile.css` | Créer | Styles mobile : bulles de chat, layout plein écran, typographie tactile |
| `src/bridge/static/icons/icon-192.png` | Créer | Icône PWA 192x192 |
| `src/bridge/static/icons/icon-512.png` | Créer | Icône PWA 512x512 |
| `src/bridge/admin_ui.py` | Modifier | Ajout `<link rel="manifest">` dans `<head>`, enregistrement SW, vue chat mobile, banner A2HS |
| `src/bridge/app.py` | Modifier | Init `PushNotificationManager` au startup (conditionnel `PUSH_ENABLED`), mount du router push |
| `src/bridge/push_api.py` | Créer | API REST `/api/push/*` — subscribe, unsubscribe, test, vapid-public-key |
| `src/bridge/notifier.py` | Modifier | Ajout du canal `webpush` dans `BroadcastNotifier` |
| `migrations/017_push_subscriptions.py` | Créer | Table `push_subscriptions` |
| `src/bridge/requirements.txt` | Modifier | Ajouter `pywebpush>=2.0` |
| `tests/test_push_notifications.py` | Créer | Tests unitaires push, subscribe, send, cleanup |

---

## 3. Manifest PWA & Service Worker

### Manifest PWA (`src/bridge/static/manifest.json`)

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

Le champ `display: "standalone"` supprime la barre d'URL et les boutons de navigation du navigateur, donnant l'apparence d'une app native. `orientation: "portrait-primary"` verrouille l'orientation — l'interface chat mobile n'est pas conçue pour le paysage. `theme_color` définit la couleur de la barre de statut Android. `purpose: "any maskable"` permet à Android d'adapter l'icône à toutes les formes (cercle, carré arrondi).

Le manifest est servi à `/static/manifest.json` via le montage `StaticFiles` existant dans FastAPI. Aucune route dédiée n'est nécessaire. Le header `Content-Type: application/manifest+json` est ajouté via un middleware de réponse.

### Lien dans le HTML (`admin_ui.py`)

```html
<!-- Ajouté dans <head>, conditionnel sur PWA_ENABLED -->
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#1a1a2e">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Nanobot">
<link rel="apple-touch-icon" href="/static/icons/icon-192.png">
```

Les balises `apple-mobile-web-app-*` sont nécessaires pour iOS Safari qui n'utilise pas le manifest standard pour certaines métadonnées.

### Service Worker (`src/bridge/static/sw.js`)

**Stratégie de cache globale :**

```
CACHE_NAME = "nanobot-v1"

Assets mis en cache à l'installation (cache-first) :
  - /static/mobile.css
  - /static/manifest.json
  - /static/icons/icon-192.png
  - /static/icons/icon-512.png
  - / (page principale — shell HTML)

Stratégie par type de requête :
  - URL commençant par /static/* → Cache-first
      1. Chercher dans le cache SW
      2. Si trouvé : retourner depuis le cache
      3. Sinon : fetch réseau, mettre en cache, retourner
  - URL commençant par /api/* → Network-first
      1. Tenter fetch réseau (timeout 10s)
      2. Si réseau OK : retourner la réponse (pas de mise en cache)
      3. Si réseau échoue (offline) : retourner offline.json ({"error": "offline"})
  - Tout autre URL → Network-first avec fallback offline.html
      → Si réseau échoue : retourner la page offline mise en cache à l'install
```

**Événements Service Worker :**

```
install
  → Ouvrir CACHE_NAME
  → Mettre en cache les assets critiques (liste ci-dessus)
  → self.skipWaiting() — activation immédiate sans attendre la fermeture des onglets

activate
  → Supprimer les anciennes versions de cache (CACHE_NAME !== "nanobot-v1")
  → clients.claim() — prendre le contrôle de tous les onglets ouverts immédiatement

fetch
  → Router selon la stratégie décrite ci-dessus

push
  → Extraire payload : {title, body, url}
  → self.registration.showNotification(title, {body, icon, badge, data: {url}})

notificationclick
  → event.notification.close()
  → clients.openWindow(event.notification.data.url || "/")
```

**Page hors-ligne :** une page HTML minimale est mise en cache à l'installation avec le message "Hors ligne — reconnectez-vous au serveur Nanobot." et un bouton "Réessayer" qui tente `location.reload()`. Aucune dépendance externe (pas de CDN, pas de fonts web).

**Versionnement du cache :** le `CACHE_NAME` inclut un suffixe de version (`"nanobot-v1"`). Lors d'une mise à jour du Service Worker, la version est incrémentée dans `sw.js`, ce qui déclenche la suppression de l'ancien cache dans l'événement `activate`.

### Enregistrement du Service Worker (dans `admin_ui.py`)

```javascript
// Bloc injecté dans le HTML si PWA_ENABLED=true, juste avant </body>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', async () => {
    try {
      const reg = await navigator.serviceWorker.register('/static/sw.js', { scope: '/' });
      console.log('[Nanobot PWA] Service Worker enregistré', reg.scope);
      window._swRegistration = reg;  // exposé pour l'abonnement push
    } catch (err) {
      console.warn('[Nanobot PWA] Enregistrement SW échoué', err);
    }
  });
}
```

---

## 4. Interface mobile (composants Alpine.js)

### Principe d'affichage conditionnel

Aucun nouvel onglet n'est ajouté à l'Admin UI. Le basculement entre vue Admin complète et vue Chat mobile est piloté par une media query CSS dans `mobile.css` :

```css
/* Sur mobile (< 768px) : masquer les onglets, afficher la vue chat */
@media (max-width: 767px) {
  #admin-tabs-nav    { display: none !important; }
  #admin-tabs-panels { display: none !important; }
  #mobile-chat-view  { display: flex !important; }
}

/* Sur desktop : masquer la vue chat mobile */
@media (min-width: 768px) {
  #mobile-chat-view { display: none !important; }
}
```

Le div `#mobile-chat-view` est injecté dans le HTML par `admin_ui.py` directement dans le `<body>`, à la même hauteur que `#admin-tabs-nav`, mais initialement masqué sur desktop.

### Composant Alpine.js `mobileChatApp()`

```
mobileChatApp()
  État :
    messages        : array<{role, content, ts, streaming}>
    inputText       : string
    isStreaming     : bool
    isRecording     : bool    // micro actif (lien Sous-projet G)
    showInstallBanner : bool  // prompt A2HS
    deferredPrompt  : null | BeforeInstallPromptEvent
    pushEnabled     : bool    // PUSH_ENABLED lu depuis /api/push/vapid-public-key
    pushSubscribed  : bool    // souscription active dans ce navigateur

  Méthodes :
    init()
      → Appeler GET /api/chat/history?limit=20 pour charger les derniers messages
      → Vérifier si SW enregistré (window._swRegistration)
      → Si PUSH_ENABLED : appeler checkPushSubscription()
      → Écouter beforeinstallprompt → stocker deferredPrompt, showInstallBanner = true

    sendMessage()
      → Ajouter message user dans messages[]
      → Vider inputText
      → Créer un message assistant vide (streaming: true)
      → Ouvrir EventSource sur /api/chat/stream?message=...
      → Accumuler les chunks SSE dans le dernier message
      → Sur event "done" : streaming = false, fermer EventSource

    startVoiceInput()          // uniquement si VOICE_ENABLED=true (vérifié via /api/voice/status)
      → Appeler navigator.mediaDevices.getUserMedia({audio: true})
      → isRecording = true
      → Enregistrer via MediaRecorder
      → Sur clic stop : POST /api/voice/chat → réponse audio + transcription
      → Injecter la transcription comme message user, la réponse comme message assistant

    newConversation()
      → POST /api/chat/reset
      → Vider messages[]

    triggerBriefing()
      → POST /api/scheduler/trigger-briefing
      → Toast "Briefing déclenché"

    installApp()
      → deferredPrompt.prompt()
      → Attendre userChoice
      → showInstallBanner = false

    checkPushSubscription()
      → Récupérer PushManager.getSubscription() depuis SW
      → pushSubscribed = (subscription !== null)

    subscribePush()
      → GET /api/push/vapid-public-key → clé publique VAPID
      → swRegistration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey})
      → POST /api/push/subscribe avec {endpoint, p256dh, auth}
      → pushSubscribed = true

    unsubscribePush()
      → PushManager.getSubscription() → subscription.unsubscribe()
      → DELETE /api/push/unsubscribe avec {endpoint}
      → pushSubscribed = false
```

### Structure HTML de la vue mobile (`#mobile-chat-view`)

```
#mobile-chat-view (flex column, height 100dvh)
  ├── .mobile-header (barre titre)
  │     ├── "Nanobot" (titre)
  │     ├── Bouton "⊕ Nouveau" → newConversation()
  │     └── Bouton "🔔" (push) → subscribePush() / unsubscribePush() si PUSH_ENABLED
  │
  ├── .mobile-messages (flex column, scroll automatique, flex-grow: 1)
  │     └── Pour chaque message dans messages[] :
  │           .message-bubble.user   (aligné à droite, fond primaire)
  │           .message-bubble.assistant (aligné à gauche, fond secondaire)
  │           [Si streaming] : indicateur de typing animé (trois points)
  │
  ├── .mobile-quick-actions (barre d'actions rapides)
  │     └── Bouton "📋 Briefing maintenant" → triggerBriefing()
  │
  └── .mobile-input-bar (barre de saisie fixe en bas)
        ├── <textarea> (auto-resize, placeholder "Message...")
        ├── [Si VOICE_ENABLED] Bouton microphone → startVoiceInput()
        │     Icône animée (rouge clignotant) quand isRecording=true
        └── Bouton "Envoyer" → sendMessage()
              @click déclenchant un court retour haptique :
              navigator.vibrate && navigator.vibrate(10)
```

### Bulles de messages

Les bulles `user` sont alignées à droite avec `margin-left: auto`, fond `#1a1a2e`, coins arrondis (`border-radius: 18px 18px 4px 18px`). Les bulles `assistant` sont alignées à gauche avec fond `#16213e`, coins arrondis (`border-radius: 18px 18px 18px 4px`). Le texte Markdown dans les réponses assistant est rendu via la bibliothèque `marked.js` (déjà présente dans le projet pour l'Admin UI existant).

Pendant le streaming SSE, le dernier message assistant affiche un indicateur de typing — trois points animés en CSS — qui disparaît dès que `streaming = false`.

### Banner A2HS (Add to Home Screen)

```html
<!-- Banner discret affiché si showInstallBanner=true (non installé, AFTER beforeinstallprompt) -->
<div x-show="showInstallBanner" class="install-banner">
  <span>Installer Nanobot sur l'écran d'accueil</span>
  <button @click="installApp()">Installer</button>
  <button @click="showInstallBanner = false">✕</button>
</div>
```

Le banner n'est affiché que sur les navigateurs qui émettent l'événement `beforeinstallprompt` (Chrome Android). Sur iOS Safari, l'installation se fait manuellement via le menu "Partager → Sur l'écran d'accueil" — aucune prompt automatique n'est disponible. Un message contextuel différent peut être affiché sur iOS (détecté via `navigator.userAgent`) pour guider l'utilisateur.

---

## 5. Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `PWA_ENABLED` | `true` | Ajouter `<link rel="manifest">` et enregistrer le Service Worker dans le HTML. Si `false`, aucune modification du HTML, aucun SW enregistré |
| `PUSH_ENABLED` | `false` | Activer le pipeline Web Push VAPID. Si `false`, les endpoints `/api/push/*` retournent HTTP 503. Ne nécessite pas HTTPS en développement local mais HTTPS est requis en production (Traefik gère cela) |
| `PUSH_VAPID_PUBLIC_KEY` | `""` (auto-généré) | Clé publique VAPID encodée base64url. Si vide au démarrage et `PUSH_ENABLED=true`, une paire de clés est générée automatiquement et les deux variables sont loguées pour être persistées dans `stack.env` |
| `PUSH_VAPID_PRIVATE_KEY` | `""` (auto-généré) | Clé privée VAPID encodée base64url. Même logique d'auto-génération que la clé publique |
| `PUSH_VAPID_EMAIL` | `admin@nanobot.local` | Email de contact VAPID transmis aux serveurs push des navigateurs (obligatoire dans l'en-tête VAPID) |

**Note sur l'auto-génération des clés VAPID :** si `PUSH_ENABLED=true` et que `PUSH_VAPID_PUBLIC_KEY` ou `PUSH_VAPID_PRIVATE_KEY` sont vides, `PushNotificationManager.__init__()` génère une nouvelle paire via `py_vapid.Vapid.generate_keys()`, affiche un WARNING dans les logs avec les valeurs à copier dans `stack.env`, et continue. Les clés sont régénérées à chaque redémarrage tant qu'elles ne sont pas persistées — ce qui invalide toutes les souscriptions push existantes. Il est donc critique de persister les clés générées dès le premier démarrage.

---

## 6. Notifications Push

### Table SQLite `push_subscriptions` (migration 017)

```sql
CREATE TABLE push_subscriptions (
    id          TEXT PRIMARY KEY,              -- UUID v4
    endpoint    TEXT NOT NULL UNIQUE,          -- URL du service push du navigateur
    p256dh      TEXT NOT NULL,                 -- Clé publique du client (base64url)
    auth        TEXT NOT NULL,                 -- Secret d'authentification (base64url)
    created_at  TEXT NOT NULL,                 -- ISO 8601 UTC
    last_used   TEXT DEFAULT NULL              -- Dernier push envoyé avec succès (ISO 8601 UTC)
);

CREATE INDEX idx_push_subscriptions_endpoint ON push_subscriptions (endpoint);
CREATE INDEX idx_push_subscriptions_created_at ON push_subscriptions (created_at);
```

`endpoint` est l'URL unique fournie par le navigateur lors de l'appel à `pushManager.subscribe()`. Elle encode l'identifiant du service push (FCM pour Chrome, Mozilla Push pour Firefox, APNs Web Push pour Safari 16+). `p256dh` et `auth` sont les clés de chiffrement côté client nécessaires pour chiffrer le contenu du message push (protocole RFC 8291). `last_used` permet de détecter les souscriptions inactives et de déclencher un cleanup.

### Flux d'inscription (côté client)

```
1. GET /api/push/vapid-public-key
     → {"vapid_public_key": "BNT...abc"}
2. swRegistration.pushManager.subscribe({
       userVisibleOnly: true,
       applicationServerKey: urlBase64ToUint8Array(vapidPublicKey)
   })
     → PushSubscription {endpoint, keys: {p256dh, auth}}
3. POST /api/push/subscribe
     Body: {"endpoint": "...", "p256dh": "...", "auth": "..."}
     → 201 Created {"id": "uuid", "message": "Souscription enregistrée"}
```

La fonction `urlBase64ToUint8Array()` est un utilitaire JS standard pour convertir la clé VAPID base64url en `Uint8Array` requis par l'API Web Push.

### Flux d'envoi côté serveur

```
PushNotificationManager.send(subscription_id, title, body, url)
  1. Lire la souscription depuis push_subscriptions WHERE id = subscription_id
  2. Construire le payload JSON :
       {"title": title, "body": body, "url": url, "icon": "/static/icons/icon-192.png"}
  3. webpush(
         subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
         data=json.dumps(payload),
         vapid_private_key=PUSH_VAPID_PRIVATE_KEY,
         vapid_claims={"sub": "mailto:" + PUSH_VAPID_EMAIL}
     )
  4. Si HTTP 201 ou 202 → succès, UPDATE push_subscriptions SET last_used = now WHERE id = ?
  5. Si HTTP 410 Gone → souscription expirée, DELETE FROM push_subscriptions WHERE id = ?
  6. Si autre erreur → log WARNING, retourner False
```

### Canal `webpush` dans `BroadcastNotifier`

```
BroadcastNotifier.notify(title, body, url, channels)
  [canal existants : ntfy, telegram, discord]
  [nouveau canal] webpush :
    Si PUSH_ENABLED=true ET "webpush" dans channels :
      PushNotificationManager.send_to_all(title, body, url)
        → Pour chaque souscription active :
              send(subscription_id, title, body, url)
        → Retourner {"sent": N, "failed": M, "expired_cleaned": K}
```

Le canal `webpush` est automatiquement inclus dans la liste des canaux par défaut si `PUSH_ENABLED=true`. Il peut être désactivé par message en l'excluant de la liste `channels`.

### Réception côté navigateur (Service Worker `sw.js`)

```javascript
self.addEventListener('push', event => {
  const data = event.data ? event.data.json() : {};
  const title = data.title || 'Nanobot';
  const options = {
    body:  data.body  || '',
    icon:  '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    data:  { url: data.url || '/' },
    vibrate: [200, 100, 200],
    requireInteraction: false
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const targetUrl = event.notification.data.url;
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
      for (const client of clientList) {
        if (client.url === targetUrl && 'focus' in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(targetUrl);
    })
  );
});
```

---

## 7. Sécurité

- **HTTPS obligatoire pour PUSH_ENABLED** : les Service Workers et l'API Web Push nécessitent HTTPS. En production, Traefik gère le TLS (certificats Let's Encrypt). En développement local (`localhost`), le navigateur autorise les SW sans HTTPS. `PUSH_ENABLED=true` sur HTTP non-localhost est détecté au startup et lève un WARNING dans les logs.
- **Clés VAPID côté serveur uniquement** : `PUSH_VAPID_PRIVATE_KEY` ne transite jamais vers le client. Seule `PUSH_VAPID_PUBLIC_KEY` est exposée via `GET /api/push/vapid-public-key`. La clé privée est utilisée exclusivement côté serveur pour signer les requêtes VAPID.
- **Authentification des endpoints push** : les endpoints `/api/push/subscribe`, `/api/push/unsubscribe` et `/api/push/test` sont protégés par le middleware `BRIDGE_TOKEN` existant, comme tous les endpoints de l'API bridge. Le token est lu depuis le cookie de session ou le header `Authorization: Bearer ...`.
- **Isolation des souscriptions** : la table `push_subscriptions` ne contient qu'une seule instance (usage mono-utilisateur). Aucune donnée utilisateur autre que l'endpoint et les clés de chiffrement n'est stockée.
- **Chiffrement end-to-end des payloads push** : `pywebpush` implémente le chiffrement RFC 8291 (ECDH + AES-GCM) avec les clés `p256dh` et `auth` du client. Le serveur de push du navigateur (FCM, Mozilla, APNs) ne peut pas lire le contenu du message.
- **Cleanup automatique des souscriptions expirées** : HTTP 410 reçu lors d'un envoi push signifie que l'utilisateur a révoqué la permission. La souscription est supprimée immédiatement de la table, sans tentative de réenvoi.
- **Service Worker scope limité** : le SW est enregistré avec `scope: "/"` mais les règles de cache réseau-first pour `/api/*` garantissent qu'aucun appel API n'est jamais servi depuis le cache — les données sensibles ne sont jamais mises en cache par le SW.
- **`PWA_ENABLED=true` par défaut** : le lien manifest et l'enregistrement du SW sont inoffensifs. Le SW ne modifie le comportement réseau que pour mettre en cache les assets statiques. `PUSH_ENABLED=false` par défaut — aucune table SQLite, aucune clé VAPID, aucun endpoint push actif tant que l'opt-in n'est pas explicite.

---

## 8. Tests

Fichier : `tests/test_push_notifications.py`

| Test | Description |
|------|-------------|
| `test_manifest_served_correctly` | `GET /static/manifest.json` → HTTP 200, `Content-Type: application/manifest+json`, champs `name`, `short_name`, `display`, `start_url`, `icons` présents et corrects |
| `test_sw_registration_in_html` | `GET /` → HTML contient `<link rel="manifest" href="/static/manifest.json">` et `navigator.serviceWorker.register('/static/sw.js')` quand `PWA_ENABLED=true` |
| `test_sw_absent_when_pwa_disabled` | `PWA_ENABLED=false` → HTML ne contient pas de `<link rel="manifest">` ni d'appel `serviceWorker.register` |
| `test_vapid_keys_auto_generated` | `PUSH_ENABLED=true`, clés vides → `PushNotificationManager.__init__()` génère une paire de clés valides, les deux clés sont non vides et encodées base64url |
| `test_vapid_public_key_endpoint` | `GET /api/push/vapid-public-key` avec `PUSH_ENABLED=true` → HTTP 200, `{"vapid_public_key": "..."}`, clé non vide |
| `test_push_endpoints_disabled` | `PUSH_ENABLED=false` → `GET /api/push/vapid-public-key`, `POST /api/push/subscribe`, `POST /api/push/test` retournent tous HTTP 503 |
| `test_subscribe_stores_in_sqlite` | `POST /api/push/subscribe` avec `{endpoint, p256dh, auth}` valides → HTTP 201, ligne insérée dans `push_subscriptions` avec les bons champs |
| `test_subscribe_duplicate_endpoint` | Deux `POST /api/push/subscribe` avec le même `endpoint` → second appel retourne HTTP 200 (update), pas d'erreur de contrainte UNIQUE |
| `test_unsubscribe_removes_row` | `DELETE /api/push/unsubscribe` avec `{endpoint}` existant → HTTP 200, ligne supprimée de `push_subscriptions` |
| `test_unsubscribe_unknown_endpoint` | `DELETE /api/push/unsubscribe` avec endpoint inconnu → HTTP 404 |
| `test_send_push_success` | Mock `webpush()` → retourne HTTP 201. `send()` retourne `True`, `last_used` mis à jour dans SQLite |
| `test_send_push_410_cleanup` | Mock `webpush()` → lève `WebPushException` avec HTTP 410. `send()` retourne `False`, souscription supprimée de `push_subscriptions` |
| `test_send_to_all_multiple_subscriptions` | 3 souscriptions en base, mock `webpush()` → retourne HTTP 201 pour toutes. `send_to_all()` retourne `{"sent": 3, "failed": 0, "expired_cleaned": 0}` |
| `test_send_to_all_partial_failure` | 2 souscriptions, 1 succès + 1 HTTP 410. Retourne `{"sent": 1, "failed": 0, "expired_cleaned": 1}` |
| `test_push_test_endpoint` | `POST /api/push/test` → appelle `send_to_all()` avec titre et body de test, retourne `{"sent": N}` |
| `test_broadcast_notifier_webpush_channel` | Mock `PushNotificationManager.send_to_all()`. `BroadcastNotifier.notify(channels=["webpush"])` appelle `send_to_all()` une fois avec les bons arguments |

---

## 9. Ordre d'implémentation

1. Migration `migrations/017_push_subscriptions.py` — table `push_subscriptions` avec index
2. `src/bridge/static/manifest.json` — manifest PWA complet
3. `src/bridge/static/icons/icon-192.png` et `icon-512.png` — icônes (générées ou intégrées depuis assets existants)
4. `src/bridge/static/sw.js` — Service Worker : install/activate (cache assets), fetch (stratégie cache-first / network-first), page offline, handlers push et notificationclick
5. `src/bridge/static/mobile.css` — media queries, bulles de chat, layout plein écran, styles input bar
6. `admin_ui.py` — balises PWA dans `<head>` (manifest, meta apple), snippet JS enregistrement SW, div `#mobile-chat-view` avec composant `mobileChatApp()`, banner A2HS
7. `src/bridge/push_notifications.py` — `PushNotificationManager` : `generate_vapid_keys()`, `subscribe()`, `unsubscribe()`, `send()`, `send_to_all()`, `_cleanup_expired()`
8. `src/bridge/push_api.py` — endpoints `/api/push/vapid-public-key`, `/api/push/subscribe`, `/api/push/unsubscribe`, `/api/push/test`
9. `app.py` — init conditionnel `PushNotificationManager` au startup (si `PUSH_ENABLED`), mount `push_router`
10. `notifier.py` — ajout du canal `webpush` dans `BroadcastNotifier.notify()`
11. Tests `tests/test_push_notifications.py`
12. Vérification fin-à-fin sur mobile Android Chrome : installation A2HS, vue chat mobile, souscription push, réception notification
