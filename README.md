# ReQuad

**Free, open-source auto-retopology for Blender.** One click turns a triangle
soup — sculpt, scan, CAD export — into a clean, animation-friendly quad mesh.

ReQuad is built on [QuadWild bi-MDF](https://github.com/cgg-bern/quadwild-bimdf),
the state-of-the-art open quad-remeshing pipeline (SIGGRAPH 2021 / SIGGRAPH
Asia 2023), and aims for feature parity with commercial tools like Quad
Remesher — then beyond.

## Features

- **Quad or triangle budget** — ask for N quads (or tris), get it within a
  few percent (iterative re-quantization)
- **Symmetry X/Y/Z** — half remesh + mirror weld, perfectly symmetric topology
- **Adaptive Size** — smaller quads on curved areas, bigger on flat ones
- **Paint Density** — vertex colors drive local quad size (red = finer,
  cyan = coarser)
- **Flow guides** — marked Sharp edges, UV seams, and (optionally) material
  boundaries become exact edge-flow lines
- **Adaptive Quad Count** — quality-priority mode where sizing wins over count
- **Keep Materials** — slots copied, per-face assignment re-projected
- **Transfer UVs** — optional nearest-polygon UV projection
- **Relax & Project** — post-smoothing re-projected onto the source surface
- **Presets** — Basic / Mechanical (sharp-feature snapping) / Organic
- **Progress bar & Esc cancel** — Blender stays responsive while the engine runs
- **Headless/batch** — works in `blender -b` for pipeline automation
- Bundled engine binaries, no external downloads or configuration

## Install

Download the extension `.zip` for your platform and install it via
`Edit > Preferences > Get Extensions > Install from Disk`.
Blender 4.2 or later.

## Usage

`View3D > Sidebar (N) > ReQuad`, in Object Mode with a mesh selected.
Pick a preset and a quad count, hit **Quad Remesh**.

Tips:
- The engine is happiest with watertight, manifold meshes above ~1k triangles.
- `Pre-remesh` should stay on unless your input is already clean and uniform.
- Dense inputs (>500k tris): decimate first, the result won't suffer.

## Building the engine from source

The engine is a patched build of
[cgg-bern/quadwild-bimdf](https://github.com/cgg-bern/quadwild-bimdf):

```sh
git clone --recursive https://github.com/cgg-bern/quadwild-bimdf
cd quadwild-bimdf
for p in ../patches/*.patch; do git apply "$p"; done
cmake . -B build -DSATSUMA_ENABLE_BLOSSOM5=0 -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
      -DCMAKE_CXX_FLAGS="-Wno-missing-template-arg-list-after-template-kw"
cmake --build build -j
```

Copy `build/Build/bin/{quadwild,quad_from_patches}` into
`engine/<platform-tag>/` (e.g. `engine/macos-arm64/`).

## Roadmap

1. **Robustness** — multi-component meshes, symmetry, transform preservation
2. **Quad Remesher parity** — adaptive density, painted density (vertex
   colors), edge-loop guides from seams/creases/materials
3. **Beyond** — curvature-aligned fields (NeurCross-class), speed work,
   in-process engine (no OBJ round-trip)

## License & attribution

GPL-3.0-or-later. ReQuad exists thanks to the researchers who published
their work as open source:

- N. Pietroni, S. Nuvoli, T. Alderighi, P. Cignoni, M. Tarini,
  *Reliable Feature-Line Driven Quad-Remeshing*, SIGGRAPH 2021.
- M. Heistermann, J. Warnett, D. Bommes,
  *Min-Deviation-Flow in Bi-directed Graphs for T-Mesh Quantization*,
  SIGGRAPH Asia 2023 (the bi-MDF solver, and the
  [satsuma](https://github.com/cgg-bern/satsuma) library).

Third-party components and their licenses: see [THIRD_PARTY.md](THIRD_PARTY.md).
