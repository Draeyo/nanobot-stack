# Spec : Interface Vocale (STT/TTS) — Sous-projet G

**Date :** 2026-03-24
**Statut :** Spécifié
**Projet :** nanobot-stack
**Scope :** Pipeline de reconnaissance vocale (STT) et synthèse vocale (TTS) self-hosted, intégration dans le pipeline de chat existant, interface microphone dans l'Admin UI, support mobile via WebRTC

---

## 1. Contexte & Objectifs

Nanobot-stack est conçu pour un usage personnel francophone. L'interaction textuelle via Telegram/Discord/WhatsApp couvre la majorité des cas, mais une interface vocale permettrait des interactions mains libres, en particulier sur mobile (voiture, déplacement, cuisine). Ce sous-projet ajoute un pipeline vocal complet, entièrement self-hosted et fonctionnel sur CPU.

**Objectifs :**
- Transcrire un fichier audio (WAV/MP3/OGG/WEBM) ou un flux temps-réel en texte via Whisper — sans aucun appel cloud
- Synthétiser du texte en audio MP3 via Piper TTS, avec voix française (fr_FR-siwis-medium), en moins de 2 secondes pour une réponse typique
- Fermer la boucle vocale complète : audio entrant → STT → pipeline chat existant → LLM → TTS → audio sortant
- Conserver la compatibilité mobile : les endpoints sont conçus pour fonctionner depuis un navigateur mobile via capture WebRTC côté client
- Opt-in explicite via `VOICE_ENABLED` — aucun service Whisper ou Piper n'est démarré si la feature est désactivée
- Aucune donnée audio n'est persistée : traitement en mémoire uniquement, seule la transcription textuelle est conservée dans l'historique de conversation

---

## 2. Architecture

### Nouveau module : `src/bridge/voice_processor.py`

Classe centrale `VoiceProcessor` avec quatre méthodes publiques asynchrones.

```
VoiceProcessor
  ├── transcribe(audio_bytes, mime_type) → TranscriptionResult
  │     ├── Validation : durée <= VOICE_MAX_AUDIO_DURATION_S, format supporté
  │     ├── Backend STT : faster-whisper (lib Python) OU httpx → WHISPER_URL
  │     └── Retourne : TranscriptionResult(text, duration_ms, audio_duration_s, model)
  ├── synthesize(text, voice) → bytes
  │     ├── Backend TTS : httpx → PIPER_URL (service Docker sidecar)
  │     └── Retourne : bytes MP3 prêts à streamer
  ├── voice_chat(audio_bytes, mime_type, session_id) → VoiceChatResult
  │     ├── transcribe() → text
  │     ├── Injecte text dans le pipeline chat existant (app.py _handle_chat)
  │     ├── synthesize(response_text) → audio_bytes
  │     ├── Enregistre la session dans voice_sessions (SQLite)
  │     └── Retourne : VoiceChatResult(transcription, response_text, audio_bytes, latency_ms)
  └── validate_audio(audio_bytes, mime_type) → ValidationResult
        ├── Vérifie MIME type (wav, mp3, ogg, webm)
        ├── Vérifie durée <= VOICE_MAX_AUDIO_DURATION_S
        └── Retourne : ValidationResult(ok, error_reason, duration_s)
```

### Intégration avec le pipeline chat existant

```
VoiceApi (voice_api.py)
  └── POST /api/voice/chat
        ├── VoiceProcessor.transcribe(audio) → text
        ├── app._handle_chat(text, session_id) → response_text   [pipeline existant]
        └── VoiceProcessor.synthesize(response_text) → audio MP3
```

### Services Docker

```
docker-compose.yml
  ├── [nouveau] piper    — Piper TTS HTTP server (CPU, port 5002)
  └── [optionnel] whisper — Whisper.cpp server (CPU, port 9000)
                            (uniquement si STT_BACKEND=whisper_server)
```

### Fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `src/bridge/voice_processor.py` | Créer | `VoiceProcessor` — STT, TTS, boucle complète |
| `src/bridge/voice_api.py` | Créer | API REST `/api/voice/*` + WebSocket `/api/voice/stream` |
| `src/bridge/app.py` | Modifier | Mount `voice_router`, init `VoiceProcessor` au startup |
| `src/bridge/admin_ui.py` | Modifier | Section "Voice" dans l'onglet Chat |
| `migrations/014_voice.py` | Créer | Table `voice_sessions` |
| `docker-compose.yml` | Modifier | Services `piper` (et optionnellement `whisper`) |
| `src/bridge/requirements.txt` | Modifier | Ajouter `faster-whisper>=1.0`, `pydub>=0.25` |
| `tests/test_voice_processor.py` | Créer | Tests unitaires avec mocks STT/TTS |

