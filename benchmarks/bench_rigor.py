# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproducible comparative benchmark: ReQuad vs Quad Remesher.

This is the exact harness behind docs/BENCHMARK_VS_QUADREMESHER.md and
docs/BENCHMARK_METHODOLOGY.md. Run it from a GUI Blender (both remeshers
are modal operators):

    blender --python benchmarks/bench_rigor.py

Environment:
    BENCH_OUT      output json path (default ./bench_rigor.json)
    BENCH_TOOLS    comma list among requad,qr,qr_exact (default all 3)
    BENCH_SHAPES   comma list to restrict shapes (default all)
    BENCH_TARGETS  comma list of quad targets to restrict (default all)
    BENCH_ASSETS   folder containing the BlenderKit statue/walkie assets
                   (shapes are skipped with a note when missing)

Methodology guarantees (see docs/BENCHMARK_METHODOLOGY.md):
- starts from an empty scene (no name collisions, no polluted settings)
- world-space metrics (immune to result-object transforms)
- bidirectional fidelity sampled at face centers + p95
- Quad Remesher measured in BOTH modes (default adaptive count, and
  ExactQuadCount) — quality is only compared at matched budgets
- ReQuad results should be aggregated as the median of 3 campaigns
  (the bi-MDF solver is not run-deterministic on symmetric shapes)
