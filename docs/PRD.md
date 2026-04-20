# PRD — Dofus 2.64 Bot (farm métiers + combat)

**Version** : 0.1.0 (MVP)
**Auteur** : product-manager (orchestrator)
**Date** : 2026-04-17
**Statut** : draft — validation utilisateur requise

---

## 1. Problème

Sur un serveur privé Dofus 2.64 (validation écrite du staff obtenue), le propriétaire du compte veut automatiser :
- le **farm de ressources métiers** (bûcheron / paysan), tâche répétitive et chronophage ;
- le **leveling PvM solo** sur zones fixes, qui mobilise plusieurs heures par soir.

L'objectif est de **récupérer du temps de jeu** pour se concentrer sur le contenu endgame (donjons guildes, PvP) pendant que le bot s'occupe du grind logistique.

Le projet n'est **pas** destiné à un serveur officiel Ankama. L'aspect légal/TOS Ankama est hors scope.

---

## 2. Utilisateur cible

**Unique utilisateur** : le propriétaire du compte (mono-compte MVP).

**Profil** :
- Connaît Dofus 2.64 et ses métiers, sait configurer un PC Windows.
- Pas forcément développeur, mais capable de lire un fichier `.env` et lancer un script PowerShell.
- Joue sur un serveur privé où le botting est autorisé.
- Possède un compte Discord et sait créer un bot Discord (optionnel).

---

## 3. Scénarios MVP

### 3.1 Session farm bûcheron

> L'utilisateur lance la GUI, choisit métier = `lumberjack`, zone = `Bonta forêt sud`, clique Start. Il laisse tourner 4h.

**Flow attendu** :
1. Le bot charge la config zone (coords de spawn des arbres, chemin entre maps, banque de référence).
2. State machine entre en `MOVING` → se déplace sur la map de farm.
3. Passe en `SCANNING` → capture d'écran → template matching OpenCV repère les arbres dispo.
4. Passe en `ACTING` → clique sur un arbre, attend l'animation de récolte (détectée visuellement ou via timer), gagne XP métier + ressource.
5. Après N récoltes ou si aucun arbre n'est détecté, re-scanne voire change de map via le pathfinding.
6. `CHECKING_INVENTORY` périodique → si inventaire ≥ 90% plein, passe en `BANKING`.
7. `BANKING` : pathfind vers banque, ouvre NPC, dépose ressources, sort, reprend.
8. Stats temps réel dans la GUI (récoltes, XP/h, kamas estimés/h, runtime).

### 3.2 Session combat PvM solo

> L'utilisateur choisit mode = `combat`, zone = `Champs de Cania`, niveau cible = groupes de tofus. Clique Start.

**Flow** :
1. State machine `EXPLORING` → se déplace, scanne la map pour groupes de monstres matching la whitelist.
2. Agresse un groupe → passe en `COMBAT`.
3. Pendant le combat : détecte le tour (template matching sur barre de tour), lance le build de sorts configuré (script séquentiel simple MVP), attend fin de tour.
4. Fin combat → récupère récompenses XP/drops, retourne en `EXPLORING`.
5. Si PV/PA/PM insuffisants détectés, rentre en ville heal/regen.

### 3.3 Inventaire plein

> Pendant une session farm, l'inventaire atteint le seuil.

**Flow** :
1. Détection via la variable interne (compteur de récoltes) + scan visuel de confirmation (icône inventaire qui rougit, ou panel inventaire ouvert puis compté).
2. Passage `BANKING`. Pathfind vers banque la plus proche de la zone de farm (config).
3. Ouverture banque, dépôt automatique des ressources (filtrage par catégorie).
4. Retour zone de farm, reprise.

### 3.4 Reconnexion / pop-up

> Le jeu crash ou affiche un pop-up modération.

**Flow** :
1. Watchdog détecte absence du process `Dofus.exe` OU fenêtre "Connexion perdue" OU dialog modération.
2. Bot passe en `PAUSED`, notifie Discord avec screenshot.
3. Si crash : tentative de relance du client (config chemin exécutable), reconnexion via credentials `.env` (jamais en clair dans le code, saisis au premier lancement puis chiffrés via keyring Windows).
4. Si pop-up modération / captcha humain : pause indéfinie, attente intervention manuelle de l'utilisateur via commande Discord `/resume`.

---

## 4. Critères de succès (mesurables)

