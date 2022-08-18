import bpy
from .engine_helper import (
    ENGINEHELPER_PT_customPropsPanel,
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

# TODO: reimplement copy props functionality with new props
def register():
    classes = (
        Flags, Others, ENGINEHELPER_PT_customPropsPanel
    )
    for _class in classes:
        bpy.utils.register_class(_class)
        
    bpy.types.Object.EH_Flags = bpy.props.PointerProperty(type=Flags)
    bpy.types.Object.EH_Others = bpy.props.PointerProperty(type=Others)


def unregister():
    # unregister the panel class
    classes = (
        Flags, Others, ENGINEHELPER_PT_customPropsPanel
    )
    for _class in classes:
        bpy.utils.register_class(_class)
        
    del bpy.types.Object.EH_Flags
    del bpy.types.Object.EH_Others


if __name__ == "__main__":
    register()