"""
import json
import math
import os
import time
from collections import defaultdict

import numpy as np

import bpy
from mathutils.bvhtree import BVHTree

ASSETS = os.environ.get("BENCH_ASSETS", os.path.expanduser(
    "./bench_assets"))
RESULTS_PATH = os.environ.get("BENCH_OUT",
                              os.path.abspath("bench_rigor.json"))
OBJ_DIR = os.path.join(os.path.dirname(RESULTS_PATH), "bench_meshes")
RUN_TIMEOUT = 300.0

os.makedirs(OBJ_DIR, exist_ok=True)

# a session-restored file collides with appended asset names — always
# start the campaign from a guaranteed-empty scene
bpy.ops.wm.read_homefile(use_empty=True)

for module in ("bl_ext.user_default.requad", "quad_remesher_1_4"):
    try:
        bpy.ops.preferences.addon_enable(module=module)
    except Exception:
        pass


def find_asset(needle):
    for root in (ASSETS, os.path.expanduser("~/blenderkit_data")):
        if not os.path.isdir(root):
            continue
        for base, dirs, files in os.walk(root):
            for f in files:
                if needle in f.lower() and f.endswith(".blend"):
                    return os.path.join(base, f)
    return None


def source_data(ob):
    """BVH + bbox diag + world-space surface sample points of the source."""
    deps = bpy.context.evaluated_depsgraph_get()
    mesh = ob.evaluated_get(deps).to_mesh()
    mw = ob.matrix_world
    verts = [mw @ v.co for v in mesh.vertices]
    polys = [tuple(p.vertices) for p in mesh.polygons]
    bvh = BVHTree.FromPolygons(verts, polys)
    diag = ob.dimensions.length
    step = max(1, len(polys) // 1500)
    samples = []
    for p in list(mesh.polygons)[::step]:
        c = mw @ p.center
        samples.append((c.x, c.y, c.z))
    ob.evaluated_get(deps).to_mesh_clear()
    return bvh, max(diag, 1e-9), samples


def metrics(ob, src_bvh, src_diag, src_samples):
    from mathutils import Vector
    me = ob.data
    mw = ob.matrix_world
    W = [mw @ v.co for v in me.vertices]
    devs = []
    aspects = []
    valence = defaultdict(set)
    ef = defaultdict(int)
    quads = 0
    centers = []
    polys = []
    for p in me.polygons:
        vs = list(p.vertices)
        polys.append(tuple(vs))
        if len(vs) == 4:
            quads += 1
        pts = [np.array(W[i]) for i in vs]
        centers.append(sum(pts) / len(pts))
        n = len(vs)
        sides = [np.linalg.norm(pts[(i + 1) % n] - pts[i]) for i in range(n)]
        aspects.append(float(max(sides) / max(min(sides), 1e-12)))
        for i in range(n):
            a, b = vs[i], vs[(i + 1) % n]
            valence[a].add(b)
            valence[b].add(a)
            ef[(min(a, b), max(a, b))] += 1
            e1 = pts[(i + 1) % n] - pts[i]
            e2 = pts[(i - 1) % n] - pts[i]
            c = float(np.dot(e1, e2)
                      / max(np.linalg.norm(e1) * np.linalg.norm(e2), 1e-12))
            devs.append(abs(90.0 - math.degrees(
                math.acos(max(-1.0, min(1.0, c))))))
    boundary = set()
    for (a, b), cnt in ef.items():
        if cnt == 1:
            boundary.add(a)
            boundary.add(b)
    interior = [v for v in valence if v not in boundary]
    val_hist = defaultdict(int)
    for v in interior:
        val_hist[len(valence[v])] += 1
    irregular = sum(1 for v in interior if len(valence[v]) != 4)

    # fidelity OUT: result surface -> source (verts + face centers)
    dists_out = []
    stepv = max(1, len(W) // 1200)
    for v in W[::stepv]:
        hit = src_bvh.find_nearest(v)
        if hit is not None and hit[0] is not None:
            dists_out.append((v - hit[0]).length)
    stepc = max(1, len(centers) // 1200)
    for c in centers[::stepc]:
        cv = Vector((float(c[0]), float(c[1]), float(c[2])))
        hit = src_bvh.find_nearest(cv)
        if hit is not None and hit[0] is not None:
            dists_out.append((cv - hit[0]).length)
    # fidelity IN: source samples -> result surface (coverage / shrinkage)
    res_bvh = BVHTree.FromPolygons(W, polys)
    dists_in = []
    for s in src_samples:
        sv = Vector(s)
        hit = res_bvh.find_nearest(sv)
        if hit is not None and hit[0] is not None:
            dists_in.append((sv - hit[0]).length)
    k = 1000.0 / src_diag
    all_d = sorted(dists_out + dists_in)
    p95 = all_d[int(0.95 * (len(all_d) - 1))] if all_d else 0.0
    return {
        "faces": len(me.polygons),
        "quads_pct": round(100.0 * quads / max(len(me.polygons), 1), 1),
        "angle_dev": round(float(sum(devs)) / max(len(devs), 1), 2),
        "aspect": round(float(sum(aspects)) / max(len(aspects), 1), 3),
        "irregular_pct": round(100.0 * irregular / max(len(interior), 1), 1),
        "val3": val_hist.get(3, 0),
        "val5": val_hist.get(5, 0),
        "val_other": sum(c for v, c in val_hist.items() if v not in (3, 4, 5)),
        "fid_out": round(k * sum(dists_out) / max(len(dists_out), 1), 3),
        "fid_in": round(k * sum(dists_in) / max(len(dists_in), 1), 3),
        "fid_p95": round(k * p95, 3),
    }


def export_obj(ob, path):
    me = ob.data
    mw = ob.matrix_world
    with open(path, "w") as f:
        for v in me.vertices:
            w = mw @ v.co
            f.write(f"v {w.x:.6f} {w.y:.6f} {w.z:.6f}\n")
        for p in me.polygons:
            f.write("f " + " ".join(str(i + 1) for i in p.vertices) + "\n")


# ---- shape builders ------------------------------------------------------

def b_sphere():
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32)
    return bpy.context.active_object


def b_torus():
    bpy.ops.mesh.primitive_torus_add(major_segments=64, minor_segments=32)
    return bpy.context.active_object


def b_suzanne():
    bpy.ops.mesh.primitive_monkey_add()
    ob = bpy.context.active_object
    m = ob.modifiers.new("s", "SUBSURF")
    m.levels = 2
    bpy.ops.object.modifier_apply(modifier="s")
    return ob


def _append_biggest(blend):
    with bpy.data.libraries.load(blend) as (src, dst):
        dst.objects = list(src.objects)
    big = None
    for ob in dst.objects:
        if ob and ob.type == "MESH" and (
                big is None or len(ob.data.polygons) > len(big.data.polygons)):
            big = ob
    if big is None:
        return None
    bpy.context.scene.collection.objects.link(big)
    big.parent = None
    bpy.context.view_layer.objects.active = big
    return big


def b_statue():
    path = find_asset("expressive-female-statue")
    return _append_biggest(path) if path else None


def b_skull():
    path = find_asset("skull")
    return _append_biggest(path) if path else None


def b_walkie():
    path = find_asset("walki")
    return _append_biggest(path) if path else None


def b_cylinder():
    bpy.ops.mesh.primitive_cylinder_add(vertices=64)
    ob = bpy.context.active_object
    bpy.ops.object.modifier_add(type="TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier="Triangulate")
    return ob


def b_beveled_cube():
    bpy.ops.mesh.primitive_cube_add()
    ob = bpy.context.active_object
    bev = ob.modifiers.new("b", "BEVEL")
    bev.width = 0.15
    bev.segments = 4
    bpy.ops.object.modifier_apply(modifier="b")
    bpy.ops.object.modifier_add(type="TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier="Triangulate")
    return ob


def b_terrain():
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=128, y_subdivisions=128,
                                    size=4)
    ob = bpy.context.active_object
    tex = bpy.data.textures.new("bench_noise", type="CLOUDS")
    tex.noise_scale = 0.6
    disp = ob.modifiers.new("d", "DISPLACE")
    disp.texture = tex
    disp.strength = 0.8
    bpy.ops.object.modifier_apply(modifier="d")
    return ob


# (case, builder, preset, targets)
SHAPES = [
    ("sphere", b_sphere, "ORGANIC", [800, 3000]),
    ("torus", b_torus, "ORGANIC", [800, 3000]),
    ("suzanne", b_suzanne, "ORGANIC", [800, 3000]),
    ("statue", b_statue, "ORGANIC", [800, 3000]),
    ("skull", b_skull, "ORGANIC", [3000]),
    ("cylinder", b_cylinder, "MECHANICAL", [800, 3000]),
    ("bevel_cube", b_beveled_cube, "MECHANICAL", [800, 3000]),
    ("terrain", b_terrain, "ORGANIC", [3000]),
    ("walkie", b_walkie, "MECHANICAL", [3000]),
]
only_shapes = {s for s in os.environ.get("BENCH_SHAPES", "").split(",") if s}
only_targets = {int(t) for t in os.environ.get("BENCH_TARGETS", "").split(",")
                if t}
BUILDERS = {n: (b, p) for n, b, p, _ in SHAPES}
EXPORT_CASES = {("walkie", 3000), ("torus", 3000), ("statue", 3000),
                ("bevel_cube", 3000), ("sphere", 3000)}

TOOLS = tuple(os.environ.get("BENCH_TOOLS", "requad,qr,qr_exact").split(","))

state = {"queue": [], "phase": "next", "results": [], "sources": {},
         "src": {}}
for name, builder, preset, targets in SHAPES:
    if only_shapes and name not in only_shapes:
        continue
    for target in targets:
        if only_targets and target not in only_targets:
            continue
        for tool in TOOLS:
            state["queue"].append((name, preset, target, tool, 0))
if not only_shapes and not only_targets:
    # ReQuad untouched BASIC defaults on mechanical shapes (preset-bias
    # control) + determinism probes
    if "requad" in TOOLS:
        for name, preset, target in (("cylinder", "BASIC", 800),
                                     ("cylinder", "BASIC", 3000),
                                     ("bevel_cube", "BASIC", 800),
                                     ("bevel_cube", "BASIC", 3000),
                                     ("walkie", "BASIC", 3000)):
            state["queue"].append((name, preset, target, "requad_basic", 0))
    for rep in (1, 2):
        for tool in TOOLS:
            state["queue"].append(("sphere", "ORGANIC", 800, tool, rep))
            state["queue"].append(("statue", "ORGANIC", 3000, tool, rep))


def select_only(ob):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)


def launch(tool, ob, preset, target):
    select_only(ob)
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=win, area=area, region=region,
                                   active_object=ob, object=ob,
                                   selected_objects=[ob]):
        if tool.startswith("requad"):
            s = bpy.context.scene.requad
            s.preset = preset
            s.count_mode = "QUADS"
            s.target_count = target
            s.adaptive_size = 50.0
            for ax in ("sym_x", "sym_y", "sym_z"):
                setattr(s, ax, False)
            return bpy.ops.requad.remesh()
        q = bpy.context.scene.qremesher
        q.target_count = target
        q.symmetry_x = q.symmetry_y = q.symmetry_z = False
        q.adapt_quad_count = (tool == "qr")
        return bpy.ops.qremesher.remesh()


def tick():
    ph = state["phase"]
    if ph == "next":
        if not state["queue"]:
            with open(RESULTS_PATH, "w") as f:
                json.dump(state["results"], f, indent=1)
            bpy.ops.wm.quit_blender()
            return None
        name, preset, target, tool, rep = state["queue"].pop(0)
        if name not in state["sources"]:
            builder, _ = BUILDERS[name]
            src_ob = builder()
            if src_ob is None:
                state["results"].append(
                    {"case": name, "tool": tool, "error": "asset not found"})
                state["queue"] = [q for q in state["queue"] if q[0] != name]
                return 0.5
            state["sources"][name] = src_ob.name
            state["src"][name] = source_data(src_ob)
        src = bpy.data.objects[state["sources"][name]]
        src.hide_set(False)
        state["before"] = {o.name for o in bpy.data.objects}
        state["current"] = (name, tool, preset, target, rep)
        state["t0"] = time.time()
        try:
            ret = launch(tool, src, preset, target)
            state["phase"] = "wait"
            if "RUNNING_MODAL" not in str(ret) and "FINISHED" not in str(ret):
                state["results"].append(
                    {"case": name, "tool": tool, "error": str(ret)})
                state["phase"] = "next"
        except Exception as exc:  # noqa: BLE001
            state["results"].append(
                {"case": name, "tool": tool, "error": str(exc)[:200]})
            state["phase"] = "next"
        return 0.5

    name, tool, preset, target, rep = state["current"]
    new = [o for o in bpy.data.objects
           if o.type == "MESH" and o.name not in state["before"]]
    if new:
        elapsed = round(time.time() - state["t0"], 1)
        res = new[0]
        entry = {"case": name, "tool": tool, "target": target, "rep": rep,
                 "time_s": elapsed}
        bvh, diag, samples = state["src"][name]
        try:
            entry.update(metrics(res, bvh, diag, samples))
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"metrics: {str(exc)[:120]}"
        state["results"].append(entry)
        if (name, target) in EXPORT_CASES and rep == 0:
            try:
                export_obj(res, f"{OBJ_DIR}/{name}_{target}_{tool}.obj")
            except Exception:
                pass
        # incremental save so a crash never loses the campaign
        with open(RESULTS_PATH, "w") as f:
            json.dump(state["results"], f, indent=1)
        for ob in new:
            ob.hide_set(True)
        state["phase"] = "next"
        return 1.0
    if time.time() - state["t0"] > RUN_TIMEOUT:
        state["results"].append(
            {"case": name, "tool": tool, "target": target, "rep": rep,
             "error": "timeout"})
        state["phase"] = "next"
    return 1.0


bpy.app.timers.register(tick, first_interval=3.0)
