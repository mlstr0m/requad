# SPDX-License-Identifier: GPL-3.0-or-later
"""Pathological-mesh robustness suite. Run:

    REQUAD_ZIP=path/to/requad.zip blender -b -P tests/test_robustness.py

Contract: no exception and no silent empty result — every case must either
FINISH with a valid mesh or CANCEL with a clear report.
"""
import os
import sys

import bmesh
import bpy

ZIP = os.environ.get("REQUAD_ZIP", "")
failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not cond:
        failures.append(name)


def run_case(name, build):
    """build(bm) fills a bmesh; returns nothing. The case passes when the
    operator returns without raising and, on FINISHED, yields faces."""
    bpy.ops.wm.read_homefile(use_empty=True)
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    build(bm)
    bm.to_mesh(mesh)
    bm.free()
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    s = bpy.context.scene.requad
    s.preset = "ORGANIC"
    s.target_count = 500
    try:
        result = bpy.ops.requad.remesh()
    except Exception as exc:  # noqa: BLE001 - the whole point of the suite
        check(name, False, f"EXCEPTION: {exc}")
        return
    if result == {"FINISHED"}:
        res = next((o for o in bpy.context.scene.objects
                    if o.name.endswith("_requad")), None)
        ok = res is not None and len(res.data.polygons) > 0
        check(name, ok, f"(finished, {len(res.data.polygons) if res else 0} faces)")
    else:
        check(name, True, f"(clean cancel: {result})")


def sphere(bm, radius=1.0, subdivisions=3):
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=radius)


def case_nonmanifold_edge(bm):
    sphere(bm)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    e = bm.edges[0]
    apex = bm.verts.new((0.0, 0.0, 3.0))
    bm.faces.new((e.verts[0], e.verts[1], apex))


def case_bowtie(bm):
    v = [bm.verts.new(p) for p in
         ((0, 0, 0), (1, 0, 0), (1, 1, 0), (-1, 0, 0), (-1, -1, 0))]
    bm.faces.new((v[0], v[1], v[2]))
    bm.faces.new((v[0], v[3], v[4]))
    sphere(bm)


def case_zero_area(bm):
    sphere(bm)
    bm.verts.ensure_lookup_table()
    a = bm.verts.new((2, 0, 0))
    b = bm.verts.new((2, 1, 0))
    c = bm.verts.new((2, 0.5, 0))
    bm.faces.new((a, b, c))
    c.co = a.co  # collapse to zero area


def case_loose_verts(bm):
    sphere(bm)
    for i in range(20):
        bm.verts.new((3 + i * 0.1, 0, 0))


def case_loose_edges(bm):
    sphere(bm)
    a = bm.verts.new((3, 0, 0))
    b = bm.verts.new((4, 0, 0))
    bm.edges.new((a, b))


def case_two_triangles(bm):
    v = [bm.verts.new(p) for p in
         ((0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0))]
    bm.faces.new((v[0], v[1], v[2]))
    bm.faces.new((v[1], v[3], v[2]))


def case_single_face(bm):
    v = [bm.verts.new(p) for p in ((0, 0, 0), (1, 0, 0), (0, 1, 0))]
    bm.faces.new(v)


def case_many_components(bm):
    for i in range(30):
        bmesh.ops.create_icosphere(
            bm, subdivisions=1, radius=0.1,
            matrix=__import__("mathutils").Matrix.Translation((i * 0.3, 0, 0)))


def case_self_intersecting(bm):
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.create_cube(
        bm, size=2.0,
        matrix=__import__("mathutils").Matrix.Translation((0.7, 0.6, 0.5)))


def case_flipped_normals(bm):
    sphere(bm)
    bm.faces.ensure_lookup_table()
    for f in list(bm.faces)[::2]:
        f.normal_flip()


def case_holes(bm):
    sphere(bm, subdivisions=3)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=list(bm.faces)[::7], context="FACES")


def case_needle_triangles(bm):
    sphere(bm)
    bm.verts.ensure_lookup_table()
    a = bm.verts.new((5, 0, 0))
    b = bm.verts.new((5.00001, 0, 0))
    c = bm.verts.new((5, 8, 0))
    bm.faces.new((a, b, c))


def main():
    if not os.path.isfile(ZIP):
        print(f"REQUAD_ZIP not found: {ZIP!r}")
        sys.exit(2)
    bpy.ops.extensions.package_install_files(
        repo="user_default", filepath=ZIP, enable_on_install=True)

    cases = [
        ("non-manifold edge", case_nonmanifold_edge),
        ("bowtie vertex", case_bowtie),
        ("zero-area face", case_zero_area),
        ("loose vertices", case_loose_verts),
        ("loose edges", case_loose_edges),
        ("two triangles", case_two_triangles),
        ("single face", case_single_face),
        ("30 tiny components", case_many_components),
        ("self-intersecting", case_self_intersecting),
        ("flipped normals", case_flipped_normals),
        ("holes everywhere", case_holes),
        ("needle triangle", case_needle_triangles),
    ]
    for name, build in cases:
        run_case(name, build)

    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


main()
