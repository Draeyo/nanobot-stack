# Spec : Memory Decay & Boucle de Feedback — Sous-projet H

**Date :** 2026-03-24
**Statut :** Spécifié
**Projet :** nanobot-stack
**Scope :** Dégradation temporelle des souvenirs dans Qdrant et apprentissage par corrections utilisateur pour ajuster le routing LLM

---

## 1. Contexte & Objectifs

### Problème 1 — Croissance non contrôlée de la mémoire

Les collections Qdrant permanentes (`memory_personal`, `conversation_summaries`) grossissent indéfiniment. Chaque conversation, chaque fait mémorisé s'accumule sans filtre. Après quelques mois d'utilisation, les résultats RAG sont pollués par des souvenirs anciens, obsolètes ou contredits par des informations plus récentes. Il n'existe aujourd'hui aucun mécanisme pour oublier ce qui n'est plus pertinent.

### Problème 2 — Les erreurs de routing ne sont pas corrigées

Quand l'`AdaptiveRouter` choisit un mauvais modèle pour un type de requête, l'utilisateur subit la conséquence (réponse de mauvaise qualité, coût excessif) mais le système n'apprend rien. Le `learning_log` du `UserProfile` enregistre des préférences textuelles, mais aucun mécanisme ne traduit un retour utilisateur en ajustement de routing.

### Objectifs

**Feature 1 — Memory Decay :**
- Introduire un `confidence_score` (0.0–1.0) sur chaque point Qdrant des collections permanentes
- Appliquer une décroissance exponentielle en fonction du temps écoulé depuis le dernier accès
- Renforcer automatiquement les souvenirs confirmés par l'usage
- Supprimer les points sous un seuil de confiance minimal, avec audit complet avant suppression
- Exposer un endpoint "oubli explicite" pour que l'utilisateur puisse forcer la suppression d'un souvenir

**Feature 2 — Feedback Loop :**
- Permettre à l'utilisateur de noter chaque réponse (thumbs up / thumbs down) avec commentaire optionnel
- Analyser les retours négatifs récurrents pour identifier les combinaisons modèle × type de requête à pénaliser
- Traduire ces pénalités en ajustements de score dans l'`AdaptiveRouter`
- Mettre à jour le `learning_log` du `UserProfile` quand les préférences de routing évoluent

Les deux features sont **opt-in via variables d'environnement** et n'ont aucun impact si non activées.

---

## 2. Architecture

### Nouveaux modules

```
MemoryDecayManager (src/bridge/memory_decay.py)
  ├── score_point(collection, point_id, payload) → float
  │     └── Calcule confidence = initial * e^(-lambda * jours_depuis_last_access)
  ├── run_decay_scan() → dict
  │     ├── Scan toutes les collections permanentes (memory_personal, conversation_summaries)
  │     ├── Applique decay sur chaque point
  │     ├── Log dans memory_decay_log si score change
  │     └── Supprime les points < MEMORY_DECAY_THRESHOLD (après log d'audit)
  ├── confirm_access(collection, point_id)
  │     └── Remet confidence_score à 1.0, met à jour last_accessed (appelé après retrieval utilisé)
  └── forget(collection, point_id)
        └── Suppression explicite : log reason='explicit_forget' → DELETE Qdrant

FeedbackLearner (src/bridge/feedback_learner.py)
  ├── record_feedback(query_type, model_used, was_helpful, correction_text)
  │     └── Upsert dans table feedback (champs étendus)
  ├── analyze_recent_feedback(window_days) → list[RoutingAdjustment]
  │     ├── Agrège feedback par (query_type, model_id) sur la fenêtre glissante
  │     ├── Si feedback négatif >= FEEDBACK_MIN_SAMPLES → pénalité
  │     └── Si feedback positif dominant → renforcement
  └── apply_adjustments(adjustments) → None
        ├── Upsert dans routing_adjustments
        └── Met à jour UserProfile.learning_log si delta significatif

AdaptiveRouter (src/bridge/adaptive_router.py) — modifié
  └── _compute_score() → intègre routing_adjustments comme facteur multiplicatif
```

### Intégration avec les composants existants

