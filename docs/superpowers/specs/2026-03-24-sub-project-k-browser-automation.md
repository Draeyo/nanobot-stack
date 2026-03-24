# Spec : Browser Automation (Playwright) — Sous-projet K

**Date :** 2026-03-24
**Statut :** Spécifié
**Projet :** nanobot-stack
**Scope :** Automatisation de navigateur headless via Playwright Python, nouveau `BrowserAgent` intégré au Trust Engine, gestion de sessions éphémères, capture de screenshots, extraction de texte, interaction avec formulaires, stockage optionnel dans Qdrant, allowlist de domaines, audit complet de toutes les actions

---

## 1. Contexte & Objectifs

Nanobot-stack peut déjà exécuter des commandes shell (OpsAgent) et rechercher sur le web (sous-projet D via SearXNG). Le sous-projet K complète ces capacités en ajoutant une interaction réelle avec des pages web : navigation, extraction de contenu structuré, remplissage de formulaires, capture d'écran. Là où SearXNG ne retourne que des métadonnées (titre, snippet, URL), le `BrowserAgent` charge la page complète dans un navigateur headless Chromium et peut en extraire le contenu exact, interagir avec ses éléments, ou en capturer une image.

Ce sous-projet est le plus sensible en termes de sécurité parmi toutes les intégrations planifiées : un navigateur contrôlé par un LLM peut soumettre des formulaires, cliquer sur des boutons, ou naviguer vers des sites malveillants. La philosophie de conception est donc **trust-first** : chaque action de navigateur passe systématiquement par le Trust Engine avant exécution, avec des niveaux de risque différenciés selon la nature de l'action.

**Objectifs :**
- Implémenter un `BrowserAgent` étendant `AgentBase`, pilotant un Chromium headless via Playwright Python (API async)
- Classifier les actions par niveau de risque (`browser_read`, `browser_fill`, `browser_submit`) avec des niveaux de confiance par défaut distincts
- Intégrer le mécanisme d'auto-promotion du Trust Engine pour les domaines récurrents de bas risque
- Appliquer une allowlist de domaines configurable via `BROWSER_ALLOWED_DOMAINS`
- Gérer des sessions éphémères : chaque `run()` crée un contexte de navigateur isolé, détruit à la fin de la tâche ou en cas de timeout
- Stocker optionnellement les screenshots dans Qdrant (collection `browser_screenshots`, TTL 24h)
- Auditer chaque action dans la table SQLite `browser_action_log`
- Opt-in explicite via `BROWSER_ENABLED=false` par défaut

---

## 2. Architecture

### Diagramme de classes

```
AgentBase (agents/base.py)
  └── BrowserAgent (agents/browser_agent.py)
        ├── name: str = "browser"
        ├── description: str
        ├── allowed_domains: list[str]        — depuis BROWSER_ALLOWED_DOMAINS
        ├── page_timeout_ms: int              — depuis BROWSER_PAGE_TIMEOUT_MS
        ├── max_session_s: int                — depuis BROWSER_MAX_SESSION_S
        ├── screenshot_store: bool            — depuis BROWSER_SCREENSHOT_STORE
        ├── browser_type: str                 — depuis PLAYWRIGHT_BROWSER
        │
        ├── run(task, context) → AgentResult              [override AgentBase]
        ├── navigate(url) → NavigateResult
        ├── screenshot() → ScreenshotResult
        ├── extract_text(selector?) → ExtractTextResult
        ├── click(selector) → ActionResult
        ├── fill(selector, value) → ActionResult
        ├── submit(selector?) → ActionResult
        ├── wait_for(selector, timeout?) → ActionResult
        │
        ├── _check_domain_allowlist(url) → bool
        ├── _get_trust_action_type(action) → str
        ├── _execute_with_trust(action_type, action_detail, fn) → Any
        ├── _log_action(session_id, action_type, url, selector,
        │              status, trust_level, approved_by,
        │              started_at, duration_ms, error_msg) → None
        ├── _store_screenshot_qdrant(b64_png, url, action_context) → None
        └── _create_browser_context(playwright) → BrowserContext

@dataclass
NavigateResult
  ├── url: str
  ├── title: str
  ├── status_code: int
  └── duration_ms: int

@dataclass
ScreenshotResult
  ├── b64_png: str           — image PNG encodée en base64
  ├── url: str               — URL de la page au moment de la capture
  ├── width: int
  ├── height: int
  └── stored_qdrant: bool    — True si stocké dans browser_screenshots

@dataclass
ExtractTextResult
  ├── text: str              — texte brut uniquement, jamais HTML
  ├── selector: str | None
  ├── char_count: int
  └── truncated: bool        — True si tronqué à 50 000 chars

@dataclass
ActionResult
  ├── success: bool
  ├── action: str
  ├── selector: str | None
  └── duration_ms: int
```

### Intégration avec le Trust Engine

```
BrowserAgent._execute_with_trust(action_type, action_detail, fn)
  └── TrustEngine.check_and_execute(action_type, action_detail, fn)
        ├── action_type = "browser_read"    → trust: notify_then_execute (auto-promotable)
        ├── action_type = "browser_fill"    → trust: approval_required   (auto-promotable)
        └── action_type = "browser_submit"  → trust: approval_required   (jamais auto-promu)
```

### Infrastructure Docker

```
Option A — Sidecar Playwright dédié (recommandé en production)
  docker-compose.yml
    └── [nouveau] browser  — mcr.microsoft.com/playwright/python:v1.44.0-focal
                             réseau privé nanobot-net uniquement
                             accès web sortant via NAT/proxy

Option B — Playwright installé dans le conteneur bridge
  bridge/Dockerfile
    └── playwright install chromium (ajout à l'étape de build)
```

L'option A est recommandée car elle isole le risque réseau dans un conteneur dédié et évite d'alourdir l'image bridge. Le `BrowserAgent` communique avec le conteneur sidecar via `playwright.connect()` ou directement si Playwright est installé localement (détecté par `BROWSER_DOCKER_SIDECAR`).

### Fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `src/bridge/agents/browser_agent.py` | Créer | `BrowserAgent` + dataclasses résultat |
| `src/bridge/agents/__init__.py` | Modifier | Enregistrer `"browser"` dans `AGENT_REGISTRY` |
| `src/bridge/browser_api.py` | Créer | Endpoints `POST /api/browser/run`, `GET /api/browser/sessions`, `GET /api/browser/action-log` |
| `src/bridge/app.py` | Modifier | Mount `browser_router`, init `BrowserAgent` au startup si `BROWSER_ENABLED=true` |
| `src/bridge/admin_ui.py` | Modifier | Onglet "Browser" — log des actions, sessions récentes, statut |
| `migrations/019_browser.py` | Créer | Table `browser_action_log` |
| `src/bridge/trust_engine.py` | Modifier | Ajouter les trois types `browser_*` aux policies par défaut |
| `docker-compose.yml` | Modifier | Service `browser` (Option A) |
| `requirements.txt` | Modifier | Ajouter `playwright>=1.44` |
| `tests/test_browser_agent.py` | Créer | Tests unitaires avec mocks Playwright |

---

## 3. Modèle de données

### Table `browser_action_log`

```sql
CREATE TABLE browser_action_log (
    id          TEXT PRIMARY KEY,          -- UUID v4 généré à l'insertion
    session_id  TEXT NOT NULL,             -- UUID de la session BrowserAgent.run()
    action_type TEXT NOT NULL,             -- 'navigate' | 'screenshot' | 'extract_text' |
                                           -- 'click' | 'fill' | 'submit' | 'wait_for'
    url         TEXT NOT NULL,             -- URL active au moment de l'action
    selector    TEXT,                      -- Sélecteur CSS/XPath si applicable (NULL sinon)
    status      TEXT NOT NULL,             -- 'ok' | 'error' | 'blocked' | 'pending' | 'timeout'
    trust_level TEXT NOT NULL,             -- 'auto' | 'notify_then_execute' |
                                           -- 'approval_required' | 'blocked'
    approved_by TEXT,                      -- 'auto' | 'admin' | NULL si non encore approuvé
    started_at  TEXT NOT NULL,             -- Timestamp ISO 8601 UTC
    duration_ms INTEGER,                   -- Durée d'exécution en millisecondes (NULL si pending)
    error_msg   TEXT                       -- Message d'erreur si status='error' ou 'timeout'
);
```

```sql
CREATE INDEX idx_browser_action_log_session_id ON browser_action_log(session_id);
CREATE INDEX idx_browser_action_log_started_at ON browser_action_log(started_at);
CREATE INDEX idx_browser_action_log_status     ON browser_action_log(status);
```

`id` est un UUID v4 généré à l'insertion. `session_id` regroupe toutes les actions d'un même appel `BrowserAgent.run()` — permet de reconstituer la séquence complète d'une session. `trust_level` reflète le niveau de confiance effectif au moment de la décision (peut avoir été auto-promu). `approved_by` vaut `'auto'` si le trust engine a exécuté sans intervention, `'admin'` si l'approbation manuelle a eu lieu, `NULL` si l'action est en état `pending` (en attente d'approbation).

### Collection Qdrant `browser_screenshots`

TTL : 86400 secondes (24 heures). Les screenshots sont des données contextuelles de session — ils perdent leur pertinence après 24h et ne doivent pas occuper la mémoire vectorielle à long terme.

Activée uniquement si `BROWSER_SCREENSHOT_STORE=true`.

| Champ payload | Type | Description |
|---------------|------|-------------|
| `b64_png` | `str` | Image PNG encodée en base64 — incluse directement dans les payloads multimodaux LLM |
| `url` | `str` | URL de la page au moment de la capture |
| `page_title` | `str` | Titre de la page (`<title>`) |
| `action_context` | `str` | Description textuelle de l'action qui a déclenché le screenshot |
| `session_id` | `str` | UUID de la session d'origine |
| `width` | `int` | Largeur de la capture en pixels |
| `height` | `int` | Hauteur de la capture en pixels |
| `source` | `str` | Toujours `"browser_screenshot"` |
| `created_at` | `str` | Timestamp ISO 8601 UTC |

Le champ vectorisé est `url + " " + page_title + " " + action_context`. Le point ID Qdrant est `uuid5(NAMESPACE_URL, session_id + "_" + url + "_" + created_at)` — déterministe, permet un upsert idempotent en cas de retry.

---

## 4. Variables d'environnement

### Activation

| Variable | Défaut | Description |
|----------|--------|-------------|
| `BROWSER_ENABLED` | `false` | Opt-in global. Si `false`, `BrowserAgent` retourne une `AgentResult` avec `status='disabled'` sans lancer Playwright. Aucun processus Chromium n'est démarré. |

### Configuration du navigateur

| Variable | Défaut | Description |
|----------|--------|-------------|
| `PLAYWRIGHT_BROWSER` | `chromium` | Navigateur Playwright à utiliser. Seul `chromium` est supporté en production (mode headless vérifié). Valeurs refusées : `firefox`, `webkit` (risque de contournement sandbox). |
| `BROWSER_DOCKER_SIDECAR` | `false` | Si `true`, Playwright se connecte au conteneur `browser` via WebSocket. Si `false`, Playwright est lancé localement dans le process bridge. |
| `BROWSER_SIDECAR_WS_URL` | `ws://browser:8765` | URL WebSocket du conteneur sidecar Playwright (ignoré si `BROWSER_DOCKER_SIDECAR=false`). |

### Sécurité & Limites

| Variable | Défaut | Description |
|----------|--------|-------------|
| `BROWSER_ALLOWED_DOMAINS` | _(vide)_ | Liste de domaines autorisés, séparés par des virgules. Ex: `github.com,docs.python.org,wikipedia.org`. Si vide, **tous les domaines sont autorisés** — comportement dangereux, avertissement loggé au démarrage en niveau `WARNING`. |
| `BROWSER_PAGE_TIMEOUT_MS` | `30000` | Timeout de chargement de page en millisecondes (30s). Plage valide : 5000–120000. |
| `BROWSER_MAX_SESSION_S` | `300` | Durée maximale d'une session complète en secondes (5 minutes). Au-delà, le contexte de navigateur est détruit et la session terminée avec `status='timeout'`. |

