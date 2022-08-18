import bpy
from .engine_helper import (
    ENGINEHELPER_PT_customPropsPanel,
    selection_callback,
    ENGINEHELPER_OT_copyCustomPropToSelection,
    Flags,
    Others
)

addon_keymaps = []

bl_info = {
    "name": "3Dcam engine custom properties helper",
    "author": "Schnappy, Afire101",
    "blender": (3, 2, 0),
    "category": "Property helper",
    "version": (0, 2, 0),
    "location": "3D view > Object",
    "location": "Properties panel > Data",
    "description": "Set/Unset/Copy flags easily",
}


def menu_func(self, context):
    self.layout.operator(ENGINEHELPER_OT_copyCustomPropToSelection.bl_idname)


def register():
    # register the panel class
    bpy.utils.register_class(Flags)
    bpy.utils.register_class(Others)
    bpy.utils.register_class(ENGINEHELPER_PT_customPropsPanel,
)
    bpy.types.Object.EH_Flags = bpy.props.PointerProperty(type=Flags)
    bpy.types.Object.EH_Others = bpy.props.PointerProperty(type=Others)
    
    #bpy.app.handlers.depsgraph_update_post.clear()
    #bpy.app.handlers.depsgraph_update_post.append(selection_callback)
    # copy helper
    bpy.utils.register_class(ENGINEHELPER_OT_copyCustomPropToSelection)
    bpy.types.VIEW3D_MT_object.append(menu_func)
    # shortcut
    wm = bpy.context.window_manager
    km = wm.keyconfigs.addon.keymaps.new(name="Object Mode", space_type="EMPTY")
    kmi = km.keymap_items.new(
        ENGINEHELPER_OT_copyCustomPropToSelection.bl_idname, "P", "PRESS", ctrl=False, shift=True
    )
    # ~ kmi.properties.total = 4
    addon_keymaps.append((km, kmi))


def unregister():
    # unregister the panel class
    bpy.utils.unregister_class(Flags)
    bpy.utils.unregister_class(Others)
    bpy.utils.unregister_class(ENGINEHELPER_PT_customPropsPanel,
)
    # unregister our components
    try:
        for key, value in bpy.samplePropertyGroups.items():
            delattr(bpy.types.Object, key)
            bpy.utils.unregister_class(value)
    except UnboundLocalError:
        pass
    bpy.samplePropertyGroups = {}
    # copy helper
    bpy.utils.unregister_class(ENGINEHELPER_OT_copyCustomPropToSelection)
    bpy.types.VIEW3D_MT_object.remove(menu_func)
    # handle the keymap
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()


if __name__ == "__main__":
    register()
