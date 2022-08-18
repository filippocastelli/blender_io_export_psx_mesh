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
    
    
bpy.propertyGroupLayouts = {
    "Flags": [
        {"name": "isAnim", "type": "boolean"},
        {"name": "isProp", "type": "boolean"},
        {"name": "isRigidBody", "type": "boolean"},
        {"name": "isStaticBody", "type": "boolean"},
        {"name": "isRound", "type": "boolean"},
        {"name": "isPrism", "type": "boolean"},
        {"name": "isActor", "type": "boolean"},
        {"name": "isLevel", "type": "boolean"},
        {"name": "isWall", "type": "boolean"},
        {"name": "isBG", "type": "boolean"},
        {"name": "isSprite", "type": "boolean"},
        {"name": "isLerp", "type": "boolean"},
        {"name": "isXA", "type": "boolean"},
    ],
    "Others": [{"name": "mass", "type": "int"}],
}
# store keymaps here to access after registration

bpy.samplePropertyGroups = {}
last_selection = []
store_att_names = []


def getActiveObjProps(active_obj):
    object_custom_props = [prop for prop in store_att_names if prop in active_obj.data]
    return object_custom_props


def copyCustomProps(context):
    # get active object
    active_obj = bpy.context.active_object
    # get active object's custom properties
    active_obj_custom_props = getActiveObjProps(active_obj)
    # get selected objects
    selection = bpy.context.selected_objects
    # discriminates against active_obj
    selection = [obj for obj in selection if obj != active_obj]
    # for each object that's not active object, add custom prop
    for obj in selection:
        for prop in active_obj_custom_props:
            obj.data[prop] = active_obj.data[prop]


def updateCustomProps(self, context):
    global last_selection
    global store_att_names
    for att in store_att_names:
        if att in last_selection.Flags:  # and last_selection.Flags[att] :
            if (
                att not in last_selection.data
                or last_selection.Flags[att] != last_selection.data[att]
            ):
                last_selection.data[att] = last_selection.Flags[att]
        if att in last_selection.Others:
            if (
                att not in last_selection.data
                or last_selection.Others[att] != last_selection.data[att]
            ):
                last_selection.data[att] = last_selection.Others[att]


@persistent
def selection_callback(scene):
    global last_selection
    global store_att_names
    if bpy.context.active_object != last_selection:
        last_selection = bpy.context.active_object
        for groupName, attributeDefinitions in bpy.propertyGroupLayouts.items():
            # build the attribute dictionary for this group
            attributes = {}
            for attributeDefinition in attributeDefinitions:
                attType = attributeDefinition["type"]
                attName = attributeDefinition["name"]
                store_att_names.append(attName)
                value = 0
                if last_selection:
                    if attName in last_selection.data:
                        value = last_selection.data[attName]
                if attType == "boolean":
                    attributes[attName] = BoolProperty(
                        name=attName, default=value, update=updateCustomProps
                    )
                elif attType == "int":
                    attributes[attName] = IntProperty(
                        name=attName, default=value, update=updateCustomProps
                    )
                elif attType == "string":
                    attributes[attName] = StringProperty(
                        name=attName, default=value, update=updateCustomProps
                    )
                else:
                    raise TypeError(
                        "Unsupported type (%s) for %s on %s!"
                        % (attType, attName, groupName)
                    )
            # now build the property group class
            propertyGroupClass = type(groupName, (PropertyGroup,), attributes)
            # register it with Blender
            bpy.utils.register_class(propertyGroupClass)
            # apply it to all Objects
            setattr(
                bpy.types.Object, groupName, PointerProperty(type=propertyGroupClass)
            )
            # store it for later
            bpy.samplePropertyGroups[groupName] = propertyGroupClass


class ENGINEHELPER_OT_copyCustomPropToSelection(bpy.types.Operator):
    """Copy last selected object's custom properties to other selected objects"""

    bl_idname = "object.copy_custom_properties"
    bl_label = "Copy custom properties to selection"

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        copyCustomProps(context)
        return {"FINISHED"}


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
