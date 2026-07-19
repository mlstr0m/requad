# SPDX-FileCopyrightText: 2026 Aurélien and the ReQuad contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""ReQuad — free auto-retopology for Blender, powered by QuadWild bi-MDF.

Pipeline: export selection as triangulated OBJ → run `quadwild` (field
computation + patch tracing) → run `quad_from_patches` (bi-MDF quantization +
quadrangulation) → import the result next to the original object.

Engine: https://github.com/cgg-bern/quadwild-bimdf (GPL-3.0)
Papers: Pietroni et al. 2021 (QuadWild), Heistermann et al. 2023 (bi-MDF).
"""

import hashlib
import math
import os
import platform
import subprocess
import sys
import tempfile
import time

import numpy as np

import bmesh
import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

BIN_QUADWILD = "quadwild"
BIN_QFP = "quad_from_patches"
EXE = ".exe" if sys.platform == "win32" else ""

# The flow/satsuma JSON paths below are resolved relative to the engine
# working directory, which is why every subprocess runs with cwd=engine root.
MAIN_CONFIG_TEMPLATE = """\
alpha 0.3
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


class ReQuadPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    custom_engine_dir: StringProperty(
        name="Custom Engine Folder",
        description="Optional quadwild-bimdf checkout (with build/Build/bin). "
                    "Leave empty to use the binaries bundled with the extension",
        subtype="DIR_PATH",
        default="",
        update=lambda self, context: _ENGINE_CACHE.clear(),
    )

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "custom_engine_dir")
        engine = resolve_engine(self)
        if engine:
            col.label(text=f"Engine: {engine[0]}", icon="CHECKMARK")
        else:
            col.label(text=f"No engine found for {_platform_tag()}", icon="ERROR")


class ReQuadSettings(bpy.types.PropertyGroup):
    preset: EnumProperty(
        name="Preset",
        items=[
            ("BASIC", "Basic", "Balanced defaults"),
            ("MECHANICAL", "Mechanical", "Hard-surface: strong sharp-feature snapping"),
            ("ORGANIC", "Organic", "Sculpts: ignore sharp features"),
        ],
        default="BASIC",
    )
    target_count: IntProperty(
        name="Count",
        description="Approximate size of the result, in the unit selected "
                    "next to this field (quads, or triangles = 2× quads)",
        default=5000, min=100, max=1000000, soft_max=100000,
    )
    count_mode: EnumProperty(
        name="Unit",
        description="How to interpret Count",
        items=[
            ("QUADS", "Quads", "Count is the number of quad faces"),
            ("TRIS", "Tris", "Count is the number of triangles after "
                             "triangulation (2 per quad) — game-engine budget"),
        ],
        default="QUADS",
    )
    sharp_angle: FloatProperty(
        name="Sharp Angle",
        description="Dihedral angle threshold for feature edges (degrees)",
        default=35.0, min=0.0, max=90.0,
    )
    symmetry_axis: EnumProperty(
        name="Symmetry",
        description="Remesh one half and mirror it for perfectly symmetric "
                    "topology (plane through the object's origin, local axis)",
        items=[
            ("NONE", "None", "No symmetry"),
            ("X", "X", "Mirror across local X (characters)"),
            ("Y", "Y", "Mirror across local Y"),
            ("Z", "Z", "Mirror across local Z"),
        ],
        default="NONE",
    )
    use_paint_density: BoolProperty(
        name="Paint Density",
        description="Drive quad size with the source's active color "
                    "attribute — red = 4× smaller quads, cyan = 4× bigger",
        default=False,
    )
    adaptive_size: FloatProperty(
        name="Adaptive Size",
        description="Concentrate smaller quads on curved areas and larger "
                    "ones on flat areas (0 = uniform quad size)",
        default=50.0, min=0.0, max=100.0, subtype="PERCENTAGE",
    )
    pre_remesh: BoolProperty(
        name="Pre-remesh",
        description="Let the engine uniformly remesh the input first (recommended, "
                    "required for dirty or very anisotropic meshes)",
        default=True,
    )
    align_singularities: BoolProperty(
        name="Align Singularities",
        description="Trade a bit of regularity for aligned singularity pairs",
        default=False,
    )
    guide_sharp: BoolProperty(
        name="Marked Sharp as Guides",
        description="Edges marked Sharp become exact flow guides",
        default=True,
    )
    guide_seams: BoolProperty(
        name="UV Seams as Guides",
        description="UV seams become flow guides — leave off for assets "
                    "whose seams exist only for texturing",
        default=False,
    )
    material_guides: BoolProperty(
        name="Material Boundaries as Guides",
        description="Treat edges between different materials as flow "
                    "guides (like marked seams)",
        default=False,
    )
    adaptive_count: BoolProperty(
        name="Adaptive Quad Count",
        description="ON: quality first — adaptive and painted sizing may "
                    "push the result beyond Count (single quantization "
                    "pass). OFF: Count is enforced by iterative correction",
        default=False,
    )
    transfer_uvs: BoolProperty(
        name="Transfer UVs",
        description="Project the source's UV map onto the result "
                    "(approximate near UV seams)",
        default=False,
    )
    relax_iterations: IntProperty(
        name="Relax Iterations",
        description="Tangential smoothing of the quads, re-projected onto "
                    "the source surface each step — straightens flow and "
                    "evens out quad shapes. On Basic/Mechanical, vertices "
                    "on sharp feature lines are pinned (0 disables)",
        default=8, min=0, max=30,
    )
    keep_materials: BoolProperty(
        name="Keep Materials",
        description="Copy the source object's materials to the result and "
                    "re-assign each quad from the nearest source face",
        default=True,
    )
    smooth_result: BoolProperty(
        name="Smoothed Result",
        description="Import the post-smoothed quadrangulation instead of the raw one",
        default=True,
    )
    hide_original: BoolProperty(
        name="Hide Original",
        description="Hide the source object after remeshing",
        default=True,
    )


