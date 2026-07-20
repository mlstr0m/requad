# SPDX-License-Identifier: GPL-3.0-or-later
"""Render side-by-side wireframe comparisons from bench_meshes/*.obj.

    blender -b --python benchmarks/render_compare.py -- <bench_meshes_dir> <out_dir>

For each case present, renders ReQuad (left) vs Quad Remesher exact-count
(right) with Freestyle quad wireframes, labeled with tool and face count.
"""
import math
import os
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
MESH_DIR = argv[0] if argv else "bench_meshes"
OUT_DIR = argv[1] if len(argv) > 1 else "media"
os.makedirs(OUT_DIR, exist_ok=True)

CASES = [
    ("walkie_3000", "requad", "qr_exact"),
    ("statue_3000", "requad", "qr_exact"),
    ("torus_3000", "requad", "qr_exact"),
    ("sphere_3000", "requad", "qr_exact"),
    ("bevel_cube_3000", "requad", "qr_exact"),
]
LABEL = {"requad": "ReQuad", "qr": "Quad Remesher (default)",
         "qr_exact": "Quad Remesher"}
# viewing elevation tuned per family: flat/lying objects need a top view
ELEV = {"walkie_3000": 1.05, "terrain_3000": 1.1, "statue_3000": 1.35}


def make_material(name, color, rough=0.55):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    return mat


def import_obj(path):
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(filepath=path, forward_axis="Y", up_axis="Z")
    return next(o for o in set(bpy.data.objects) - before if o.type == "MESH")


def bounds(objects):
    lo = Vector((1e18, 1e18, 1e18))
    hi = Vector((-1e18, -1e18, -1e18))
    for ob in objects:
        for corner in ob.bound_box:
            w = ob.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    return lo, hi


for case, tool_a, tool_b in CASES:
    pa = os.path.join(MESH_DIR, f"{case}_{tool_a}.obj")
    pb = os.path.join(MESH_DIR, f"{case}_{tool_b}.obj")
    if not (os.path.isfile(pa) and os.path.isfile(pb)):
        print(f"skip {case} (missing obj)")
        continue
    bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.use_freestyle = True
    scene.render.line_thickness = 1.1
    vl = bpy.context.view_layer
    vl.use_freestyle = True
    ls = vl.freestyle_settings.linesets.new("wires")
    ls.select_silhouette = False
    ls.select_border = True
    ls.select_crease = False
    ls.select_edge_mark = True
    ls.linestyle.color = (0.05, 0.05, 0.06)

    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (
        0.93, 0.93, 0.95, 1.0)
    scene.world = world

    mats = {tool_a: make_material("base_a", (0.62, 0.70, 0.80)),
            tool_b: make_material("base_b", (0.80, 0.68, 0.58))}

    oa = import_obj(pa)
    ob_ = import_obj(pb)
    span = max(max(oa.dimensions), max(ob_.dimensions))
    gap = span * 0.58
    for ob, tool, off in ((oa, tool_a, -gap), (ob_, tool_b, gap)):
        # the two tools bake different world offsets into their results —
        # recenter each mesh's bbox on its slot so the pair lines up
        olo, ohi = bounds([ob])
        ob.location = ob.location - (olo + ohi) / 2.0 + Vector((off, 0, 0))
        for p in ob.data.polygons:
            p.use_smooth = True
        fm = ob.data.attributes.get("freestyle_edge") \
            or ob.data.attributes.new("freestyle_edge", "BOOLEAN", "EDGE")
        fm.data.foreach_set("value", [True] * len(ob.data.edges))
        ob.data.materials.clear()
        ob.data.materials.append(mats[tool])

    bpy.context.view_layer.update()  # refresh matrix_world in -b mode
    lo, hi = bounds([oa, ob_])
    center = (lo + hi) / 2.0
    width = hi.x - lo.x

    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.5
    sun.data.angle = 0.4
    sun.rotation_euler = (0.85, 0.15, 0.5)
    scene.collection.objects.link(sun)
    fill = bpy.data.objects.new("fill", bpy.data.lights.new("fill", "SUN"))
    fill.data.energy = 1.2
    fill.rotation_euler = (1.2, -0.3, -2.2)
    scene.collection.objects.link(fill)

    # camera: computed rotation (constraints are unreliable in -b renders)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    elev = ELEV.get(case, 0.42)
    direction = Vector((0.0, -1.0, elev)).normalized()
    cam.location = center + direction * (width * 0.9 + span * 1.32)
    # aim slightly below center so the labels share the frame
    view = (center - cam.location).normalized()
    quat = view.to_track_quat("-Z", "Y")
    cam.rotation_euler = quat.to_euler()
    cam.location += quat @ Vector((0.0, -1.0, 0.0)) * (span * 0.10)
    cam.data.lens = 50

    # billboard labels: object center pushed toward the screen-bottom
    # direction of THIS camera — works for any elevation
    screen_down = quat @ Vector((0.0, -1.0, 0.0))
    lbl_mat = make_material("label", (0.08, 0.08, 0.09))
    for ob in (oa, ob_):
        olo, ohi = bounds([ob])
        oc = (olo + ohi) / 2.0
        tool = tool_a if ob is oa else tool_b
        bpy.ops.object.text_add()
        t = bpy.context.active_object
        t.data.body = f"{LABEL[tool]} — {len(ob.data.polygons)} faces"
        t.data.align_x = "CENTER"
        t.data.size = span * 0.072
        t.data.materials.append(lbl_mat)
        t.rotation_euler = quat.to_euler()
        t.location = oc + screen_down * (span * 0.56)

    out = os.path.join(OUT_DIR, f"compare_{case}.png")
    scene.render.filepath = out
    bpy.ops.render.render(write_still=True)
    print(f"rendered {out}")
