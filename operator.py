# SPDX-FileCopyrightText: 2026 Aurélien and the ReQuad contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The remesh operator: engine pipeline, post-processing, count helpers."""

import hashlib
import math
import os
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace

import numpy as np

import bmesh
import bpy
from bpy.props import FloatProperty

from .common import (
    BIN_QFP,
    BIN_QUADWILD,
    EXE,
    MAIN_CONFIG_TEMPLATE,
    MIN_QUADS_PER_PATCH,
    PREP_CONFIG_TEMPLATE,
    QFP_BASE,
    QFP_MARKERS,
    QFP_SPAN,
    QUADWILD_MARKERS,
    REM_FACES_MAX,
    REM_FACES_MIN,
    REM_FACES_PER_QUAD,
    _STEP1_CACHE,
    _STEP1_CACHE_MAX,
    _target_quads,
    resolve_engine,
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
        mat = None if self.sym_axes else src.matrix_world
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
            uniq = np.unique(fs)
            vids = uniq[::max(1, len(uniq) // 40)][:40]  # spatial spread
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
        settings = self.opts
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
        if self.opts.adaptive_count:
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
        """Evaluated copy of the source cut at every enabled symmetry plane
        (local space), keeping the positive sides. Returns a mesh datablock
        the caller must remove, or None when nothing remains."""
        deps = context.evaluated_depsgraph_get()
        mesh = bpy.data.meshes.new_from_object(
            src.evaluated_get(deps), depsgraph=deps)
        bm = bmesh.new()
        bm.from_mesh(mesh)
        for axis in self.sym_axes:
            normal = [0.0, 0.0, 0.0]
            normal["XYZ".index(axis)] = 1.0
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

    def _collect_guide_segments(self, context, src, mesh, co, settings):
        """Guide polylines in export space: marked edges plus any Grease
        Pencil object whose name contains 'guide'/'requad'. Used by the
        relaxation to attract edge flow onto arbitrary curves (including
        jagged marked bands the engine's pre-remesh would simplify)."""
        segs = []
        n_e = len(mesh.edges)
        flags = []
        if settings.guide_sharp:
            flags.append("use_edge_sharp")
        if settings.guide_seams:
            flags.append("use_seam")
        if n_e and flags:
            ev = np.empty(n_e * 2, dtype=np.int64)
            mesh.edges.foreach_get("vertices", ev)
            ev = ev.reshape(-1, 2)
            marked = np.zeros(n_e, dtype=bool)
            for flag in flags:
                arr = np.empty(n_e, dtype=bool)
                mesh.edges.foreach_get(flag, arr)
                marked |= arr
            for a, b in ev[marked]:
                segs.append((co[a], co[b]))

        to_space = src.matrix_world.inverted() if self.sym_axes else None
        for gp in context.scene.objects:
            if gp.type not in {"GPENCIL", "GREASEPENCIL"}:
                continue
            name = gp.name.lower()
            if "guide" not in name and "requad" not in name:
                continue
            try:
                mw = gp.matrix_world
                for layer in gp.data.layers:
                    frame = getattr(layer, "current_frame", None)
                    frame = frame() if callable(frame) else layer.active_frame
                    if frame is None:
                        continue
                    drawing = getattr(frame, "drawing", frame)
                    for stroke in drawing.strokes:
                        pts = []
                        for pt in stroke.points:
                            p = getattr(pt, "position", None)
                            if p is None:
                                p = pt.co
                            p = mw @ p
                            if to_space is not None:
                                p = to_space @ p
                            pts.append((p.x, p.y, p.z))
                        for i in range(len(pts) - 1):
                            segs.append((pts[i], pts[i + 1]))
            except (AttributeError, TypeError):
                continue  # GP API variant not handled: skip, don't crash
        if not segs:
            return None
        arr = np.array(segs, dtype=float)
        if len(arr) > 20000:
            arr = arr[::len(arr) // 20000 + 1]
        return arr

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
        if matrix is not None and np.linalg.det(m[:3, :3]) < 0:
            # negatively-scaled transforms flip the winding: un-flip it so
            # the engine doesn't receive an inside-out mesh
            tris = tris[:, ::-1].copy()
        return co, tris

    def _write_obj(self, co, tris):
        """Write the triangulated OBJ in Blender axes (no Y-up conversion —
        the import side uses the same convention)."""
        with open(self.input_obj, "w") as f:
            np.savetxt(f, co, fmt="v %.8f %.8f %.8f")
            np.savetxt(f, tris + 1, fmt="f %d %d %d")

    def _mirror_result(self, ob, axis):
        """Mirror the imported part across one symmetry plane and weld the
        seam. ob.matrix_world currently maps mesh space to source-local
        space (the part was exported with an identity transform)."""
        idx = "XYZ".index(axis)
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
            matrix=to_local, merge_dist=1e-4 * dim, axis=axis)
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
        mat = None if self.sym_axes else src.matrix_world
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

        # guide attraction setup: vertices near a guide polyline get
        # projected onto it every iteration (flow follows the curve)
        guided_idx = None
        g_a = g_ab = g_len2 = None
        guide = getattr(self, "guide_segments", None)
        if guide is not None and len(guide):
            g_a = guide[:, 0]
            g_ab = guide[:, 1] - g_a
            g_len2 = np.maximum((g_ab * g_ab).sum(axis=1), 1e-18)
            mean_edge = float(np.linalg.norm(
                world[edges[:, 0]] - world[edges[:, 1]], axis=1).mean())
            capture = 0.6 * mean_edge
            picked = []
            for i in range(n):
                if fixed[i]:
                    continue
                d = world[i] - g_a
                t = np.clip((d * g_ab).sum(axis=1) / g_len2, 0.0, 1.0)
                closest = g_a + g_ab * t[:, None]
                if float(np.linalg.norm(closest - world[i],
                                        axis=1).min()) < capture:
                    picked.append(i)
            guided_idx = np.array(picked, dtype=np.int64)

        quads = np.array([list(p.vertices) for p in me.polygons
                          if len(p.vertices) == 4], dtype=np.int64)
        degree = np.zeros(n)
        np.add.at(degree, edges[:, 0], 1.0)
        np.add.at(degree, edges[:, 1], 1.0)
        degree = np.maximum(degree, 1.0)[:, None]

        from mathutils import Vector
        movable = ~fixed
        # keep the projection cost bounded on very dense results
        iterations = min(iterations, max(1, 500000 // max(n, 1)))
        counts = np.zeros(n)
        if len(quads):
            np.add.at(counts, quads.reshape(-1), 1.0)
        has_quad = counts > 0
        counts = np.maximum(counts, 1.0)[:, None]
        for _ in range(iterations):
            if not len(quads):
                break
            # Local-global "rectangle fitting": every quad votes for the
            # corner positions of its best-fit rectangle (averaged edge
            # axes, orthogonalized, lengths preserved); vertices average
            # the votes. Drives corners toward 90° much more directly
            # than plain Laplacian smoothing.
            p = world[quads]                     # Q x 4 x 3
            c = p.mean(axis=1)
            eu = (p[:, 1] - p[:, 0] + p[:, 2] - p[:, 3]) * 0.5
            ev = (p[:, 3] - p[:, 0] + p[:, 2] - p[:, 1]) * 0.5
            lu = np.linalg.norm(eu, axis=1)
            lv = np.linalg.norm(ev, axis=1)
            u = eu / np.maximum(lu, 1e-12)[:, None]
            ev_orth = ev - (ev * u).sum(axis=1)[:, None] * u
            v = ev_orth / np.maximum(
                np.linalg.norm(ev_orth, axis=1), 1e-12)[:, None]
            hu = (u * lu[:, None]) * 0.5
            hv = (v * lv[:, None]) * 0.5
            targets = np.stack([c - hu - hv, c + hu - hv,
                                c + hu + hv, c - hu + hv], axis=1)
            acc = np.zeros_like(world)
            np.add.at(acc, quads.reshape(-1), targets.reshape(-1, 3))
            rect_target = np.where(has_quad[:, None], acc / counts, world)
            lap = np.zeros_like(world)
            np.add.at(lap, edges[:, 0], world[edges[:, 1]])
            np.add.at(lap, edges[:, 1], world[edges[:, 0]])
            lap_target = lap / degree
            # rectangles drive 90° corners, the Laplacian equalizes edge
            # lengths — the blend beats either alone (measured)
            target = 0.5 * rect_target + 0.5 * lap_target
            world[movable] += step * (target[movable] - world[movable])
            if guided_idx is not None and len(guided_idx):
                # attract flow onto guide polylines (curves, jagged bands)
                for i in guided_idx:
                    d = world[i] - g_a
                    t = np.clip((d * g_ab).sum(axis=1) / g_len2, 0.0, 1.0)
                    closest = g_a + g_ab * t[:, None]
                    j = int(np.linalg.norm(closest - world[i], axis=1).argmin())
                    world[i] = closest[j]
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

    def _transfer_weights(self, context, ob):
        """Project the source's vertex group weights onto the result."""
        src = context.scene.objects.get(self.src_name)
        if src is None or src.type != "MESH" or not src.vertex_groups:
            return
        try:
            with context.temp_override(
                    active_object=src, object=src,
                    selected_objects=[ob],
                    selected_editable_objects=[ob]):
                bpy.ops.object.data_transfer(
                    data_type="VGROUP_WEIGHTS", use_create=True,
                    vert_mapping="POLYINTERP_NEAREST",
                    layers_select_src="ALL", layers_select_dst="NAME",
                    use_object_transform=True)
        except RuntimeError as exc:
            self.report({"WARNING"}, f"Weight transfer failed: {exc}")

    def _finish(self, context):
        settings = self.opts
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
        if self.sym_axes:
            for axis in reversed(self.sym_axes):
                self._mirror_result(ob, axis)
            ob.matrix_world = self.src_matrix @ ob.matrix_world
        if settings.keep_materials:
            self._transfer_materials(context, ob)
        if settings.transfer_uvs:
            self._transfer_uvs(context, ob)
        if settings.transfer_weights:
            self._transfer_weights(context, ob)
        if settings.hide_original and self.src_name in context.scene.objects:
            context.scene.objects[self.src_name].hide_set(True)

        quads = len(ob.data.polygons)
        elapsed = time.time() - self._t0
        target = _target_quads(settings)
        floor = self.floor_estimate * (2 ** len(self.sym_axes))
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

    SETTING_KEYS = (
        "preset", "target_count", "count_mode", "sharp_angle",
        "sym_x", "sym_y", "sym_z", "use_paint_density", "adaptive_size",
        "adaptive_count", "material_guides", "guide_sharp", "guide_seams",
        "pre_remesh", "relax_iterations", "align_singularities",
        "keep_materials", "transfer_uvs", "transfer_weights",
        "smooth_result", "hide_original")

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        if context.window_manager.requad_progress >= 0:
            self.report({"WARNING"}, "A ReQuad run is already in progress")
            return {"CANCELLED"}
        # Snapshot the settings: changing them mid-run must not affect a
        # remesh that already started.
        scene_settings = context.scene.requad
        settings = SimpleNamespace(**{k: getattr(scene_settings, k)
                                      for k in self.SETTING_KEYS})
        self.opts = settings
        engine = resolve_engine(prefs)
        if engine is None:
            self.report({"ERROR"}, "QuadWild engine not found — check the "
                                   "extension preferences")
            return {"CANCELLED"}
        src = context.active_object
        self.bin_dir, self.engine_workdir = engine
        self.src_name = src.name
        self.src_matrix = src.matrix_world.copy()
        self.sym_axes = [a for a, on in (("X", settings.sym_x),
                                         ("Y", settings.sym_y),
                                         ("Z", settings.sym_z)) if on]
        self.effective_target = _target_quads(settings)
        for _ in self.sym_axes:
            self.effective_target = max(self.effective_target // 2, 50)
        self.floor_estimate = 0
        self._qfp_runs = 0
        self.best_run = 0
        self.best_quads = 0
        self.run_history = []
        self.progress = 0
        self.log_offset = 0

        if self.sym_axes:
            export_mesh = self._make_half_mesh(context, src)
            if export_mesh is None:
                self.report({"ERROR"}, "Symmetry: no geometry on the "
                                       "positive side of the axes")
                return {"CANCELLED"}
            matrix = None  # source-local coordinates
        else:
            deps = context.evaluated_depsgraph_get()
            export_mesh = bpy.data.meshes.new_from_object(
                src.evaluated_get(deps), depsgraph=deps)
            matrix = src.matrix_world
        co, tris = self._mesh_arrays(export_mesh, matrix)
        self.guide_segments = self._collect_guide_segments(
            context, src, export_mesh, co, settings)
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
            _STEP1_CACHE[key] = _STEP1_CACHE.pop(key)  # LRU refresh
            bpy.data.meshes.remove(export_mesh)
            self.workdir = cached
            self._set_workdir_paths()
            self.log_handle = open(self.log_path, "a")
            self.log_offset = os.path.getsize(self.log_path)
            self.progress = QFP_BASE - 2
            try:
                self._start_qfp()
            except OSError as exc:
                self.log_handle.close()
                self.report({"ERROR"}, f"Engine launch failed: {exc}")
                return {"CANCELLED"}
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
                evicted = _STEP1_CACHE.pop(next(iter(_STEP1_CACHE)))
                shutil.rmtree(evicted, ignore_errors=True)
            _STEP1_CACHE[key] = self.workdir
            try:
                self._start_quadwild()
            except OSError as exc:
                self.log_handle.close()
                self.report({"ERROR"}, f"Engine launch failed: {exc}")
                return {"CANCELLED"}

        if bpy.app.background:
            return self._run_blocking(context)

        wm = context.window_manager
        wm.progress_begin(0, 100)
        wm.requad_progress = max(self.progress, 0)  # double-run guard arms now
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


class REQUAD_OT_set_count(bpy.types.Operator):
    """Set Count relative to the active object's current polycount
    (ZRemesher's Half / Same / Double)"""
    bl_idname = "requad.set_count"
    bl_label = "Set Count From Source"
    bl_options = {"INTERNAL"}

    factor: FloatProperty(default=1.0)

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == "MESH"

    def execute(self, context):
        s = context.scene.requad
        me = context.active_object.data
        tris = sum(len(p.vertices) - 2 for p in me.polygons)
        quads_equiv = max(tris // 2, 1)
        if s.count_mode == "TRIS":
            s.target_count = max(int(2 * quads_equiv * self.factor), 100)
        else:
            s.target_count = max(int(quads_equiv * self.factor), 100)
        return {"FINISHED"}

