# Publication — état et étapes restantes

État (2026-07-20) : repo public ✅, CI 3 plateformes verte avec garde-fous
anti-zip-vide ✅, releases GitHub v0.11.1 avec zips vérifiés ✅,
`blender --command extension validate` propre ✅, 49/49 tests ✅.
Restent les étapes qui engagent le nom du mainteneur :

## 1. Rendre le repo public

```sh
gh repo edit mlstr0m/requad --visibility public --accept-visibility-change-consequences
```

## 2. Réactiver la CI automatique

Les Actions sont gratuites sur les repos publics. Dans
`.github/workflows/build-engine.yml`, remettre :

```yaml
on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:
```

Premier run à surveiller : les jobs Windows (flags MSVC vs clang) et
linux (paquets manquants éventuels) n'ont jamais tourné — prévoir une ou
deux itérations de correction.

## 3. Binaires macOS : signature

- Les binaires arm64 sont signés ad hoc par le linker (suffisant pour un
  usage local et pour l'extension installée via Blender).
- Pour une distribution hors extensions.blender.org sans avertissement
  Gatekeeper : compte Apple Developer (99 $/an) + `codesign` avec un
  Developer ID + notarisation (`xcrun notarytool`). À décider.

## 4. Soumission extensions.blender.org

- Compte sur https://extensions.blender.org, soumettre le zip produit par
  `blender --command extension build --split-platforms`.
- Points de vigilance du review Blender :
  - GPL-3.0 ✅ (LICENSE + THIRD_PARTY.md complets)
  - binaires précompilés : la politique demande des builds reproductibles —
    pointer vers `patches/` + le README « Building the engine from source »
  - `blender_manifest.toml` : platforms doit correspondre aux binaires
    réellement embarqués (CI `--split-platforms`)

## 5. Annonce

- BlenderArtists (section Released Scripts and Themes), reddit r/blender,
  BlenderNation. Matériel : les rendus de `docs/` + le tableau de
  benchmarks de `docs/ROADMAP.md`.

## 6. Après publication

- Réactiver les triggers CI (étape 2) si pas déjà fait.
- Créer un tag/release par version (`gh release create vX.Y.Z dist/*.zip`).
- Brancher les issues GitHub comme canal de retours utilisateurs — chaque
  mesh pathologique signalé rejoint `tests/test_robustness.py`.

## 5. Système qualité anti-délistage (en place, 2026-07-20)

Le store délite vite sur bug critique ; les garde-fous mécaniques :

1. **Gate CI** : à chaque push, les zips sont construits, `extension
   validate` passe sur chacun, puis la batterie complète (49 tests :
   26 headless + 12 meshes pathologiques + 11 workflows) tourne contre
   le zip Linux fraîchement construit. Gate rouge = on ne release pas.
2. **Aucune exception ne peut atteindre l'utilisateur** : le handler
   modal est ceinturé — tout imprévu devient un cancel propre avec
   message court + chemin du log moteur (jamais de popup traceback) ;
   le teardown est infaillible (timer/progress toujours nettoyés).
3. **Permissions déclarées** dans le manifest (`files` : export temp +
   exécution du moteur embarqué). Pas de réseau, pas de télémétrie.
4. **Templates d'issue** orientés reproduction (version, mesh joint,
   réglages) pour un triage en minutes.

### Runbook bug critique (objectif : correctif < 24 h)

1. Reproduire avec le mesh de l'issue (ou son log moteur).
2. Écrire le test qui échoue AVANT le fix (il entre dans la batterie).
3. Fixer, gate CI vert, bump patch (0.x.y+1), release GitHub + upload
   de la nouvelle version sur extensions.blender.org.
4. Répondre dans l'issue et dans le thread d'annonce avec la version
   corrigée. Ne jamais minimiser : le changelog dit ce qui était cassé.

## 6. Transparence IA (brouillon à valider par le mainteneur)

La communauté Blender est très sensible au sujet. Position proposée,
à assumer publiquement si la question se pose (et elle se posera — les
commits publics portent un Co-Authored-By) :

> ReQuad est développé par un artiste 3D avec l'assistance intensive
> d'une IA (Claude), sous revue humaine. Chaque comportement livré est
> couvert par une batterie de 49 tests exécutée en CI sur les binaires
> distribués, et les affirmations de qualité sont adossées à un
> benchmark publié, reproductible et auditable (protocole + données
> brutes + harness dans le repo). Le moteur de remeshing lui-même est
> le travail publié des chercheurs de QuadWild/bi-MDF, crédités
> partout. Jugez le code et les mesures, pas l'outil d'écriture.

Points d'appui factuels : méthodo de bench auto-critique (six failles
de NOS propres benchs documentées), tests durcis contre le
non-déterminisme, historique git complet et lisible.
