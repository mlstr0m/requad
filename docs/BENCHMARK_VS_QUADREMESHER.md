# ReQuad 0.8.0 vs Quad Remesher 1.4.1 — A/B mesuré

Premier duel direct (2026-07-20), même machine (Apple Silicon), Blender 5.2,
réglages par défaut des deux outils, cible 3000 quads, orchestration
automatisée (`scratchpad/ab_duel.py`), métriques identiques.

## Résultats bruts

| Cas | Outil | Faces (cible 3000) | Angle dev | Aspect | Singularités | Temps |
|---|---|---|---|---|---|---|
| Suzanne (organique) | **ReQuad** | **2 809 (94 %)** | 8,24° | **1,363** | 2,9 % | 4,5 s |
| | Quad Remesher | 4 196 (140 %) | **7,64°** | 1,690 | **1,8 %** | **1,5 s** |
| Cylindre (CAD) | **ReQuad** | **3 056 (102 %)** | **2,79°** | 2,046 | 0,8 % | 3,5 s |
| | Quad Remesher | 2 702 (90 %) | 3,02° | **1,326** | **0,3 %** | **0,5 s** |
| Statue (sculpt réel) | **ReQuad** | **3 091 (103 %)** | 8,06° | **1,285** | 4,3 % | 4,5 s |
| | Quad Remesher | 5 189 (173 %) | **7,08°** | 1,638 | **2,1 %** | **1,5 s** |

Contrôle à budget égal (ReQuad relancé aux comptes réels de QR) :
Suzanne @4196 → 8,95° / statue @5189 → 10,32° — l'écart d'angles en
organique persiste à budget constant : il vient du placement des
singularités, pas du budget.

## Lecture honnête

**ReQuad gagne** : fidélité au compte demandé (94-103 % partout, vs
90-173 % — QR dépasse de +40 à +73 % en organique avec ses réglages par
défaut), uniformité des quads en organique (aspect 1,29-1,36 vs 1,64-1,69),
angles hard-surface. Plus les exclusivités hors tableau : déterminisme,
cache d'itération, headless, guides Grease Pencil, gratuit/open source.

**Quad Remesher gagne** : lissage des angles en organique (~1 à 1,3°
d'écart, structurel — meilleur placement de singularités), nombre de
singularités (~2× moins), vitesse sur petits meshes (3-7×), layout des
caps du cylindre.

**Verdict** : décision partagée, profils différents — ReQuad =
prévisibilité, contrôle et uniformité ; QR = flow organique plus doux et
vitesse brute. L'écart organique confirme la priorité R&D déjà identifiée
(ROADMAP 9b : optimisation de connectivité / singularités).

Reproduction : `ab_duel.py` (état-machine sur bpy.app.timers, nécessite
les deux addons installés et une session GUI).