---

## 3. Modèle de données

### Table `voice_sessions`

```sql
CREATE TABLE voice_sessions (
    id                  TEXT PRIMARY KEY,           -- UUID v4
    started_at          TEXT NOT NULL,              -- ISO 8601 UTC
    audio_duration_s    REAL NOT NULL DEFAULT 0.0,  -- durée audio entrant (secondes)
    transcription_chars INTEGER NOT NULL DEFAULT 0, -- longueur de la transcription STT
    tts_chars           INTEGER NOT NULL DEFAULT 0, -- longueur du texte synthétisé
    model_stt           TEXT NOT NULL,              -- ex: 'whisper-base', 'whisper-small'
    model_tts           TEXT NOT NULL,              -- ex: 'fr_siwis', 'fr_upmc_pierre'
    latency_ms          INTEGER NOT NULL DEFAULT 0, -- latence totale de la boucle (ms)
    status              TEXT NOT NULL DEFAULT 'ok'  -- 'ok' | 'error'
);
```

`id` : UUID généré à l'issue de chaque boucle vocale complète (ou transcription seule). `started_at` est le timestamp de début de traitement (avant STT). `audio_duration_s` est extrait par `pydub` avant traitement — permet de surveiller l'usage. `latency_ms` est la latence totale mesurée côté serveur (de la réception de l'audio au retour de l'audio synthétisé). Aucun champ contenant les données audio brutes ni le texte de transcription complet n'est stocké ici — seuls les méta-données de session le sont, pour limiter l'exposition des données personnelles.

**Index recommandés :**

```sql
CREATE INDEX idx_voice_sessions_started_at ON voice_sessions (started_at);
```

---

## 4. Variables d'environnement

### Activation

| Variable | Défaut | Description |
|----------|--------|-------------|
| `VOICE_ENABLED` | `false` | Opt-in global. Si `false`, tous les endpoints `/api/voice/*` retournent HTTP 503 |

### Backend STT

| Variable | Défaut | Description |
|----------|--------|-------------|
| `STT_BACKEND` | `faster_whisper` | `faster_whisper` (lib Python locale) ou `whisper_server` (sidecar Docker via HTTP) |
| `STT_MODEL` | `whisper-base` | Modèle Whisper chargé (`whisper-base`, `whisper-small`, `whisper-medium`) |
| `STT_LANGUAGE` | `fr` | Langue de transcription (code ISO 639-1) — `None` pour auto-détection |
| `WHISPER_URL` | `http://whisper:9000` | URL du sidecar whisper-server (ignoré si `STT_BACKEND=faster_whisper`) |
| `STT_DEVICE` | `cpu` | Device pour faster-whisper : `cpu` ou `cuda` |
| `STT_COMPUTE_TYPE` | `int8` | Type de calcul pour faster-whisper : `int8`, `float16`, `float32` |

### Backend TTS

| Variable | Défaut | Description |
|----------|--------|-------------|
| `TTS_MODEL` | `fr_siwis` | Alias de voix Piper (voir mapping dans `voice_processor.py`) |
| `PIPER_URL` | `http://piper:5002` | URL du service Piper TTS Docker |
| `TTS_SPEED` | `1.0` | Vitesse de synthèse (0.5–2.0) |

### Contraintes

| Variable | Défaut | Description |
|----------|--------|-------------|
| `VOICE_MAX_AUDIO_DURATION_S` | `60` | Durée maximale d'un fichier audio entrant (secondes) — HTTP 413 si dépassée |
| `VOICE_MAX_TTS_CHARS` | `2000` | Longueur maximale du texte à synthétiser — troncature au-delà |

---

## 5. Pipeline d'exécution

### Pipeline STT (`transcribe`)

