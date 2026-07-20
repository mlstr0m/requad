> **SUPERSEDED** — cette campagne (2026-07-20, avant l'audit de
> rigueur) contenait des biais méthodologiques documentés dans
> `BENCHMARK_METHODOLOGY.md`. Elle est conservée comme trace de
> l'audit ; les chiffres de référence sont dans
> `BENCHMARK_VS_QUADREMESHER.md`.

# Campagne comparative complète — ReQuad vs Quad Remesher 1.4.1

36 runs (2026-07-20) : 9 formes × cibles 800/3000 × les deux outils, plus
variantes ReQuad (align singularities, relax 20) sur les organiques.
Données brutes : `bench_full_2026-07-20.json`. Nouvelle métrique :
**fidélité** = distance moyenne des vertices du résultat à la surface
source, en ‰ de la diagonale de la bbox.

## Enseignements majeurs (et actions déclenchées)

### 1. Fidélité à la surface : domination ReQuad (invisible jusqu'ici)
ReQuad : 0,00-0,01 ‰ partout (reprojection systématique). QR : 0,07-3,62 ‰
— son lissage rétrécit les résultats (statue 3,62 ‰ !). **À mettre en
avant dans la communication : le résultat ReQuad EST sur la surface.**

### 2. Fidélité au compte : domination ReQuad
92-102 % sur les 18 runs. QR : 61 % (sphère-800) à 279 % (statue-800) —
son compte est une indication, le nôtre un contrat.

### 3. Défaut grave trouvé chez nous → corrigé en 0.8.1
Coque CAD fine (talkie) : 40,0° d'erreur d'angle moyenne, aspect 6,1 —
l'agrandissement adaptatif créait des quads plus grands que l'épaisseur
locale. **Fix : Mechanical = taille uniforme** (12,9°/2,6 après fix), et
effet bonus : l'aspect des caps du cylindre passe de 2,11 à **1,08, devant
QR (1,33)**.

### 4. Écarts restants, chiffrés, avec leur cause
- **Angles organiques** : QR garde ~1° d'avance (7,1-7,6 vs 8,2-10,5) à
  ses budgets, ~2× moins de singularités (statue 2,1 % vs 5,2 %).
  Cause identifiée : placement/nombre de singularités → ROADMAP 9b.
  Les variantes testées (align ON : +0,4° suzanne mais -0,5° statue ;
  relax 20 : gain ~0,3°) ne comblent pas l'écart — c'est bien structurel.
- **Tore** : QR 1,1-2,1° vs nous 2,8-3,5° — sur une surface à flow
  trivial, notre quantizer laisse une distorsion résiduelle. Piste :
  alpha/regularity sur les cas à singularités quasi nulles.
- **Coarse organique** (statue-800) : 17,5 % de singularités (tempête près
  du plancher). Piste : coarse mode dédié (fusion de patches, 9b).
- **Départ à froid petits meshes** : 2,5-5,5 s vs 0,5-1,5 s (overhead
  export/parse/import/relax ≈ 2 s incompressible actuellement). À chaud
  (cache) : 1,5-2,5 s ≈ parité. Piste : moteur in-process (pybind11).
- Talkie post-fix (12,9°) vs QR (9,3°) : le solde est engine-level
  (la coque était dégénérée à TOUTES les valeurs d'alpha en CLI).

### 5. Confirmations
- bevel_cube : aspect 1,04 (vs QR 1,11-1,42), compte exact — le
  hard-surface épais est notre terrain.
- Sphère-800 : QR s'effondre (61 % du compte, 13,45°) — nous 96 %, 3,5°.
- Terrain à bords ouverts : nous devant sur tous les axes sauf le temps.

## Plan d'amélioration priorisé (issu de ces données)

1. ✅ (0.8.1) Mechanical sans adaptatif — fait, validé, 26/26.
2. **Singularités organiques** (9b) : l'unique écart de qualité structurel.
3. **Overhead à froid** : profiler les ~2 s hors moteur ; candidats :
   écriture OBJ, double parse, boucle BVH du relax.
4. **Tore/regularité** : sweep alpha sur les formes à singularités rares.
5. **Coarse organique** : plancher de singularités près du floor.
