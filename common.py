# SPDX-FileCopyrightText: 2026 Aurélien and the ReQuad contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared constants, engine resolution and target helpers."""

import os
import platform
import shutil
import sys

import bpy

BIN_QUADWILD = "quadwild"
BIN_QFP = "quad_from_patches"
EXE = ".exe" if sys.platform == "win32" else ""

# The flow/satsuma JSON paths below are resolved relative to the engine
# working directory, which is why every subprocess runs with cwd=engine root.
MAIN_CONFIG_TEMPLATE = """\
alpha {alpha}
ilpMethod 1
timeLimit 200
gapLimit 0.0
callbackTimeLimit 8 3.00 5.000 10.0 20.0 30.0 60.0 90.0 120.0
callbackGapLimit 8 0.005 0.02 0.05 0.10 0.15 0.20 0.25 0.3
minimumGap 0.4
isometry 1
regularityQuadrilaterals 1
regularityNonQuadrilaterals 1
regularityNonQuadrilateralsWeight 0.9
alignSingularities {align}
alignSingularitiesWeight 0.1
repeatLosingConstraintsIterations 1
repeatLosingConstraintsQuads 0
repeatLosingConstraintsNonQuads 0
repeatLosingConstraintsAlign 0
hardParityConstraint 1
scaleFact {scale}
fixedChartClusters 0
useFlowSolver 1
flow_config_filename "config/main_config/flow_virtual_simple.json"
satsuma_config_filename "config/satsuma/lemon.json"
"""

PREP_CONFIG_TEMPLATE = """\
do_remesh {remesh}
sharp_feature_thr {sharp}
alpha 0.01
scaleFact 1
remesh_target_faces {rem_faces}
"""

# Pre-remesh density follows the requested quad count (engine patch 0002):
# the field is computed at a resolution matched to the output, which keeps
# patch counts (and thus the minimum reachable quad count) proportionate.
REM_FACES_PER_QUAD = 14
REM_FACES_MIN = 8000
REM_FACES_MAX = 150000

# Empirical: at the coarsest, the quantizer settles around ~3.2 quads per
# traced patch with alpha 0.3 (measured on the bundled engine; alpha 0.005
# floored at ~10). Used to warn the user when their target is below what
# this shape's patch layout can reach.
MIN_QUADS_PER_PATCH = 4

# Progress milestones scanned from the engine log. The engine gives no
# machine-readable progress, but its log lines are stable markers; percents
# are calibrated on typical runs (field ≈ first half, tracing ≈ up to ~2/3,
# then one or more quantization passes).
QUADWILD_MARKERS = (
    ("1 - Remesh and field", 8),
    ("Smooth Field Computation", 16),
    ("Saving Mesh TO", 40),
    ("2 - Tracing", 44),
    ("FIRST TRACING STEP", 50),
    ("SUBPATCH TRACING", 56),
    ("THERE ARE 0 Unsolved", 66),
)
QFP_BASE = 70          # percent when the first quantization pass starts
QFP_SPAN = 8           # percent budget per quantization pass
QFP_MARKERS = (
    ("Loaded", 2),     # inputs loaded (relative to the pass base)
    ("Solved BiMDF", 6),
)


def _target_quads(settings):
    """Requested count converted to quads (a quad renders as 2 triangles)."""
    if settings.count_mode == "TRIS":
        return max(settings.target_count // 2, 50)
    return settings.target_count


# ---- engine resolution --------------------------------------------------

def _platform_tag():
    system = {"darwin": "macos", "win32": "windows", "linux": "linux"}.get(
        sys.platform, sys.platform)
    machine = platform.machine().lower()
    arch = {"arm64": "arm64", "aarch64": "arm64",
            "x86_64": "x64", "amd64": "x64"}.get(machine, machine)
    return f"{system}-{arch}"


# Success-only cache: the panel calls resolve_engine on every redraw, so
# don't re-stat the binaries once found. Cleared when the pref changes.
_ENGINE_CACHE = {}

# Field/tracing results keyed by input content hash. The heavy engine step
# (pre-remesh + field + tracing) does not depend on the quantization target,
# so re-running the same object at a different Count only re-quantizes
# (~1s instead of the full pipeline). Bounded to the last few runs.
_STEP1_CACHE = {}
_STEP1_CACHE_MAX = 3


def clear_step1_cache():
    """Delete all cached field/tracing workdirs and forget their entries."""
    for workdir in set(_STEP1_CACHE.values()):
        shutil.rmtree(workdir, ignore_errors=True)
    _STEP1_CACHE.clear()


def resolve_engine(prefs):
    """Return (bin_dir, workdir) for the engine, or None if unavailable.

    Order: custom folder from preferences (a quadwild-bimdf checkout), then
    the binaries bundled with the extension for this platform.
    """
    custom = prefs.custom_engine_dir.strip()
    cached = _ENGINE_CACHE.get(custom)
    if cached is not None:
        return cached
    if custom:
        root = bpy.path.abspath(custom)
        candidate = (os.path.join(root, "build", "Build", "bin"), root)
    else:
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine")
        candidate = (os.path.join(root, _platform_tag()), root)

    bin_dir, workdir = candidate
    for name in (BIN_QUADWILD + EXE, BIN_QFP + EXE):
        exe = os.path.join(bin_dir, name)
        if not os.path.isfile(exe):
            return None
        # Zip extraction (extension install) may drop the executable bit.
        if os.name == "posix" and not os.access(exe, os.X_OK):
            os.chmod(exe, 0o755)
    if not os.path.isdir(os.path.join(workdir, "config")):
        return None
    _ENGINE_CACHE[custom] = candidate
    return candidate
