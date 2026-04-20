# Règles Dofus 2.64 — Combat tour par tour

## Grille et déplacement
- Grille isométrique en losanges. Une **case** = 1 unité.
- Distance = nb de cases en ligne droite (pas de diagonale, sauf sorts spéciaux).
- **PM (Points de Mouvement)** : 1 PM = 1 case. Utilisé pour se déplacer.
- **PA (Points d'Action)** : consommés pour lancer un sort. Reset au début de chaque tour.
- **PO (Portée)** : distance max entre le lanceur et la case ciblée.

## Ligne de vue
- Presque tous les sorts à distance nécessitent **ligne de vue** : une ligne droite entre le lanceur et la cible sans obstacle (mur, case bloquée, ennemi).
- Obstacles peuvent être contournés en bougeant, pas en tirant à travers.

## États essentiels
- **Tacle** : si un ennemi adjacent tente de bouger, il perd PA/PM proportionnellement à la Tacle de l'adversaire. Empêche la fuite.
- **Fuite** : stat opposée de la tacle. Permet de bouger malgré un adversaire adjacent.
- **Invisibilité** : cible non ciblable tant qu'invisible (certaines classes).
- **Érosion** : chaque coup réduit durablement les HP max (Eca a de l'érosion passive).

## Priorisation de cibles (heuristique générale)
1. **Cible faible (HP<25%)** : finir pour supprimer un ennemi du tour.
2. **Cible dangereuse** : classes qui infligent beaucoup (Iop, Sram, Cra dos).
3. **Cible isolée** : moins de soutien adverse = plus facile à tuer.
4. **Cible soigneur** : Eniripsa en premier pour casser la healbot chain.

## Règles d'engagement
- **Mélée (1 case)** : risque de tacle, mais dégâts max pour les mélée.
- **Distance (2-8 cases)** : lignes de vue critiques, mobilité = clé.
- **Dos d'ennemi** : certains sorts infligent +50% dans le dos.
- **Diagonale** : 1 case en diagonale = 1.4 case en ligne droite (rare : la plupart des sorts sont en ligne droite).

## Fin de tour
- Cliquer "TERMINER LE TOUR" (bouton jaune-vert en bas-droite) ou appuyer sur `F1` → `Espace` selon config.
- Si tu perds tous tes PA, le tour ne passe PAS automatiquement.
- Si tu attends trop, le timer termine ton tour (typiquement 30-60s).

## Stratégie par archétype
- **Burst (Iop, Cra dommages)** : spam gros sorts, tuer fast.
- **Sustain (Eniripsa, Feca)** : soin/boucliers, attendre la fatigue ennemie.
- **Mobile (Sacrieur, Xelor)** : repositionnement agressif, hit-and-run.
- **Tacle (Eniripsa Agi, Pandawa)** : bloquer l'ennemi sur place.

## Cooldowns typiques
- Sorts ultimes (AoE massive) : 1-3 tours de cooldown.
- Sorts signature (Bond Iop, Flèche Explosive Cra) : parfois cooldown 1 tour.
- Sorts de base (tape mélée) : 0 cooldown, spammables.

## Bonus IA : conseils tactiques généraux
- **Toujours laisser 1 PM en réserve** pour esquiver le tacle si besoin.
- **Ne pas gaspiller gros sort sur cible à 5% HP** : utiliser une tape basique.
- **Se rapprocher d'un allié soigneur** si HP critique.
- **Ne pas rester immobile** sauf si déjà en position optimale.
