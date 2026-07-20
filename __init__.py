# SPDX-FileCopyrightText: 2026 Aurélien and the ReQuad contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""ReQuad — free auto-retopology for Blender, powered by QuadWild bi-MDF.

Pipeline: export selection as triangulated OBJ → run `quadwild` (field
computation + patch tracing) → run `quad_from_patches` (bi-MDF quantization +
quadrangulation) → import the result next to the original object.

Engine: https://github.com/cgg-bern/quadwild-bimdf (GPL-3.0)
Papers: Pietroni et al. 2021 (QuadWild), Heistermann et al. 2023 (bi-MDF).
"""
import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from .common import _ENGINE_CACHE, _platform_tag, resolve_engine
from .operator import REQUAD_OT_remesh, REQUAD_OT_set_count

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
    sym_x: BoolProperty(
        name="X", default=False,
        description="Mirror symmetry across the local X plane (characters); "
                    "axes can be combined")
    sym_y: BoolProperty(
        name="Y", default=False,
        description="Mirror symmetry across the local Y plane")
    sym_z: BoolProperty(
        name="Z", default=False,
        description="Mirror symmetry across the local Z plane")
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
        description="Align singularity pairs in the quantizer — combined "
                    "with the long relaxation this measurably improves "
                    "organic corner angles (statue 7.71° → 7.10°)",
        default=True,
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
    transfer_weights: BoolProperty(
        name="Transfer Vertex Groups",
        description="Project the source's vertex group weights onto the "
                    "result (nearest polygon interpolation)",
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
        default=30, min=0, max=50,
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
        row = col.row(align=True)
        for label, factor in (("½", 0.5), ("Same", 1.0), ("×2", 2.0)):
            row.operator("requad.set_count", text=label).factor = factor
        row = col.row(align=True)
        row.label(text="Symmetry")
        row.prop(settings, "sym_x", toggle=True)
        row.prop(settings, "sym_y", toggle=True)
        row.prop(settings, "sym_z", toggle=True)
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
        col.prop(settings, "transfer_weights")
        col.prop(settings, "smooth_result")
        col.prop(settings, "hide_original")


classes = (
    ReQuadPreferences,
    ReQuadSettings,
    REQUAD_OT_remesh,
    REQUAD_OT_set_count,
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
