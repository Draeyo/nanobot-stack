# Spec : Mémoire Procédurale — Apprentissage de workflows répétables

**Date :** 2026-03-24
**Statut :** Implémenté ✅
**Projet :** nanobot-stack v10
**Scope :** Observation des séquences d'actions, détection de patterns par LLM, suggestion et replay de workflows

---

## 1. Contexte & Objectifs

L'assistant exécute souvent les mêmes séquences d'actions à travers des sessions différentes (diagnostic serveur, mise à jour, sauvegarde, etc.). La mémoire procédurale observe ces séquences, détecte les patterns récurrents via LLM, et propose de rejouer le workflow automatiquement quand un trigger similaire est reconnu.

**Objectifs :**
- Enregistrer chaque exécution d'outil sans impact sur les performances (INSERT SQLite rapide, sans LLM)
- Détecter des patterns dans la fenêtre glissante d'actions uniquement si suffisamment de nouvelles actions ont été accumulées (seuil configurable)
- Matcher les workflows appris avec les nouvelles requêtes via similarité sémantique (Qdrant)
- Proposer et rejouer les workflows via le trust engine existant

---

## 2. Architecture

```
ProceduralMemory (procedural_memory.py)
  ├── log_action(session_id, action, params, result_summary)
  │     └── INSERT SQLite action_log (rapide, synchrone)
  ├── detect_patterns()
  │     └── Scan fenêtre 100 dernières actions
  │         → LLM (modèle cheap) → JSON patterns
  │         → Embed trigger_pattern → Qdrant upsert
  ├── match_workflow(query) → WorkflowMatch | None
  │     └── Qdrant cosine search procedural_workflows (threshold 0.85)
  ├── suggest_workflow(query) → str | None
  │     └── match_workflow() + confidence check (>= 0.7)
  └── execute_workflow(workflow_id, context) → AgentResult
        └── Rejoue les étapes via trust engine

Base SQLite : state/procedural_memory.db
Collection Qdrant : procedural_workflows
```

### Points d'intégration

| Fichier | Modification |
|---------|-------------|
| `planner.py` → `execute_step()` | Appel `log_action()` après chaque étape |
| `tools.py` → `run_shell_command()` | Appel `log_action()` après exécution |
| `extensions.py` → `/conversation-hook` | Incrémenter compteur; si >= threshold, lancer `detect_patterns()` en background |
| `extensions.py` → `/context-prefetch` | Appel `match_workflow()` pour suggestions |

---

## 3. Modèle de données

```sql
-- state/procedural_memory.db

CREATE TABLE action_sequences (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_pattern     TEXT NOT NULL UNIQUE,
    trigger_embedding_id TEXT DEFAULT '',
    steps_json          TEXT NOT NULL,       -- JSON: [{action, params, description}]
    frequency           INTEGER DEFAULT 1,
    last_observed       TEXT NOT NULL,
    last_executed       TEXT DEFAULT '',
    success_rate        REAL DEFAULT 1.0,
    confidence          REAL DEFAULT 0.0,
    auto_suggest        INTEGER DEFAULT 0,   -- 1 = proposer automatiquement
    created_at          TEXT NOT NULL
);

CREATE TABLE action_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL,
    action              TEXT NOT NULL,       -- ex: 'run_shell_command', 'search_memory'
    params_json         TEXT NOT NULL,
    result_summary      TEXT DEFAULT '',
    timestamp           TEXT NOT NULL
);
```

### Collection Qdrant `procedural_workflows`

**Payload d'un point :**

```json
{
  "workflow_id": 42,
  "trigger_pattern": "vérifier la santé du serveur",
  "steps_count": 3,
  "frequency": 7,
  "confidence": 0.85,
  "last_observed": "2026-03-24T10:00:00Z"
}
```

Le vecteur est l'embedding du `trigger_pattern`.

---

## 4. Pipeline de détection

### Conditions de déclenchement de `detect_patterns()`

