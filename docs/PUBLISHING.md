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
