# ReQuad 0.11.0 vs Quad Remesher 1.4.1 — benchmark rigoureux

Protocole complet et auto-critique : `BENCHMARK_METHODOLOGY.md` (l'audit y
liste six failles d'objectivité trouvées dans nos propres campagnes
précédentes, toutes corrigées ici). Données brutes :
`bench_rigor_2026-07-20.json` (QR, bit-stable, 2 modes) et
`bench_v011_median3_2026-07-20.json` (ReQuad, **médiane de 3 campagnes**
complètes — le solveur bi-MDF n'est pas run-déterministe sur les formes
très symétriques). Blender 5.2.0, macOS arm64, 15 scénarios (9 formes ×
cibles 800/3000), réglages par défaut des deux outils, symétries off.

## Verdict à budgets appariés

Comparaison vs QR en mode ExactQuadCount ; les scénarios dont les comptes
atteints divergent de plus de 15 % sont exclus de la comparaison qualité.

| Axe | ReQuad | QR | Égalité |
|---|---|---|---|
| Angles (déviation moyenne à 90°) | **10** | 2 | 2 |
| Aspect des quads | **12** | 2 | 0 |
| Vertices irréguliers | **4** | 3 | 7 |
| Fidélité p95 (bidirectionnelle) | **9** | 6 | 0 |
| Erreur de compte (moyenne / pire) | **3,2 % / 7,7 %** | 7,8 % / 26,8 % | — |

En mode défaut, QR dépasse son budget de +40 à +179 % (moyenne +52 %) —
ses chiffres de qualité « par défaut » sont achetés avec ces faces
supplémentaires : à budget réel, statue 7,08° devient 8,33° (ReQuad
7,09°), suzanne 7,64° devient 8,57° (ReQuad 7,42°).

## Résultats saillants (médiane ReQuad)

- **Tore 3000** : les trois outils émettent la MÊME grille pure de 2862
  quads, 0 singularité — 1,08° partout, mais aspect ReQuad 1,048 vs
  QR 1,203. Le 1,08° est l'optimum théorique de la grille uniforme.
- **Sphère** : 3,55°/3,64° vs QR 5,60°/5,43°, tous axes devant.
- **Cube biseauté 3000** : 0,92° vs 1,30°, aspect 1,033 vs 1,306,
  fidélité 2,8‰ vs 7,0‰ (le lissage QR arrondit les biseaux).
- **Statue 3000** : angles 7,09° vs 8,33°, fidélité p95 3,1‰ vs 8,6‰
  (le rétrécissement QR est réel et mesurable) ; QR garde l'avantage
  singularités (3,7 % vs 4,5 %).
- **Coque CAD fine (talkie) 3000** : le seul outil qui respecte compte
  ET forme. QR-exact : 94,6‰ de fidélité p95 (géométrie détruite) ;
  QR-défaut : correct mais +65 % de faces. ReQuad : 1,3‰ à 104 % du
  compte — angles moins bons (15,5° vs 11,9°), arbitrage assumé.
- **Terrain à bords ouverts** : tous axes devant (8,71° vs 11,52°).

## Ce que Quad Remesher garde (honnêtement)

- **Organique très coarse** (statue-800) : 10,81° / 12,4 % vs nous
  13,10° / 16,0 % — près du plancher structurel de patchs du moteur
  (roadmap 9b : optimisation de connectivité).
- **Adaptivité fine du détail** : à compte égal sur skull/suzanne-800,
  sa densité par vertex suit mieux le détail (fid p95 2,5‰ vs 4,9‰ sur
  skull) ; notre champ de taille est par patch (contrainte moteur).
- **Angles sur coque fine** (au prix de la forme ou du budget, cf. supra).
- **Démarrage à froid** : 0,5-1,5 s vs 2,5-9,5 s (à chaud, cache de
  champ : quasi-parité). Les temps ReQuad du tableau incluent des runs
  à cache chaud pour les formes répétées entre campagnes.

## Améliorations issues de cette campagne (0.11.0)

1. Polissage de convergence gaté par CV d'arêtes + garde-fou de fidélité
   bidirectionnel avec rollback (tore 2,54°→1,08°, bevel 1,65°→0,92°,
   organiques adaptatifs et coques fines protégés par le garde).
2. Suppression des doublets (valence 2 intérieure).
3. Annihilation des paires de singularités 3-5 (Organic) — l'axe
   « vertices irréguliers » passe de 3-4 contre nous à 4-3 pour nous.