class REQUAD_OT_remesh(bpy.types.Operator):
    """Quad-remesh the active mesh object with QuadWild bi-MDF"""
    bl_idname = "requad.remesh"
    bl_label = "Quad Remesh"
    bl_options = {"REGISTER", "UNDO"}

    _timer = None
    _proc = None
    _phase = ""
    _t0 = 0.0

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == "MESH" and context.mode == "OBJECT"

    # ---- pipeline steps -------------------------------------------------

    def _spawn(self, args):
        return subprocess.Popen(
            args,
            cwd=self.engine_workdir,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
        )

    def _set_workdir_paths(self):
        self.input_obj = os.path.join(self.workdir, "input.obj")
        self.patches_obj = os.path.join(self.workdir, "input_rem_p0.obj")
        self.sharp_path = os.path.join(self.workdir, "input.sharp")
        self.log_path = os.path.join(self.workdir, "engine.log")
        self.main_config = os.path.join(self.workdir, "main.txt")
        self.prep_config = os.path.join(self.workdir, "prep.txt")

    def _start_quadwild(self):
        self._phase = "QUADWILD"
        args = [os.path.join(self.bin_dir, BIN_QUADWILD + EXE),
                self.input_obj, "2", self.prep_config]
        if self.use_sharp:
            args.append(self.sharp_path)
        self._proc = self._spawn(args)

    def _prepare_quantization(self, target, strength, use_paint):
        """Parse the patches mesh once and return the global scaleFact for
        the requested count (quads ≈ area / edge², calibrated).

        When strength > 0 or paint density is on, also write per-patch edge
        multipliers next to the mesh ("<mesh>.scales", read by our engine
        patch 0003). Curvature proxy per patch is the area-weighted normal
        spread: 1 - |Σ area·n̂| / Σ area (Quad Remesher's Adaptive Size);
        painted density samples the source's active color attribute
        (red = 4× smaller, cyan = 4× bigger).
        """
        verts = []
        tris = []
        with open(self.patches_obj) as f:
            for line in f:
                if line.startswith("v "):
                    _, x, y, z = line.split()[:4]
                    verts.append((float(x), float(y), float(z)))
                elif line.startswith("f "):
                    tris.append([int(t.split("/")[0]) - 1
                                 for t in line.split()[1:4]])
        scales_path = self.patches_obj[:-4] + ".scales"
        try:
            os.remove(scales_path)
        except OSError:
            pass
        if not tris:
            return 1.0
        v = np.asarray(verts)
        f = np.asarray(tris)
        e0 = v[f[:, 1]] - v[f[:, 0]]
        e1 = v[f[:, 2]] - v[f[:, 0]]
        e2 = v[f[:, 2]] - v[f[:, 1]]
        cross = np.cross(e0, e1)
        area2 = np.linalg.norm(cross, axis=1)  # 2x triangle area
        area_total = float(area2.sum()) / 2.0
        edge_lengths = np.concatenate(
            [np.linalg.norm(e, axis=1) for e in (e0, e1, e2)])
        avg_edge = float(edge_lengths.mean())
        if area_total <= 0.0 or avg_edge <= 0.0:
            return 1.0
        # 1.033: measured calibration — raw formula overshoots by ~6-7%
        scale = 1.033 * math.sqrt(area_total / target) / avg_edge

        if strength <= 0.0 and not use_paint:
            return scale
        try:
            patch_ids = np.loadtxt(self.patches_obj[:-4] + ".patch",
                                   skiprows=1, dtype=np.int64)
        except OSError:
            return scale
        if patch_ids.ndim != 1 or patch_ids.shape[0] != f.shape[0]:
            return scale
        n_patches = int(patch_ids.max()) + 1
        sum_a = np.zeros(n_patches)
        np.add.at(sum_a, patch_ids, area2 / 2.0)
        sum_a = np.maximum(sum_a, 1e-12)
        mult = np.ones(n_patches)
        if strength > 0.0:
            sum_n = np.zeros((n_patches, 3))
            np.add.at(sum_n, patch_ids, cross / 2.0)
            curv = 1.0 - np.linalg.norm(sum_n, axis=1) / sum_a
            mean_curv = float(np.average(curv, weights=sum_a))
            rel = (curv + 1e-4) / (mean_curv + 1e-4)
            # At full strength a patch 4x more curved than average gets ~2x
            # smaller edges; clamped to Quad Remesher's 0.25..4 range.
            mult *= np.clip(rel ** (-0.5 * strength), 0.25, 4.0)
        if use_paint:
            paint = self._paint_patch_multipliers(v, f, patch_ids, n_patches)
            if paint is not None:
                mult *= paint
        # Keep the global count calibration unbiased: local contrast stays,
        # but the area-weighted mean multiplier is 1 (on mostly-flat shapes
        # an un-normalized Adaptive Size skewed counts by up to 2x).
        mult /= max(float(np.average(mult, weights=sum_a)), 1e-9)
        mult = np.clip(mult, 0.2, 5.0)
        np.savetxt(scales_path, mult, fmt="%.6f")
        return scale

    def _paint_patch_multipliers(self, verts, faces, patch_ids, n_patches):
        """Per-patch multipliers from the source's active color attribute.

        Quad Remesher's convention: red asks for 4× smaller quads, cyan for
        4× bigger. Each patch samples up to 40 of its vertices against a
        KDTree of the source mesh (same space as the exported OBJ)."""
        src = bpy.context.scene.objects.get(self.src_name)
        if src is None or src.type != "MESH":
            return None
        deps = bpy.context.evaluated_depsgraph_get()
        src_eval = src.evaluated_get(deps)
        mesh = src_eval.to_mesh()
        attr = mesh.color_attributes.active_color
        if attr is None or len(mesh.vertices) == 0:
            src_eval.to_mesh_clear()
            return None

        n = len(mesh.vertices)
        if attr.domain == "POINT":
            cols = np.empty(len(attr.data) * 4)
            attr.data.foreach_get("color", cols)
            cols = cols.reshape(-1, 4)[:, :3]
        elif attr.domain == "CORNER":
            loop_cols = np.empty(len(attr.data) * 4)
            attr.data.foreach_get("color", loop_cols)
            loop_cols = loop_cols.reshape(-1, 4)[:, :3]
            loop_vi = np.empty(len(mesh.loops), dtype=np.int64)
            mesh.loops.foreach_get("vertex_index", loop_vi)
            cols = np.zeros((n, 3))
            counts = np.zeros(n)
            np.add.at(cols, loop_vi, loop_cols)
            np.add.at(counts, loop_vi, 1.0)
            cols /= np.maximum(counts, 1.0)[:, None]
        else:
            src_eval.to_mesh_clear()
            return None
        # signal in [-1, 1]: +1 = pure red (finer), -1 = pure cyan (coarser)
        signal = cols[:, 0] - (cols[:, 1] + cols[:, 2]) / 2.0

        from mathutils import kdtree
        kd = kdtree.KDTree(n)
        # The exported OBJ is in world space, except with symmetry where it
        # is in source-local space (identity temp object).
        mat = None if self.sym_axis != "NONE" else src.matrix_world
        for i, vert in enumerate(mesh.vertices):
            co = vert.co if mat is None else mat @ vert.co
            kd.insert(co, i)
        kd.balance()
        src_eval.to_mesh_clear()

        order = np.argsort(patch_ids, kind="stable")
        f_sorted = faces[order]
        p_sorted = patch_ids[order]
        bounds = np.searchsorted(p_sorted, np.arange(n_patches + 1))
        mult = np.ones(n_patches)
        for pi in range(n_patches):
            fs = f_sorted[bounds[pi]:bounds[pi + 1]]
            if not len(fs):
                continue
            vids = np.unique(fs)[:40]
            total = 0.0
            for vid in vids:
                _, i, _ = kd.find(verts[vid])
                total += signal[i]
            mult[pi] = 4.0 ** (-(total / len(vids)))
        return mult

    def _update_progress(self):
        """Advance self.progress by scanning new engine log output."""
        try:
            with open(self.log_path) as f:
                f.seek(self.log_offset)
                chunk = f.read()
                self.log_offset = f.tell()
        except OSError:
            return
        if not chunk:
            return
        if self._phase == "QUADWILD":
            for marker, pct in QUADWILD_MARKERS:
                if marker in chunk:
                    self.progress = max(self.progress, pct)
        else:
            base = min(QFP_BASE + (self._qfp_runs - 1) * QFP_SPAN, 94 - QFP_SPAN)
            self.progress = max(self.progress, base)
            for marker, delta in QFP_MARKERS:
                if marker in chunk:
                    self.progress = max(self.progress, base + delta)

    def _reachable_floor(self):
        """Estimated coarsest quad count for this shape's patch layout."""
        patch_file = self.patches_obj[:-4] + ".patch"
        try:
            with open(patch_file) as f:
                # first token is the face count, not a patch id
                patches = len(set(f.read().split()[1:]))
        except OSError:
            return 0
        return patches * MIN_QUADS_PER_PATCH

    def _start_qfp(self):
        settings = bpy.context.scene.requad
        if self._qfp_runs == 0:
            self.floor_estimate = self._reachable_floor()
            self.qfp_scale = self._prepare_quantization(
                self.effective_target, settings.adaptive_size / 100.0,
                settings.use_paint_density)
        self._qfp_runs += 1
        with open(self.main_config, "w") as f:
            f.write(MAIN_CONFIG_TEMPLATE.format(
                scale=self.qfp_scale,
                align=int(settings.align_singularities)))
        self._phase = f"QUANTIZE {self._qfp_runs}"
        self._proc = self._spawn(
            [os.path.join(self.bin_dir, BIN_QFP + EXE),
             self.patches_obj, str(self._qfp_runs), self.main_config])

    def _run_quads(self, run):
        """Quad count of one quantization run's output (0 if missing)."""
        path = self.patches_obj[:-4] + f"_{run}_quadrangulation.obj"
        try:
            with open(path) as f:
                return sum(1 for line in f if line.startswith("f "))
        except OSError:
            return 0

    def _needs_requantize(self, context):
        """After a quantization pass: keep the best run so far and decide
        whether another corrected pass is worth it.

        Patch-side rounding makes single-pass counts overshoot on shapes
        with many patches, so we re-quantize (cheap) with the scale
        corrected by sqrt(got/target) until within tolerance.
        """
        target = self.effective_target
        got = self._run_quads(self._qfp_runs)
        if got and (self.best_run == 0
                    or abs(got - target) < abs(self.best_quads - target)):
            self.best_run, self.best_quads = self._qfp_runs, got
        if got:
            self.run_history.append((self.qfp_scale, got))
        if context.scene.requad.adaptive_count:
            return False  # quality priority: keep the single-pass result
        if (not got
                or self._qfp_runs >= 3
                or target < self.floor_estimate
                or abs(got - target) <= 0.08 * target):
            return False

        if len(self.run_history) >= 2:
            # Two data points fit the count model quads = a/scale² + b
            # (b is the additive per-patch rounding overhead), solved exactly.
            (s1, g1), (s2, g2) = self.run_history[-2], self.run_history[-1]
            denom = 1.0 / (s1 * s1) - 1.0 / (s2 * s2)
            if abs(denom) > 1e-12:
                a = (g1 - g2) / denom
                b = g1 - a / (s1 * s1)
                if a > 0 and target > b:
                    self.qfp_scale = math.sqrt(a / (target - b))
                    return True
            return False  # degenerate fit or target below overhead: give up
        self.qfp_scale *= math.sqrt(got / target)
        return True

    def _make_half_mesh(self, context, src):
        """Evaluated copy of the source cut at the symmetry plane (local
        space), keeping the positive side. Returns a mesh datablock the
        caller must remove, or None when nothing remains."""
        deps = context.evaluated_depsgraph_get()
        mesh = bpy.data.meshes.new_from_object(
            src.evaluated_get(deps), depsgraph=deps)
        idx = "XYZ".index(self.sym_axis)
        normal = [0.0, 0.0, 0.0]
        normal[idx] = 1.0
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.bisect_plane(
            bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces),
            dist=1e-6, plane_co=(0.0, 0.0, 0.0), plane_no=tuple(normal),
            clear_inner=True, clear_outer=False)
        empty = len(bm.faces) == 0
        bm.to_mesh(mesh)
        bm.free()
        if empty:
            bpy.data.meshes.remove(mesh)
            return None
        return mesh

    def _mesh_arrays(self, mesh, matrix):
        """Triangulate the (owned) mesh in place and return (coords, tris)
        in export space. Triangulation uses BEAUTY diagonals: naive splits
        of bent quads create phantom >35° edges that the engine's feature
        detection mistakes for creases (measured: 332 patches vs 215 on a
        real statue asset)."""
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.triangulate(bm, faces=bm.faces,
                              quad_method="BEAUTY", ngon_method="BEAUTY")
        bm.to_mesh(mesh)
        bm.free()
        n_v = len(mesh.vertices)
        co = np.empty(n_v * 3)
        mesh.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        if matrix is not None:
            m = np.array(matrix)
            co = co @ m[:3, :3].T + m[:3, 3]
        tris = np.empty(len(mesh.polygons) * 3, dtype=np.int64)
        mesh.polygons.foreach_get("vertices", tris)
        tris = tris.reshape(-1, 3)
        return co, tris

    def _write_obj(self, co, tris):
        """Write the triangulated OBJ in Blender axes (no Y-up conversion —
        the import side uses the same convention)."""
        with open(self.input_obj, "w") as f:
            np.savetxt(f, co, fmt="v %.8f %.8f %.8f")
            np.savetxt(f, tris + 1, fmt="f %d %d %d")

    def _mirror_result(self, ob):
        """Mirror the imported half across the symmetry plane and weld the
        seam. ob.matrix_world currently maps mesh space to source-local
        space (the half was exported with an identity transform)."""
        idx = "XYZ".index(self.sym_axis)
        dim = max(ob.dimensions.length, 1e-9)
        to_local = ob.matrix_world
        from_local = to_local.inverted()
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        # Snap boundary vertices that sit near the plane exactly onto it so
        # the mirrored copies coincide and weld cleanly.
        snap = 0.02 * dim
        for v in bm.verts:
            if any(len(e.link_faces) == 1 for e in v.link_edges):
                p = to_local @ v.co
                if abs(p[idx]) < snap:
                    p[idx] = 0.0
                    v.co = from_local @ p
        bmesh.ops.mirror(
            bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces),
            matrix=to_local, merge_dist=1e-4 * dim, axis=self.sym_axis)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(ob.data)
        bm.free()
        for poly in ob.data.polygons:
            poly.use_smooth = True

    def _cleanup_degenerate(self, ob):
        """At extreme coarse targets the quantizer can emit near-zero
        edges (collapsed quad sides). Dissolve them and re-join any
        triangles that appear, keeping the mesh as close to pure quads
        as possible."""
        me = ob.data
        if not len(me.edges):
            return
        vco = np.empty(len(me.vertices) * 3)
        me.vertices.foreach_get("co", vco)
        vco = vco.reshape(-1, 3)
        ev = np.empty(len(me.edges) * 2, dtype=np.int64)
        me.edges.foreach_get("vertices", ev)
        ev = ev.reshape(-1, 2)
        lens = np.linalg.norm(vco[ev[:, 0]] - vco[ev[:, 1]], axis=1)
        thr = 0.03 * float(lens.mean())
        if not bool((lens < thr).any()):
            return
        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.dissolve_degenerate(bm, dist=thr, edges=bm.edges)
        tris = [f for f in bm.faces if len(f.verts) == 3]
        if tris:
            bmesh.ops.join_triangles(
                bm, faces=tris,
                angle_face_threshold=3.14, angle_shape_threshold=3.14)
        bm.to_mesh(me)
        bm.free()
        for poly in me.polygons:
            poly.use_smooth = True

    def _relax_result(self, context, ob, iterations, step=0.5,
                      pin_angle=None):
        """Tangential Laplacian relaxation with re-projection onto the
        evaluated source surface. Boundary vertices stay fixed, so open
        borders and the symmetry seam are untouched. With pin_angle set,
        vertices near the source's sharp feature edges (dihedral above the
        angle) are pinned too, so hard-surface creases keep their shape.
        Improves quad angles and flow without changing connectivity."""
        src = context.scene.objects.get(self.src_name)
        if src is None or src.type != "MESH":
            return
        from mathutils.bvhtree import BVHTree
        deps = context.evaluated_depsgraph_get()
        src_eval = src.evaluated_get(deps)
        mesh = src_eval.to_mesh()
        # With symmetry the result is still in source-local space here;
        # otherwise both live in world space.
        mat = None if self.sym_axis != "NONE" else src.matrix_world
        src_verts = [(v.co if mat is None else mat @ v.co)
                     for v in mesh.vertices]
        src_polys = [tuple(p.vertices) for p in mesh.polygons]
        bvh = BVHTree.FromPolygons(src_verts, src_polys)

        feature_segments = None
        if pin_angle is not None:
            n_p = len(mesh.polygons)
            pn = np.empty(n_p * 3)
            mesh.polygons.foreach_get("normal", pn)
            pn = pn.reshape(-1, 3)
            adj = {}
            for pi, poly in enumerate(mesh.polygons):
                for a_i, b_i in poly.edge_keys:
                    k = (a_i, b_i) if a_i < b_i else (b_i, a_i)
                    adj.setdefault(k, []).append(pi)
            cos_thr = math.cos(math.radians(pin_angle))
            segs = [k for k, ps in adj.items()
                    if len(ps) == 2
                    and float(np.dot(pn[ps[0]], pn[ps[1]])) < cos_thr]
            if len(segs) > 20000:  # bound the pinning cost on heavy CAD
                segs = segs[::len(segs) // 20000 + 1]
            if segs:
                pts = np.array([[list(src_verts[a_i]), list(src_verts[b_i])]
                                for a_i, b_i in segs])
                feature_segments = pts
        src_eval.to_mesh_clear()

        me = ob.data
        n = len(me.vertices)
        co = np.empty(n * 3)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)

        edges = np.empty(len(me.edges) * 2, dtype=np.int64)
        me.edges.foreach_get("vertices", edges)
        edges = edges.reshape(-1, 2)

        from collections import Counter
        edge_faces = Counter()
        for poly in me.polygons:
            vs = poly.vertices
            for i in range(len(vs)):
                a, b = vs[i], vs[(i + 1) % len(vs)]
                edge_faces[(min(a, b), max(a, b))] += 1
        fixed = np.zeros(n, dtype=bool)
        for (a, b), c in edge_faces.items():
            if c == 1:
                fixed[a] = fixed[b] = True

        to_world = np.array(ob.matrix_world)
        if feature_segments is not None:
            # pin result vertices sitting on source feature lines
            seg_a = feature_segments[:, 0]
            seg_ab = feature_segments[:, 1] - seg_a
            seg_len2 = np.maximum((seg_ab * seg_ab).sum(axis=1), 1e-18)
            rot0, tr0 = to_world[:3, :3], to_world[:3, 3]
            wpos = co @ rot0.T + tr0
            mean_e = float(np.linalg.norm(
                wpos[edges[:, 0]] - wpos[edges[:, 1]], axis=1).mean())
            tol = 0.3 * mean_e
            for i in range(n):
                if fixed[i]:
                    continue
                d = wpos[i] - seg_a
                t = np.clip((d * seg_ab).sum(axis=1) / seg_len2, 0.0, 1.0)
                closest = seg_a + seg_ab * t[:, None]
                if float(np.linalg.norm(closest - wpos[i], axis=1).min()) < tol:
                    fixed[i] = True
        rot, tr = to_world[:3, :3], to_world[:3, 3]
        inv = np.linalg.inv(rot)
        world = co @ rot.T + tr

        degree = np.zeros(n)
        np.add.at(degree, edges[:, 0], 1.0)
        np.add.at(degree, edges[:, 1], 1.0)
        degree = np.maximum(degree, 1.0)[:, None]

        from mathutils import Vector
        movable = ~fixed
        # keep the projection cost bounded on very dense results
        iterations = min(iterations, max(1, 500000 // max(n, 1)))
        for _ in range(iterations):
            acc = np.zeros_like(world)
            np.add.at(acc, edges[:, 0], world[edges[:, 1]])
            np.add.at(acc, edges[:, 1], world[edges[:, 0]])
            target = acc / degree
            world[movable] += step * (target[movable] - world[movable])
            for i in np.nonzero(movable)[0]:
                hit = bvh.find_nearest(Vector(world[i]))
                if hit is not None and hit[0] is not None:
                    world[i] = hit[0]

        co = (world - tr) @ inv.T
        me.vertices.foreach_set("co", co.reshape(-1))
        me.update()

    def _write_sharp_file(self, mesh, tris, auto_angle, edge_flags,
                          material_guides):
        """Emit the engine's .sharp feature file from the enabled guide
        sources (edge flags and/or material boundaries), plus — when
        auto_angle is set — our own dihedral detection, since the engine
        disables its detection when a feature file is supplied. Returns the
        number of feature entries written."""
        n_e = len(mesh.edges)
        ev = np.empty(n_e * 2, dtype=np.int64)
        mesh.edges.foreach_get("vertices", ev)
        ev = ev.reshape(-1, 2)
        marked = np.zeros(n_e, dtype=bool)
        for flag in edge_flags:
            arr = np.empty(n_e, dtype=bool)
            mesh.edges.foreach_get(flag, arr)
            marked |= arr
        marked_pairs = {(int(min(a, b)), int(max(a, b)))
                        for a, b in ev[marked]}
        want_mat = material_guides and len(mesh.materials) > 1
        if not marked_pairs and not want_mat:
            # no user guides: leave feature detection to the engine
            return 0

        n_p = len(mesh.polygons)
        pn = np.empty(n_p * 3)
        mesh.polygons.foreach_get("normal", pn)
        pn = pn.reshape(-1, 3)
        pc = np.empty(n_p * 3)
        mesh.polygons.foreach_get("center", pc)
        pc = pc.reshape(-1, 3)
        adj = {}
        for pi, poly in enumerate(mesh.polygons):
            for a, b in poly.edge_keys:
                key = (a, b) if a < b else (b, a)
                adj.setdefault(key, []).append(pi)

        if want_mat:
            mat_idx = np.empty(n_p, dtype=np.int64)
            mesh.polygons.foreach_get("material_index", mat_idx)
            for key, ps in adj.items():
                if len(ps) == 2 and mat_idx[ps[0]] != mat_idx[ps[1]]:
                    marked_pairs.add(key)
            if not marked_pairs:
                return 0

        cos_thr = math.cos(math.radians(auto_angle)) \
            if auto_angle is not None else None
        feature = {}
        for key, ps in adj.items():
            if len(ps) != 2:
                if key in marked_pairs:
                    feature[key] = 1  # border/non-manifold mark: convex
                continue
            a, b = ps
            is_sharp = key in marked_pairs or (
                cos_thr is not None
                and float(np.dot(pn[a], pn[b])) < cos_thr)
            if not is_sharp:
                continue
            concave = float(np.dot(pn[a], pc[b] - pc[a])) > 1e-9
            feature[key] = 0 if concave else 1
        if not feature:
            return 0

        lines = []
        for ti, t in enumerate(tris):
            for j in range(3):
                a, b = int(t[j]), int(t[(j + 1) % 3])
                kind = feature.get((a, b) if a < b else (b, a))
                if kind is not None:
                    lines.append(f"{kind},{ti},{j},")
        with open(self.sharp_path, "w") as f:
            f.write(f"{len(lines)}\n")
            f.write("\n".join(lines) + "\n")
        return len(lines)

    def _transfer_materials(self, context, ob):
        """Copy the source's material slots to the result; with several
        slots, re-assign each result face from the nearest source face
        (world space)."""
        src = context.scene.objects.get(self.src_name)
        if src is None or not src.data.materials:
            return
        for mat in src.data.materials:
            ob.data.materials.append(mat)
        if len(src.data.materials) < 2:
            return
        from mathutils.bvhtree import BVHTree
        deps = context.evaluated_depsgraph_get()
        src_eval = src.evaluated_get(deps)
        mesh = src_eval.to_mesh()
        mw = src.matrix_world
        verts = [mw @ v.co for v in mesh.vertices]
        polys = [tuple(p.vertices) for p in mesh.polygons]
        indices = [p.material_index for p in mesh.polygons]
        bvh = BVHTree.FromPolygons(verts, polys)
        rmw = ob.matrix_world
        for poly in ob.data.polygons:
            hit = bvh.find_nearest(rmw @ poly.center)
            if hit is not None and hit[2] is not None:
                poly.material_index = indices[hit[2]]
        src_eval.to_mesh_clear()

    def _transfer_uvs(self, context, ob):
        """Project the source UV map onto the result with Blender's data
        transfer (nearest polygon interpolation, world-space alignment)."""
        src = context.scene.objects.get(self.src_name)
        if src is None or src.type != "MESH" or not src.data.uv_layers:
            return
        try:
            with context.temp_override(
                    active_object=src, object=src,
                    selected_objects=[ob],
                    selected_editable_objects=[ob]):
                bpy.ops.object.data_transfer(
                    data_type="UV", use_create=True,
                    loop_mapping="POLYINTERP_NEAREST",
                    use_object_transform=True)
        except RuntimeError as exc:
            self.report({"WARNING"}, f"UV transfer failed: {exc}")

    def _finish(self, context):
        settings = context.scene.requad
        suffix = "_quadrangulation_smooth.obj" if settings.smooth_result \
            else "_quadrangulation.obj"
        result = self.patches_obj[:-4] + f"_{self.best_run or 1}" + suffix
        if not os.path.isfile(result):
            self.report({"ERROR"},
                        f"Engine finished but produced no output — see log: {self.log_path}")
            return {"CANCELLED"}

        before = set(context.scene.objects)
        mats_before = set(bpy.data.materials)
        # our exporter writes Blender axes: import with no conversion
        bpy.ops.wm.obj_import(filepath=result, forward_axis="Y", up_axis="Z")
        new = [o for o in set(context.scene.objects) - before if o.type == "MESH"]
        if not new:
            self.report({"ERROR"}, "Import of the result failed")
            return {"CANCELLED"}

        ob = new[0]
        ob.name = self.src_name + "_requad"
        ob.data.name = ob.name
        # The engine writes one material per patch — pure debug noise.
        ob.data.materials.clear()
        for mat in set(bpy.data.materials) - mats_before:
            bpy.data.materials.remove(mat)
        for poly in ob.data.polygons:
            poly.use_smooth = True
        self._cleanup_degenerate(ob)
        if settings.relax_iterations > 0:
            pin = None
            if settings.preset != "ORGANIC":
                pin = settings.sharp_angle
                if settings.preset == "MECHANICAL":
                    pin = max(pin, 25.0)
            self._relax_result(context, ob, settings.relax_iterations,
                               pin_angle=pin)
        if self.sym_axis != "NONE":
            self._mirror_result(ob)
            ob.matrix_world = self.src_matrix @ ob.matrix_world
        if settings.keep_materials:
            self._transfer_materials(context, ob)
        if settings.transfer_uvs:
            self._transfer_uvs(context, ob)
        if settings.hide_original and self.src_name in context.scene.objects:
            context.scene.objects[self.src_name].hide_set(True)

        quads = len(ob.data.polygons)
        elapsed = time.time() - self._t0
        target = _target_quads(settings)
        floor = self.floor_estimate * (2 if self.sym_axis != "NONE" else 1)
        if settings.count_mode == "TRIS":
            got_txt = f"{quads} quads ({2 * quads} tris)"
            floor_txt = f"≈{2 * floor} tris"
        else:
            got_txt = f"{quads} quads"
            floor_txt = f"≈{floor} quads"
        if floor and target < floor * 0.9:
            self.report({"WARNING"},
                        f"ReQuad: {got_txt} in {elapsed:.1f}s — this shape "
                        f"can't go below {floor_txt}; raise Count or simplify "
                        f"the shape for a coarser result")
        else:
            self.report({"INFO"}, f"ReQuad: {got_txt} in {elapsed:.1f}s")
        return {"FINISHED"}

    # ---- modal machinery ------------------------------------------------

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        settings = context.scene.requad
        engine = resolve_engine(prefs)
        if engine is None:
            self.report({"ERROR"}, "QuadWild engine not found — check the "
                                   "extension preferences")
            return {"CANCELLED"}
        src = context.active_object
        self.bin_dir, self.engine_workdir = engine
        self.src_name = src.name
        self.src_matrix = src.matrix_world.copy()
        self.sym_axis = settings.symmetry_axis
        self.effective_target = _target_quads(settings)
        if self.sym_axis != "NONE":
            self.effective_target = max(self.effective_target // 2, 50)
        self.floor_estimate = 0
        self._qfp_runs = 0
        self.best_run = 0
        self.best_quads = 0
        self.run_history = []
        self.progress = 0
        self.log_offset = 0

        if self.sym_axis != "NONE":
            export_mesh = self._make_half_mesh(context, src)
            if export_mesh is None:
                self.report({"ERROR"}, "Symmetry: no geometry on the "
                                       f"positive {self.sym_axis} side")
                return {"CANCELLED"}
            matrix = None  # source-local coordinates
        else:
            deps = context.evaluated_depsgraph_get()
            export_mesh = bpy.data.meshes.new_from_object(
                src.evaluated_get(deps), depsgraph=deps)
            matrix = src.matrix_world
        co, tris = self._mesh_arrays(export_mesh, matrix)
        if tris.shape[0] == 0:
            bpy.data.meshes.remove(export_mesh)
            self.report({"ERROR"}, f"'{self.src_name}' has no faces to remesh")
            return {"CANCELLED"}

        # -1 disables feature detection entirely (the engine's own Organic
        # preset value). 0 would mark EVERY edge above 0° as sharp and blow
        # up preprocessing on dense noisy scans.
        sharp = settings.sharp_angle if settings.preset != "ORGANIC" else -1.0
        if settings.preset == "MECHANICAL":
            sharp = max(sharp, 25.0)
        edge_flags = []
        if settings.guide_sharp:
            edge_flags.append("use_edge_sharp")
        if settings.guide_seams:
            edge_flags.append("use_seam")
        # Bucketed so nearby Counts share a field-cache entry; a real
        # density jump recomputes the field (correct for floor quality).
        raw = REM_FACES_PER_QUAD * self.effective_target
        rem_faces = REM_FACES_MAX
        for level in (8000, 16000, 32000, 64000, 128000):
            if raw <= level:
                rem_faces = level
                break
        prep_content = PREP_CONFIG_TEMPLATE.format(
            remesh=int(settings.pre_remesh), sharp=sharp,
            rem_faces=rem_faces)

        # Everything that influences the engine's field/tracing step goes
        # into the cache key; a hit skips export + quadwild entirely.
        digest = hashlib.sha1()
        digest.update(co.tobytes())
        digest.update(tris.tobytes())
        digest.update(prep_content.encode())
        digest.update(f"{edge_flags}|{settings.material_guides}".encode())
        n_e = len(export_mesh.edges)
        for flag in ("use_edge_sharp", "use_seam"):
            arr = np.empty(n_e, dtype=bool)
            export_mesh.edges.foreach_get(flag, arr)
            digest.update(arr.tobytes())
        key = digest.hexdigest()

        cached = _STEP1_CACHE.get(key)
        self._t0 = time.time()
        if cached and os.path.isfile(
                os.path.join(cached, "input_rem_p0.obj")):
            bpy.data.meshes.remove(export_mesh)
            self.workdir = cached
            self._set_workdir_paths()
            self.log_handle = open(self.log_path, "a")
            self.log_offset = os.path.getsize(self.log_path)
            self.progress = QFP_BASE - 2
            self._start_qfp()
        else:
            self.workdir = tempfile.mkdtemp(prefix="requad_")
            self._set_workdir_paths()
            self._write_obj(co, tris)
            # Guides: user-marked edges become engine features. Supplying a
            # feature file disables the engine's own detection, so ours
            # (same dihedral threshold) is baked in with it.
            self.use_sharp = self._write_sharp_file(
                export_mesh, tris,
                auto_angle=sharp if sharp >= 0 else None,
                edge_flags=edge_flags,
                material_guides=settings.material_guides) > 0
            bpy.data.meshes.remove(export_mesh)
            with open(self.prep_config, "w") as f:
                f.write(prep_content)
            self.log_handle = open(self.log_path, "w")
            while len(_STEP1_CACHE) >= _STEP1_CACHE_MAX:
                _STEP1_CACHE.pop(next(iter(_STEP1_CACHE)))
            _STEP1_CACHE[key] = self.workdir
            self._start_quadwild()

        if bpy.app.background:
            return self._run_blocking(context)

        wm = context.window_manager
        wm.progress_begin(0, 100)
        self._timer = wm.event_timer_add(0.25, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _run_blocking(self, context):
        if self._phase == "QUADWILD":
            if self._proc.wait() != 0:
                return self._cancel(
                    context, f"Engine failed in {self._phase} — see {self.log_path}")
            if not os.path.isfile(self.patches_obj):
                return self._cancel(context, f"No patch output — see {self.log_path}")
            self._start_qfp()
        while True:
            if self._proc.wait() != 0:
                return self._cancel(
                    context, f"Engine failed in {self._phase} — see {self.log_path}")
            if not self._needs_requantize(context):
                break
            self._start_qfp()
        self._teardown(context)
        return self._finish(context)

    def modal(self, context, event):
        if event.type == "ESC":
            return self._cancel(context, "ReQuad cancelled")

        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        self._update_progress()
        context.workspace.status_text_set(
            f"ReQuad {self.progress}% — {self._phase} — "
            f"{time.time() - self._t0:.0f}s — Esc to cancel")
        wm = context.window_manager
        wm.progress_update(self.progress)
        if wm.requad_progress != self.progress:
            wm.requad_progress = self.progress
            for area in context.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()

        code = self._proc.poll()
        if code is None:
            return {"PASS_THROUGH"}
        if code != 0:
            return self._cancel(
                context, f"Engine failed in {self._phase} — see log: {self.log_path}")

        if self._phase == "QUADWILD":
            if not os.path.isfile(self.patches_obj):
                return self._cancel(
                    context, f"No patch output — see log: {self.log_path}")
            self._start_qfp()
            return {"PASS_THROUGH"}

        if self._needs_requantize(context):
            self._start_qfp()
            return {"PASS_THROUGH"}

        self._teardown(context)
        return self._finish(context)

    def _cancel(self, context, message):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self._teardown(context)
        self.report({"WARNING"}, message)
        return {"CANCELLED"}

    def _teardown(self, context):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
            context.window_manager.progress_end()
            context.window_manager.requad_progress = -1
            for area in context.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
        if context.workspace:
            context.workspace.status_text_set(None)
        self.log_handle.close()


class REQUAD_PT_panel(bpy.types.Panel):
    """One decision to make: how many quads. Everything else has defaults."""
    bl_label = "ReQuad"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ReQuad"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.requad
        prefs = context.preferences.addons[__name__].preferences

        col = layout.column()
        row = col.row(align=True)
        row.prop(settings, "target_count")
        row.prop(settings, "count_mode", text="")
        col.prop(settings, "symmetry_axis")
        col.separator()
        running = context.window_manager.requad_progress
        if running >= 0:
            col.progress(factor=running / 100.0,
                         text=f"ReQuad {running}%  (Esc to cancel)")
        elif resolve_engine(prefs):
            col.operator("requad.remesh", icon="MOD_REMESH")
        else:
            col.label(text="Engine not found — check preferences", icon="ERROR")


class REQUAD_PT_advanced(bpy.types.Panel):
    bl_label = "Advanced"
    bl_parent_id = "REQUAD_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ReQuad"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        settings = context.scene.requad
        col = self.layout.column()
        col.prop(settings, "adaptive_size", slider=True)
        col.prop(settings, "use_paint_density")
        col.prop(settings, "adaptive_count")
        col.separator()
        col.prop(settings, "preset")
        sub = col.column()
        sub.active = settings.preset != "ORGANIC"
        sub.prop(settings, "sharp_angle")
        col.prop(settings, "guide_sharp")
        col.prop(settings, "guide_seams")
        col.prop(settings, "material_guides")
        col.separator()
        col.prop(settings, "pre_remesh")
        col.prop(settings, "relax_iterations")
        col.prop(settings, "align_singularities")
        col.prop(settings, "keep_materials")
        col.prop(settings, "transfer_uvs")
        col.prop(settings, "smooth_result")
        col.prop(settings, "hide_original")


classes = (
    ReQuadPreferences,
    ReQuadSettings,
    REQUAD_OT_remesh,
    REQUAD_PT_panel,
    REQUAD_PT_advanced,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.requad = PointerProperty(type=ReQuadSettings)
    # -1 = idle; 0-100 = a remesh is running (drives the panel progress bar)
    bpy.types.WindowManager.requad_progress = IntProperty(default=-1)


def unregister():
    del bpy.types.WindowManager.requad_progress
    del bpy.types.Scene.requad
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
