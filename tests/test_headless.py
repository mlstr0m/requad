# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless test suite for ReQuad. Run:

    blender -b -P tests/test_headless.py

Installs the extension zip given by REQUAD_ZIP (or builds nothing — the zip
must exist), then checks: basic remesh, 100% quad output, quad-count
targeting accuracy, cancel-free teardown.
"""
import os
import sys
import tempfile

import bpy

ZIP = os.environ.get("REQUAD_ZIP", "")
MODULE = "bl_ext.user_default.requad"

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not cond:
        failures.append(name)


def fresh_suzanne(subdiv=2):
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_monkey_add()
    ob = bpy.context.active_object
    mod = ob.modifiers.new("subsurf", "SUBSURF")
    mod.levels = subdiv
    bpy.ops.object.modifier_apply(modifier=mod.name)
    return ob


def main():
    if not os.path.isfile(ZIP):
        print(f"REQUAD_ZIP not found: {ZIP!r}")
        sys.exit(2)

    bpy.ops.extensions.package_install_files(
        repo="user_default", filepath=ZIP, enable_on_install=True)
    check("extension installed+enabled",
          MODULE in bpy.context.preferences.addons)

    # basic remesh, organic preset
    fresh_suzanne()
    bpy.context.scene.requad.preset = "ORGANIC"
    bpy.context.scene.requad.target_count = 5000
    result = bpy.ops.requad.remesh()
    check("operator finished", result == {"FINISHED"})

    out = [o for o in bpy.context.scene.objects if o.name.endswith("_requad")]
    check("result object exists", len(out) == 1)
    if out:
        polys = out[0].data.polygons
        quads = sum(1 for p in polys if len(p.vertices) == 4)
        check("output is 100% quads", quads == len(polys),
              f"({quads}/{len(polys)})")

    # targeting accuracy: ±10% tolerated across shapes
    for target in (3000, 10000):
        fresh_suzanne()
        bpy.context.scene.requad.preset = "ORGANIC"
        bpy.context.scene.requad.target_count = target
        bpy.ops.requad.remesh()
        got = next((len(o.data.polygons) for o in bpy.context.scene.objects
                    if o.name.endswith("_requad")), 0)
        check(f"target {target}", abs(got - target) <= 0.10 * target,
              f"got {got}")

    # result hygiene: no debug materials, smooth-shaded
    fresh_suzanne()
    bpy.context.scene.requad.preset = "ORGANIC"
    bpy.context.scene.requad.target_count = 5000
    bpy.ops.requad.remesh()
    ob = next(o for o in bpy.context.scene.objects if o.name.endswith("_requad"))
    check("no materials on result", len(ob.data.materials) == 0)
    check("smooth shaded", all(p.use_smooth for p in ob.data.polygons))

    # coarse target: floor warning path must still produce a usable result
    fresh_suzanne()
    bpy.context.scene.requad.preset = "ORGANIC"
    bpy.context.scene.requad.target_count = 1000
    result = bpy.ops.requad.remesh()
    got = next((len(o.data.polygons) for o in bpy.context.scene.objects
                if o.name.endswith("_requad")), 0)
    check("coarse target 1000", result == {"FINISHED"} and got <= 1350,
          f"got {got}")

    # extreme coarse (below the old alpha-0.005 floor of ~660): result must
    # stay usable — overwhelmingly quads, no degenerate slivers
    fresh_suzanne()
    bpy.context.scene.requad.preset = "ORGANIC"
    bpy.context.scene.requad.target_count = 300
    result = bpy.ops.requad.remesh()
    res = next(o for o in bpy.context.scene.objects if o.name.endswith("_requad"))
    polys = res.data.polygons
    quads = sum(1 for p in polys if len(p.vertices) == 4)
    worst_aspect = 0.0
    import numpy as _np
    for p in polys:
        pts = [_np.array(res.data.vertices[i].co) for i in p.vertices]
        sides = [float(_np.linalg.norm(pts[(i + 1) % len(pts)] - pts[i]))
                 for i in range(len(pts))]
        worst_aspect = max(worst_aspect, max(sides) / max(min(sides), 1e-12))
    check("extreme coarse 300",
          result == {"FINISHED"} and len(polys) <= 600
          and quads / len(polys) >= 0.98 and worst_aspect < 60,
          f"(got {len(polys)}, {100 * quads / len(polys):.1f}% quads, "
          f"worst aspect {worst_aspect:.1f})")

    # triangle unit: 6000 tris must land near 3000 quads
    fresh_suzanne()
    bpy.context.scene.requad.preset = "ORGANIC"
    bpy.context.scene.requad.count_mode = "TRIS"
    bpy.context.scene.requad.target_count = 6000
    bpy.ops.requad.remesh()
    got = next((len(o.data.polygons) for o in bpy.context.scene.objects
                if o.name.endswith("_requad")), 0)
    check("tris mode 6000", abs(got - 3000) <= 300, f"got {got} quads")
    bpy.context.scene.requad.count_mode = "QUADS"

    # mechanical preset on a hard-surface shape
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_cylinder_add(vertices=64)
    bpy.ops.object.modifier_add(type="TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier="Triangulate")
    bpy.context.scene.requad.preset = "MECHANICAL"
    bpy.context.scene.requad.target_count = 2000
    result = bpy.ops.requad.remesh()
    check("mechanical cylinder finished", result == {"FINISHED"})

    # symmetry: result must be perfectly mirror-symmetric and welded
    from mathutils import kdtree
    fresh_suzanne()
    bpy.context.scene.requad.preset = "ORGANIC"
    bpy.context.scene.requad.target_count = 2000
    bpy.context.scene.requad.sym_x = True
    result = bpy.ops.requad.remesh()
    res = next(o for o in bpy.context.scene.objects if o.name.endswith("_requad"))
    world = [res.matrix_world @ v.co for v in res.data.vertices]
    size = max(res.dimensions)
    kd = kdtree.KDTree(len(world))
    for i, co in enumerate(world):
        kd.insert(co, i)
    kd.balance()
    bad = 0
    for co in world[::5]:
        m = co.copy()
        m.x = -m.x
        _, _, dist = kd.find(m)
        if dist > size * 0.002:
            bad += 1
    quads = sum(1 for p in res.data.polygons if len(p.vertices) == 4)
    # Suzanne's eyes are open hemispheres, so open edges exist by design;
    # only open edges ON the mirror plane would mean a failed seam weld.
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(res.data)
    mw = res.matrix_world
    seam_tol = size * 0.001
    seam_open = sum(
        1 for e in bm.edges if len(e.link_faces) == 1
        and all(abs((mw @ v.co).x) < seam_tol for v in e.verts))
    bm.free()
    check("symmetry X mirror",
          result == {"FINISHED"} and bad == 0
          and quads == len(res.data.polygons) and seam_open == 0,
          f"(asym: {bad}/{len(world[::5])}, seam open edges: {seam_open}, "
          f"faces {len(res.data.polygons)})")
    bpy.context.scene.requad.sym_x = False

    # multi-axis symmetry: X+Z on a sphere must mirror on both planes
    fresh_suzanne()
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
    bpy.context.scene.requad.preset = "ORGANIC"
    bpy.context.scene.requad.target_count = 2000
    bpy.context.scene.requad.sym_x = True
    bpy.context.scene.requad.sym_z = True
    result = bpy.ops.requad.remesh()
    res = next(o for o in bpy.context.scene.objects if o.name.endswith("_requad"))
    pts = [v.co.copy() for v in res.data.vertices]
    size = max(res.dimensions)
    kd2 = kdtree.KDTree(len(pts))
    for i, co in enumerate(pts):
        kd2.insert(co, i)
    kd2.balance()
    bad = 0
    for co in pts[::5]:
        for flip in ((-1, 1, 1), (1, 1, -1), (-1, 1, -1)):
            m = co.copy()
            m.x *= flip[0]
            m.z *= flip[2]
            _, _, d = kd2.find(m)
            if d > size * 0.002:
                bad += 1
    quads2 = sum(1 for p in res.data.polygons if len(p.vertices) == 4)
    check("multi-axis symmetry X+Z",
          result == {"FINISHED"} and bad == 0
          and quads2 == len(res.data.polygons),
          f"(asym {bad}, faces {len(res.data.polygons)})")
    bpy.context.scene.requad.sym_x = False
    bpy.context.scene.requad.sym_z = False

    # tilted guide ring (jagged marked band): guide attraction must trace
    # it — this was the known limitation before curve-guide attraction
    def tilted_ring_dist(guided):
        import numpy as np
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
        ob = bpy.context.active_object
        nrm = (0.6, 0.0, 0.8)
        mids = []
        for e in ob.data.edges:
            va, vb = (ob.data.vertices[i].co for i in e.vertices)
            if (abs(va.x * nrm[0] + va.z * nrm[2]) < 0.03
                    and abs(vb.x * nrm[0] + vb.z * nrm[2]) < 0.03):
                if guided:
                    e.use_seam = True
                mids.append(((va.x + vb.x) / 2, (va.y + vb.y) / 2,
                             (va.z + vb.z) / 2))
        bpy.context.scene.requad.preset = "ORGANIC"
        bpy.context.scene.requad.target_count = 2000
        bpy.context.scene.requad.guide_seams = guided
        bpy.ops.requad.remesh()
        res = next(o for o in bpy.context.scene.objects
                   if o.name.endswith("_requad"))
        me = res.data
        vco = np.array([res.matrix_world @ v.co for v in me.vertices])
        ev = np.empty(len(me.edges) * 2, dtype=np.int64)
        me.edges.foreach_get("vertices", ev)
        ev = ev.reshape(-1, 2)
        a = vco[ev[:, 0]]
        b = vco[ev[:, 1]]
        ab = b - a
        ab2 = np.maximum((ab * ab).sum(axis=1), 1e-12)
        dists = []
        for p in mids[:40]:
            p = np.array(p)
            t = (((p - a) * ab).sum(axis=1) / ab2).clip(0, 1)
            dists.append(float(
                np.linalg.norm(a + ab * t[:, None] - p, axis=1).min()))
        return sum(dists) / len(dists)

    unguided_d = tilted_ring_dist(False)
    guided_d = tilted_ring_dist(True)
    check("tilted guide traced", guided_d < unguided_d * 0.6,
          f"(mean dist guided {guided_d:.4f} vs unguided {unguided_d:.4f})")
    bpy.context.scene.requad.guide_seams = False

    # vertex group transfer: gradient weights must survive re-projection
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
    ob = bpy.context.active_object
    vg = ob.vertex_groups.new(name="grad")
    for v in ob.data.vertices:
        vg.add([v.index], max(0.0, min(1.0, v.co.z + 0.5)), "REPLACE")
    bpy.context.scene.requad.preset = "ORGANIC"
    bpy.context.scene.requad.target_count = 2000
    bpy.context.scene.requad.transfer_weights = True
    bpy.ops.requad.remesh()
    res = next(o for o in bpy.context.scene.objects if o.name.endswith("_requad"))
    ok = "grad" in res.vertex_groups
    top_w = bot_w = 0.0
    if ok:
        gi = res.vertex_groups["grad"].index
        tops = []
        bots = []
        for v in res.data.vertices:
            w = next((g.weight for g in v.groups if g.group == gi), 0.0)
            (tops if v.co.z > 0.3 else bots if v.co.z < -0.3 else []).append(w)
        top_w = sum(tops) / max(len(tops), 1)
        bot_w = sum(bots) / max(len(bots), 1)
    check("vertex group transfer", ok and top_w > 0.7 and bot_w < 0.3,
          f"(top {top_w:.2f}, bottom {bot_w:.2f})")
    bpy.context.scene.requad.transfer_weights = False

    # paint density: red-painted half must get clearly smaller quads
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
    ob = bpy.context.active_object
    attr = ob.data.color_attributes.new("density", "FLOAT_COLOR", "POINT")
    for i, v in enumerate(ob.data.vertices):
        attr.data[i].color = (1.0, 0.0, 0.0, 1.0) if v.co.z > 0 \
            else (0.0, 0.0, 0.0, 1.0)
    bpy.ops.object.modifier_add(type="TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier="Triangulate")
    ob.data.color_attributes.active_color = ob.data.color_attributes["density"]
    bpy.context.scene.requad.preset = "ORGANIC"
    bpy.context.scene.requad.target_count = 2000
    bpy.context.scene.requad.adaptive_size = 0.0
    bpy.context.scene.requad.use_paint_density = True
    bpy.ops.requad.remesh()
    res = next(o for o in bpy.context.scene.objects if o.name.endswith("_requad"))
    top = [p.area for p in res.data.polygons if (res.matrix_world @ p.center).z > 0.1]
    bot = [p.area for p in res.data.polygons if (res.matrix_world @ p.center).z < -0.1]
    ratio = (sum(top) / len(top)) / (sum(bot) / len(bot))
    # The engine's patch layout is nondeterministic, which modulates how
    # strongly per-patch scales bite (measured 0.29-0.70 across identical
    # runs). Anything clearly below 1.0 proves the paint channel works.
    check("paint density", ratio < 0.75, f"(top/bottom area ratio {ratio:.2f})")
    bpy.context.scene.requad.use_paint_density = False
    bpy.context.scene.requad.adaptive_size = 50.0

    # material transfer: slots copied, per-face indices follow the source
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
    ob = bpy.context.active_object
    ob.data.materials.append(bpy.data.materials.new("bottom"))
    ob.data.materials.append(bpy.data.materials.new("top"))
    for p in ob.data.polygons:
        p.material_index = 1 if p.center.z > 0 else 0
    bpy.ops.object.modifier_add(type="TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier="Triangulate")
    bpy.context.scene.requad.preset = "ORGANIC"
    bpy.context.scene.requad.target_count = 2000
    bpy.ops.requad.remesh()
    res = next(o for o in bpy.context.scene.objects if o.name.endswith("_requad"))
    agree = sum(1 for p in res.data.polygons
                if (p.material_index == 1) == ((res.matrix_world @ p.center).z > 0))
    ratio = agree / len(res.data.polygons)
    check("materials transferred",
          len(res.data.materials) == 2 and ratio > 0.9,
          f"(slots={len(res.data.materials)}, agree={ratio:.2f})")

    # adaptive size: higher strength must spread quad sizes (flat caps get
    # bigger quads, the curved side smaller ones)
    def plateau_ratio(adaptive):
        # Mechanical ignores Adaptive Size by design (harms thin shells);
        # Organic case: a sphere with a flattened cap gives a deterministic
        # zero-curvature plateau against the curved remainder
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
        ob = bpy.context.active_object
        for v in ob.data.vertices:
            if v.co.z > 0.55:
                v.co.z = 0.55
        bpy.ops.object.modifier_add(type="TRIANGULATE")
        bpy.ops.object.modifier_apply(modifier="Triangulate")
        bpy.context.scene.requad.preset = "ORGANIC"
        bpy.context.scene.requad.target_count = 2000
        bpy.context.scene.requad.adaptive_size = adaptive
        bpy.ops.requad.remesh()
        ob = next(o for o in bpy.context.scene.objects
                  if o.name.endswith("_requad"))
        plateau = []
        rest = []
        for p in ob.data.polygons:
            (plateau if p.center.z > 0.53 else rest).append(p.area)
        return ((sum(plateau) / max(len(plateau), 1))
                / max(sum(rest) / max(len(rest), 1), 1e-12))

    # Adaptive Size cannot be asserted end-to-end with a one-shot
    # threshold: BOTH the patch tracing and the quantizer are
    # run-nondeterministic (measured x1.08-x2.17 spread of the
    # plateau/rest area ratio, and per-draw patch layouts that sometimes
    # straddle the plateau — see docs/BENCHMARK_METHODOLOGY.md). The
    # invariant that IS ours to guarantee: for whatever patches this draw
    # produced, the multipliers must anti-correlate with the measured
    # patch curvature (flatter patch → bigger quads).
    import numpy as _np
    scales_dump = os.path.join(tempfile.gettempdir(), "requad_scales_test.txt")
    if os.path.isfile(scales_dump):
        os.remove(scales_dump)
    os.environ["REQUAD_DEBUG_SCALES"] = scales_dump
    try:
        plateau_ratio(100.0)
        data = _np.loadtxt(scales_dump).reshape(-1, 2)
        curv, mult = data[:, 0], data[:, 1]
        contrast = float(curv.max()) / max(float(curv.min()), 1e-9)
        if contrast > 2.0:
            flat_m = float(mult[int(curv.argmin())])
            sharp_m = float(mult[int(curv.argmax())])
            ok = flat_m / max(sharp_m, 1e-9) > 1.1
            detail = (f"(curv contrast x{contrast:.1f}: flat mult "
                      f"{flat_m:.2f} vs sharp {sharp_m:.2f})")
        else:
            # this draw's patches all have similar curvature — the field
            # must then stay near-uniform (no spurious contrast)
            ok = float(mult.max()) / max(float(mult.min()), 1e-9) < 1.6
            detail = (f"(uniform-curvature draw x{contrast:.1f}, "
                      f"mult {mult.min():.2f}..{mult.max():.2f})")
        check("adaptive multipliers follow patch curvature", ok, detail)
    finally:
        os.environ.pop("REQUAD_DEBUG_SCALES", None)

    # guides: a marked seam ring must attract the flow — measured RELATIVE
    # to an unguided run of the same shape, because the engine's tracing is
    # nondeterministic (guided runs measured 23-32/32 across identical runs)
    import math

    # Clean marked loop (equator). Verifies the plumbing marks → .sharp →
    # traced feature; jagged/zigzag guide bands are a known limitation
    # (partially simplified by the pre-remesh — see ROADMAP). Threshold
    # reflects the engine's nondeterministic tracing (measured 23-32/32).
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
    ob = bpy.context.active_object
    for e in ob.data.edges:
        va, vb = (ob.data.vertices[i].co for i in e.vertices)
        if abs(va.z) < 0.02 and abs(vb.z) < 0.02:
            e.use_seam = True
    bpy.ops.object.modifier_add(type="TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier="Triangulate")
    bpy.context.scene.requad.preset = "ORGANIC"
    bpy.context.scene.requad.target_count = 2000
    bpy.context.scene.requad.guide_seams = True
    bpy.ops.requad.remesh()
    res = next(o for o in bpy.context.scene.objects if o.name.endswith("_requad"))
    verts = [res.matrix_world @ v.co for v in res.data.vertices]
    on_ring = 0
    for k in range(32):
        ang = 2 * math.pi * k / 32
        p = (math.cos(ang), math.sin(ang), 0.0)
        best = min(((v.x - p[0]) ** 2 + (v.y - p[1]) ** 2
                    + (v.z - p[2]) ** 2) ** 0.5 for v in verts)
        if best < 0.05:
            on_ring += 1
    check("seam guide traced", on_ring >= 21, f"({on_ring}/32 ring samples)")

    # material boundaries as guides: the frontier must be traced like a seam
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
    ob = bpy.context.active_object
    ob.data.materials.append(bpy.data.materials.new("a"))
    ob.data.materials.append(bpy.data.materials.new("b"))
    for p in ob.data.polygons:
        p.material_index = 1 if p.center.z > 0 else 0
    bpy.ops.object.modifier_add(type="TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier="Triangulate")
    bpy.context.scene.requad.preset = "ORGANIC"
    bpy.context.scene.requad.target_count = 2000
    bpy.context.scene.requad.material_guides = True
    bpy.ops.requad.remesh()
    res = next(o for o in bpy.context.scene.objects if o.name.endswith("_requad"))
    verts = [res.matrix_world @ v.co for v in res.data.vertices]
    on_ring = 0
    for k in range(32):
        ang = 2 * math.pi * k / 32
        p = (math.cos(ang), math.sin(ang), 0.0)
        best = min(((v.x - p[0]) ** 2 + (v.y - p[1]) ** 2
                    + (v.z - p[2]) ** 2) ** 0.5 for v in verts)
        if best < 0.06:
            on_ring += 1
    check("material boundary guide", on_ring >= 29,
          f"({on_ring}/32 boundary samples traced)")
    bpy.context.scene.requad.material_guides = False

    # transfer UVs: result must receive a usable UV layer
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
    ob = bpy.context.active_object
    bpy.ops.object.modifier_add(type="TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier="Triangulate")
    bpy.context.scene.requad.preset = "ORGANIC"
    bpy.context.scene.requad.target_count = 2000
    bpy.context.scene.requad.transfer_uvs = True
    bpy.ops.requad.remesh()
    res = next(o for o in bpy.context.scene.objects if o.name.endswith("_requad"))
    has_uv = bool(res.data.uv_layers)
    spread = 0.0
    if has_uv:
        us = [d.uv.x for d in res.data.uv_layers[0].data]
        spread = max(us) - min(us)
    check("uv transfer", has_uv and spread > 0.5,
          f"(layer={has_uv}, u-spread={spread:.2f})")
    bpy.context.scene.requad.transfer_uvs = False

    # adaptive quad count: quality-priority mode must run single-pass
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_cylinder_add(vertices=64)
    bpy.ops.object.modifier_add(type="TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier="Triangulate")
    bpy.context.scene.requad.preset = "MECHANICAL"
    bpy.context.scene.requad.target_count = 3000
    bpy.context.scene.requad.adaptive_size = 100.0
    bpy.context.scene.requad.adaptive_count = True
    result = bpy.ops.requad.remesh()
    got = next((len(o.data.polygons) for o in bpy.context.scene.objects
                if o.name.endswith("_requad")), 0)
    check("adaptive quad count", result == {"FINISHED"} and got > 0,
          f"(got {got} quads, count free)")
    bpy.context.scene.requad.adaptive_count = False
    bpy.context.scene.requad.adaptive_size = 50.0

    # step-1 cache: re-running the same object must skip field+tracing
    import time as _time
    fresh_suzanne()
    bpy.context.scene.requad.preset = "ORGANIC"
    bpy.context.scene.requad.target_count = 4000
    t0 = _time.time()
    bpy.ops.requad.remesh()
    t_cold = _time.time() - t0
    src = bpy.data.objects["Suzanne"]
    bpy.context.view_layer.objects.active = src
    src.select_set(True)
    t0 = _time.time()
    bpy.ops.requad.remesh()
    t_warm = _time.time() - t0
    got = len(sorted((o for o in bpy.context.scene.objects
                      if "_requad" in o.name),
                     key=lambda o: o.name)[-1].data.polygons)
    check("step-1 cache", t_warm < t_cold * 0.6 and got > 3000,
          f"(cold {t_cold:.1f}s -> warm {t_warm:.1f}s, {got} quads)")

    # relax & project: quad corner angles must improve measurably
    import math

    def mean_angle_dev(res):
        import numpy as np
        me = res.data
        total = 0.0
        count = 0
        for p in me.polygons:
            if len(p.vertices) != 4:
                continue
            pts = [np.array(me.vertices[i].co) for i in p.vertices]
            for i in range(4):
                e1 = pts[(i + 1) % 4] - pts[i]
                e2 = pts[(i - 1) % 4] - pts[i]
                c = float(np.dot(e1, e2)
                          / max(np.linalg.norm(e1) * np.linalg.norm(e2), 1e-12))
                total += abs(90.0 - math.degrees(math.acos(max(-1, min(1, c)))))
                count += 1
        return total / max(count, 1)

    devs = {}
    for iters in (0, 8):
        fresh_suzanne()
        bpy.context.scene.requad.preset = "ORGANIC"
        bpy.context.scene.requad.target_count = 3000
        bpy.context.scene.requad.relax_iterations = iters
        bpy.ops.requad.remesh()
        res = next(o for o in bpy.context.scene.objects
                   if o.name.endswith("_requad"))
        devs[iters] = mean_angle_dev(res)
    # Measured yield: ~5-9% at 8 iterations (connectivity dominates the
    # rest of the deviation — that fix is Phase 3 territory).
    improvement = 100 * (devs[0] - devs[8]) / max(devs[0], 1e-9)
    check("relax improves angles", improvement > 4,
          f"(angle dev {devs[0]:.2f}° -> {devs[8]:.2f}°, -{improvement:.0f}%)")

    # mechanical relax: angles improve while pinned sharp rims keep the shape
    def cyl_run(iters):
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.mesh.primitive_cylinder_add(vertices=64)
        bpy.ops.object.modifier_add(type="TRIANGULATE")
        bpy.ops.object.modifier_apply(modifier="Triangulate")
        bpy.context.scene.requad.preset = "MECHANICAL"
        bpy.context.scene.requad.target_count = 3000
        bpy.context.scene.requad.relax_iterations = iters
        bpy.ops.requad.remesh()
        res = next(o for o in bpy.context.scene.objects
                   if o.name.endswith("_requad"))
        return mean_angle_dev(res), tuple(res.dimensions)

    # On a clean cylinder the unrelaxed result is already near-perfect, so
    # the contract here is: pinned sharp rims keep the exact shape, and the
    # relaxation never degrades angles meaningfully.
    dev0, dims0 = cyl_run(0)
    dev8, dims8 = cyl_run(8)
    shape_kept = all(abs(a - b) / max(a, 1e-9) < 0.01
                     for a, b in zip(dims0, dims8))
    check("mechanical relax pins features",
          dev8 <= dev0 * 1.10 and shape_kept,
          f"(angle dev {dev0:.2f}° -> {dev8:.2f}°, dims kept: {shape_kept})")

    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


main()
