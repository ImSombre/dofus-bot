# Security — Dofus Bot

**Auteur** : security-expert (orchestrator)
**Date** : 2026-04-17
**Scope** : application locale Windows mono-utilisateur, optionnellement exposée à Discord.

---

## 1. Modèle de menace

| Acteur | Capacité | Motivation |
|---|---|---|
| Utilisateur légitime | Full access machine | Faire fonctionner le bot |
| Co-usager machine | Session Windows partagée | Lire logs / screenshots contenant pseudo, kamas |
| Compromission Discord | Token Discord leak | Prendre contrôle du bot à distance, spam |
| Fuite repo | Push accidentel de `.env` | Leak credentials, token |
| Dépendances PyPI | Supply-chain attack | Exfiltration / RCE |
| Screenshots partagés | Fuite via screenshot posté sur Discord/réseaux | Doxx, info perso |

Hors scope : reverse-engineering du client Dofus, attaques sur le serveur privé.

---

## 2. Secrets

### Stockage
- **.env** local, chemin `./.env`, **jamais** committé.
  - Ajouté à `.gitignore` (`!.env.example` whitelist l'exemple).
- Secrets candidats : `DISCORD_TOKEN`, futurs credentials de compte Dofus.
- Pour les credentials de compte Dofus (si jamais implémentés) : **stockage via `keyring` (Windows Credential Manager)**, pas `.env`. Prompt à la première utilisation.

### Checklist
- [ ] `.gitignore` contient `.env` avant le premier `git add`.
- [ ] Pre-commit hook `git-secrets` ou équivalent configuré (voir §8).
- [ ] Aucun secret dans les logs (filtre loguru sur `discord_token`, etc.).
- [ ] Aucun secret dans les screenshots (si UI affiche un token → reset immédiat).

---

## 3. Validation d'input

### GUI
- Les champs texte (zone, job, seuils) valident via pydantic au niveau `Settings` et `JobConfig`.
- Les chemins (Tesseract, Dofus.exe) sont résolus en `Path.resolve()` et vérifiés `.exists()` avant usage.

### Discord
- Chaque commande est validée :
  - User ID ∈ whitelist (`DISCORD_ALLOWED_USER_IDS`).
  - Guild ID == `DISCORD_GUILD_ID` (si défini).
  - Arguments slash typés côté discord.py (pas d'eval).
- Rate limit : **token bucket** 10 cmds/min/user. Dépassement → reply silencieux + log.
- Pas d'exécution de code arbitraire (pas de `exec`, pas de `eval`, pas d'appel subprocess avec input user).

### YAML configs
- Chargement avec `yaml.safe_load` (jamais `yaml.load`).
- Validation via pydantic (`JobConfig.model_validate`).

---

## 4. Injection

| Vecteur | Risque | Mitigation |
|---|---|---|
| SQL (SQLite) | Injection via `record_event` payload | Utiliser **placeholders paramétrés uniquement** (déjà le cas, jamais de f-string SQL) |
| Commande shell | Pas de `subprocess` user-input dans le MVP | Si ajouté : `shlex.quote`, préférer liste d'args |
| Path traversal | Si un user Discord fournit un chemin | Ne jamais accepter de paths depuis Discord en MVP |
| YAML Bombs | Config externe | `safe_load` + taille max 100 Ko |

---

## 5. Dépendances

### Audit
```powershell
pip-audit --strict
```
Exécuté automatiquement en CI (non-bloquant MVP, bloquant après release 0.2).

### Politique de version
- Versions **pinnées** dans `requirements.txt`.
- Upgrade mensuel : `pip list --outdated` + review changelogs.
- Refuser tout package <1 k stars GitHub sans justification.

### Packages critiques
- `pyautogui`, `pynput` : contrôlent clavier/souris — source de confiance uniquement (PyPI officiel).
- `discord.py` : si remplacé, s'assurer du maintien actif.
- `opencv-python` : préférer au port unofficial `opencv-python-headless` seulement si Qt inutile côté CLI.

---

## 6. Données au repos

| Fichier | Sensibilité | Protection |
|---|---|---|
| `.env` | Haute (token) | Hors repo, NTFS ACL par défaut |
| `data/bot.sqlite3` | Moyenne (pseudo, kamas) | Hors repo |
| `logs/*.log` | Moyenne | Rotation + purge 14 j |
| `screenshots/*.png` | Élevée (UI de jeu, pseudo visible) | Rotation 7 j ; **ne jamais partager** |

Si un screenshot doit être envoyé à Discord (ex: notif erreur) : **flouter automatiquement la zone du chat** (mode debug à coder) pour éviter leaks de pseudos alliés.

---

## 7. Logs — pas de leaks

Loguru filter :

```python
SENSITIVE_KEYS = {"discord_token", "password", "token", "secret"}

def _filter_sensitive(record):
    for k in SENSITIVE_KEYS:
        if k in record["extra"]:
            record["extra"][k] = "***"
    return True
```

- Jamais de `logger.info(settings)` sans exclusion explicite (`model_dump(exclude={"discord_token"})`).
- Niveau DEBUG désactivé en prod.
- Les stack traces peuvent inclure des valeurs de variables locales → niveau ERROR seulement, et screenshot séparé.

---

## 8. Pre-commit hooks recommandés

`.pre-commit-config.yaml` (à ajouter en Phase 2 next) :

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.2
    hooks:
      - id: ruff
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: detect-private-key
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.4
    hooks:
      - id: gitleaks
```

Install : `pip install pre-commit && pre-commit install`.

---

## 9. Réseau

### Sortant
- Discord WSS (api.discord.com, gateway.discord.gg) — seul flux externe.
- Pas d'appel HTTP non documenté. Tout ajout d'URL → review.

### Entrant
- Aucun port ouvert. Le bot ne sert rien.

### Pare-feu
- Règles Windows par défaut suffisantes.
- Si pare-feu tiers : autoriser `python.exe` sortant uniquement.

---

## 10. Sandboxing / permissions OS

- Le bot tourne **hors admin**. Aucun besoin de privilèges élevés.
- Exception : si pyautogui/pynput doivent interagir avec une fenêtre lancée en admin (Dofus), lancer le bot aussi en admin. Éviter si possible.
- Pas de modif registre, pas d'installation de service.

---

## 11. Aspects opérationnels

### Ne pas partager
- Screenshots contenant :
  - Pseudo du perso.
  - Montant de kamas.
  - Noms de guilde / membres.
  - Messages privés visibles.
- Logs bruts (ils contiennent des coords et noms de maps → peuvent identifier un joueur).

### Avant de demander de l'aide publique
- Sanitize : `python scripts/sanitize_logs.py logs/...` (à coder, roadmap) — remplace pseudo par `PLAYER_A`.
- Expurger les screenshots : effacer au moins la zone chat + nom du perso.

### Discord whitelist
- Strictement l'ID de l'utilisateur propriétaire + éventuellement un compte secondaire perso.
- Jamais "tout le serveur" comme whitelist.

---

## 12. Incident response (lightweight)

| Incident | Action immédiate | Suivi |
|---|---|---|
| Token Discord leak | Régénérer token sur Developer Portal + update `.env` | Purge logs du token |
| `.env` pushé | Forcer re-commit `git rm --cached .env` ; si pushé : rotate **tous** secrets | Revoir `.gitignore` + hooks |
| Bannissement serveur privé | Stopper le bot, contacter staff | Post-mortem : pattern détecté ? |
| Dépendance PyPI compromise | Pin à la dernière version safe, rebuild venv | `pip-audit` après fix |
| PC compromis | Changer mdp Dofus, régénérer Discord token, réinstaller OS si doute | — |

---

## 13. Checklist globale pré-release

- [ ] `.gitignore` exclut `.env`, `data/`, `logs/`, `screenshots/`.
- [ ] Aucun secret dans le repo : `gitleaks detect` vert.
- [ ] `pip-audit --strict` vert (ou findings acceptés et documentés).
- [ ] Pas de `yaml.load` (uniquement `safe_load`).
- [ ] Pas de SQL construit par f-string.
- [ ] Logs filtrent les secrets.
- [ ] Rate limit Discord actif et testé.
- [ ] Whitelist Discord documentée et minimale.
- [ ] Tesseract / pyautogui / pynput uniquement depuis PyPI officiel.
