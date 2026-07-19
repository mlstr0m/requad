# Changelog

## 0.7.0 — 2026-07-19

- **Hybrid Relax & Project**: each quad now also votes for its best-fit
  rectangle (averaged edge axes, orthogonalized), blended 50/50 with the
  Laplacian term — corners are driven toward 90° while edge lengths stay
  even. Default iterations raised to 12. Measured on the reference sculpt:
  9.10° → 8.09° mean corner deviation (vs 8.42° for pure Laplacian).

## 0.6.0 — 2026-07-19

- **Deterministic results** (engine patch 0004): the patch-fill library
  seeded its RNG with the system clock — every remesh of the same input
  produced a different mesh. Fixed seed; the whole pipeline is now verified
  bit-identical across runs.
- **Extreme coarse unlocked**: the quantizer's `alpha` moves from 0.005 to
  0.3, dropping the per-patch floor from ~10 to ~3 quads (Suzanne at 296
  quads — the old floor was 663) and improving quality across the board
  (statue: angle deviation 12.06° → 8.95°, aspect 1.63 → 1.32). A
  degenerate-edge cleanup pass keeps extreme-coarse results ≥ 99% quads.
- **Relax & Project on all presets**: on Basic/Mechanical, vertices on
  sharp feature lines are pinned so creases keep their exact shape
  (verified: dimensions preserved to 1%).
- **Robustness suite**: 12 pathological meshes (non-manifold, bowtie,
  zero-area, loose geometry, self-intersections, flipped normals, holes…)
  — no crash, valid output or clean cancel everywhere. Empty meshes are
  refused with a clear message.

## 0.5.1 — 2026-07-19

- **Field cache**: the heavy engine step (pre-remesh + field + tracing)
  is content-hashed and reused — re-running the same object at another
  Count, Adaptive Size, or paint only re-quantizes (about a second instead
  of the full pipeline). Bounded to the last 3 runs.
- **13× faster on dense meshes**: profiling showed the old export path was
  the real bottleneck, not the engine (1.28M-face sculpt: 4 min 30 → 20 s
  end-to-end with the 0.5.0 exporter; engine step itself is ~12 s).
- **Adaptive Size no longer skews the count**: multipliers are normalized
  to an area-weighted mean of 1 — on mostly-flat scans the bias reached
  2× (5000 requested → 2568 delivered before the fix).

## 0.5.0 — 2026-07-19

- **Flow guides**: edges marked Sharp (default on) and UV seams (opt-in —
  texturing seams on downloaded assets are rarely flow intent, and
  treating them as guides exploded patch counts on a real textured statue)
  become engine feature lines traced exactly by the quad flow (test:
  29-32/32 ring samples). With guides present, our own dihedral detection
  replaces the engine's (which turns off when a feature file is supplied).