```
VoiceProcessor.transcribe(audio_bytes, mime_type)
  1. validate_audio(audio_bytes, mime_type)
       a. Vérifier MIME type dans {'audio/wav','audio/mpeg','audio/ogg','audio/webm'}
          → HTTP 415 si non supporté
       b. Charger via pydub.AudioSegment.from_file(BytesIO(audio_bytes))
       c. Calculer durée en secondes
       d. Si durée > VOICE_MAX_AUDIO_DURATION_S → HTTP 413
          Payload : {"error": "audio_too_long", "max_s": N, "received_s": M}
  2. Convertir en WAV 16kHz mono (format attendu par Whisper)
       pydub : set_frame_rate(16000).set_channels(1).set_sample_width(2)
  3. Selon STT_BACKEND :
       [faster_whisper]
         a. Charger WhisperModel(STT_MODEL, device=STT_DEVICE, compute_type=STT_COMPUTE_TYPE)
            (modèle mis en cache à l'init de VoiceProcessor — un seul chargement)
         b. segments, info = model.transcribe(wav_io, language=STT_LANGUAGE, beam_size=5)
         c. text = " ".join(seg.text for seg in segments).strip()
       [whisper_server]
         a. httpx.AsyncClient.post(WHISPER_URL + "/inference",
              files={"file": ("audio.wav", wav_bytes, "audio/wav")},
              data={"language": STT_LANGUAGE},
              timeout=30)
         b. response.json()["text"]
  4. Retourner TranscriptionResult(
         text=text,
         duration_ms=elapsed_ms,
         audio_duration_s=audio_duration_s,
         model=STT_MODEL
     )
```

### Pipeline TTS (`synthesize`)

```
VoiceProcessor.synthesize(text, voice=TTS_MODEL)
  1. Tronquer text à VOICE_MAX_TTS_CHARS si nécessaire (troncature sur mot entier)
  2. Résoudre l'alias voice → nom de modèle Piper complet
       VOICE_ALIAS_MAP = {
           "fr_siwis":        "fr_FR-siwis-medium",
           "fr_siwis_low":    "fr_FR-siwis-low",
           "fr_upmc_pierre":  "fr_FR-upmc_pierre-medium",
       }
  3. httpx.AsyncClient.post(PIPER_URL + "/api/tts",
         json={"text": text, "voice": piper_voice, "speed": TTS_SPEED},
         timeout=30)
     → Piper retourne audio/mp3 en streaming
  4. Collecter bytes complets
  5. Retourner bytes MP3
```

### Pipeline boucle complète (`voice_chat`)

```
VoiceProcessor.voice_chat(audio_bytes, mime_type, session_id)
  t0 = time.monotonic()
  1. transcribe(audio_bytes, mime_type)
       → TranscriptionResult (texte + durée audio)
  2. Appeler le pipeline chat existant avec le texte transcrit
       response_text = await app._handle_chat(
           message=transcription.text,
           session_id=session_id,
           source="voice"
       )
  3. synthesize(response_text)
       → audio_bytes MP3
  4. latency_ms = int((time.monotonic() - t0) * 1000)
  5. Insérer dans voice_sessions (SQLite) :
       id=uuid4(), started_at=now, audio_duration_s=transcription.audio_duration_s,
       transcription_chars=len(transcription.text), tts_chars=len(response_text),
       model_stt=STT_MODEL, model_tts=TTS_MODEL, latency_ms=latency_ms, status='ok'
  6. Retourner VoiceChatResult(
         transcription=transcription.text,
         response_text=response_text,
         audio_bytes=audio_bytes_mp3,
         latency_ms=latency_ms
     )
```

### Pipeline WebSocket temps-réel (`/api/voice/stream`) — Stretch goal

```
WebSocket /api/voice/stream
  1. Handshake + auth (token dans query param ?token=...)
  2. Boucle :
       a. Recevoir chunk audio binaire depuis le client (WebRTC → chunks WEBM/OGG)
       b. Accumuler dans un buffer jusqu'à silence détecté (VAD)
          OU jusqu'à message de contrôle {"action": "end_utterance"}
       c. transcribe(buffer_bytes, "audio/webm")
       d. Envoyer message JSON : {"type": "transcription", "text": "..."}
       e. Appeler pipeline chat → response_text
       f. Envoyer message JSON : {"type": "response_text", "text": "..."}
       g. synthesize(response_text) → mp3_bytes
       h. Envoyer message JSON : {"type": "audio_ready"}
       i. Envoyer message binaire : mp3_bytes (chunks de 4096 octets)
  3. Fermeture propre sur déconnexion client
```

Le WebSocket est exposé uniquement si `VOICE_ENABLED=true`. En cas d'erreur STT ou TTS, un message `{"type": "error", "code": "...", "message": "..."}` est envoyé avant fermeture.

---

## 6. Sécurité