```
app.py → endpoint /chat
  └── Après assemblage réponse :
        └── Si mémoire utilisée (retrieved_points non vide) :
              └── MemoryDecayManager.confirm_access(collection, point_id)  [pour chaque point utilisé]

AdaptiveRouter.route_query()
  └── _compute_score(model, query_type)
        └── score_base * routing_adjustments.get(query_type, model_id, default=1.0)

APScheduler (scheduler_registry.py) — modifié
  └── Job système "Memory Decay Scan" — hebdomadaire (0 3 * * 1 — lundi 3h du matin)
        └── MemoryDecayManager.run_decay_scan()
```

### Fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `src/bridge/memory_decay.py` | Créer | `MemoryDecayManager` — decay scan, confirm, forget |
| `src/bridge/feedback_learner.py` | Créer | `FeedbackLearner` — analyse feedback, ajustements routing |
| `src/bridge/adaptive_router.py` | Modifier | Intégrer `routing_adjustments` dans `_compute_score()` |
| `src/bridge/app.py` | Modifier | Appels `confirm_access()` après retrieval, mount feedback router |
| `src/bridge/scheduler_registry.py` | Modifier | Enregistrer le job hebdomadaire decay scan |
| `src/bridge/admin_ui.py` | Modifier | Section "Santé mémoire" dans l'onglet "Avancé" |
| `migrations/016_memory_decay_feedback.py` | Créer | Tables `memory_decay_log` et `routing_adjustments`, extension `feedback` |
| `tests/test_memory_decay.py` | Créer | Tests unitaires decay math, seuil, audit |
| `tests/test_feedback_learner.py` | Créer | Tests unitaires analyse feedback, ajustements routing |

---

## 3. Modèle de données

### Extension de la table `feedback` (existante dans `rag.db`)

La table `feedback` est étendue via la migration 016. Les colonnes existantes sont conservées telles quelles ; quatre colonnes sont ajoutées :

```sql
ALTER TABLE feedback ADD COLUMN query_type      TEXT DEFAULT NULL;
ALTER TABLE feedback ADD COLUMN model_used      TEXT DEFAULT NULL;
ALTER TABLE feedback ADD COLUMN was_helpful     INTEGER DEFAULT NULL;  -- 1 = oui, 0 = non, NULL = non renseigné
ALTER TABLE feedback ADD COLUMN correction_text TEXT DEFAULT NULL;
```

`query_type` reprend les valeurs définies dans `QueryClassifier` (ex: `"factual"`, `"creative"`, `"code"`, `"analysis"`, `"briefing"`, etc.). `model_used` est l'identifiant LiteLLM du modèle ayant généré la réponse. `was_helpful` est l'indicateur binaire du rating utilisateur. `correction_text` est le commentaire libre optionnel.

### Nouvelle table `memory_decay_log`

```sql
CREATE TABLE memory_decay_log (
    id           TEXT PRIMARY KEY,          -- UUID v4
    collection   TEXT NOT NULL,             -- 'memory_personal' | 'conversation_summaries'
    point_id     TEXT NOT NULL,             -- ID Qdrant du point concerné
    old_score    REAL NOT NULL,             -- confidence_score avant modification
    new_score    REAL NOT NULL,             -- confidence_score après modification (0.0 si suppression)
    reason       TEXT NOT NULL,             -- 'decay' | 'confirm' | 'explicit_forget' | 'threshold_delete'
    created_at   TEXT NOT NULL              -- timestamp ISO 8601 UTC
);

CREATE INDEX idx_memory_decay_log_collection ON memory_decay_log(collection);
CREATE INDEX idx_memory_decay_log_created_at ON memory_decay_log(created_at);
```

`reason='decay'` : score mis à jour par le cron hebdomadaire. `reason='confirm'` : score remis à 1.0 suite à un accès confirmé. `reason='explicit_forget'` : suppression demandée par l'utilisateur. `reason='threshold_delete'` : point supprimé de Qdrant car score < `MEMORY_DECAY_THRESHOLD` (la ligne est le dernier enregistrement avant la disparition du point).

### Nouvelle table `routing_adjustments`

```sql
CREATE TABLE routing_adjustments (
    query_type      TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    adjustment      REAL NOT NULL DEFAULT 1.0,   -- facteur multiplicatif, < 1.0 = pénalité, > 1.0 = bonus
    feedback_count  INTEGER NOT NULL DEFAULT 0,   -- nombre de feedbacks ayant contribué
    updated_at      TEXT NOT NULL,                -- timestamp ISO 8601 UTC
    PRIMARY KEY (query_type, model_id)
);
```

