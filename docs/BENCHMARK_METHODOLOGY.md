# Méthodologie de benchmark — ReQuad vs Quad Remesher

Ce document décrit le protocole du benchmark comparatif et, par transparence,
les **failles d'objectivité découvertes dans nos propres campagnes
précédentes** lors de l'audit de rigueur du 2026-07-20. Les chiffres publiés
avant cet audit sont remplacés par ceux de `bench_rigor_2026-07-20.json`.

## Failles corrigées par l'audit (auto-critique)

1. **Fidélité biaisée par construction.** L'ancienne métrique mesurait la
   distance des *vertices* du résultat à la surface source. Or ReQuad
   reprojette ses vertices sur la source à chaque itération de relaxation :
   le score ~0 était garanti par construction, pas mesuré. Un maillage peut
   avoir tous ses vertices sur la surface et couper les coins entre eux.
   → Nouvelle métrique **bidirectionnelle** : (a) vertices *et centres de
   faces* du résultat → surface source, (b) échantillons de la source →
   surface du résultat (capture rétrécissement et zones non couvertes),
   plus un **p95** (quasi-Hausdorff). Résultat : notre fidélité sphère
   passe de « 0,00 ‰ » à 0,56 ‰ — toujours devant QR (0,86 ‰), mais honnête.
2. **Défaut contre défaut ≠ capacité contre capacité.** QR livre
   `Adaptive Quad Count` activé par défaut : son dépassement de budget
   gonflait à la fois son erreur de compte *et* ses scores d'angles (plus
   de quads = angles plus faciles). → QR est désormais benchmarké dans
   **les deux modes** : défaut (adapt ON) et capacité (`ExactQuadCount=1`).
   Les métriques de qualité ne sont comparées qu'à **budgets appariés**
   (comptes atteints à moins de 15 % d'écart), sinon marquées
   « confondues par le budget ».
3. **Biais de preset.** ReQuad recevait un preset choisi à la main par
   forme (Mechanical/Organic) pendant que QR tournait sans réglage. → Runs
   de contrôle ReQuad en **BASIC intact** sur toutes les formes mécaniques.
4. **Scène polluée.** Les campagnes précédentes tournaient dans une session
   Blender restaurée (`test_file.blend`) : collisions de noms d'objets
   (l'append de la statue retombait sur une copie préexistante) et réglages
   QR persistés dans la scène. Symptôme mesuré : « QR s'effondre sur
   sphere-800 (61 % du compte) » ne se reproduit **pas** en scène vierge
   (96 % du compte). → La campagne démarre par `read_homefile(use_empty)`.
5. **Espace local vs monde.** Les angles étaient calculés en coordonnées
   locales — faussés si un résultat porte une scale non uniforme. → Toutes
   les métriques sont calculées en espace monde.
6. **Un seul run par scénario.** Aucun contrôle de variance. → Sondes de
   déterminisme : 3 répétitions de deux scénarios pour chaque outil.

## Protocole (campagne `bench_rigor.py`)

- **Formes** (9) : sphère UV, tore, Suzanne subdivisée ×2, statue sculptée
  350 k tris, crâne sculpté BlenderKit, cylindre trianglé, cube biseauté
  trianglé, terrain déplacé à bords ouverts, talkie-walkie CAD coque fine.
- **Cibles** : 800 et 3000 quads (3000 seul pour skull/terrain/walkie).
- **Outils** : ReQuad 0.10.0 (preset par catégorie + contrôle BASIC),
  Quad Remesher 1.4.1 (défaut, puis ExactQuadCount). Symétries off partout.
- **Métriques** (espace monde) : faces, % quads, déviation d'angle moyenne
  (|90°−θ|), aspect (côté max/min), % de vertices intérieurs de valence ≠ 4
  + histogramme de valence, fidélité bidirectionnelle en ‰ de la diagonale
  (sortante, entrante, p95), temps mur.
- **Environnement** : Blender 5.2.0, macOS arm64 (M-series), scène vierge,
  62 runs, sauvegarde incrémentale.
- Les maillages résultats des cas clés sont exportés en OBJ pour l'analyse
  structurelle (placement des singularités) — dossier `qr_secrets/`.

## Découverte : le déterminisme de ReQuad était surestimé

Les sondes initiales (3 répétitions intra-session de sphere-800 et
statue-3000) étaient bit-identiques — mais une sonde dédiée sur
sphere-3000 a révélé une **variance réelle du solveur bi-MDF** :
4 runs consécutifs → 2954-3016 faces, 1,46-4,36° d'angle moyen. Le seed
fixé (patch 0004) couvre le RNG des motifs, pas le solveur de
quantification, dont les optima quasi-dégénérés sur les formes très
symétriques dépendent de l'ordre d'itération interne (non contrôlable par
configuration ; les checkpoints de temps ont été mis hors de cause en les
multipliant par 3 sans effet sur la variance). Conséquence sur le
protocole : **les résultats ReQuad sont la médiane de 3 campagnes
complètes** (min-max rapportés) ; les runs Quad Remesher sont bit-stables
(vérifié sur 3 répétitions × 2 scénarios × 2 modes).

## Limites connues, non corrigées

- Le temps « à froid » de ReQuad inclut son overhead export/import (~2 s) ;
  le cache de champ le fait disparaître au 2e run — les deux chiffres sont
  rapportés, aucun n'est caché.
- L'erreur bénigne `mode_set` de QR en fin de run (contexte d'orchestration
  sans objet actif) survient *après* l'import du résultat et n'affecte pas
  les mesures.
- Choix des formes fait par nous ; le jeu inclut délibérément les cas
  historiquement favorables à QR (coque fine, tore, biseaux).
