# ReQuad — Roadmap vers le niveau ZRemesher (et au-delà)

Objectif : faire de ReQuad l'auto-retopologie de référence dans Blender,
au niveau de ZRemesher/Quad Remesher sur la qualité, et au-dessus sur
l'intégration (open source, natif Blender, scriptable, transparent).

## Où on en est (mesuré, pas fantasmé)

| Capacité | ZRemesher | Quad Remesher | ReQuad 0.3.0 |
|---|---|---|---|
| Cible de polycount | ✅ | ✅ | ✅ ±8 % (raffinement itératif) |
| 100 % quads | ✅ | ✅ | ✅ (vérifié sur toute la suite de tests) |
| Adaptive Size (courbure) | ✅ continu | ✅ continu | ✅ par patch (granularité plus grossière) |
| Densité peinte | ✅ (masques) | ✅ (vertex colors) | ❌ → Phase 1 |
| Guides de flow | ✅ (curves) | ✅ (materials/hard edges) | ❌ → Phase 1 |
| Symétrie | ✅ | ✅ | ❌ → Phase 2 |
| Arêtes vives (hard-surface) | ⚠️ moyen | ✅ | ✅ (détection + presets) |
| Sculpts très denses (>500k) | ✅ rapide | ✅ | ⚠️ en cours de mesure |
| Coarse extrême (<1k quads) | ✅ | ✅ | ⚠️ plancher structurel, annoncé honnêtement |
| Progression/annulation | ✅ | ✅ | ✅ (%, Esc, non-bloquant) |
| Open source | ❌ | ❌ | ✅ GPL-3.0 |
| Scriptable/batch | ⚠️ | ⚠️ | ✅ headless natif |

Benchmarks actuels (assets réels, machine locale Apple Silicon, sortie
100 % quads partout) :

| Asset | Input | Cible | Obtenu | Temps |
|---|---|---|---|---|
| Statue femme (asset user) | 9k tris | 3 000 | 3 069 (102 %) | ~5 s |
| Statue subdivisée | 147k tris | 3 000 | 3 132 (104 %) | ~10 s |
| Talkie CAD (Mechanical) | 4.8k tris | 3 000 | 2 998 (100 %) | ~5 s |
| Skull 3D Sculpt (BlenderKit) | 176k tris | 5 000 | 5 015 (100,3 %) | ≤60 s |
| **Stylized Girl sculpt** | **1,28M tris** | 8 000 | **7 991 (99,9 %)** | ~4 min 30 |
| Scan photogrammétrie 560k | 560k tris | 5 000 | 5 082 (102 %) | 55 s |

Écart vitesse vs ZRemesher sur le 1M+ : ~5-9× plus lent (ZR ≈ 30-60 s) —
c'est l'item 11 (Phase 3). La précision du compte est en revanche
systématiquement meilleure que ZR/QR (99,9-104 % mesuré).

Robustesse acquise en test : crash segfault sur scan 1,1M diagnostiqué et
corrigé (seuil sharp 0° du preset Organic + boucle de raffinement moteur
non bornée, désormais cappée à 100 tours — patch 0002).

## Phase 1 — Contrôle artistique complet (parité QR)

1. ✅ **Densité peinte** (livré 0.4.0) — vertex colors → multiplicateurs par
   patch via KDTree, convention QR (rouge = 4× plus fin, cyan = 4× plus
   gros). Gate atteint : ratio d'aires 0,23 entre zone peinte et neutre,
   compte global tenu.
2. ✅ **Guides de flow** (livré 0.5.0) — arêtes Mark Sharp / seams UV +
   frontières de matériaux (toggle) → fichier `.sharp` du moteur. Gate
   atteint sur boucles propres (23-32/32 selon le non-déterminisme moteur).
   *Limitation connue : les bandes de guides en zigzag (arêtes marquées ne
   formant pas une polyline propre) sont partiellement simplifiées par le
   pré-remesh — v2 : rééchantillonner les polylines marquées, ou snapping
   post-remesh des vertices sur les guides.*