`adjustment` est un facteur multiplicatif appliqué sur le score de base de l'`AdaptiveRouter`. Une pénalité minimale est plafonnée à `0.5` (le modèle ne peut pas être réduit à zéro). Un bonus maximal est plafonné à `1.5`. Ces bornes sont codées en dur pour éviter les dérives d'apprentissage.

### Payload Qdrant — champs ajoutés

Les points des collections `memory_personal` et `conversation_summaries` reçoivent deux nouveaux champs dans leur payload lors de leur première rencontre avec le système de decay (champs absents → valeur par défaut appliquée à la volée, pas de migration batch sur Qdrant) :

| Champ payload | Type | Défaut | Description |
|---------------|------|--------|-------------|
| `confidence_score` | `float` | `1.0` | Score de confiance actuel (0.0–1.0) |
| `last_accessed` | `str` | date de création | Timestamp ISO 8601 UTC du dernier accès confirmé |

---

## 4. Variables d'environnement

### Feature 1 — Memory Decay

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MEMORY_DECAY_ENABLED` | `false` | Activer le système de decay. Si `false`, `MemoryDecayManager` ne fait rien (no-op complet) |
| `MEMORY_DECAY_LAMBDA` | `0.01` | Constante de décroissance λ. Valeur `0.01` → demi-vie ≈ 70 jours |
| `MEMORY_DECAY_THRESHOLD` | `0.1` | Score de confiance minimal. Tout point en dessous est supprimé lors du scan hebdomadaire |
| `MEMORY_DECAY_COLLECTIONS` | `memory_personal,conversation_summaries` | Collections Qdrant ciblées par le decay (liste séparée par virgules) |

**Note sur le choix de λ :** λ = 0.01 signifie qu'un souvenir non accédé depuis 70 jours tombe à 50% de confiance, et depuis 230 jours à 10% (seuil de suppression par défaut). λ = 0.02 donne une demi-vie de ~35 jours pour des profils d'usage plus actifs.

### Feature 2 — Feedback Loop

| Variable | Défaut | Description |
|----------|--------|-------------|
| `FEEDBACK_LEARNING_ENABLED` | `false` | Activer l'apprentissage par feedback. Si `false`, `FeedbackLearner` enregistre les feedbacks mais n'ajuste pas le routing |
| `FEEDBACK_WINDOW_DAYS` | `7` | Fenêtre glissante (en jours) pour l'analyse des feedbacks récents |
| `FEEDBACK_MIN_SAMPLES` | `3` | Nombre minimum de feedbacks négatifs consécutifs sur une combinaison (query_type, model_id) pour déclencher une pénalité |

---

## 5. Pipeline d'exécution

### Pipeline decay (Feature 1)

#### Calcul du score de confiance

```
score_point(collection, point_id, payload)
  1. Lire payload['confidence_score'] (défaut 1.0 si absent)
  2. Lire payload['last_accessed'] (défaut payload['created_at'] si absent)
  3. Calculer jours_écoulés = (now - last_accessed).total_seconds() / 86400
  4. Appliquer formule : confidence = confidence_score * e^(-MEMORY_DECAY_LAMBDA * jours_écoulés)
  5. Retourner max(0.0, min(1.0, confidence))
```

La formule applique le decay sur le score **actuel** (pas sur le score initial). Chaque run hebdomadaire réduit progressivement le score. Un souvenir confirmé (confidence remis à 1.0) repart de zéro dans sa décroissance.

#### Scan hebdomadaire (`run_decay_scan`)

```
MemoryDecayManager.run_decay_scan() → dict
  1. Vérifier MEMORY_DECAY_ENABLED (retourner {"scanned": 0, "updated": 0, "deleted": 0} si false)
  2. Pour chaque collection dans MEMORY_DECAY_COLLECTIONS :
     a. Scroll Qdrant (page par page, batch de 100) — récupérer tous les points avec payload
     b. Pour chaque point :
        i.  Calculer new_score = score_point(collection, point_id, payload)
        ii. Si |new_score - old_score| < 0.001 → skip (pas de mise à jour inutile)
        iii. Si new_score < MEMORY_DECAY_THRESHOLD :
               - Log dans memory_decay_log (reason='threshold_delete', new_score=0.0)
               - DELETE point dans Qdrant
        iv.  Sinon :
               - Upsert payload dans Qdrant : {confidence_score: new_score, last_accessed: last_accessed}
               - Log dans memory_decay_log (reason='decay') si delta > 0.05
  3. Retourner {"scanned": N, "updated": M, "deleted": K, "collections": [...]}
