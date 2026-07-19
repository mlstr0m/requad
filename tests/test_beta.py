# SPDX-License-Identifier: GPL-3.0-or-later
"""Beta-test battery: real-user workflows and hostile object states. Run:

    REQUAD_ZIP=path/to/requad.zip blender -b -P tests/test_beta.py
"""
import math
import os
import sys

import bpy
from mathutils import kdtree

ZIP = os.environ.get("REQUAD_ZIP", "")
failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not cond:
        failures.append(name)


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)


def remesh(ob, target=2000, preset="ORGANIC", **kw):
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    s = bpy.context.scene.requad
    s.preset = preset
    s.target_count = target
    for k, v in kw.items():
        setattr(s, k, v)
    before = set(bpy.context.scene.objects)
    result = bpy.ops.requad.remesh()
    new = [o for o in set(bpy.context.scene.objects) - before
           if o.type == "MESH"]
    return result, (new[0] if new else None)


def main():
    if not os.path.isfile(ZIP):
        print(f"REQUAD_ZIP not found: {ZIP!r}")
        sys.exit(2)
    bpy.ops.extensions.package_install_files(
        repo="user_default", filepath=ZIP, enable_on_install=True)

    # 1. negatively scaled object: result must not be inside-out
    fresh()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    ob = bpy.context.active_object
    ob.scale = (-1.0, 1.0, 1.0)
    result, res = remesh(ob)
    outward = 0
    mw = res.matrix_world
    import numpy as np
    for p in res.data.polygons:
        c = mw @ p.center
        n = (mw.to_3x3() @ p.normal)
        if c.dot(n) > 0:
            outward += 1
    ratio = outward / len(res.data.polygons)
    check("negative scale keeps normals outward", ratio > 0.95,
          f"({100 * ratio:.0f}% outward)")

    # 2. rotated object + local-axis symmetry
    fresh()
    bpy.ops.mesh.primitive_monkey_add()
    ob = bpy.context.active_object
    mod = ob.modifiers.new("s", "SUBSURF")
    mod.levels = 1
    bpy.ops.object.modifier_apply(modifier="s")
    ob.rotation_euler = (0.3, 0.2, 0.8)
    result, res = remesh(ob, sym_x=True)
    # with symmetry the result inherits the source transform and its mesh
    # data lives in source-local space: local-frame mirror check
    local = [v.co.copy() for v in res.data.vertices]
    size = max(res.dimensions)
    kd = kdtree.KDTree(len(local))
    for i, co in enumerate(local):
        kd.insert(co, i)
    kd.balance()
    bad = 0
    for co in local[::7]:
        m = co.copy()
        m.x = -m.x
        _, _, d = kd.find(m)
        if d > size * 0.002:
            bad += 1
    check("rotated object local symmetry", result == {"FINISHED"} and bad == 0,
          f"(asym {bad}/{len(local[::7])})")
    bpy.context.scene.requad.sym_x = False

    # 3-4. extreme object scales
    for radius, label in ((0.001, "tiny 1mm"), (500.0, "huge 500m")):
        fresh()
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=48, ring_count=24, radius=radius)
        result, res = remesh(bpy.context.active_object, target=2000)
        got = len(res.data.polygons) if res else 0
        check(f"{label} sphere", result == {"FINISHED"}
              and abs(got - 2000) <= 300, f"(got {got})")

    # 5. live (unapplied) modifier stack is what gets remeshed
    fresh()
    bpy.ops.mesh.primitive_cube_add()
    ob = bpy.context.active_object
    ob.modifiers.new("s", "SUBSURF").levels = 3
    result, res = remesh(ob, target=1500)
    dims = res.dimensions
    rounded = max(dims) < 1.9  # subsurfed cube shrinks well under 2m
    check("live modifier stack used", result == {"FINISHED"} and rounded,
          f"(dims {tuple(round(d, 2) for d in dims)})")

    # 6. multi-user mesh data: sibling object must stay untouched
    fresh()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    a = bpy.context.active_object
    b = a.copy()  # shares the mesh datablock
    bpy.context.scene.collection.objects.link(b)
    before_polys = len(b.data.polygons)
    result, res = remesh(a)
    check("multi-user mesh untouched",
          result == {"FINISHED"} and len(b.data.polygons) == before_polys
          and b.data is a.data,
          f"(sibling still {len(b.data.polygons)} polys)")

    # 7. remesh of a remesh (chained)
    fresh()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
    result, res = remesh(bpy.context.active_object, target=3000)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    result2, res2 = remesh(res, target=1000)
    got = len(res2.data.polygons) if res2 and res2 != res else 0
    check("chained remesh", result2 == {"FINISHED"} and got > 500,
          f"(second pass {got} quads)")

    # 8. shape keys: the evaluated (deformed) shape is remeshed
    fresh()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    ob = bpy.context.active_object
    ob.shape_key_add(name="Basis")
    key = ob.shape_key_add(name="Stretch")
    for pt in key.data:
        pt.co.z *= 2.0
    key.value = 1.0
    ob.data.shape_keys.key_blocks["Stretch"].value = 1.0
    result, res = remesh(ob)
    stretched = res.dimensions.z / max(res.dimensions.x, 1e-9) > 1.6
    check("shape key deformation captured",
          result == {"FINISHED"} and stretched,
          f"(z/x ratio {res.dimensions.z / max(res.dimensions.x, 1e-9):.2f})")

    # 9. double-run guard
    fresh()
    bpy.ops.mesh.primitive_uv_sphere_add()
    ob = bpy.context.active_object
    bpy.context.window_manager.requad_progress = 42
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    result = bpy.ops.requad.remesh()
    bpy.context.window_manager.requad_progress = -1
    check("double-run guard", result == {"CANCELLED"}, f"({result})")

    # 10. poll blocks edit mode
    fresh()
    bpy.ops.mesh.primitive_uv_sphere_add()
    bpy.ops.object.mode_set(mode="EDIT")
    can_run = bpy.ops.requad.remesh.poll()
    bpy.ops.object.mode_set(mode="OBJECT")
    check("edit mode blocked", not can_run)

    # 11. undo after remesh does not corrupt state
    fresh()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    ob = bpy.context.active_object
    try:
        result, res = remesh(ob)
        try:
            bpy.ops.ed.undo_push(message="pre-undo")
            bpy.ops.ed.undo()
        except RuntimeError:
            pass  # background mode has no interactive undo stack
        fresh()
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
        result2, res2 = remesh(bpy.context.active_object)
        check("undo then rerun", result2 == {"FINISHED"} and res2 is not None)
    except Exception as exc:  # noqa: BLE001
        check("undo then rerun", False, f"EXCEPTION: {exc}")

    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


main()