- Exécuté uniquement si `action_log` a >= `PROCEDURAL_DETECT_THRESHOLD` nouvelles actions depuis la dernière détection
- Lancé en tâche de fond (`asyncio.create_task` ou thread) pour ne pas bloquer la réponse
- Fenêtre de scan : 100 dernières actions (`PROCEDURAL_SCAN_WINDOW`)

### Prompt LLM pour détection

Le prompt soumet les 100 dernières actions (condensées) au LLM (modèle cheap via adaptive_router `"classify_query"`) et demande de retourner un JSON des patterns détectés :

```json
{
  "patterns": [
    {
      "trigger_pattern": "description courte du déclencheur",
      "steps": [
        {"action": "run_shell_command", "params": {"cmd": "systemctl status nginx"}, "description": "Vérifier nginx"}
      ]
    }
  ]
}
```

### Après détection

Pour chaque pattern :
1. Si `trigger_pattern` existe déjà dans `action_sequences` → incrémenter `frequency`, mettre à jour `last_observed`
2. Sinon → INSERT + embed `trigger_pattern` + upsert Qdrant

---

## 5. Pipeline de suggestion

```
match_workflow(query)
  1. Embed query
  2. Qdrant search procedural_workflows (cosine >= 0.85, limit=3)
  3. Pour chaque match : récupérer action_sequence depuis SQLite
  4. Retourner le meilleur match avec confidence >= 0.7

suggest_workflow(query) → str | None
  1. match_workflow(query) → match
  2. Si None ou confidence < PROCEDURAL_SUGGEST_CONFIDENCE → None
  3. Formater suggestion :
     "J'ai un workflow appris pour '{{trigger_pattern}}' ({{frequency}}x, confiance {{confidence}}).
      Étapes : {{steps_summary}}. Voulez-vous l'exécuter ?"
```

---

## 6. Pipeline de replay

```
execute_workflow(workflow_id, context)
  1. Charger steps_json depuis action_sequences
  2. Pour chaque étape :
     a. Appeler trust_engine.check_and_execute(step.action, step.params, executor)
     b. Si trust level bloque → arrêter, retourner status='needs_approval'
     c. Enregistrer résultat
  3. Mettre à jour success_rate, last_executed dans action_sequences
  4. Retourner AgentResult(status='completed', actions_taken=[...])
```

---

## 7. Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `PROCEDURAL_MEMORY_ENABLED` | `false` | Activer la mémoire procédurale |
| `PROCEDURAL_DETECT_THRESHOLD` | `10` | Nouvelles actions avant re-détection |
| `PROCEDURAL_SCAN_WINDOW` | `100` | Actions max dans la fenêtre de scan |
| `PROCEDURAL_SUGGEST_CONFIDENCE` | `0.7` | Seuil de confiance pour suggestion |
| `PROCEDURAL_MATCH_THRESHOLD` | `0.85` | Seuil cosine pour Qdrant match |

---

## 8. Sécurité & Contraintes

- Les paramètres `params_json` stockés dans `action_log` sont tronqués à 500 chars pour éviter la fuite de données sensibles (secrets, tokens)
- La détection de patterns est asynchrone et non-bloquante — un crash de `detect_patterns()` ne casse pas la session principale
- Le replay passe par le trust engine — aucune exécution automatique sans policy adéquate
- `auto_suggest=1` doit être activé manuellement par l'admin (pas d'auto-suggestion sans validation)

---

## 9. Tests

- `log_action()` : INSERT rapide, pas d'appel LLM
- `detect_patterns()` : déclenché uniquement si >= threshold nouvelles actions ; résultat parsé et inséré correctement
- `match_workflow()` : mock Qdrant, retour None si confidence < 0.85
- `suggest_workflow()` : retour None si confidence < 0.7 ; format correct si match
- `execute_workflow()` : respect trust level, mise à jour success_rate, comportement si trust bloque
- Intégration : log 15 actions → `detect_patterns()` déclenché automatiquement