### Stockage screenshots

| Variable | Défaut | Description |
|----------|--------|-------------|
| `BROWSER_SCREENSHOT_STORE` | `false` | Si `true`, les screenshots sont encodés en base64 et upsertés dans la collection Qdrant `browser_screenshots` (TTL 24h). Si `false`, les screenshots restent en mémoire pour la durée de la session uniquement. |

Si `BROWSER_ALLOWED_DOMAINS` est vide et `BROWSER_ENABLED=true`, le bridge émet au démarrage :

```
WARNING [BrowserAgent] BROWSER_ALLOWED_DOMAINS est vide — tous les domaines sont autorisés.
         Définir BROWSER_ALLOWED_DOMAINS pour restreindre la navigation aux domaines de confiance.
```

---

## 5. Pipeline d'exécution

### 5.1 Cycle de vie d'une session

```
BrowserAgent.run(task, context) → AgentResult
  1. Vérifier BROWSER_ENABLED → retourner AgentResult(status='disabled') si false
  2. Générer session_id = uuid4()
  3. t_session_start = now()
  4. LLM décompose task → liste d'actions ordonnées
       (prompt court, modèle cheap — adaptive_router "task_decomposition")
       Format attendu : [{"action": "navigate", "url": "..."}, {"action": "extract_text"}, ...]
  5. Lancer asyncio.timeout(BROWSER_MAX_SESSION_S)
  6. async with playwright.chromium.launch(headless=True) as browser:
       context = await _create_browser_context(browser)
       page = await context.new_page()
       Pour chaque action dans la liste :
         a. Vérifier timeout session (t_now - t_session_start > BROWSER_MAX_SESSION_S)
         b. Résoudre l'URL active (page.url)
         c. Vérifier _check_domain_allowlist(url_active)
            → Si domaine non autorisé : log action blocked, lever BrowserDomainBlockedError
         d. action_type_trust = _get_trust_action_type(action["action"])
         e. _execute_with_trust(action_type_trust, action_detail, fn)
            → TrustEngine.check_and_execute(...)
         f. Exécuter l'action Playwright correspondante
         g. _log_action(session_id, action_type, url, selector, status, ...)
  7. await context.close()
  8. Construire AgentResult avec actions_taken, output, artifacts
  9. Retourner AgentResult
```

### 5.2 Mapping actions → trust types

```
_get_trust_action_type(action) → str
  ├── "navigate"     → "browser_read"
  ├── "screenshot"   → "browser_read"
  ├── "extract_text" → "browser_read"
  ├── "wait_for"     → "browser_read"
  ├── "click"        → "browser_read"    (click non-submit : lecture navigable)
  ├── "fill"         → "browser_fill"
  └── "submit"       → "browser_submit"
```

**Justification du classement de `click` en `browser_read` :** cliquer sur un lien de navigation ou un accordéon n'a pas d'effet persistant sur un serveur distant. Un `click` sur un bouton `[type=submit]` doit être détecté et requalifié en `browser_submit` — `_get_trust_action_type` inspecte le sélecteur pour détecter ce cas.

### 5.3 Action `navigate`

```
BrowserAgent.navigate(url) → NavigateResult
  1. Valider URL (scheme http ou https uniquement — pas de file://, javascript:, data:)
  2. _check_domain_allowlist(url)
     → BrowserDomainBlockedError si domaine non autorisé
  3. t0 = now()
  4. await page.goto(url, wait_until="domcontentloaded",
                     timeout=BROWSER_PAGE_TIMEOUT_MS)
  5. title = await page.title()
  6. status_code = réponse.status (issu de la réponse de goto)
  7. duration_ms = (now() - t0) * 1000
  8. Retourner NavigateResult(url=page.url, title=title,
                              status_code=status_code, duration_ms=duration_ms)
```

### 5.4 Action `screenshot`

```
BrowserAgent.screenshot() → ScreenshotResult
  1. t0 = now()
  2. png_bytes = await page.screenshot(type="png", full_page=False)
  3. b64_png = base64.b64encode(png_bytes).decode("utf-8")
  4. viewport = page.viewport_size
  5. duration_ms = (now() - t0) * 1000
  6. stored = False
  7. Si BROWSER_SCREENSHOT_STORE=true :
       _store_screenshot_qdrant(b64_png, page.url, action_context)
       stored = True
  8. Retourner ScreenshotResult(b64_png, url=page.url,
                                width=viewport["width"],
                                height=viewport["height"],
                                stored_qdrant=stored)
```

Le `b64_png` peut être directement injecté dans un message multimodal LiteLLM (`{"type": "image_url", "image_url": {"url": "data:image/png;base64,<b64_png>"}}`), permettant au LLM de "voir" la page.

### 5.5 Action `extract_text`

```
BrowserAgent.extract_text(selector?) → ExtractTextResult
  1. Si selector fourni :
       element = await page.query_selector(selector)
       Si element est None : retourner ExtractTextResult(text="", truncated=False)
       text = await element.inner_text()    ← inner_text, jamais inner_html
  2. Sinon :
       text = await page.inner_text("body")
  3. Tronquer à 50 000 chars si nécessaire (truncated=True)
  4. Retourner ExtractTextResult(text=text, selector=selector,
                                 char_count=len(text), truncated=truncated)
```

`inner_text()` est utilisé exclusivement — jamais `inner_html()` ni `content()`. Cela garantit que seul du texte brut est retourné, éliminant tout risque d'injection HTML/XSS dans le contexte LLM.

### 5.6 Action `fill`

```
BrowserAgent.fill(selector, value) → ActionResult
  1. Résoudre le trust level via _execute_with_trust("browser_fill", ...)
  2. Si trust non approuvé : retourner ActionResult(success=False, ...)
  3. t0 = now()
  4. element = await page.query_selector(selector)
  5. Vérifier que element est un input/textarea (pas un div ou button)
     → Lever BrowserInvalidSelectorError si le sélecteur cible un élément non-fillable
  6. await page.fill(selector, value)
  7. Retourner ActionResult(success=True, action="fill",
                            selector=selector, duration_ms=...)
```

