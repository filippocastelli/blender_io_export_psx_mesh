import bpy
from exportpsx import ExportPSX

bl_info = {
    "name": "PSX TMesh exporter",
    "author": "Schnappy, TheDukeOfZill, Afire101",
    "description": "",
    "blender": (3, 2, 0),
    "version": (0, 2, 0),
    "location": "File > Import-Export",
    "warning": "",
    "category": "Import-Export",
}


def menu_func_export(self, context):
    self.layout.operator(ExportPSX.bl_idname, text="PSX Format(.c)")


def register():
    bpy.utils.register_class(ExportPSX)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.utils.unregister_class(ExportPSX)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