3. ✅ **Adaptive Quad Count** (livré 0.5.0) — ON : passe unique, qualité
   prioritaire ; OFF : compte imposé par correction itérative.

**→ Phase 1 complète.**

## Phase 2 — Robustesse production

4. ✅ **Symétrie X/Y/Z** (livré 0.4.0) — bisect au plan local, remesh d'une
   moitié, miroir bmesh + soudure. Gate atteint : zéro vertex asymétrique
   (échantillon 418), zéro arête ouverte.
5. **Très haute densité** : pipeline validé de 500k à 3M tris (décimation
   intelligente en pré-passe si nécessaire — c'est ce que fait ZRemesher en
   interne). *Gate : scan 1M tris → résultat propre en < 2 min sans
   intervention.*
6. **Géométrie sale** : non-manifold, trous, composants multiples,
   auto-intersections — réparation ou dégradation gracieuse avec message
   clair. *Gate : suite de 20 meshes pathologiques → 0 crash, 0 sortie vide
   silencieuse.*
6b. **Déterminisme** : le traceur produit des layouts différents d'un run à
   l'autre (mesuré : 2010 vs 2012 quads, et amplitude du paint variant de
   0,29 à 0,70 sur input identique). Trouver et fixer la source (ordre de
   hash, multithreading) pour des résultats reproductibles. *Gate : deux
   runs identiques → même mesh au bit près.*
7. 🟡 **Transfert d'attributs** : UVs livrés (0.5.0, data transfer par
   interpolation au polygone le plus proche — approximatif près des seams
   UV). Restent : vertex groups, shape keys.

## Phase 3 — Dépasser

8. **Coarse extrême** : casser le plancher `~10 quads/patch` — fusion de
   patches (metamesh collapse plus agressif) ou layout grossier calculé sur
   un champ simplifié. C'est LE point où QuadWild est structurellement
   derrière ZRemesher ; c'est aussi le plus risqué (R&D).
9. **Flow rectiligne sur zones plates** : lissage directionnel du champ dans
   les régions à faible courbure (la faiblesse visible sur le talkie).
9b. **Qualité des angles au-delà de la relaxation** : mesuré (0.4.1) — le
   sweep des configs moteur ne laisse aucun gain gratuit (défauts déjà
   optimaux : 7,39° / 1,9 % irréguliers sur la référence), et la relaxation
   tangentielle + reprojection plafonne à ~6-9 % d'amélioration d'angles.
   Le reste vient de la connectivité : optimisation type local-global
   (rectangularisation par quad) puis édition de singularités. C'est le
   chantier « qualité » à plus fort levier identifié à ce jour.
10. **Champ neural en option** : NeurCross/CrossGen-style pour le placement
    des singularités — qualité max, temps de calcul assumé.
11. 🟡 **Vitesse** — largement résolu en 0.5.x : le profilage a montré que
    le goulot était l'export (pas le moteur) ; l'exporteur maison donne
    1,28M faces → 8000 quads en ~19 s (13× plus rapide qu'avant, au niveau
    de ZR/QR), et le **cache du champ** (hash de contenu, livré 0.5.1)
    ramène les re-runs à ~4 s sur le même objet (paliers de densité
    partagés). Restent : moteur in-process (pybind11) et parallélisation
    du field solve pour grappiller encore.

## Phase 4 — Distribution

12. Repo GitHub + CI 4 plateformes (workflow prêt) + binaires signés macOS.
13. Soumission extensions.blender.org (installation en 1 clic dans Blender).
14. Site/docs + comparatifs publics reproductibles (notre suite de bench).

## Principes

- Chaque phase a des *gates* mesurables ; rien n'est « fini » sans test
  automatisé ou benchmark reproductible.
- La suite headless (`tests/test_headless.py`) s'étend à chaque feature.
- Les patchs moteur restent minimaux, documentés, et upstreamables.