### 5.7 Action `submit`

```
BrowserAgent.submit(selector?) → ActionResult
  1. Résoudre le trust level via _execute_with_trust("browser_submit", ...)
     → "browser_submit" est TOUJOURS approval_required, jamais auto-promu
  2. Si trust non approuvé : retourner ActionResult(success=False, status='pending')
  3. t0 = now()
  4. Si selector fourni :
       await page.click(selector)
  5. Sinon : chercher [type=submit], puis button[type=submit], puis input[type=submit]
       await page.click(sélecteur_trouvé)
  6. Attendre stabilisation réseau : page.wait_for_load_state("networkidle", timeout=10s)
  7. Retourner ActionResult(success=True, action="submit", ...)
```

### 5.8 Vérification allowlist de domaines

```
BrowserAgent._check_domain_allowlist(url) → bool
  1. Si BROWSER_ALLOWED_DOMAINS est vide : retourner True (avec log WARNING)
  2. Extraire le hostname de l'URL (urllib.parse.urlparse)
  3. Normaliser : supprimer le préfixe "www." si présent
  4. Pour chaque domaine dans BROWSER_ALLOWED_DOMAINS :
       Normaliser (supprimer "www.")
       Si hostname == domaine OU hostname.endswith("." + domaine) :
         retourner True    ← sous-domaines autorisés
  5. Retourner False
     → _log_action avec status='blocked', trust_level='blocked'
     → Lever BrowserDomainBlockedError(url, hostname)
```

Les sous-domaines sont autorisés par défaut : si `github.com` est dans l'allowlist, `gist.github.com` et `api.github.com` passent également. Ce comportement est intentionnel pour couvrir les CDN et APIs de premier niveau des domaines de confiance.

### 5.9 Auto-promotion par domaine

La logique d'auto-promotion du Trust Engine standard s'applique, avec la particularité que le `action_detail` transmis à `TrustEngine.check_and_execute()` inclut le domaine normalisé. Cela permet au compteur de succès de `trust_policies` de s'incrémenter par `(action_type, domaine)` plutôt que globalement.

Après 20 exécutions réussies consécutives de `browser_read` sur le même domaine, le niveau passe de `notify_then_execute` à `auto`. Après 20 exécutions réussies consécutives de `browser_fill` sur le même domaine, le niveau passe de `approval_required` à `notify_then_execute`.

**Exception permanente :** `browser_submit` ne peut jamais être auto-promu. La policy est créée avec `auto_promote_after=0` (désactivé). La soumission de formulaires requiert toujours une approbation manuelle, quel que soit le domaine et le nombre de succès passés.

---

## 6. Sécurité & Bac à sable

### 6.1 Mode headless obligatoire

Playwright est toujours lancé avec `headless=True`. Aucun paramètre d'environnement ne permet de passer en mode `headful`. Si un appelant tente de passer `headless=False` via un paramètre interne, `BrowserAgent` ignore la valeur et force `headless=True` avec un log `WARNING`. Cette contrainte est vérifiée dans `_create_browser_context()` avant tout lancement.

### 6.2 Configuration du contexte de navigateur

```python
context = await browser.new_context(
    # Sécurité de base
    accept_downloads=False,           # Aucun téléchargement de fichier autorisé
    java_script_enabled=True,         # JavaScript nécessaire — ne pas désactiver
    bypass_csp=False,                 # Respecter la Content Security Policy des sites

    # Isolation
    has_touch=False,
    is_mobile=False,
    locale="fr-FR",
    timezone_id="Europe/Paris",

    # Blocage permissions sensibles
    permissions=[],                   # Aucune permission accordée (micro, caméra, notifs...)
    geolocation=None,                 # Géolocalisation bloquée
    color_scheme="light",

    # Pas de persistance
    # storage_state non passé → aucun cookie, localStorage, sessionStorage persistant
    # Aucun profil utilisateur monté
)
```

**Aucun cookie ne persiste entre les sessions.** Chaque `BrowserAgent.run()` crée un nouveau contexte vierge. Les cookies de session, tokens d'authentification, et historique de navigation sont détruits à la fermeture du contexte.

### 6.3 Blocage des téléchargements

`accept_downloads=False` est la première ligne de défense. En supplément, un gestionnaire d'événement `download` est enregistré sur le contexte pour logger et bloquer tout téléchargement inattendu :

```python
context.on("download", lambda download: _handle_unexpected_download(download))
```

`_handle_unexpected_download()` annule le téléchargement, logue l'événement en niveau `WARNING`, et insère une entrée dans `browser_action_log` avec `action_type='download_blocked'`, `status='blocked'`.

### 6.4 Session unique — pas de nouveaux onglets

Un seul onglet (`Page`) est créé par session. Si un site tente d'ouvrir un nouvel onglet (via `window.open` ou `target="_blank"`), le handler `context.on("page")` capture la tentative, ferme immédiatement la nouvelle page, et logue l'événement :

```python
context.on("page", lambda new_page: _block_new_tab(new_page, session_id))
```

### 6.5 Restriction réseau via allowlist

La vérification `_check_domain_allowlist()` est appliquée :
1. Avant chaque appel `navigate()` — côté applicatif
2. Dans un handler `route` Playwright pour les requêtes XHR/fetch/ressources — côté réseau :

```python
async def _block_unlisted_routes(route, request):
    if not _check_domain_allowlist(request.url):
        await route.abort()
    else:
        await route.continue_()

await page.route("**/*", _block_unlisted_routes)
```

Cela bloque non seulement la navigation principale, mais aussi les requêtes AJAX, les images provenant de CDN non listés, et les appels API tiers. Les ressources bloquées sont loguées en mode `DEBUG`.

### 6.6 Protection contre l'injection XSS via les données extraites

`extract_text()` utilise exclusivement `element.inner_text()`, qui retourne le contenu textuel rendu par le navigateur, sans balises HTML, sans scripts, sans attributs. Cette garantie est fournie par l'API Playwright elle-même. Le texte extrait est passé directement au LLM sans transformation supplémentaire — il n'y a pas d'étape de parsing HTML côté Python.