- **Données audio non persistées** : les `audio_bytes` entrants ne sont jamais écrits sur disque. Tout traitement se fait en mémoire (`BytesIO`). Seules les méta-données de session (durée, nombre de caractères, latence) sont stockées dans SQLite.
- **Transcription dans l'historique uniquement** : le texte transcrit est injecté dans le pipeline chat et stocké dans l'historique de conversation existant, comme n'importe quel message texte. Il n'existe pas de table dédiée au stockage des transcriptions.
- **Opt-in strict** : `VOICE_ENABLED=false` par défaut. Les services Docker Piper et Whisper ne sont démarrés que si `VOICE_ENABLED=true` (contrôle via condition `profiles` dans `docker-compose.yml` ou variable d'environnement Docker).
- **Validation des formats** : seuls les MIME types `audio/wav`, `audio/mpeg`, `audio/ogg`, `audio/webm` sont acceptés. Les autres retournent HTTP 415. `pydub` valide structurellement le fichier avant tout traitement.
- **Limite de durée** : `VOICE_MAX_AUDIO_DURATION_S` (défaut 60s) protège contre les attaques par saturation mémoire (fichiers audio très longs). La vérification est effectuée avant tout appel STT.
- **Isolation réseau** : `WHISPER_URL` et `PIPER_URL` sont des URLs internes Docker (réseau bridge privé). Les services Whisper et Piper ne sont pas exposés sur l'hôte.
- **Authentification** : les endpoints `/api/voice/*` sont protégés par le même middleware `BRIDGE_TOKEN` que le reste de l'API. Le WebSocket exige le token en query param (`?token=...`) car les headers WebSocket ne sont pas supportés par tous les navigateurs mobiles.
- **PII** : le texte transcrit passe par le filtre PII existant (`pii_filter.py`) avant d'être stocké dans l'historique de conversation, conformément au comportement standard du pipeline chat.

---

## 7. Dépendances Python / Docker

### Dépendances Python (`requirements.txt`)

```
faster-whisper>=1.0
pydub>=0.25
```

`httpx` est déjà présent dans le projet. `faster-whisper` inclut les bindings CTranslate2 — compatible CPU sans GPU.

**Note sur `ffmpeg` :** `pydub` nécessite `ffmpeg` installé sur le système (pour décoder MP3/OGG/WEBM). Ajouter dans le `Dockerfile` du bridge :

```dockerfile
RUN apt-get update && apt-get install -y ffmpeg --no-install-recommends && rm -rf /var/lib/apt/lists/*
```

### Services Docker (`docker-compose.yml`)

**Service `piper` (requis pour TTS) :**

```yaml
piper:
  image: rhasspy/wyoming-piper:latest
  restart: unless-stopped
  volumes:
    - piper_voices:/data
  environment:
    - PIPER_VOICE=fr_FR-siwis-medium
  ports:
    - "5002:5002"
  networks:
    - nanobot_internal
  profiles:
    - voice
```

**Service `whisper` (optionnel — uniquement si `STT_BACKEND=whisper_server`) :**

```yaml
whisper:
  image: onerahmet/openai-whisper-asr-webservice:latest-cpu
  restart: unless-stopped
  environment:
    - ASR_MODEL=base
    - ASR_ENGINE=faster_whisper
  ports:
    - "9000:9000"
  networks:
    - nanobot_internal
  profiles:
    - voice
    - whisper_server
```

Le profil `voice` est activé en ajoutant `COMPOSE_PROFILES=voice` dans le `.env`. Le backend `faster_whisper` (par défaut) ne nécessite pas le service `whisper` — le modèle est chargé directement dans le processus bridge.

**Volume Piper :**

```yaml
volumes:
  piper_voices:
```

Les voix Piper sont téléchargées automatiquement au premier démarrage du conteneur et mises en cache dans le volume `piper_voices`.

---

## 8. API REST

Préfixe : `/api/voice`

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| POST | `/transcribe` | Upload audio → retourne `{"text": "...", "duration_ms": N, "audio_duration_s": F}` |
| POST | `/synthesize` | Body `{"text": "...", "voice": "fr_siwis"}` → retourne stream `audio/mp3` |
| POST | `/chat` | Upload audio → pipeline complet → retourne stream `audio/mp3` + headers metadata |
| GET | `/status` | Retourne `{"enabled": bool, "stt_backend": "...", "stt_model": "...", "tts_model": "...", "piper_ok": bool, "whisper_ok": bool}` |
| WebSocket | `/stream` | Boucle temps-réel STT → chat → TTS (stretch goal) |

### Détail des endpoints

**`POST /api/voice/transcribe`**

- Content-Type request : `multipart/form-data` avec champ `file` (audio)
- Champ optionnel `language` (défaut : valeur de `STT_LANGUAGE`)
- Réponse 200 :
  ```json
  {
    "text": "Quelle est la météo aujourd'hui ?",
    "duration_ms": 1240,
    "audio_duration_s": 3.2,
    "model": "whisper-base"
  }
  ```
- Erreurs : 503 si `VOICE_ENABLED=false`, 413 si audio trop long, 415 si format non supporté, 422 si fichier corrompu

**`POST /api/voice/synthesize`**

- Content-Type request : `application/json`
- Body :
  ```json
  {"text": "Il fait beau et ensoleillé avec 22 degrés.", "voice": "fr_siwis"}
  ```
- Réponse 200 : stream `audio/mp3` avec headers :
  ```
  Content-Type: audio/mpeg
  X-TTS-Model: fr_FR-siwis-medium
  X-TTS-Chars: 44
  ```
- Erreurs : 503 si `VOICE_ENABLED=false`, 422 si texte vide, 502 si Piper indisponible

**`POST /api/voice/chat`**

- Content-Type request : `multipart/form-data` avec champ `file` (audio) et champ optionnel `session_id`
- Réponse 200 : stream `audio/mp3` avec headers :
  ```
  Content-Type: audio/mpeg
  X-Transcription: Quelle est la météo aujourd'hui ?
  X-Response-Text: Il fait beau et ensoleillé avec 22 degrés.
  X-Latency-Ms: 1843
  ```
- La transcription et le texte de réponse sont tronqués dans les headers (max 500 chars) pour éviter les headers HTTP trop longs.
- Erreurs : 503 si `VOICE_ENABLED=false`, 413 si audio trop long, 415 si format non supporté

**`GET /api/voice/status`**

- Vérifie la disponibilité de Piper (GET `PIPER_URL/health`) et de Whisper si `STT_BACKEND=whisper_server` (GET `WHISPER_URL/health`)
- Réponse 200 :
  ```json
  {
    "enabled": true,
    "stt_backend": "faster_whisper",
    "stt_model": "whisper-base",
    "stt_language": "fr",
    "tts_model": "fr_siwis",
    "piper_ok": true,
    "whisper_ok": null
  }
  ```
  `whisper_ok` est `null` si `STT_BACKEND=faster_whisper` (le sidecar n'est pas utilisé).

---

## 9. Admin UI

Extension de l'onglet "Chat" existant — pas de nouvel onglet.

### Bloc "Interface Vocale"

Affiché en bas de l'onglet Chat, visible uniquement si `VOICE_ENABLED=true` (vérifié via `GET /api/voice/status`).

**Contrôles principaux :**

- **Bouton microphone** — Lance l'enregistrement audio via `navigator.mediaDevices.getUserMedia({audio: true})`. L'enregistrement s'arrête sur second clic ou après silence prolongé (VAD basique : niveau sonore < seuil pendant 1.5s)
- **Indicateur d'enregistrement** — Visualiseur de niveau audio (barre animée) pendant l'enregistrement
- **Lecture de réponse** — Lecteur audio HTML5 (`<audio controls>`) affiché après réception de la réponse MP3
- **Transcription affichée** — Texte transcrit affiché sous le microphone pour confirmation avant envoi (avec bouton "Corriger" pour éditer manuellement)
- **Sélecteur de voix** — `<select>` avec les voix disponibles : `fr_siwis (naturelle)`, `fr_siwis_low (rapide)`, `fr_upmc_pierre`

**Flux UX complet :**

```
[Clic microphone] → [Enregistrement en cours — affiche visualiseur]
  → [Clic stop / silence détecté]
  → [Envoi POST /api/voice/chat multipart]
  → [Affiche "Transcription : ..."]
  → [Réception audio MP3]
  → [Lecture automatique de la réponse audio]
  → [Affiche réponse texte dans le fil de chat]
```

**Paramètres de configuration :**

Panneau "Paramètres Voix" (collapsible, Alpine.js) :
- Voix TTS (sélecteur)
- Langue STT (sélecteur : `fr`, `en`, `auto`)
- Lecture automatique (toggle)
- Bouton "Tester la voix" → `POST /api/voice/synthesize` avec phrase test, lecture immédiate

**Statistiques vocales :**

Tableau récapitulatif en bas du bloc (dernières 24h) :
- Sessions vocales : N
- Durée audio totale : X secondes
- Latence moyenne : Y ms
- Disponibilité Piper : badge vert/rouge

Si `VOICE_ENABLED=false` : message informatif "Interface vocale désactivée — définir `VOICE_ENABLED=true` et redémarrer les services Docker (`piper`)."

### Considérations mobile

Le bouton microphone utilise `MediaRecorder` avec codec `audio/webm;codecs=opus` (supporté Chrome/Firefox mobile). Sur iOS Safari, le fallback est `audio/mp4` (AAC). Le MIME type est détecté dynamiquement côté client et transmis dans le champ `Content-Type` du fichier multipart pour que `VoiceProcessor.validate_audio()` choisisse le bon décodeur `pydub`.

---

## 10. Tests

Fichier : `tests/test_voice_processor.py`

| Test | Description |
|------|-------------|
| `test_transcribe_faster_whisper_mock` | Mock `WhisperModel.transcribe()`, vérifie que `transcribe()` retourne le texte correct et le bon `duration_ms` |
| `test_transcribe_whisper_server_mock` | `STT_BACKEND=whisper_server` — mock `httpx.AsyncClient.post()`, vérifie le payload multipart et la lecture de `response.json()["text"]` |
| `test_transcribe_unsupported_mime` | Fichier avec `Content-Type: video/mp4` → HTTP 415 |
| `test_transcribe_audio_too_long` | Audio de 90s avec `VOICE_MAX_AUDIO_DURATION_S=60` → HTTP 413, payload `{"error": "audio_too_long"}` |
| `test_transcribe_corrupted_file` | `pydub.AudioSegment.from_file()` lève une exception → HTTP 422 |
| `test_synthesize_fr_siwis` | Mock `httpx.AsyncClient.post(PIPER_URL)`, vérifie l'alias `fr_siwis` → `fr_FR-siwis-medium` dans le body JSON |
| `test_synthesize_text_truncation` | Texte de 3000 chars avec `VOICE_MAX_TTS_CHARS=2000` → text tronqué sur mot entier avant envoi à Piper |
| `test_synthesize_piper_unavailable` | Piper retourne HTTP 500 → HTTP 502 avec message d'erreur |
| `test_voice_chat_round_trip` | Mock STT + mock LLM + mock TTS → vérifie `VoiceChatResult` complet (transcription, response_text, audio_bytes non vides, latency_ms > 0) |
| `test_voice_chat_session_stored` | Après `voice_chat()`, vérifier qu'une ligne est insérée dans `voice_sessions` (SQLite mock) avec les bons champs |
| `test_voice_disabled_flag` | `VOICE_ENABLED=false` → tous les endpoints retournent HTTP 503 sans appel STT/TTS |
| `test_audio_conversion_to_16khz_mono` | Audio stéréo 44100Hz → vérifier que le WAV produit avant STT est bien 16000Hz mono |
| `test_pii_filter_on_transcription` | Transcription contenant un numéro de téléphone → pipeline chat reçoit le texte filtré |

---

## 11. Ordre d'implémentation

1. Migration `migrations/014_voice.py` — table `voice_sessions`
2. `voice_processor.py` — `VoiceProcessor.__init__()` + `validate_audio()` + `transcribe()` via `faster-whisper`
3. `voice_processor.py` — `synthesize()` via httpx → Piper
4. `voice_processor.py` — `voice_chat()` — boucle complète + écriture `voice_sessions`
5. `docker-compose.yml` — service `piper` (profil `voice`) + volume `piper_voices`
6. `voice_api.py` — endpoints `POST /transcribe`, `POST /synthesize`, `GET /status`
7. `voice_api.py` — endpoint `POST /chat`
8. `app.py` — mount `voice_router`, init `VoiceProcessor` au startup conditionnel sur `VOICE_ENABLED`
9. Tests (`tests/test_voice_processor.py`)
10. `admin_ui.py` — section "Interface Vocale" dans l'onglet Chat (microphone, lecteur, sélecteur voix)
11. `docker-compose.yml` — service `whisper` optionnel (profil `whisper_server`) pour `STT_BACKEND=whisper_server`
12. `voice_api.py` — WebSocket `/stream` (stretch goal, après validation des endpoints HTTP)
