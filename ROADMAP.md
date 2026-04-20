# Roadmap — Dofus Bot

## MVP — Now (4 à 6 semaines)
- [x] Scaffolding projet
- [x] Docs (PRD, architecture, UI, sécurité, déploiement, tests)
- [ ] Services de base : vision, input, persistence
- [ ] State machine + IDLE/MOVING/SCANNING/ACTING basique
- [ ] 1 métier end-to-end : bûcheron
- [ ] Banque : détection + dépôt
- [ ] GUI PyQt6 Dashboard + Debug
- [ ] SQLite schéma v1 + migrations
- [ ] Combat script simple (1 classe / 1 build)
- [ ] Discord bot minimal (start/stop/status)
- [ ] CI : ruff + mypy + pytest

## Next (3 mois)
- [ ] Annotation dataset YOLO (~200 img/classe) + entraînement YOLOv8n
- [ ] Métier paysan
- [ ] Pathfinding graph multi-maps
- [ ] Combat YAML-configurable multi-builds
- [ ] Quêtes métiers simples automatisées
- [ ] Hot-reload configs
- [ ] Onglet Stats historiques avec graphs pyqtgraph
- [ ] Humanisation avancée (courbes Bezier, micro-pauses)
- [ ] Tests E2E mockés (simulateur de frames)
- [ ] Bouton GUI "Recalibrer" (déclenche BotState.CALIBRATING)
- [ ] Active learning : log des détections incertaines dans data/uncertain_crops/

## Later (6+ mois)
- [ ] Mineur / pêcheur / alchimiste
- [ ] Multi-compte orchestration
- [ ] Donjons solos basiques
- [ ] OCR chat en jeu + réactions scripted
- [ ] HDV basique (consultation prix)
- [ ] Migration SQLModel + Alembic si schéma >15 tables
- [ ] Packaging PyInstaller en exe unique
