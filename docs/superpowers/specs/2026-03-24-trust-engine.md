# Spec : Trust Engine — Exécution conditionnelle des actions

**Date :** 2026-03-24
**Statut :** Implémenté ✅
**Projet :** nanobot-stack v10
**Scope :** Niveaux de confiance configurables pour chaque type d'action, auto-promotion, audit trail

---

## 1. Contexte & Objectifs

Nanobot-stack peut exécuter des actions à fort impact (commandes shell, web fetch, envoi de notifications). Sans contrôle de confiance, toute action serait soit systématiquement refusée (inutile) soit automatiquement exécutée (risqué). Le Trust Engine introduit une politique par type d'action avec quatre niveaux, configurable et évolutif via auto-promotion.

**Objectifs :**
- Associer un niveau de confiance à chaque type d'action (`shell_read`, `shell_write`, `web_fetch`, `notify`, etc.)
- Permettre l'exécution automatique des actions de confiance élevée, l'approbation manuelle pour les autres
- Auto-promouvoir les actions vers des niveaux de confiance plus élevés après N exécutions réussies consécutives
- Auditer toutes les décisions avec possibilité de rollback

---

## 2. Architecture

```
TrustEngine (trust_engine.py)
  ├── get_trust_level(action_type) → str
  ├── check_and_execute(action_type, action_detail, executor_fn) → Any
  ├── record_outcome(action_type, outcome, rollback_info)
  ├── promote(action_type)
  └── cancel_pending(audit_id)

Base SQLite : state/trust.db
  ├── trust_policies  — niveau par type d'action
  └── trust_audit     — historique de toutes les décisions

API REST : /trust/*  (monté dans app.py)
```

### Fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `src/bridge/trust_engine.py` | Créer | TrustEngine complet |
| `src/bridge/elevated_shell.py` | Modifier | `propose_action()` consulte trust level |
| `src/bridge/tools.py` | Modifier | Wrapper trust sur `run_shell_command()`, `web_fetch()` |
| `src/bridge/app.py` | Modifier | Mount router trust, passer `trust_engine` à `run_chat_task()` |

---

## 3. Modèle de données

```sql
CREATE TABLE trust_policies (
    action_type             TEXT PRIMARY KEY,
    trust_level             TEXT NOT NULL DEFAULT 'approval_required',
    auto_promote_after      INTEGER DEFAULT 0,   -- 0 = désactivé
    successful_executions   INTEGER DEFAULT 0,
    failed_executions       INTEGER DEFAULT 0,
    last_promoted_at        TEXT DEFAULT '',
    updated_at              TEXT NOT NULL
);

CREATE TABLE trust_audit (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type             TEXT NOT NULL,
    action_detail           TEXT NOT NULL,
    trust_level             TEXT NOT NULL,
    outcome                 TEXT NOT NULL,    -- 'executed' | 'approved' | 'cancelled' | 'blocked' | 'pending'
    rollback_info           TEXT DEFAULT '',
    created_at              TEXT NOT NULL
);
```

---

## 4. Niveaux de confiance

| Niveau | Comportement |
|--------|-------------|
| `auto` | Exécution immédiate, notification optionnelle (`TRUST_NOTIFY_CHANNEL`) |
| `notify_then_execute` | Notification envoyée, exécution après 60s sauf `POST /trust/cancel/{audit_id}` |
| `approval_required` | File d'attente (`elevated_actions.db`), exécution après approbation admin |
| `blocked` | Refusé systématiquement, audit loggé |

### Ordre des niveaux (promotion)

```
blocked → approval_required → notify_then_execute → auto
```

---

## 5. Auto-promotion

Après `TRUST_AUTO_PROMOTE_THRESHOLD` exécutions réussies consécutives (sans échec entre elles), le niveau monte d'un cran. Les échecs réinitialisent le compteur `successful_executions` à 0.

**Conditions d'auto-promotion :**
- `successful_executions >= auto_promote_after` (si `auto_promote_after > 0`)
- Dernière exécution réussie (outcome = `executed`)
- Niveau actuel n'est pas déjà `auto`

---

## 6. Types d'actions prédéfinis

| `action_type` | Niveau par défaut | Description |
|--------------|-------------------|-------------|
| `shell_read` | `notify_then_execute` | Commandes shell read-only |
| `shell_write` | `approval_required` | Commandes shell avec effets |
| `web_fetch` | `notify_then_execute` | Requêtes HTTP vers l'extérieur |
| `notify` | `auto` | Envoi de notifications |
| `memory_write` | `auto` | Écriture en mémoire Qdrant |
| `file_read` | `notify_then_execute` | Lecture de fichiers |
| `file_write` | `approval_required` | Écriture de fichiers |
| `code_exec` | `approval_required` | Exécution de code |
| `agent_delegate` | `notify_then_execute` | Délégation à un sous-agent |

---

## 7. API REST

Préfixe : `/trust`

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/policies` | Liste toutes les policies avec compteurs |
| POST | `/policies/{type}` | Modifier le trust level d'un type |
| GET | `/audit` | Historique paginé (`?limit=50&offset=0`) |
| POST | `/promote/{type}` | Promotion manuelle d'un niveau |
| POST | `/cancel/{audit_id}` | Annuler une action `notify_then_execute` en attente |

---

## 8. Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `TRUST_ENGINE_ENABLED` | `true` | Activer le trust engine |
| `TRUST_DEFAULT_LEVEL` | `approval_required` | Niveau pour nouvelles actions inconnues |
| `TRUST_AUTO_PROMOTE_THRESHOLD` | `20` | Exécutions réussies avant auto-promotion |
| `TRUST_NOTIFY_CHANNEL` | _(vide)_ | Webhook pour notifications trust |
| `TRUST_ROLLBACK_WINDOW_HOURS` | `24` | Fenêtre de rollback (heures) |

---

## 9. Sécurité & Contraintes

- Si `TRUST_ENGINE_ENABLED=false`, toutes les actions tombent sur le comportement `approval_required` (comportement v9 inchangé)
- `blocked` est irréversible via l'API (nécessite modification directe de la DB ou flag admin)
- Les notifications `notify_then_execute` utilisent un timer en mémoire — un redémarrage du process annule l'action (safe by default)
- Chaque action est toujours auditée, y compris les actions `blocked`

---

## 10. Tests

- Niveau `auto` → exécution directe sans timer
- Niveau `notify_then_execute` → notification envoyée, exécution après délai, cancel via API
- Niveau `approval_required` → mise en file, exécution bloquée
- Niveau `blocked` → refus immédiat, audit loggé
- Auto-promotion : 20 succès consécutifs → niveau monte ; 1 échec → compteur reset
- `TRUST_ENGINE_ENABLED=false` → comportement `approval_required` pour toutes les actions
