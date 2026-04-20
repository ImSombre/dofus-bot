# Testing strategy — Dofus Bot

**Auteur** : qa-tester (orchestrator)
**Date** : 2026-04-17

---

## 1. Pyramide de tests

```
          /\
         /  \    E2E  (5%)  — manual checklist + scripted on real client
        /----\
       /      \  Integration (25%)  — captures fixtures + mocked IO
      /--------\
     /          \ Unit (70%)  — pure logic: state machine, pathfinding, inventory, repos
    /------------\
```

### Unit — pytest
Cibles (toutes pure-Python, aucune IO système) :
- `src/handlers/inventory_manager.py` — seuils, reset, compteurs.
- `src/handlers/state_machine.py` — transitions, guards, callbacks (avec services mockés).
- `src/services/pathfinding.py` — BFS correctness, cas d'erreur.
- `src/services/persistence.py` — schéma, CRUD sessions/events/errors (avec SQLite tmpfile).
- `src/config/settings.py` — validators, parsing `discord_allowed_user_ids`.
- `src/models/*` — validators pydantic.

**Objectif couverture** : ≥ 75% sur handlers + services.

### Integration — pytest + fixtures
- Fixtures d'images PNG dans `tests/fixtures/captures/` pour simuler des captures OpenCV.
- Mock `VisionService.capture` pour retourner ces PNG chargés via PIL/cv2.
- Valider que `LumberjackRunner.tick()` déclenche un clic aux bonnes coords.
- Valider que détection popup → transition PAUSED.

### E2E — manuel
Checklist dans `docs/TESTING.md#checklist-e2e`, exécutée :
- avant chaque release,
- à chaque mise à jour du client Dofus.

---

## 2. Organisation du dossier `tests/`

```
tests/
├── __init__.py
├── conftest.py                     # fixtures communes (tmp_db, persistence, mocks)
├── fixtures/
│   ├── captures/                   # PNG réels pour integration
│   └── templates/                  # templates utilisés par tests
├── unit/
│   ├── test_inventory_manager.py
│   ├── test_pathfinding.py
│   ├── test_persistence.py
│   ├── test_settings.py
│   └── test_state_machine.py
├── integration/
│   └── test_lumberjack_tick.py
└── qt/
    └── test_main_window.py         # @pytest.mark.qt, pytest-qt
```

(Le MVP livre uniquement `conftest.py` + 3 fichiers unit au niveau `tests/`. La
réorganisation en sous-dossiers arrive quand la suite grandit.)

---

## 3. Outils

- **pytest** — runner.
- **pytest-cov** — couverture.
- **pytest-mock** — mocking fluent.
- **pytest-qt** — widgets/signaux Qt (marker `@pytest.mark.qt`).
- **hypothesis** (futur) — property-based pour pathfinding edge cases.

---

## 4. Commandes

```bash
pytest                                # suite complète
pytest -m "not slow and not e2e"      # rapide
pytest tests/test_pathfinding.py -v
pytest --cov=src --cov-report=html    # coverage HTML dans htmlcov/
```

---

## 5. Checklist E2E manuelle (MVP)

### Setup
- [ ] Dofus 2.64 lancé, fenêtré 1920x1080, perso chargé sur la zone de test.
- [ ] `.env` correctement rempli.
- [ ] Templates calibrés pour la résolution courante.

### Farm bûcheron
- [ ] Start → le perso se déplace vers un arbre détecté.
- [ ] Récolte → XP augmente dans la GUI.
- [ ] Après 10 récoltes → le bot change d'arbre / de map.
- [ ] Inventaire plein → le bot part vers la banque.
- [ ] Dépôt → inventaire vide, retour zone, reprise.

### Combat PvM
- [ ] Start en mode combat → le perso agresse un groupe de la whitelist.
- [ ] Barre de tour détectée → rotation de sorts lancée.
- [ ] Fin combat → retour exploration.
- [ ] PV bas → fuite déclenchée.

### Robustesse
- [ ] Pop-up modération simulé → bot pause + notif Discord.
- [ ] Coupure client Dofus → bot tente la reconnexion auto.
- [ ] F5 → start, F6 → stop immédiat, Ctrl+Shift+P → panic stop + screenshot.

### Discord (si activé)
- [ ] `/status` depuis user whitelisté → réponse OK.
- [ ] `/status` depuis user non whitelisté → refus.
- [ ] Spam de commandes → rate limit déclenché.

---

## 6. Critères pour release 0.1.0 (MVP)

- Tests unit verts, couverture ≥ 70%.
- Lint ruff vert.
- Checklist E2E à 100%.
- 3h de run réel sans intervention manuelle en farm bûcheron (critère PRD §4).
- 0 leak de secret dans les logs.
