# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the extensions.blender.org store visuals for ReQuad.

    REQUAD_ZIP=dist/requad-x.y.z-macos_arm64.zip \
        blender -b -P benchmarks/render_store_visuals.py -- <out_dir>

Produces the featured image plus one Mechanical and one Organic preview,
by installing the given extension archive and remeshing through it -- the
pictures therefore show the binary that actually ships.

Every input is Blender's own Suzanne, a primitive, or metaball geometry
built by this script: no third-party asset, no rights question, and no
competitor named or rendered.
"""
import math
import os
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT_DIR = argv[0] if argv else "out"
os.makedirs(OUT_DIR, exist_ok=True)

ZIP = os.environ.get("REQUAD_ZIP", "")
MODULE = "bl_ext.user_default.requad"

BG = (0.043, 0.048, 0.056, 1.0)
INPUT_COLOR = (0.30, 0.32, 0.35)
RESULT_COLOR = (0.055, 0.42, 0.44)
WIRE_COLOR = (0.88, 0.90, 0.90)
LABEL_COLOR = (0.62, 0.66, 0.70)


def install():
    bpy.ops.extensions.package_install_files(
        repo="user_default", filepath=ZIP, enable_on_install=True)
    assert MODULE in bpy.context.preferences.addons, "extension not enabled"


def reset():
    bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene
    for name in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
        try:
            scene.render.engine = name
            break
        except TypeError:
            continue
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.film_transparent = False
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = BG
    world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
    scene.world = world
    return scene


def material(name, color, rough=0.42):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    if "Metallic" in bsdf.inputs:
        bsdf.inputs["Metallic"].default_value = 0.0
    return mat


def freestyle(scene, thickness=0.85):
    scene.render.use_freestyle = True
    scene.render.line_thickness = thickness
    vl = bpy.context.view_layer
    vl.use_freestyle = True
    fs = vl.freestyle_settings
    for ls in list(fs.linesets):
        fs.linesets.remove(ls)
    ls = fs.linesets.new("wires")
    ls.select_silhouette = False
    ls.select_border = True
    ls.select_crease = False
    ls.select_edge_mark = True
    ls.linestyle.color = WIRE_COLOR
    return ls


def mark_wires(ob):
    attr = ob.data.attributes.get("freestyle_edge") or \
        ob.data.attributes.new("freestyle_edge", "BOOLEAN", "EDGE")
    attr.data.foreach_set("value", [True] * len(ob.data.edges))


def set_shading(ob, smooth):
    """Explicit both ways -- ReQuad hands back a smooth-shaded result, so
    hard-surface shots must actively clear it, not merely skip setting it."""
    for p in ob.data.polygons:
        p.use_smooth = smooth


def bounds(objects):
    lo = Vector((1e18,) * 3)
    hi = Vector((-1e18,) * 3)
    for ob in objects:
        for corner in ob.bound_box:
            w = ob.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    return lo, hi


def center_on(ob, target):
    lo, hi = bounds([ob])
    ob.location = ob.location - (lo + hi) / 2.0 + Vector(target)
    bpy.context.view_layer.update()


def lights(scene, key_energy=4.0):
    key = bpy.data.objects.new("key", bpy.data.lights.new("key", "SUN"))
    key.data.energy = key_energy
    key.data.angle = 0.35
    key.rotation_euler = (0.92, 0.10, 0.62)
    scene.collection.objects.link(key)
    fill = bpy.data.objects.new("fill", bpy.data.lights.new("fill", "SUN"))
    fill.data.energy = 1.35
    fill.rotation_euler = (1.15, -0.25, -2.15)
    scene.collection.objects.link(fill)
    rim = bpy.data.objects.new("rim", bpy.data.lights.new("rim", "SUN"))
    rim.data.energy = 2.0
    rim.rotation_euler = (1.9, 0.0, 3.5)
    scene.collection.objects.link(rim)


def fit_camera(scene, objects, elev=0.30, lens=52, margin=1.16, shift_up=0.0):
    """Frame every object with a real projection fit (no cropping)."""
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = lens
    cam_data.sensor_fit = "HORIZONTAL"
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    lo, hi = bounds(objects)
    center = (lo + hi) / 2.0

    direction = Vector((0.0, -1.0, elev)).normalized()
    quat = (-direction).to_track_quat("-Z", "Y")
    cam.rotation_euler = quat.to_euler()

    right = quat @ Vector((1.0, 0.0, 0.0))
    up = quat @ Vector((0.0, 1.0, 0.0))
    forward = quat @ Vector((0.0, 0.0, -1.0))

    aspect = scene.render.resolution_x / scene.render.resolution_y
    half_h_fov = math.atan(cam_data.sensor_width / 2.0 / lens)
    half_v_fov = math.atan(math.tan(half_h_fov) / aspect)

    # exact fit: the extent of the real corners along the camera axes
    corners = []
    for ob in objects:
        for c in ob.bound_box:
            corners.append(ob.matrix_world @ Vector(c))
    need = 0.0
    for c in corners:
        rel = c - center
        x, y, z = rel.dot(right), rel.dot(up), rel.dot(forward)
        need = max(need,
                   abs(x) / math.tan(half_h_fov) + z,
                   abs(y) / math.tan(half_v_fov) + z)
    cam.location = center - forward * (need * margin)
    if shift_up:
        cam.data.shift_y = shift_up
    bpy.context.view_layer.update()
    return cam, quat


def label(text, anchor, quat, size, mat):
    bpy.ops.object.text_add()
    t = bpy.context.active_object
    t.data.body = text
    t.data.align_x = "CENTER"
    t.data.size = size
    t.data.materials.append(mat)
    t.rotation_euler = quat.to_euler()
    t.location = anchor
    return t


def requad(ob, count, preset="ORGANIC"):
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    scene = bpy.context.scene
    scene.requad.preset = preset
    scene.requad.target_count = count
    before = set(bpy.context.scene.objects)
    res = bpy.ops.requad.remesh()
    assert res == {"FINISHED"}, res
    new = [o for o in set(bpy.context.scene.objects) - before
           if o.name.endswith("_requad")]
    assert len(new) == 1, [o.name for o in new]
    return new[0]


def dense_suzanne(subdiv=3):
    bpy.ops.mesh.primitive_monkey_add()
    ob = bpy.context.active_object
    m = ob.modifiers.new("subsurf", "SUBSURF")
    m.levels = subdiv
    bpy.ops.object.modifier_apply(modifier=m.name)
    m = ob.modifiers.new("tri", "TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier=m.name)
    return ob


def bracket():
    """Drilled mechanical plate -- flat faces, sharp edges, round holes."""
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    plate = bpy.context.active_object
    plate.scale = (1.55, 0.95, 0.20)
    bpy.ops.object.transform_apply(scale=True)

    cutters = []
    for x in (-0.92, 0.0, 0.92):
        bpy.ops.mesh.primitive_cylinder_add(radius=0.34, depth=3.0,
                                            vertices=64, location=(x, 0, 0))
        c = bpy.context.active_object
        c.rotation_euler = (0.0, 0.0, 0.0)
        cutters.append(c)

    for c in cutters:
        m = plate.modifiers.new("bool", "BOOLEAN")
        m.operation = "DIFFERENCE"
        m.object = c
        bpy.context.view_layer.objects.active = plate
        bpy.ops.object.modifier_apply(modifier=m.name)
    for c in cutters:
        bpy.data.objects.remove(c, do_unlink=True)

    # no bevel: crisp 90-degree CAD edges are what the Mechanical preset is
    # built to snap to, and a bevel finer than the quad size only aliases
    bpy.context.view_layer.objects.active = plate
    m = plate.modifiers.new("tri", "TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier=m.name)
    return plate


def blob():
    """Sculpt-like organic mass from metaballs -- generated, no asset rights."""
    parts = [((0.0, 0.0, 0.0), 1.05), ((0.95, 0.10, 0.55), 0.72),
             ((-0.85, -0.15, 0.35), 0.62), ((0.25, 0.05, 1.15), 0.55),
             ((-0.35, 0.30, -0.85), 0.68), ((0.70, -0.35, -0.60), 0.48)]
    bpy.ops.object.metaball_add(location=parts[0][0], radius=parts[0][1])
    base = bpy.context.active_object
    base.data.resolution = 0.06
    base.data.render_resolution = 0.06
    for loc, r in parts[1:]:
        bpy.ops.object.metaball_add(location=loc, radius=r)
    bpy.ops.object.select_all(action="SELECT")
    bpy.context.view_layer.objects.active = base
    bpy.ops.object.convert(target="MESH")
    ob = bpy.context.active_object
    m = ob.modifiers.new("tri", "TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier=m.name)
    return ob


def pair_shot(make_input, count, preset, out_name, labels=True,
              elev=0.30, rot_z=0.0, shade_smooth=True):
    scene = reset()
    freestyle(scene)
    src = make_input()
    src.rotation_euler = (0.0, 0.0, rot_z)
    bpy.ops.object.transform_apply(rotation=True)
    res = requad(src, count, preset)

    span = max(max(src.dimensions), max(res.dimensions))
    gap = span * 0.62
    center_on(src, (-gap, 0.0, 0.0))
    center_on(res, (gap, 0.0, 0.0))

    src.data.materials.clear()
    src.data.materials.append(material("in", INPUT_COLOR))
    res.data.materials.clear()
    res.data.materials.append(material("out", RESULT_COLOR, rough=0.36))
    set_shading(src, shade_smooth)
    set_shading(res, shade_smooth)
    mark_wires(res)

    lights(scene)
    objs = [src, res]
    cam, quat = fit_camera(scene, objs, elev=elev,
                           margin=1.30 if labels else 1.20)

    if labels:
        lbl_mat = material("lbl", LABEL_COLOR, rough=1.0)
        down = quat @ Vector((0.0, -1.0, 0.0))
        # anchor each label under the object's real screen-space extent,
        # otherwise compact shapes swallow their own caption
        lo, hi = bounds(objs)
        base_y = min((c - (lo + hi) / 2.0).dot(down)
                     for ob in objs for c in
                     [ob.matrix_world @ Vector(v) for v in ob.bound_box])
        center = (lo + hi) / 2.0
        drop = down * (abs(base_y) + span * 0.16)
        texts = ((src, -gap, f"input  {len(src.data.polygons):,} triangles"),
                 (res, gap, f"ReQuad  {len(res.data.polygons):,} quads"))
        for _ob, off, body in texts:
            label(body.replace(",", " "),
                  center + Vector((off, 0, 0)) + drop, quat,
                  span * 0.058, lbl_mat)
        cam, quat = fit_camera(scene, objs + [o for o in scene.objects
                                              if o.type == "FONT"],
                               elev=elev, margin=1.10)

    out = os.path.join(OUT_DIR, out_name)
    scene.render.filepath = out
    bpy.ops.render.render(write_still=True)
    print(f"[render] {out}")
    return out


def main():
    if not os.path.isfile(ZIP):
        print(f"REQUAD_ZIP not found: {ZIP!r}")
        sys.exit(2)
    install()
    pair_shot(dense_suzanne, 3000, "ORGANIC", "featured-1920x1080.png",
              labels=True, elev=0.16)
    pair_shot(bracket, 3000, "MECHANICAL", "preview-mechanical.png",
              labels=True, elev=0.62, rot_z=0.35, shade_smooth=False)
    pair_shot(blob, 2500, "ORGANIC", "preview-organic.png",
              labels=True, elev=0.26)
    print("done")


main()