Les valeurs de formulaire (`fill()`) sont passées à `page.fill()` comme chaînes opaques — Playwright les insère dans les `value` des inputs sans interprétation. Aucune valeur remplie n'est évaluée comme JavaScript.

### 6.7 Pas de stockage de credentials

- Aucun `storage_state` (cookies, localStorage) n'est sauvegardé entre sessions
- Aucun profil utilisateur navigateur n'est monté (`user_data_dir` non spécifié)
- Les champs `fill(selector, value)` dont le `selector` cible un `input[type=password]` sont loggés avec `value` masqué (`***`) dans `browser_action_log`
- Les passwords ne sont jamais stockés dans Qdrant, ni dans les payloads de screenshots

### 6.8 Audit complet

Chaque action du navigateur — même celles bloquées ou rejetées par le trust engine — est enregistrée dans `browser_action_log` avec :
- Le `trust_level` effectif au moment de la décision
- L'`approved_by` (qui a approuvé, ou `NULL` si action en attente)
- Le `status` final (`ok`, `error`, `blocked`, `pending`, `timeout`)
- L'`error_msg` le cas échéant

Aucune action ne peut être exécutée sans être tracée. Le log est non-modifiable via l'API REST (lecture seule). La suppression des logs nécessite un accès direct à la base SQLite (opération d'administration).

### 6.9 Timeout de session

Si `BROWSER_MAX_SESSION_S` est dépassé, `asyncio.timeout()` lève `TimeoutError`. Le handler de timeout :
1. Ferme le contexte Playwright (`await context.close()`)
2. Insère une entrée dans `browser_action_log` avec `status='timeout'`
3. Retourne un `AgentResult(status='error', error="session_timeout")`

Le processus Chromium est garanti d'être terminé même en cas de timeout, évitant les fuites de processus.

---

## 7. Dépendances Python / Docker

### Python (`requirements.txt`)

```
playwright>=1.44
```

`playwright` inclut l'API Python async (`playwright.async_api`) et le gestionnaire de navigateurs. La version `1.44` est alignée avec l'image Docker officielle `mcr.microsoft.com/playwright/python:v1.44.0-focal`.

**Post-installation obligatoire :**

```dockerfile
# Dans le Dockerfile du bridge (Option B) ou du sidecar (Option A)
RUN playwright install chromium
RUN playwright install-deps chromium
```

Sans ces deux commandes, Playwright lèvera `BrowserType.launch: Executable doesn't exist` au premier lancement. L'installation de Chromium télécharge ~170 Mo — elle doit être effectuée à l'étape de build Docker, pas au runtime.

### Docker — Option A (sidecar dédié recommandé)

```yaml
services:
  browser:
    image: mcr.microsoft.com/playwright/python:v1.44.0-focal
    container_name: nanobot-browser
    restart: unless-stopped
    command: ["python", "-m", "playwright", "run-server", "--port", "8765", "--host", "0.0.0.0"]
    networks:
      - nanobot-net               # Réseau privé — accessible uniquement par le bridge
    environment:
      - PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
    cap_drop:
      - ALL
    cap_add:
      - SYS_ADMIN                 # Requis pour le sandbox Chromium (namespace)
    security_opt:
      - seccomp:unconfined        # Chromium nécessite des syscalls supplémentaires
    shm_size: 2gb                 # Chromium utilise /dev/shm pour le rendu
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8765/json/version"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s
```

Le service `browser` n'expose aucun port vers l'hôte. Il est joignable uniquement depuis le bridge via `ws://browser:8765` sur le réseau interne `nanobot-net`. L'accès web sortant du conteneur `browser` est contrôlé par la configuration réseau Docker (iptables, bridge network).

### Docker — Option B (Playwright dans le bridge)

Si `BROWSER_DOCKER_SIDECAR=false`, le Dockerfile du bridge doit inclure :

```dockerfile
FROM python:3.11-slim
# ... dépendances existantes ...
RUN pip install playwright>=1.44
RUN playwright install chromium
RUN playwright install-deps chromium
```

Cette option alourdit l'image bridge (+500 Mo) mais simplifie le déploiement. Recommandée uniquement pour les environnements de développement.

---

## 8. API REST

Préfixe : `/api/browser`

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| POST | `/api/browser/run` | Exécute une tâche de navigation et retourne le log d'actions, le texte extrait et l'URL du screenshot |
| GET | `/api/browser/sessions` | Retourne les sessions récentes (paginé) |
| GET | `/api/browser/action-log` | Retourne le log des actions (paginé, filtrable) |
| GET | `/api/browser/status` | Retourne `enabled`, `allowed_domains`, `pending_approvals` |

### `POST /api/browser/run`

**Corps de la requête :**

```json
{
  "task": "Aller sur la page de documentation de Playwright Python et extraire le titre de la section Getting Started",
  "url": "https://playwright.dev/python/docs/intro"
}
```

| Champ | Type | Requis | Défaut | Contraintes |
|-------|------|--------|--------|-------------|
| `task` | `str` | Oui | — | 10–2000 chars, non vide |
| `url` | `str` | Non | — | URL valide `http://` ou `https://` si fournie |

**Réponse 200 :**

```json
{
  "session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "completed",
  "actions_taken": [
    {
      "action": "navigate",
      "url": "https://playwright.dev/python/docs/intro",
      "trust_level": "notify_then_execute",
      "status": "ok",
      "duration_ms": 1240
    },
    {
      "action": "extract_text",
      "selector": "h1",
      "trust_level": "notify_then_execute",
      "status": "ok",
      "duration_ms": 42
    }
  ],
  "extracted_text": "Installation",
  "screenshot_stored": false,
  "screenshot_b64": null,
  "total_duration_ms": 2318,
  "error": null
}
```