```

Le seuil de log `delta > 0.05` évite de saturer la table `memory_decay_log` avec des microvariations. Seuls les changements significatifs et les suppressions sont tracés.

#### Confirmation d'accès (`confirm_access`)

```
MemoryDecayManager.confirm_access(collection, point_id)
  1. Vérifier MEMORY_DECAY_ENABLED (no-op si false)
  2. Récupérer payload actuel du point Qdrant
  3. Lire old_score = payload.get('confidence_score', 1.0)
  4. Upsert payload : {confidence_score: 1.0, last_accessed: now_iso()}
  5. Log dans memory_decay_log (reason='confirm', old_score=old_score, new_score=1.0)
```

`confirm_access` est appelé dans `app.py` après chaque réponse qui a utilisé un ou plusieurs points Qdrant. Le contexte d'appel est : après construction du prompt final, pour chaque `retrieved_point_id` dans la liste des sources du RAG.

#### Oubli explicite (`forget`)

```
MemoryDecayManager.forget(collection, point_id)
  1. Récupérer payload actuel (pour enregistrer old_score)
  2. Log dans memory_decay_log (reason='explicit_forget', new_score=0.0)
  3. DELETE point dans Qdrant
  4. Retourner {"deleted": true, "collection": collection, "point_id": point_id}
```

### Pipeline feedback (Feature 2)

#### Enregistrement d'un feedback (`record_feedback`)

```
FeedbackLearner.record_feedback(query_id, query_type, model_used, was_helpful, correction_text)
  1. Vérifier existence de query_id dans feedback (table existante)
  2. UPDATE feedback SET
       query_type = query_type,
       model_used = model_used,
       was_helpful = was_helpful,
       correction_text = correction_text
     WHERE id = query_id
  3. Si FEEDBACK_LEARNING_ENABLED : appeler analyze_and_apply() en tâche de fond (asyncio.create_task)
```

#### Analyse et application des ajustements (`analyze_recent_feedback` + `apply_adjustments`)

```
FeedbackLearner.analyze_recent_feedback(window_days) → list[RoutingAdjustment]
  1. Requête SQLite :
       SELECT query_type, model_used,
              COUNT(*) FILTER (WHERE was_helpful=0) AS neg,
              COUNT(*) FILTER (WHERE was_helpful=1) AS pos,
              COUNT(*) AS total
       FROM feedback
       WHERE created_at >= (now - window_days jours)
         AND query_type IS NOT NULL
         AND model_used IS NOT NULL
       GROUP BY query_type, model_used
  2. Pour chaque combinaison (query_type, model_id) :
       a. Si neg >= FEEDBACK_MIN_SAMPLES ET neg > pos :
            → adjustment = max(0.5, current_adjustment - 0.1 * neg)
            → type = 'penalty'
       b. Si pos >= FEEDBACK_MIN_SAMPLES ET pos > neg * 2 :
            → adjustment = min(1.5, current_adjustment + 0.05 * pos)
            → type = 'bonus'
       c. Sinon : skip (pas assez de signal)
  3. Retourner list[RoutingAdjustment(query_type, model_id, adjustment, feedback_count, type)]

FeedbackLearner.apply_adjustments(adjustments)
  1. Pour chaque RoutingAdjustment :
       a. Upsert dans routing_adjustments (query_type, model_id, adjustment, feedback_count, updated_at=now)
       b. Si |delta_adjustment| > 0.1 :
            → UserProfile.learning_log.append({
                "type": "routing_adjustment",
                "query_type": ...,
                "model_id": ...,
                "adjustment": ...,
                "timestamp": now_iso()
              })
  2. Invalider le cache de scores de l'AdaptiveRouter (appel AdaptiveRouter.invalidate_score_cache())
