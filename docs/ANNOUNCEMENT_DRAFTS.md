# Brouillons d'annonce (à relire / éditer / poster par le mainteneur)

Deux versions : BlenderArtists (longue) et Reddit (courte). Images
suggérées : `docs/media/compare_*.png` (générées par
`benchmarks/render_compare.py`).

---

## BlenderArtists — thread "Released"

**Title:** ReQuad — free, open-source auto-retopology (QuadWild bi-MDF), with a published benchmark vs Quad Remesher

Hi everyone,

I'm releasing **ReQuad**, a free and open-source (GPL-3.0) auto-retopology
extension for Blender 4.2+. One click turns a triangle soup — sculpt,
scan, CAD export — into a clean quad mesh at the count you asked for.

**Where it stands.** I benchmarked it against Quad Remesher 1.4.1 under a
protocol I tried hard to make attack-proof: both QR modes measured
(default and ExactQuadCount), quality compared only at matched face
budgets, world-space metrics, bidirectional surface-fidelity sampling,
medians of 3 campaigns (the solver isn't run-deterministic), raw JSON
data and the full methodology published in the repo — including the six
objectivity flaws I found in my OWN earlier benchmarks and how they were
fixed. At equal budgets ReQuad currently leads on corner angles (10-2),
quad aspect (12-2), irregular vertices (4-3), surface fidelity (9-6),
and count accuracy (3.2% vs 7.8% mean error). Quad Remesher keeps the
lead on very coarse organic targets, fine detail adaptivity, and
cold-start speed (0.5-1.5s vs 2.5-9.5s cold; warm cache is near parity).
All of it is in the repo — if you find a flaw in the methodology, please
open an issue, I mean it.

**Features:** quad/tri budget as a contract (iterative re-quantization),
X/Y/Z symmetry with welded seams, adaptive size, painted density (vertex
colors), flow guides from marked Sharp edges / UV seams / material
boundaries, materials & UV & vertex-group transfer, presets
(Basic/Mechanical/Organic), progress bar + Esc cancel, headless/batch
support, bundled engine binaries for macOS arm64 / Linux x64 /
Windows x64 — no downloads, no license server, no accounts.

**Engine credits.** ReQuad drives the QuadWild bi-MDF pipeline by
Pietroni, Nuvoli, Alderighi, Cignoni, Tarini (SIGGRAPH 2021) and
Heistermann, Warnett, Bommes (SIGGRAPH Asia 2023), with a few patches
(deterministic seeding, per-patch size fields, target-count control) —
all published in the repo. This project exists because those researchers
released their work as open source.

**Links:**
- Releases: https://github.com/mlstr0m/requad/releases
- Benchmark & methodology: https://github.com/mlstr0m/requad/blob/main/docs/BENCHMARK_VS_QUADREMESHER.md
- Roadmap: https://github.com/mlstr0m/requad/blob/main/docs/ROADMAP.md

I'd love real-world meshes that break it — dirty scans, huge sculpts,
thin CAD shells. Issue template is set up for exactly that.

---

## Reddit r/blender

**Title:** I built a free, open-source alternative to Quad Remesher — and published the full benchmark, including where it loses

**Body:**

ReQuad is a GPL-3.0 auto-retopology extension for Blender 4.2+ built on
the QuadWild bi-MDF research engine (SIGGRAPH 2021/2023). Pick a quad
count, hit Remesh: the count is treated as a contract (3.2% mean error),
the result is reprojected on your surface, symmetry/guides/adaptive size
included. Engine bundled for macOS arm64, Linux, Windows — no accounts,
no license server.

The part I'm most proud of isn't the tool, it's the benchmark: fully
published methodology + raw data, measured against Quad Remesher in both
its modes at matched budgets, medians over repeated campaigns, and an
honest list of what QR still does better (very coarse organics, detail
adaptivity, cold-start speed). If you can find a hole in the
methodology, open an issue — that's the point of publishing it.

Repo + releases: https://github.com/mlstr0m/requad

---

## Notes de posting

- Joindre 3-4 images : `compare_walkie` (le plus spectaculaire : à budget
  égal, QR-exact détruit la coque, nous non), `compare_statue` (organique
  à budget égal), `compare_torus` (aspect), `compare_sphere`.
- Répondre aux demandes de meshes cassés avec le template d'issue.
- Ne JAMAIS sur-vendre : le benchmark dit "devant à budget égal sur ces
  axes", pas "meilleur partout". La crédibilité du projet est SA
  différenciation.