| Critère | Cible MVP | Mesure |
|---|---|---|
| Autonomie farm bûcheron | 3h continues sans intervention humaine sur zone stable | timer runtime dans la DB |
| Autonomie combat PvM | 2h continues sur zone stable | idem |
| Taux de détection arbres (recall) | ≥ 85% | test E2E manuel sur 100 captures labellisées |
| Faux positifs combat (attaque d'un mauvais groupe) | < 5% | logs + review manuelle |
| Temps moyen `BANKING` complet | < 2 min | instrumentation |
| MTTR reconnexion auto crash | < 60 s | logs |
| Consommation RAM bot (hors Dofus) | < 500 Mo | Task Manager sample |
| Crashes du bot / 4h | ≤ 1 | logs error level |

---

## 5. Non-goals (MVP)

- Multi-compte / multi-fenêtre.
- Donjons complexes multi-joueurs.
- PvP.
- Métiers autres que bûcheron + paysan (architecture le permet, mais pas implémentés).
- Trading / hôtel des ventes automatisé.
- Analyse de marché / optimisation économique.
- Mobile / web (desktop Windows uniquement).
- Support serveur officiel Ankama.

---

## 6b. Configuration — approche zéro template

**Avant** : l'utilisateur devait fournir manuellement des templates PNG pour chaque ressource.

**Maintenant** : au premier lancement, le bot entre en état `CALIBRATING` et :
1. Détecte automatiquement les zones UI (HP, PA/PM, minimap, chat) par heuristiques OpenCV.
   L'utilisateur confirme via un dialogue Qt ("C'est bien la barre de vie ?").
2. Sur chaque map farmée, la Phase 2 (optionnelle) détecte les ressources par couleur,
   survole les candidats avec la souris, lit les tooltips OCR et propose "Ajouter Frêne (15) ?".
3. Les ressources confirmées sont sauvegardées en SQLite + templates 32×32 pour les runs suivants.

**Résultat** : aucun fichier template à préparer. Le bot apprend les maps au fur et à mesure.

---

## 6. Risques

| # | Risque | Probabilité | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Template matching fragile si patch graphique | Moyenne | Élevé | Templates versionnés par version client, script de re-calibrage rapide |
| R2 | Détection visuelle fausse au combat (attaque mobs trop forts) | Moyenne | Élevé | Whitelist stricte par niveau + seuil PV min avant engage |
| R3 | Banque déplacée ou UI bancaire changée | Faible | Moyen | Config zones/banques externalisée YAML, rechargeable à chaud |
| R4 | Captcha humain / modération | Moyenne | Moyen | Pause auto + notif Discord, pas de tentative de bypass |
| R5 | Fuite de creds / tokens Discord | Faible | Élevé | `.env` + keyring Windows, `.gitignore` strict, pre-commit git-secrets |
| R6 | Saturation disque par screenshots debug | Moyenne | Faible | Rotation automatique (max 500 Mo, 7 jours) |
| R7 | Input simulation bloquée par anti-cheat serveur privé | Faible | Critique | Serveur privé validé par staff, mais coder humanisation forte (jitter, délais variables) |
| R8 | Dofus.exe en focus perdu pendant clic | Élevée | Moyen | Mode fenêtré + détection active window + refocus auto |

---

## 7. Roadmap

### Now (MVP — 4 à 6 semaines)
- Scaffolding + services de base (vision, input, persistence).
- GUI PyQt6 minimale (start/stop, stats, preview debug).
- 1 métier implémenté end-to-end (bûcheron) + banque.
- Combat script simple (1 classe, 1 build hardcodé).
- Discord bot minimal (start/stop/status).
- SQLite state.
- CI lint + test unit.

### Next (3 mois)
- Métier paysan.
- Pathfinding graph-based multi-maps.
- Combat avec builds multiples configurables YAML.
- Système de quêtes métiers simples.
- Observabilité renforcée (dashboard stats historiques).
- Rechargement hot-reload des configs zones.

### Later (6+ mois)
- Mineur / pêcheur / alchimiste.
- Multi-compte orchestration (plusieurs clients en parallèle).
- Donjons basiques (Tofu Royal, Bouftou Royal).
- OCR avancé pour lire chat et réagir à commandes en jeu.
- Marchand HDV basique.
- Migration éventuelle vers SQLModel + Alembic si schéma croît.

---

## 8. Dépendances externes

- Client Dofus 2.64 installé et fonctionnel.
- Tesseract OCR installé (via installer Windows).
- Python 3.11+.
- Accès Discord Developer Portal (si contrôle Discord activé).
- Compte utilisateur avec droit d'admin local (pour pyautogui / pynput hooks).

---

## 9. Métriques produit à suivre

- Runtime total / semaine.
- XP métier / heure moyenne par métier.
- XP combat / heure par zone.
- Nombre d'erreurs par session.
- Nombre de reconnexions auto.
- Nombre d'interventions manuelles (pause Discord).