Si `BROWSER_SCREENSHOT_STORE=false`, `screenshot_b64` est `null` dans la réponse (le screenshot n'est pas retourné en base64 par défaut pour ne pas alourdir la réponse HTTP). Pour recevoir le screenshot inline, le client doit passer `"include_screenshot": true` dans le corps de la requête.

**Codes d'erreur :**

| Code | Condition |
|------|-----------|
| 400 | `BROWSER_ENABLED=false` — retourne `{"error": "browser_disabled"}` |
| 400 | URL fournie avec schème invalide (`file://`, `javascript:`, etc.) |
| 403 | Domaine bloqué par allowlist — retourne `{"error": "domain_blocked", "domain": "..."}` |
| 422 | Validation échouée (task vide, URL malformée) |
| 503 | Playwright indisponible ou Chromium non démarré |

### `GET /api/browser/sessions`

**Paramètres query :** `?limit=20&offset=0`

**Réponse 200 :**

```json
{
  "sessions": [
    {
      "session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
      "started_at": "2026-03-24T09:15:00Z",
      "duration_ms": 2318,
      "actions_count": 2,
      "status": "completed",
      "last_url": "https://playwright.dev/python/docs/intro"
    }
  ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

### `GET /api/browser/action-log`

**Paramètres query :** `?limit=50&offset=0&session_id=<uuid>&status=ok&action_type=fill`

Tous les filtres sont optionnels et cumulables. Retourne une liste d'entrées `browser_action_log` avec tous les champs, sauf `error_msg` tronqué à 500 chars.

### `GET /api/browser/status`

**Réponse 200 :**

```json
{
  "enabled": true,
  "browser_type": "chromium",
  "docker_sidecar": true,
  "sidecar_reachable": true,
  "allowed_domains": ["playwright.dev", "docs.python.org"],
  "allowed_domains_count": 2,
  "all_domains_allowed": false,
  "page_timeout_ms": 30000,
  "max_session_s": 300,
  "screenshot_store": false,
  "pending_approvals": 0
}
```

`sidecar_reachable` est évalué en live (requête HTTP vers `BROWSER_SIDECAR_WS_URL` avec timeout 2s). Si `BROWSER_DOCKER_SIDECAR=false`, le champ retourne `null`. `pending_approvals` est le nombre d'entrées `browser_action_log` avec `status='pending'`.

---

## 9. Intégration Trust Engine

### Nouvelles policies par défaut

Les trois types d'actions browser sont ajoutés à l'initialisation de `TrustEngine` dans `trust_engine.py` :

```python
DEFAULT_BROWSER_POLICIES = [
    {
        "action_type":           "browser_read",
        "trust_level":           "notify_then_execute",
        "auto_promote_after":    20,    # Promotable vers 'auto' après 20 succès
    },
    {
        "action_type":           "browser_fill",
        "trust_level":           "approval_required",
        "auto_promote_after":    20,    # Promotable vers 'notify_then_execute' après 20 succès
    },
    {
        "action_type":           "browser_submit",
        "trust_level":           "approval_required",
        "auto_promote_after":    0,     # JAMAIS auto-promu — toujours approbation manuelle
    },
]
```

Ces policies sont insérées en base lors de la migration 019 si elles n'existent pas (idempotent).

### Comportement par niveau pour les actions browser

| Niveau | Comportement concret pour BrowserAgent |
|--------|----------------------------------------|
| `auto` | Exécution immédiate, action loguée avec `approved_by='auto'` |
| `notify_then_execute` | Notification envoyée via `TRUST_NOTIFY_CHANNEL`, exécution après 60s sauf cancel. Pendant l'attente, la session est suspendue (asyncio.Event). |
| `approval_required` | Session suspendue, entrée `browser_action_log` avec `status='pending'`. Exécution seulement après `POST /trust/approve/<audit_id>`. Timeout : `BROWSER_MAX_SESSION_S` toujours actif. |
| `blocked` | Action refusée immédiatement, `BrowserActionBlockedError` levée, session terminée. |

### Auto-promotion par domaine — implémentation

Le champ `action_detail` passé à `TrustEngine.check_and_execute()` contient le domaine normalisé de l'URL active, permettant au compteur de succès d'être distinct par domaine :

```python
action_detail = f"{action_type}:{normalized_domain}"
# Ex: "browser_read:playwright.dev", "browser_fill:github.com"
```

Cela crée des lignes distinctes dans `trust_policies` par paire `(action_type, domaine)`. Un domaine peu connu ne bénéficie pas des succès accumulés sur un autre domaine. La table `trust_policies` utilise `action_type` comme `PRIMARY KEY` dans le schéma existant — l'implémentation encode le domaine dans `action_type` via le format `browser_read:playwright.dev`.

---

## 10. Admin UI

Extension de l'onglet "Agents" existant avec un nouveau sous-onglet "Browser", plus un panneau dédié dans l'onglet "Trust Policies".

### Sous-onglet "Browser" (dans l'onglet Agents)

Affiché uniquement si `BROWSER_ENABLED=true` (retourné par `GET /api/browser/status`).

**Bloc "Statut"**
- Badge `enabled` / `disabled`
- Type de navigateur (`chromium`)
- Mode : `sidecar` ou `local`, avec indicateur de connectivité (`sidecar_reachable`)
- Nombre de domaines autorisés, avec avertissement visuel si `all_domains_allowed=true`
- `pending_approvals` : badge rouge si > 0, avec lien vers la file d'approbation

**Bloc "Sessions récentes"**
- Tableau paginé : session_id (6 premiers chars), date, durée, nombre d'actions, statut (badge coloré)
- Clic sur une session → expand avec la liste des actions de la session (depuis `browser_action_log`)
- Bouton "Voir le log complet" → scroll vers le bloc "Log des actions"

**Bloc "Log des actions"**
- Tableau paginé avec filtres : `action_type`, `status`, `trust_level`
- Colonnes : date, session, action, URL (tronquée à 60 chars), sélecteur, statut, trust, durée
- Ligne en rouge si `status='error'` ou `status='blocked'`
- Ligne en orange si `status='pending'` avec bouton "Approuver" inline (→ `POST /trust/approve/<audit_id>`)

**Bloc "Configuration"**
- Affiche les variables d'env actives (lecture seule) : `BROWSER_ALLOWED_DOMAINS`, `BROWSER_PAGE_TIMEOUT_MS`, `BROWSER_MAX_SESSION_S`, `BROWSER_SCREENSHOT_STORE`
- Si `BROWSER_ENABLED=false` : message informatif "Browser Automation désactivé — définir `BROWSER_ENABLED=true` pour activer"

### Panneau dans l'onglet "Trust Policies"

Les trois policies `browser_read`, `browser_fill`, `browser_submit` sont affichées dans la table existante. `browser_submit` apparaît avec le badge `🔒 Jamais promu` dans la colonne auto-promotion, et le dropdown de trust level est grisé pour le passage vers `auto` (uniquement `approval_required` autorisé).

---

## 11. Tests

Fichier : `tests/test_browser_agent.py`

| Test | Description |
|------|-------------|
| `test_disabled_flag` | `BROWSER_ENABLED=false` → `run()` retourne `AgentResult(status='disabled')` sans lancer Playwright (mock `assert_not_called`) |
| `test_navigate_valid_url` | Mock `page.goto()`, vérifie retour `NavigateResult` avec `url`, `title`, `status_code` corrects |
| `test_navigate_scheme_validation` | `navigate("file:///etc/passwd")` → `BrowserInvalidSchemeError` levée avant tout appel Playwright |
| `test_navigate_javascript_scheme` | `navigate("javascript:alert(1)")` → rejeté immédiatement |
| `test_domain_allowlist_blocks` | `BROWSER_ALLOWED_DOMAINS="playwright.dev"`, navigation vers `github.com` → `BrowserDomainBlockedError`, entrée log `status='blocked'` |
| `test_domain_allowlist_allows_subdomain` | Allowlist contient `playwright.dev`, navigation vers `api.playwright.dev` → autorisée |
| `test_domain_allowlist_empty_warning` | `BROWSER_ALLOWED_DOMAINS=""` → avertissement loggé au démarrage, navigation autorisée (avec mock logger) |
| `test_extract_text_uses_inner_text` | Vérifie que `extract_text()` appelle `element.inner_text()` et jamais `inner_html()` ou `content()` |
| `test_extract_text_truncation` | Texte de 60 000 chars retourné par le mock → tronqué à 50 000, `truncated=True` |
| `test_extract_text_selector_not_found` | `page.query_selector()` retourne `None` → retourne `ExtractTextResult(text="")` sans erreur |
| `test_fill_trust_routing` | `fill()` déclenche `TrustEngine.check_and_execute("browser_fill", ...)` — vérifier via mock |
| `test_fill_non_fillable_selector` | `fill("div#content", "value")` → `BrowserInvalidSelectorError` |
| `test_submit_always_approval_required` | Mock trust engine, vérifier que `submit()` envoie toujours `browser_submit` indépendamment des succès passés |
| `test_submit_button_detection` | `submit()` sans sélecteur → détecte `[type=submit]` en premier, puis `button[type=submit]` |
| `test_trust_level_routing_read` | `navigate()` et `screenshot()` → `TrustEngine` reçoit `"browser_read"` |
| `test_trust_level_routing_fill` | `fill()` → `TrustEngine` reçoit `"browser_fill"` |
| `test_trust_level_routing_submit` | `submit()` → `TrustEngine` reçoit `"browser_submit"` |
| `test_session_cleanup_on_timeout` | Session dépasse `BROWSER_MAX_SESSION_S` → `context.close()` appelé, log `status='timeout'` inséré |
| `test_session_cleanup_on_error` | Exception Playwright en cours de session → `context.close()` toujours appelé (finally) |
| `test_no_new_tabs` | Mock `context.on("page")` → la nouvelle page est fermée immédiatement, événement loggé |
| `test_no_downloads` | `accept_downloads=False` vérifié dans `_create_browser_context` |
| `test_screenshot_base64_format` | `screenshot()` retourne `ScreenshotResult` avec `b64_png` décodable en PNG valide |
| `test_screenshot_store_qdrant` | `BROWSER_SCREENSHOT_STORE=true` → `_store_screenshot_qdrant()` appelé avec bons champs (mock Qdrant) |
| `test_screenshot_no_store` | `BROWSER_SCREENSHOT_STORE=false` → `_store_screenshot_qdrant()` jamais appelé |
| `test_action_log_insertion` | Après `navigate()` réussie → une entrée dans `browser_action_log` avec `status='ok'`, `trust_level` et `duration_ms` corrects |
| `test_action_log_blocked_insertion` | Domaine bloqué → entrée dans `browser_action_log` avec `status='blocked'` même si action non exécutée |
| `test_password_field_masking` | `fill("input[type=password]", "s3cr3t")` → `browser_action_log.error_msg` et payload ne contiennent pas `"s3cr3t"` |
| `test_network_route_blocking` | Mock Playwright `page.route()`, vérifier que les requêtes XHR vers domaines non-listés déclenchent `route.abort()` |
| `test_api_post_run_200` | `POST /api/browser/run` valide → 200 avec `session_id`, `actions_taken`, `status` |
| `test_api_post_run_disabled_400` | `BROWSER_ENABLED=false` → 400 avec `error="browser_disabled"` |
| `test_api_post_run_domain_blocked_403` | Domaine hors allowlist → 403 avec `error="domain_blocked"` |
| `test_api_get_sessions` | `GET /api/browser/sessions` → liste paginée avec les bons champs |
| `test_api_get_action_log_filter` | `GET /api/browser/action-log?status=ok&action_type=fill` → filtre appliqué |
| `test_api_get_status` | `GET /api/browser/status` → tous les champs présents, `all_domains_allowed` cohérent |
| `test_auto_promotion_browser_read` | 20 succès `browser_read` sur `playwright.dev` → trust level monte à `auto` (mock TrustEngine.promote) |
| `test_no_auto_promotion_browser_submit` | 100 succès `browser_submit` → trust level reste `approval_required`, `auto_promote_after=0` vérifié |

---

## 12. Ordre d'implémentation

1. **Migration** — `migrations/019_browser.py` : table `browser_action_log` + index `session_id`, `started_at`, `status`. Insérer les trois policies par défaut dans `trust_policies` (`browser_read`, `browser_fill`, `browser_submit`).

2. **Infrastructure Docker** — Choisir Option A ou B. Pour l'Option A : ajouter le service `browser` dans `docker-compose.yml` avec l'image `mcr.microsoft.com/playwright/python:v1.44.0-focal`. Vérifier la connectivité depuis le bridge avec `playwright connect ws://browser:8765`.

3. **Dépendance Python** — Ajouter `playwright>=1.44` à `requirements.txt`. Ajouter `playwright install chromium` et `playwright install-deps chromium` dans le Dockerfile (Option B) ou vérifier présence dans l'image sidecar (Option A).

4. **`BrowserAgent` — structure de base** — `src/bridge/agents/browser_agent.py` : classe, dataclasses (`NavigateResult`, `ScreenshotResult`, `ExtractTextResult`, `ActionResult`), chargement env vars, `_create_browser_context()`, `_check_domain_allowlist()`, `_log_action()`, `_get_trust_action_type()`.

5. **`BrowserAgent` — actions `browser_read`** — Implémenter `navigate()`, `screenshot()`, `extract_text()`, `wait_for()`. Tester unitairement avec mocks Playwright async.

6. **`BrowserAgent` — intégration Trust Engine** — Implémenter `_execute_with_trust()` qui délègue à `TrustEngine.check_and_execute()`. Vérifier que `browser_submit` ne peut pas être auto-promu.

7. **`BrowserAgent` — actions `browser_fill` et `browser_submit`** — Implémenter `fill()` avec validation sélecteur, `submit()` avec détection automatique du bouton, masquage du mot de passe dans les logs.

8. **`BrowserAgent` — sécurité réseau** — Implémenter le handler `page.route()` pour bloquer les requêtes XHR/ressources vers domaines non listés. Implémenter le handler `context.on("page")` pour bloquer les nouveaux onglets. Implémenter le handler `context.on("download")` pour bloquer les téléchargements.

9. **`BrowserAgent` — méthode `run()`** — Assemblage complet : décomposition LLM → boucle d'actions → timeout session → construction `AgentResult`. Intégrer `asyncio.timeout()`.

10. **`BrowserAgent` — stockage screenshots Qdrant** — Implémenter `_store_screenshot_qdrant()` : encode base64, calcule vecteur, upsert dans `browser_screenshots` avec TTL 24h. Activer uniquement si `BROWSER_SCREENSHOT_STORE=true`.

11. **`AGENT_REGISTRY`** — Enregistrer `"browser": BrowserAgent(...)` dans `src/bridge/agents/__init__.py`. Conditionnel : instancier uniquement si `BROWSER_ENABLED=true`.

12. **API REST** — `src/bridge/browser_api.py` : endpoints `POST /api/browser/run`, `GET /api/browser/sessions`, `GET /api/browser/action-log`, `GET /api/browser/status`. Mount du router dans `app.py` au startup.

13. **Trust Engine** — Modifier `src/bridge/trust_engine.py` pour insérer les trois policies browser par défaut à l'initialisation (si absentes). Vérifier que `auto_promote_after=0` est bien respecté pour `browser_submit`.

14. **Tests** — `tests/test_browser_agent.py` (liste complète §11). Tous les tests mockent l'API Playwright async — aucun test n'instancie un vrai Chromium.

15. **Admin UI** — Ajouter le sous-onglet "Browser" dans l'onglet "Agents" : bloc statut, sessions récentes, log des actions avec filtres, file d'approbation des actions `pending`. Mettre à jour l'onglet "Trust Policies" pour afficher correctement `browser_submit` comme non-promoable.

---

## Annexe — Format de migration

```python
# migrations/019_browser.py
VERSION = 19

def check(ctx) -> bool:
    """Idempotency guard — retourne True si la migration est déjà appliquée."""
    tables = ctx.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='browser_action_log'"
    ).fetchall()
    return len(tables) > 0

def migrate(ctx):
    ctx.execute("""
        CREATE TABLE IF NOT EXISTS browser_action_log (
            id          TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            action_type TEXT NOT NULL,
            url         TEXT NOT NULL,
            selector    TEXT,
            status      TEXT NOT NULL,
            trust_level TEXT NOT NULL,
            approved_by TEXT,
            started_at  TEXT NOT NULL,
            duration_ms INTEGER,
            error_msg   TEXT
        );
    """)
    ctx.execute(
        "CREATE INDEX IF NOT EXISTS idx_browser_action_log_session_id "
        "ON browser_action_log(session_id);"
    )
    ctx.execute(
        "CREATE INDEX IF NOT EXISTS idx_browser_action_log_started_at "
        "ON browser_action_log(started_at);"
    )
    ctx.execute(
        "CREATE INDEX IF NOT EXISTS idx_browser_action_log_status "
        "ON browser_action_log(status);"
    )
    # Insérer les policies browser par défaut dans trust_policies (idempotent)
    for policy in [
        ("browser_read",   "notify_then_execute", 20),
        ("browser_fill",   "approval_required",   20),
        ("browser_submit", "approval_required",    0),
    ]:
        ctx.execute("""
            INSERT OR IGNORE INTO trust_policies
              (action_type, trust_level, auto_promote_after,
               successful_executions, failed_executions,
               last_promoted_at, updated_at)
            VALUES (?, ?, ?, 0, 0, '', datetime('now'))
        """, policy)
```

---

## Annexe — Avertissement BROWSER_ALLOWED_DOMAINS vide

Ce comportement est intentionnel pour permettre une mise en route rapide en développement, mais il est **dangereux en production**. Avec `BROWSER_ALLOWED_DOMAINS` vide, le LLM peut potentiellement naviguer vers n'importe quel site — y compris des sites qui collectent les informations de session, des formulaires de phishing, ou des services internes accessibles depuis le réseau du serveur.

**Recommandation de production :** toujours définir `BROWSER_ALLOWED_DOMAINS` avec la liste minimale des domaines nécessaires à la tâche. En cas de doute, commencer par une liste restrictive et l'étendre au besoin. Le niveau `approval_required` sur `browser_submit` constitue un filet de sécurité supplémentaire, mais il ne dispense pas de définir l'allowlist.