- **Material boundaries as guides** (Quad Remesher's "Use Materials"):
  optional toggle, boundaries between materials are traced like seams.
- **Transfer UVs** (optional): the source UV map is projected onto the
  result via nearest-polygon interpolation.
- **Adaptive Quad Count** (Quad Remesher parity): ON = quality priority,
  single quantization pass, the count follows adaptive/painted sizing;
  OFF (default) = count enforced by iterative correction.
- **Own OBJ writer/reader convention**: exports are now written by the
  add-on (controlled triangle order — required by guides) in Blender axes,
  and imported without axis conversion. Removes a whole class of
  axis-convention pitfalls; no more selection juggling during export, and
  hidden objects can now be remeshed.

## 0.4.1 — 2026-07-19

- **Relax & Project** (Organic preset, default 8 iterations): tangential
  Laplacian relaxation of the result with per-step re-projection onto the
  evaluated source surface. Boundaries and the symmetry seam stay fixed;
  cost is bounded on dense results. Measured: mean quad-angle deviation
  6.92° → 6.53° (8 it) / 6.33° (20 it) on the reference sculpt.
- Quality lever sweep (documented in docs/ROADMAP.md): the engine-config
  defaults (alignSingularities 0, simple flow target, curvature-aligned
  field) were confirmed optimal by measurement — no free quality remains
  at the config level; the next gain is connectivity-level (Phase 3).

## 0.4.0 — 2026-07-19

- **Symmetry X/Y/Z**: the evaluated source is bisected at the object-local
  plane, one half is remeshed, then mirrored and welded at the seam —
  perfectly symmetric topology (verified vertex-for-vertex in the test
  suite, zero open edges). Target count and floor estimates account for
  the halving.
- **Paint Density** (Quad Remesher parity): the source's active color
  attribute drives quad size — red = 4× smaller, cyan = 4× bigger. Works
  with Point and Corner color attributes, combines with Adaptive Size
  (per-patch multipliers, clamped 0.2–5×).
- Fixed an axis-convention bug in paint sampling (OBJ is Y-up) and an
  off-by-one in the patch-count floor estimate (the .patch header line was
  counted as a patch id).
- The user's selection is restored after the internal export isolates the
  target object; engine lookups are cached between panel redraws.

## 0.3.3 — 2026-07-19

- **Keep Materials** (on by default): the result now receives the source
  object's material slots; with several materials, each quad is re-assigned
  from the nearest source face (world-space BVH lookup). Disable in
  Advanced to get a bare mesh.
- Guard: remeshing a hidden object now reports a clear error instead of
  silently exporting nothing.

## 0.3.2 — 2026-07-19

- **Visible progress bar in the panel**: the status-bar text proved
  unreliable in live sessions, so the panel button is now replaced by a
  native progress widget (with %) while a remesh runs, refreshed on every
  engine milestone via forced redraws.

## 0.3.1 — 2026-07-19

- **Fix crash on dense scans (Organic preset)**: the Organic preset passed
  a sharp-feature threshold of 0° — the engine treats that as "every edge
  is a feature", which exploded preprocessing (and segfaulted the engine)
  on a 1.1M-triangle photogrammetry scan. Organic now disables feature
  detection with -1, matching the engine's own Organic configuration.
- **Engine: bounded edge refinement** (patch 0002): on degenerate open
  borders the engine's sharp-edge refinement loop never converged, growing
  the mesh until a segfault. The loop is now capped at 100 rounds with a
  warning, which turns a crash into a graceful continuation.

## 0.3.0 — 2026-07-19

- **Adaptive Size** (Quad Remesher parity): new slider (default 50%) that
  concentrates smaller quads on curved areas and larger ones on flat areas.
  Engine patch 0003 lets `quad_from_patches` read per-patch edge-size
  multipliers; the add-on derives them from each patch's area-weighted
  normal spread, clamped to Quad Remesher's 0.25–4× range.
- **Progress reporting**: live percentage in the status bar (and system
  progress cursor) driven by engine log milestones — field computation,
  tracing, then each quantization pass.
- **Triangle budgets**: the count field now has a Quads/Tris unit selector;
  Tris means triangles after triangulation (2 per quad), the way
  game-engine budgets are usually expressed.

## 0.2.1 — 2026-07-19

- **Simplified UI**: the panel is now Quad Count + one button; everything
  else moved to a collapsed Advanced sub-panel.
- **Clean result**: the engine's per-patch debug materials are stripped on
  import and the result is smooth-shaded.
- **Coarse targets**: the pre-remesh density now follows the requested quad
  count (engine patch 0002 exposes `remesh_target_faces` and
  `field_curv_align`), and when a shape's patch layout cannot go as coarse
  as requested, ReQuad now says so explicitly instead of silently
  overshooting (measured floor ≈ 10 quads per traced patch).
- **Iterative quad-count refinement**: quantization is re-run (up to 3
  passes, ~0.6 s each) with the scale corrected by a 2-point fit of
  `quads = a/scale² + b`, absorbing the additive per-patch rounding
  overhead on patch-heavy shapes. Real-asset benchmark (statue, 224
  patches): target 5000 went from 145% to 102%, target 3000 from 172% to
  within tolerance.

## 0.2.0 — 2026-07-19

- Migrated from legacy add-on (`bl_info`) to the Blender Extension format
  (`blender_manifest.toml`), Blender 4.2+.
- Engine binaries are now bundled per platform inside the extension
  (`engine/<platform-tag>/`) — no external setup. A custom engine checkout
  can still be pointed to in the preferences.
- Reproducible engine builds: local patches extracted to `patches/`.
- Restored the executable bit on bundled binaries after extension install.
- Isolate the active object on export (multi-selection no longer merges
  objects into one input).

## 0.1.0 — 2026-07-19

- First working version (legacy add-on format).
- QuadWild bi-MDF pipeline: export → quadwild → quad_from_patches → import.
- Quad Count target (±2% after calibration), presets Basic/Mechanical/Organic,
  sharp angle, singularity alignment, smoothed/raw result, non-blocking modal
  operator with Esc cancel, headless mode for batch use.
