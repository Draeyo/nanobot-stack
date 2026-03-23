# Spec : Architecture Sub-Agents — Orchestration hiérarchique

**Date :** 2026-03-24
**Statut :** Implémenté ✅
**Projet :** nanobot-stack v10
**Scope :** AgentBase, OrchestratorAgent, OpsAgent, API `/agent/*`

---

## 1. Contexte & Objectifs

Le planner v9 décompose les tâches en étapes séquentielles. Pour les tâches complexes multi-domaines (ex: "diagnostique le serveur et prépare un rapport"), une architecture d'agents hiérarchiques offre une meilleure séparation des responsabilités, un contexte spécialisé par domaine, et une gestion des coûts tokens plus fine.

**Objectifs :**
- Introduire une couche `AgentBase` réutilisable avec interface commune
- Implémenter un `OrchestratorAgent` qui décompose les tâches et délègue aux agents spécialistes
- Implémenter un `OpsAgent` (SRE personnel) spécialisé en diagnostique et maintenance serveur
- Rester rétrocompatible : le planner v9 et les endpoints existants sont inchangés

---

## 2. Architecture

```
agents/
  ├── __init__.py        — AGENT_REGISTRY
  ├── base.py            — AgentBase + AgentResult
  ├── orchestrator.py    — OrchestratorAgent
  └── ops_agent.py       — OpsAgent

Flux :
POST /agent/run (task)
  └── OrchestratorAgent.run(task)
        ├── LLM decompose → [{agent: "self", task: ...}, {agent: "ops", task: ...}]
        ├── Exécuter en séquence (depends_on) ou parallèle
        └── Synthétiser résultats → réponse finale
```

### Relation avec le planner existant

L'orchestrateur traduit ses sous-tâches au format planner existant via `_subtask_to_plan_step()` avant de les passer à `execute_plan_parallel()`. Les endpoints `/plan` et `/execute-plan` sont **inchangés**.

---

## 3. AgentBase (`agents/base.py`)

```python
class AgentBase:
    name: str
    description: str
    tools: list[str]
    trust_overrides: dict
    max_steps: int = 10

    async def run(self, task: str, context: dict) -> AgentResult: ...
    def _build_system_prompt(self, context: dict) -> str: ...
    def _select_tools(self, task: str) -> list[dict]: ...

@dataclass
class AgentResult:
    status: str           # 'completed' | 'needs_approval' | 'failed' | 'delegated'
    output: str
    actions_taken: list
    cost_tokens: int
    sub_results: list
    artifacts: dict
```

---

## 4. OrchestratorAgent (`agents/orchestrator.py`)

**Rôle :** Décomposer les tâches complexes et déléguer aux agents appropriés.

### Prompt de décomposition

```
You are a task orchestrator. Decompose the user's request into sub-tasks.
For each sub-task, assign to an agent:
- "self": handle directly (text generation, memory lookup, factual answers)
- "ops": server/infrastructure tasks (monitoring, diagnostics, maintenance, logs)

Return ONLY JSON:
{
  "goal": "one-line description",
  "subtasks": [
    {"id": 1, "agent": "self", "task": "...", "depends_on": [], "priority": "high|medium|low"}
  ],
  "estimated_cost": "low|medium|high"
}
Keep to 1-5 subtasks. Prefer fewer subtasks.
```

### Pipeline d'exécution

```
OrchestratorAgent.run(task, context)
  1. LLM (adaptive_router "tool_planning") → plan JSON
  2. Trier subtasks par depends_on (topological sort)
  3. Grouper les subtasks sans dépendances → asyncio.gather (exécution parallèle)
  4. Pour chaque subtask :
     - agent="self" → run_chat_task() existant
     - agent="ops" → OpsAgent.run()
  5. Collecter tous les AgentResult
  6. LLM (adaptive_router "general_chat") → synthèse finale
  7. Retourner AgentResult global
```

---

## 5. OpsAgent (`agents/ops_agent.py`)

**Rôle :** SRE personnel — diagnostique serveur, analyse logs, maintenance.

### Commandes diagnostiques additionnelles (read-only, ajoutées à l'allowlist)

```python
EXTRA_DIAGNOSTIC_CMDS = {
    "ss": True,            # sockets
    "ps": ["aux"],         # processus
    "top": ["-bn1"],       # snapshot CPU/RAM
    "netstat": ["-tlnp"],  # ports ouverts
    "lsof": ["-i"],        # fichiers ouverts réseau
    "du": ["-sh"],         # utilisation disque
    "last": True,          # dernières connexions
    "w": True,             # utilisateurs connectés
}
```

### Comportement

1. Consulte `ops_runbooks` (Qdrant) avant toute action
2. Diagnostique d'abord, n'agit qu'ensuite
3. Exécute les actions via le trust engine
4. Escalade vers l'utilisateur pour les actions `approval_required`

### Prompt système

```
You are an Ops/SysAdmin agent — a personal SRE for a self-hosted server.
Capabilities: diagnostic commands, log analysis, maintenance actions.
Before acting: diagnose first → explain → suggest → execute only if trusted.
Consult runbooks (search_memory, collection=ops_runbooks) for known procedures.
```

---

## 6. Agent Registry (`agents/__init__.py`)

```python
AGENT_REGISTRY = {
    "orchestrator": OrchestratorAgent,
    "ops": OpsAgent,
}

def get_agent(name: str) -> AgentBase:
    cls = AGENT_REGISTRY.get(name)
    if not cls:
        raise ValueError(f"Unknown agent: {name}")
    return cls()
```

---

## 7. API REST

Préfixe : `/agent`

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| POST | `/run` | Exécuter une tâche via l'orchestrateur |
| GET | `/status` | Agents disponibles et capacités |
| GET | `/history` | Historique d'exécution (`?limit=20&offset=0`) |

**Body `POST /agent/run` :**

```json
{
  "task": "Vérifie la santé du serveur et donne-moi un rapport",
  "context": {}
}
```

**Réponse :**

```json
{
  "status": "completed",
  "output": "...",
  "subtasks": [...],
  "cost_tokens": 1234,
  "duration_ms": 2500
}
```

---

## 8. Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `AGENT_ORCHESTRATOR_ENABLED` | `false` | Activer le endpoint `/agent/run` |

---

## 9. Rétrocompatibilité

- Les endpoints `/plan`, `/execute-plan`, `/smart-chat` sont **inchangés**
- L'orchestrateur est opt-in via `AGENT_ORCHESTRATOR_ENABLED=true`
- Si désactivé, `POST /agent/run` retourne HTTP 503 avec message explicatif
- L'`OpsAgent` utilise la même allowlist que `elevated_shell.py` + les commandes diagnostiques additionnelles (read-only uniquement)

---

## 10. Tests

- `AgentBase` : interface commune, `AgentResult` dataclass
- `OrchestratorAgent.run()` : décomposition JSON, délégation correcte (self vs ops), synthèse
- `OrchestratorAgent` : dépendances `depends_on` respectées (topological sort)
- `OpsAgent.run()` : consultation runbooks avant action, commandes diagnostiques autorisées
- `OpsAgent` : escalade si trust level `approval_required`
- API `/agent/run` : retour 503 si `AGENT_ORCHESTRATOR_ENABLED=false`
- Intégration : tâche complexe → décomposition 2 subtasks → résultats agrégés
