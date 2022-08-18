# Based on https://blog.hamaluik.ca/posts/dynamic-blender-properties by Kenton Hamaluik
import bpy
from bpy.app.handlers import persistent
from bpy.props import IntProperty, StringProperty, PointerProperty, BoolProperty
from bpy.types import PropertyGroup

class Flags(bpy.types.PropertyGroup):
    isAnim: BoolProperty()
    isProp: BoolProperty()
    isRigidBody: BoolProperty()
    isStaticBody: BoolProperty()
    isRound: BoolProperty()
    isPrism: BoolProperty()
    isActor: BoolProperty()
    isLevel: BoolProperty()
    isWall: BoolProperty()
    isBG: BoolProperty()
    isSprite: BoolProperty()
    isLerp: BoolProperty()
    isXA:   BoolProperty()

class Others(bpy.types.PropertyGroup):
    mass: IntProperty()
    

class ENGINEHELPER_PT_customPropsPanel(bpy.types.Panel):
    bl_label = "3D Cam engine custom properties"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"

    @staticmethod
    def _draw_group(obj: bpy.types.Object,
                    layout: bpy.types.UILayout,
                    ppgroup: bpy.types.PropertyGroup):
        col = layout.column_flow(columns=2)
        ppgroup_name = ppgroup.__name__
        col.label(text=ppgroup_name)
        keys = ppgroup.__annotations__.keys()
        i = 0
        custom_props = getattr(obj, "EH_"+ppgroup_name)
        
        for attr in keys:
            if i == len(keys) / 2:
                col.label("")
            col.prop(custom_props, attr)
            i += 1
        
    def draw(self, context):
        layout = self.layout
        obj = context.object
        
        self._draw_group(obj, layout, Flags)
        layout.separator()
        self._draw_group(obj, layout, Others)