```

#### Intégration dans `AdaptiveRouter._compute_score()`

```python
# Ajout dans _compute_score(model_id, query_type) :
adjustment = self._get_routing_adjustment(query_type, model_id)  # lecture SQLite ou cache
score = score_base * adjustment
```

`_get_routing_adjustment()` lit la table `routing_adjustments` avec un cache mémoire TTL 5 minutes pour éviter les lectures SQLite répétées à chaque requête.

---

## 6. Sécurité & Garde-fous

### Memory Decay

- **Audit obligatoire avant suppression** : tout point supprimé de Qdrant doit avoir une entrée dans `memory_decay_log` avec `reason='threshold_delete'` ou `reason='explicit_forget'`. Aucune suppression ne peut se produire sans trace SQLite.
- **Pas de suppression en masse** : le scan hebdomadaire n'applique pas de suppression batch non contrôlée. Chaque point est évalué individuellement. Si plus de 20% des points d'une collection sont sous le seuil en un seul scan, le job s'arrête et alerte sans supprimer (`"bulk_delete_guard"` → log WARNING + retour d'état d'erreur).
- **Opt-in strict** : `MEMORY_DECAY_ENABLED=false` par défaut. Aucune modification de payload Qdrant, aucune écriture dans `memory_decay_log`, aucun cron actif tant que la variable n'est pas explicitement à `true`.
- **Immutabilité du `memory_decay_log`** : aucun endpoint API ne permet de modifier ou supprimer des entrées de cette table. Elle est en append-only depuis la couche applicative.
- **Collections protégées** : la collection `docs_reference` (documents de référence) n'est jamais incluse dans `MEMORY_DECAY_COLLECTIONS` par défaut. Son contenu est géré manuellement et ne doit pas se dégrader avec le temps.

### Feedback Loop

- **Plafonnement des ajustements** : `adjustment` est borné entre `0.5` et `1.5`. Ces bornes sont des constantes non configurables pour éviter qu'un feedback abusif ou incohérent ne désactive complètement un modèle.
- **Signal minimum requis** : `FEEDBACK_MIN_SAMPLES=3` empêche qu'un seul feedback négatif provoque une pénalité. Le signal doit être répété et cohérent.
- **Fenêtre glissante** : l'analyse porte sur `FEEDBACK_WINDOW_DAYS` jours récents uniquement. Un ancien pattern négatif résolu ne continue pas à pénaliser indéfiniment.
- **Traçabilité** : chaque ajustement appliqué est enregistré dans `routing_adjustments.updated_at` et dans `UserProfile.learning_log` (si delta significatif). L'Admin UI affiche l'historique complet.
- **Pas d'auto-apprentissage sans supervision** : `FEEDBACK_LEARNING_ENABLED=false` par défaut. Les feedbacks sont enregistrés dans tous les cas, mais les ajustements de routing ne sont appliqués qu'avec l'opt-in explicite.

---

## 7. Dépendances Python

Aucune dépendance externe nouvelle. Les deux features utilisent exclusivement des bibliothèques déjà présentes :

```
# Déjà dans requirements.txt — aucun ajout nécessaire
qdrant-client    # scroll, upsert, delete de points Qdrant
litellm          # via AdaptiveRouter existant
fastapi          # endpoints REST
math             # math.exp() pour la formule de decay (stdlib)
```

---

## 8. API REST

### Préfixe : `/api/memory`

| Méthode | Endpoint | Corps / Params | Description |
|---------|----------|----------------|-------------|
| POST | `/decay/run` | — | Déclenche manuellement le scan de decay. Retourne `{"scanned": N, "updated": M, "deleted": K}` |
| GET | `/decay/log` | `?limit=50&offset=0&collection=memory_personal` | Historique paginé de `memory_decay_log` |
| GET | `/decay/preview` | `?collection=memory_personal&limit=20` | Aperçu des points avec score calculé sans appliquer les changements |
| POST | `/forget` | `{"collection": "...", "point_id": "..."}` | Oubli explicite d'un souvenir — log + suppression Qdrant |
| GET | `/health` | — | Statistiques globales : points par collection, score moyen, nombre sous seuil |

### Préfixe : `/api/feedback`

| Méthode | Endpoint | Corps / Params | Description |
|---------|----------|----------------|-------------|
| POST | `/` | `{"query_id": "...", "query_type": "...", "model_used": "...", "was_helpful": true, "correction_text": "..."}` | Soumettre un feedback sur une réponse |
| GET | `/summary` | `?window_days=7` | Statistiques d'apprentissage : feedbacks par modèle/type, ajustements actifs, tendances |
| GET | `/adjustments` | — | Liste complète des `routing_adjustments` actifs avec historique |
| DELETE | `/adjustments/{query_type}/{model_id}` | — | Réinitialiser un ajustement à 1.0 (reset manuel par l'utilisateur) |

**Codes de retour :**
- `POST /api/feedback/` : `201 Created` si enregistré, `404` si `query_id` inconnu, `422` si `query_type` invalide (non reconnu par `QueryClassifier`)
- `POST /api/memory/forget` : `200 OK` avec `{"deleted": true}`, `404` si le point n'existe pas dans Qdrant
- `POST /api/memory/decay/run` : `200 OK` si `MEMORY_DECAY_ENABLED=true`, `503 Service Unavailable` avec message explicatif si `false`

---

## 9. Admin UI

Extension de l'onglet "Avancé" existant (pas de nouvel onglet). Un nouveau bloc "Santé de la mémoire & Apprentissage" est ajouté en bas de l'onglet.

### Bloc "Santé de la mémoire" (Feature 1)

Affiché uniquement si `MEMORY_DECAY_ENABLED=true`. Alimenté par `GET /api/memory/health`.

**Métriques affichées :**
- Points totaux par collection (`memory_personal`, `conversation_summaries`) avec badge coloré
- Score moyen de confiance par collection (barre de progression colorée : vert > 0.7, orange > 0.4, rouge ≤ 0.4)
- Nombre de points sous le seuil de suppression (alerte si > 0)
- Date et résultat du dernier scan (points scannés / mis à jour / supprimés)
- Prochain scan prévu (basé sur le cron hebdomadaire)

**Actions :**
- Bouton "Aperçu du decay" → appelle `GET /api/memory/decay/preview` et affiche un tableau des 20 points avec le score le plus bas
- Bouton "Lancer le scan maintenant" → `POST /api/memory/decay/run` avec confirmation modale ("Cette action peut supprimer des souvenirs de façon irréversible. Continuer ?")
- Tableau "Journal d'audit" → liste paginée depuis `GET /api/memory/decay/log` avec filtre par collection et par raison

Si `MEMORY_DECAY_ENABLED=false` : message informatif "Decay désactivé — définir `MEMORY_DECAY_ENABLED=true` pour activer la gestion de la mémoire."

### Bloc "Apprentissage & Feedback" (Feature 2)

Affiché dans tous les cas (le feedback est enregistré même sans `FEEDBACK_LEARNING_ENABLED`). Alimenté par `GET /api/feedback/summary`.

**Métriques affichées :**
- Feedbacks soumis (total, 7 derniers jours) avec répartition positif / négatif
- Tableau des ajustements de routing actifs : colonnes `type de requête`, `modèle`, `ajustement`, `feedbacks`, `mis à jour le`
  - Ajustements < 1.0 en rouge (pénalité), > 1.0 en vert (bonus), = 1.0 en gris (neutre)
  - Bouton "Réinitialiser" par ligne → `DELETE /api/feedback/adjustments/{query_type}/{model_id}` avec confirmation
- Dernier apprentissage appliqué (timestamp + résumé)

Si `FEEDBACK_LEARNING_ENABLED=false` : avertissement jaune "L'apprentissage automatique est désactivé. Les feedbacks sont enregistrés mais n'influencent pas le routing."

---

## 10. Tests

### Fichier : `tests/test_memory_decay.py`

| Test | Description |
|------|-------------|
| `test_decay_formula_math` | Vérifie que `score_point()` retourne la valeur correcte pour plusieurs couples (score initial, jours écoulés) selon `confidence = score * e^(-λ * jours)` |
| `test_decay_with_zero_days` | Un point accédé aujourd'hui ne perd aucun score (jours = 0 → multiplicateur = 1.0) |
| `test_decay_half_life` | Avec λ = 0.01, un point non accédé depuis 69.3 jours → score ≈ 0.5 × initial (tolérance ± 0.01) |
| `test_threshold_triggers_delete` | Point avec score calculé < MEMORY_DECAY_THRESHOLD → DELETE Qdrant appelé, entrée `memory_decay_log` avec `reason='threshold_delete'` |
| `test_threshold_no_delete_above` | Point avec score calculé > MEMORY_DECAY_THRESHOLD → pas de DELETE Qdrant, juste upsert payload |
| `test_audit_log_created_before_delete` | Vérifier que l'entrée `memory_decay_log` existe avant le DELETE Qdrant (ordre des opérations) |
| `test_confirm_access_resets_score` | `confirm_access()` → payload confidence_score = 1.0, last_accessed = now, entrée log `reason='confirm'` |
| `test_forget_logs_and_deletes` | `forget()` → entrée log `reason='explicit_forget'`, puis DELETE Qdrant |
| `test_bulk_delete_guard` | Mock : 25% des points sous le seuil → run_decay_scan retourne erreur, aucune suppression effectuée |
| `test_disabled_noop` | `MEMORY_DECAY_ENABLED=false` → toutes les méthodes retournent valeurs vides, aucun appel Qdrant, aucune écriture SQLite |
| `test_missing_payload_defaults` | Point sans `confidence_score` ni `last_accessed` → défauts appliqués (1.0 et created_at) sans exception |

### Fichier : `tests/test_feedback_learner.py`

| Test | Description |
|------|-------------|
| `test_record_feedback_updates_table` | `record_feedback()` → champs `query_type`, `model_used`, `was_helpful`, `correction_text` mis à jour dans `feedback` |
| `test_record_feedback_unknown_query_id` | `query_id` inexistant → `ValueError` levée |
| `test_analyze_triggers_penalty_at_min_samples` | 3 feedbacks négatifs pour (query_type="code", model="gpt-4o-mini") dans la fenêtre → pénalité générée |
| `test_analyze_no_penalty_below_min_samples` | 2 feedbacks négatifs seulement → aucun ajustement |
| `test_analyze_triggers_bonus_positive_dominance` | 4 positifs, 1 négatif → bonus généré |
| `test_adjustment_lower_bound` | Pénalités répétées → `adjustment` ne descend jamais sous 0.5 |
| `test_adjustment_upper_bound` | Bonus répétés → `adjustment` ne monte jamais au-dessus de 1.5 |
| `test_apply_adjustments_upsert` | `apply_adjustments()` → upsert correct dans `routing_adjustments`, `updated_at` mis à jour |
| `test_learning_log_updated_on_large_delta` | Delta > 0.1 → `UserProfile.learning_log` reçoit une entrée `routing_adjustment` |
| `test_learning_log_not_updated_small_delta` | Delta ≤ 0.1 → `UserProfile.learning_log` non modifié |
| `test_adaptive_router_uses_adjustment` | Mock `routing_adjustments` avec pénalité 0.7 pour (code, gpt-4o-mini) → `_compute_score()` retourne score_base * 0.7 |
| `test_feedback_window_filter` | Feedback vieux de `window_days + 1` jours → exclu de l'analyse |
| `test_feedback_learning_disabled_no_adjustment` | `FEEDBACK_LEARNING_ENABLED=false` → feedbacks enregistrés, mais `apply_adjustments()` non appelé |

---

## 11. Ordre d'implémentation

1. Migration `migrations/016_memory_decay_feedback.py` — `ALTER TABLE feedback`, création `memory_decay_log` et `routing_adjustments`
2. `memory_decay.py` — `MemoryDecayManager` : `score_point()` + formule de decay
3. `memory_decay.py` — `run_decay_scan()` : scroll Qdrant + bulk_delete_guard + audit log
4. `memory_decay.py` — `confirm_access()` et `forget()`
5. `feedback_learner.py` — `FeedbackLearner` : `record_feedback()` + `analyze_recent_feedback()`
6. `feedback_learner.py` — `apply_adjustments()` + mise à jour `UserProfile.learning_log`
7. `adaptive_router.py` — intégration `routing_adjustments` dans `_compute_score()` avec cache TTL 5 min
8. `app.py` — appels `confirm_access()` après chaque retrieval utilisé dans la réponse
9. `scheduler_registry.py` — enregistrement job hebdomadaire `Memory Decay Scan` (`0 3 * * 1`)
10. `app.py` — mount des routers `/api/memory` et `/api/feedback`
11. Tests `tests/test_memory_decay.py` et `tests/test_feedback_learner.py`
12. `admin_ui.py` — bloc "Santé de la mémoire & Apprentissage" dans l'onglet "Avancé"
