import os
from re import sub
from enum import Enum
from collections import defaultdict
from typing import List, Union, Tuple, List, TextIO
from math import degrees, sqrt, ceil
import mathutils
from mathutils import Vector
import unicodedata
import subprocess

import bpy
import bmesh
from bpy_extras.io_utils import ExportHelper
from bpy.types import Operator
from bpy.props import (
    BoolProperty,
    StringProperty,
    FloatProperty,
    IntProperty,
)
from bpy_extras.object_utils import world_to_camera_view


class ResMode(Enum):
    PAL = (320, 256)
    NTSC = (320, 240)


class Sound:
    def __init__(
        self,
        objName: str,
        soundName: str,
        soundPath: str,  # TODO: convert to pathlib
        convertedSoundPath: str,
        parent: bpy.types.Object,
        location: Tuple[float, float, float],
        volume: int,
        volume_min: int,
        volume_max: int,
        index: int,
        XAfile: str = -1,
        XAchannel: int = -1,
        XAsize: int = -1,
        XAend: int = -1,
    ):
        self.objName = objName
        self.soundName = soundName
        self.soundPath = soundPath
        self.convertedSoundPath = convertedSoundPath
        self.parent = parent
        self.location = location
        self.volume = volume
        self.volume_min = volume_min
        self.volume_max = volume_max
        self.index = index
        self.XAfile = XAfile
        self.XAcahnnel = XAchannel
        self.XAsize = XAsize
        self.XAend = XAend

    def __eq__(self, other):
        return self.convertedSoundPath == other.convertedSoundPath


class ExportPSX(Operator, ExportHelper):
    """PSX exporter"""

    bl_idname = "export_psx.c"
    bl_label = "PSX compatible scene exporter"
    filename_ext = ".c"
    filter_glob = StringProperty(default="*.c", options={"HIDDEN"})
    bl_options = {
        "PRESET",
    }
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    check_extension = False

    # PROPS
    exp_Triangulate: BoolProperty(
        name="Triangulate meshes ( Destructive ! )",
        description="Triangulate meshes (destructive ! Do not use your original file)",
        default=False,
    )

    exp_Scale: FloatProperty(
        name="Scale",
        description="Scale of exported mesh.",
        min=1,
        max=1000,
        default=65.0,
    )

    exp_Precalc: BoolProperty(
        name="Use precalculated BGs",
        description="Render backgrounds and converts them to TIMs",
        default=False,
    )

    # exp_ShowPortals: BoolProperty(
    #     name="Render Portals in precalculated BGs",
    #     description="Useful for debugging",
    #     default=False,
    # )

    exp_useIMforTIM: BoolProperty(
        name="Use ImageMagick",
        description="Use installed Image Magick's convert tool to convert PNGs to 8/4bpp",
        default=False,
    )

    exp_convTexToPNG: BoolProperty(
        name="Convert images to PNG",
        description="Use installed Image Magick's convert tool to convert images to PNG.",
        default=True,
    )

    exp_TIMbpp: BoolProperty(
        name="Use 4bpp TIMs",
        description="Converts rendered backgrounds to 4bpp TIMs instead of the default 8bpp",
        default=False,
    )

    exp_LvlNbr: IntProperty(
        name="Level number",
        description="That number is used in the symbols name.",
        min=1,
        max=10,
        default=0,
    )

    exp_expMode: BoolProperty(
        name="Use blend file directory for export",
        description="Files will be exported in the same folder as the blend file.",
        default=False,
    )

    exp_CustomTexFolder: StringProperty(
        name="Textures Dir",
        description="By default, the script looks for / saves textures in the ./TEX folder. You can tell it to use a different folder.",
        default="TEX",
    )

    exp_XAmode: IntProperty(
        name="XA mode",
        description="XA sector size : 0 = 2352, 1=2336",
        min=0,
        max=1,
        default=1,
    )

    exp_isoCfg: StringProperty(
        name="mkpsxiso cfg",
        description="Where should we look for mkpsxiso's config file ?",
        default="." + os.sep + "config" + os.sep + "3dcam.xml",
    )

    exp_CompressAnims: BoolProperty(
        name="Compress animation data",
        description="Use Delta/RLE compression on animations 's data.",
        default=False,
    )

    exp_mixOverlapingStrips: BoolProperty(
        name="Mix overlaping nla animation tracks",
        description="If set, the resulting animation will be an interpolation between the overlapping nla tracks.",
        default=False,
    )

    def __init__(self, resMode: ResMode = ResMode.NTSC):

        # setting up attributes to avoid out of init definitions, keeping things clean
        self.resmode = resMode

        # VRAM layout
        self.nextTpage = None
        self.nextClutSlot = None
        self.freeTpage = None
        self.freeClutSlot = None
        self.tpageY = None

        # TIMs
        self.TIMBbb = None
        self.TIMshift = None
        self.filepath = None
        self.timList = []

        # Camera angles
        self.camAngles = []
        self.defaultCam = "NULL"
        self.rayTargets = []
        self.camPathPoints = []

        # ANIMs
        self.objAnims = defaultdict()

        # Objects
        self.soundFiles = None
        # lights
        self.lmpObjects = {}
        # meshes
        self.mshObjects = {}

        # SOUNDS
        self.freeXAfile = 0
        self.freeXAchannel = 0
        self.level_symbols = []

    def execute(self):
        # Set blender to render in render mode
        self.set_resolution(self.resmode)

        # Setting up VRAM Layout
        self.nextTpage = 320
        self.nextClutSlot = 480
        self.freeTpage = 21
        self.freeClutSlot = 32
        self.tpageY = 0

        # Setting TIM bpp
        self.TIMbpp = 4 if self.exp_TIMbpp else 8
        self.TIMshift = 0 if self.TIMbpp == 4 else 1

        # Setting context area to 3D view
        self.set_context_area()

        # Triangulate all objects
        if self.exp_Triangulate:
            self.triangulate_all_objects()

        # Setup paths
        self.setup_paths()

        # Export precalculated backgrounds
        if self.exp_Precalc:
            self.export_backgrounds()

    def write_custom_types_h(self):
        custom_types_h = self.expFolder + os.sep + "custom_types.h"
        h = open(os.path.normpath(custom_types_h), "w")

        # C STRUCTURE DEFINITIONS
        h.write(
            "#pragma once\n"
            + "#include <sys/types.h>\n"
            + "#include <libgte.h>\n"
            + "#include <stdint.h>\n"
            + "#include <libgpu.h>\n\n"
        )

        # Partial declaration of structures to avoid inter-dependencies issues
        h.write(
            "struct BODY;\n"
            + "struct BVECTOR;\n"
            + "struct VANIM;\n"
            + "struct MESH_ANIMS_TRACKS;\n"
            + "struct PRIM;\n"
            + "struct MESH;\n"
            + "struct CAMPOS;\n"
            + "struct CAMPATH;\n"
            + "struct CAMANGLE;\n"
            + "struct SIBLINGS;\n"
            + "struct CHILDREN;\n"
            + "struct NODE;\n"
            + "struct QUAD;\n"
            + "struct LEVEL;\n"
            + "struct VAGsound;\n"
            + "struct VAGbank;\n"
            + "struct XAsound;\n"
            + "struct XAbank;\n"
            + "struct XAfiles;\n"
            + "struct SOUND_OBJECT;\n"
            + "struct LEVEL_SOUNDS;\n"
            + "\n"
        )
        # BODY
        h.write(
            "typedef struct BODY {\n"
            + "\tVECTOR  gForce;\n"
            + "\tVECTOR  position;\n"
            + "\tSVECTOR velocity;\n"
            + "\tint     mass;\n"
            + "\tint     invMass;\n"
            + "\tVECTOR  min; \n"
            + "\tVECTOR  max; \n"
            + "\tint     restitution; \n"
            +
            # ~ "\tstruct NODE * curNode; \n" +
            "\t} BODY;\n\n"
        )
        # VANIM
        h.write(
            "typedef struct BVECTOR {\n"
            + "\tint8_t	vx, vy;\n"
            + "\tint8_t	vz;\n"
            + "\t// int8_t factor; // could be useful for anims where delta is > 256 \n"
            + "} BVECTOR;\n\n"
        )

        h.write(
            "typedef struct VANIM { \n"
            + "\tint nframes;    // number of frames e.g   20\n"
            + "\tint nvert;      // number of vertices e.g 21\n"
            + "\tint cursor;     // anim cursor : -1 == not playing, n>=0 == current frame number\n"
            + "\tint lerpCursor; // anim cursor\n"
            + "\tint loop;       // loop anim : -1 == infinite, n>0  == play n times\n"
            + "\tint dir;        // playback direction (1 or -1)\n"
            + "\tint pingpong;   // ping pong animation (A>B>A)\n"
            + "\tint interpolate; // use lerp to interpolate keyframes\n"
            + "\tBVECTOR data[]; // vertex pos as SVECTORs e.g 20 * 21 SVECTORS\n"
            + "\t} VANIM;\n\n"
        )

        h.write(
            "typedef struct MESH_ANIMS_TRACKS {\n"
            + "\tu_short index;\n"
            + "\tVANIM * strips[];\n"
            + "} MESH_ANIMS_TRACKS;\n\n"
        )
        # PRIM
        h.write(
            "typedef struct PRIM {\n"
            + "\tVECTOR order;\n"
            + "\tint    code; // Same as POL3/POL4 codes : Code (F3 = 1, FT3 = 2, G3 = 3,\n// GT3 = 4) Code (F4 = 5, FT4 = 6, G4 = 7, GT4 = 8)\n"
            + "\t} PRIM;\n\n"
        )
        # MESH
        h.write(
            "typedef struct MESH {  \n"
            + "\tint      totalVerts;\n"
            + "\tTMESH   *    tmesh;\n"
            + "\tPRIM    *    index;\n"
            + "\tTIM_IMAGE *  tim;  \n"
            + "\tunsigned long * tim_data;\n"
            + "\tMATRIX      mat;\n"
            + "\tVECTOR      pos;\n"
            + "\tSVECTOR     rot;\n"
            + "\tshort       isProp;\n"
            + "\tshort       isRigidBody;\n"
            + "\tshort       isStaticBody;\n"
            + "\tshort       isRound;\n"
            + "\tshort       isPrism;\n"
            + "\tshort       isAnim;\n"
            + "\tshort       isActor;\n"
            + "\tshort       isLevel;\n"
            + "\tshort       isWall;\n"
            + "\tshort       isBG;\n"
            + "\tshort       isSprite;\n"
            + "\tlong        p;\n"
            + "\tlong        OTz;\n"
            + "\tBODY     *  body;\n"
            + "\tMESH_ANIMS_TRACKS    *  anim_tracks;\n"
            + "\tVANIM *     currentAnim;\n"
            + "\tstruct NODE   *    node;\n"
            + "\tVECTOR      pos2D;\n"
            + "\t} MESH;\n\n"
        )
        # QUAD
        h.write(
            "typedef struct QUAD {\n"
            + "\tVECTOR       v0, v1;\n"
            + "\tVECTOR       v2, v3;\n"
            + "\t} QUAD;\n\n"
        )
        # CAMPOS
        h.write(
            "typedef struct CAMPOS {\n"
            + "\tSVECTOR  pos;\n"
            + "\tSVECTOR rot;\n"
            + "\t} CAMPOS;\n\n"
            + "\n// Blender cam ~= PSX cam with these settings : \n"
            + "// NTSC - 320x240, PAL 320x256, pixel ratio 1:1,\n"
            + "// cam focal length : perspective 90° ( 16 mm ))\n"
            + "// With a FOV of 1/2, camera focal length is ~= 16 mm / 90°\n"
            + "// Lower values mean wider angle\n\n"
        )
        # CAMANGLE
        h.write(
            "typedef struct CAMANGLE {\n"
            + "\tCAMPOS    * campos;\n"
            + "\tTIM_IMAGE * BGtim;\n"
            + "\tunsigned long * tim_data;\n"
            + "\tQUAD  bw, fw;\n"
            + "\tint index;\n"
            + "\tMESH * objects[];\n"
            + "\t} CAMANGLE;\n\n"
        )
        # CAMPATH
        h.write(
            "typedef struct CAMPATH {\n"
            + "\tshort len, cursor, pos;\n"
            + "\tVECTOR points[];\n"
            + "\t} CAMPATH;\n\n"
        )
        # SIBLINGS
        h.write(
            "typedef struct SIBLINGS {\n"
            + "\tint index;\n"
            + "\tstruct NODE * list[];\n"
            + "\t} SIBLINGS ;\n\n"
        )
        # CHILDREN
        h.write(
            "typedef struct CHILDREN {\n"
            + "\tint index;\n"
            + "\tMESH * list[];\n"
            + "\t} CHILDREN ;\n\n"
        )
        # NODE
        h.write(
            "typedef struct NODE {\n"
            + "\tMESH * plane;\n"
            + "\tSIBLINGS * siblings;\n"
            + "\tCHILDREN * objects;\n"
            + "\tCHILDREN * rigidbodies;\n"
            + "\t} NODE;\n\n"
        )
        # SOUND
        # VAG
        h.write(
            "//VAG\n"
            + "typedef struct VAGsound {\n"
            + "\tu_char * VAGfile;        // Pointer to VAG data address\n"
            + "\tu_long spu_channel;      // SPU voice to playback to\n"
            + "\tu_long spu_address;      // SPU address for memory freeing spu mem\n"
            + "\t} VAGsound;\n\n"
        )

        h.write(
            "typedef struct VAGbank {\n"
            + "\tu_int index;\n"
            + "\tVAGsound samples[];\n"
            + "\t} VAGbank;\n\n"
        )

        h.write(
            "// XA\n"
            + "typedef struct XAsound {\n"
            + "\tu_int id;\n"
            + "\tu_int size;\n"
            + "\tu_char file, channel;\n"
            + "\tu_int start, end;\n"
            + "\tint cursor;\n"
            + "\t} XAsound;\n\n"
        )

        h.write(
            "typedef struct XAbank {\n"
            + "\tchar name[16];\n"
            + "\tu_int index;\n"
            + "\tint offset;\n"
            + "\tXAsound samples[];\n"
            + "\t} XAbank;\n\n"
        )

        h.write(
            "typedef struct XAfiles {\n"
            + "\tu_int index;\n"
            + "\tXAbank * banks[];\n"
            + "\t} XAfiles;\n\n"
        )

        h.write(
            "typedef struct SOUND_OBJECT {\n"
            + "\tVECTOR location;\n"
            + "\tint volumeL, volumeR, volume_min, volume_max;\n"
            + "\tVAGsound * VAGsample;\n"
            + "\tXAsound * XAsample;\n"
            + "\tMESH * parent;\n"
            + "} SOUND_OBJECT;\n\n"
        )

        h.write(
            "typedef struct LEVEL_SOUNDS {\n"
            + "\tint index;\n"
            + "\tSOUND_OBJECT * sounds[];\n"
            + "} LEVEL_SOUNDS;\n\n"
        )

        # LEVEL
        h.write(
            "typedef struct LEVEL {\n"
            + "\tCVECTOR * BGc;\n"
            + "\tVECTOR * BKc;\n"
            + "\tMATRIX * cmat;\n"
            + "\tMATRIX * lgtmat;\n"
            + "\tMESH   ** meshes;\n"
            + "\tint * meshes_length;\n"
            + "\tMESH * actorPtr;\n"
            + "\tMESH * levelPtr;\n"
            + "\tMESH * propPtr;\n"
            + "\tCAMANGLE * camPtr;\n"
            + "\tCAMPATH * camPath;\n"
            + "\tCAMANGLE ** camAngles;\n"
            + "\tNODE * curNode;\n"
            + "\tLEVEL_SOUNDS * levelSounds;\n"
            + "\tVAGbank * VAG;\n"
            + "\tXAfiles * XA;\n"
            + "\t} LEVEL;\n"
        )
        h.close()

    def parse_speaker(self, obj):
        # build a dict of objects that have child SPEAKER objects
        if obj.parent is not None and obj.parent.type == "MESH":
            parent = obj.parent
        else:
            parent = 0
        # get sound info
        objName = obj.name
        soundName = obj.data.sound.name
        soundPath = bpy.path.abspath(obj.data.sound.filepath)
        location = obj.location
        volume = int(obj.data.volume)
        volume_min = int(obj.data.volume_min)
        volume_max = int(obj.data.volume_max)

        # convert sound
        if obj.data.get("isXA"):
            XAsectorsize = 2336 if self.exp_XAmode else 2352
            if self.freeXAchannel > 7:
                self.freeXAfile += 1
                self.freeXAchannel = 0

            convertedSoundPath = self.sound2XA(
                soundPath=soundPath,
                soundName=soundName,
                bpp=4,
                XAfile=self.freeXAfile,
                XAchannel=self.freeXAchannel,
            )
            XAfile = self.freeXAfile
            XAchannel = self.freeXAchannel
            self.freeXAchannel += 1
            if os.path.exists(convertedSoundPath):
                XAsize = os.path.getsize(convertedSoundPath)
                XAend = int(((XAsize / XAsectorsize) - 1))
            else:
                XAsize = -1
                XAend = -1
            self.soundFiles.append(
                Sound(
                    objName=objName,
                    soundName=soundName,
                    soundPath=soundPath,
                    convertedSoundPath=convertedSoundPath,
                    parent=parent,
                    location=location,
                    volume=volume,
                    volume_min=volume_min,
                    volume_max=volume_max,
                    index=-1,
                    XAfile=XAfile,
                    XAchannel=XAchannel,
                    XAsize=XAsize,
                    XAend=XAend,
                )
            )
        else:
            convertedSoundPath = self.sound2VAG(soundPath, soundName)
            self.soundFiles.append(
                Sound(
                    objName=objName,
                    soundName=soundName,
                    soundPath=soundPath,
                    convertedSoundPath=convertedSoundPath,
                    parent=parent,
                    location=location,
                    volume=volume,
                    volume_min=volume_min,
                    volume_max=volume_max,
                    index=-1,
                )
            )

    def parse_mesh(self, obj: bpy.types.Object):
        self.mshObjects[obj.data.name] = obj.name
        # If isAnim flag is set, export object's vertex animations
        # Vertex animation is possible using keyframes or shape keys
        # Using nla tracks allows to export several animation for the same mesh
        # If the mixAnim flag is set, the resulting animation will be an interpolation between the overlapping nla tracks.
        if obj.active_shape_key:
            # get shape key name
            shapeKeyName = obj.active_shape_key.id_data.name
            shapeKey = bpy.data.shape_keys[shapeKeyName]
            # bake action to LNA
            if self.bakeActionToNLA(shapeKey):
                self.getTrackList(shapeKey, obj)
        # find object-based animations
        if self.bakeActionToNLA(obj):
            self.getTrackList(obj, obj)
        # Append to raytargets
        if (
            obj.data.get("isRigidBody")
            or obj.data.get("isStaticBody")
            # or bpy.data.objects[o].data.get('isPortal')
        ):
            self.rayTargets.append(obj)

    def parse_anim(self, obj: bpy.types.Object, f: TextIO):
        # TODO: parse_anim, parse_speaker and parse_mesh are inconsistent in arguments
        # If mixing nla tracks, only export one track
        if self.exp_mixOverlapingStrips:
            overlappingStrips = self.findOverlappingTrack(self.objAnims[obj])
            self.level_symbols.append(
                self.writeMESH_ANIMS(f, obj, overlappingStrips, self.fileName)
            )
            for strip in overlappingStrips:
                # Min frame start
                strip_start = min(
                    strip.frame_start,
                    min([action.frame_start for action in overlappingStrips[strip]]),
                )
                # Max frame end
                strip_end = max(
                    strip.frame_start,
                    max([action.frame_end for action in overlappingStrips[strip]]),
                )
                self.level_symbols.append(
                    self.writeVANIM(
                        f, obj, strip, self.fileName, strip_start, strip_end
                    )
                )
        else:
            allStrips = self.getStripsTotal(self.objAnims[obj])
            self.level_symbols.append(
                self.writeMESH_ANIMS(f, obj, allStrips, self.fileName)
            )
            for track in self.objAnims[obj]:
                # if flag is set, hide others nla_tracks
                track.is_solo = True
                for strip in self.objAnims[obj][track]:
                    # Use scene's Start/End frames as default
                    strip_start = strip.frame_start
                    strip_end = strip.frame_end
                    self.level_symbols.append(
                        self.writeVANIM(
                            f, obj, strip, self.fileName, strip_start, strip_end
                        )
                    )
                track.is_solo = False
        # Close struct declaration
        # ~ f.write("\t\t},\n")
        # ~ f.write("\t}\n};\n")
        # ~ level_symbols.append( "MESH_ANIMS_TRACKS " + fileName + "_model" +  CleanName(obj.data.name) + "_anims" )

    def parse_cam(self, obj: bpy.types.Object, f: TextIO):
        # set camera position and rotation in the scene
        # Add objects of type MESH with a Rigidbody or StaticBody flag set to a list

        # Set object of type CAMERA with isDefault flag as default camera
        if bpy.data.objects[obj].data.get("isDefault"):
            defaultCam = bpy.data.objects[obj].name
        # Declare each blender camera as a CAMPOS
        f.write(
            "CAMPOS "
            + self.fileName
            + "_camPos_"
            + self.cleanName(bpy.data.objects[obj].name)
            + " = {\n"
            + "\t{ "
            + str(round(-bpy.data.objects[obj].location.x * self.exp_Scale))
            + ","
            + str(round(bpy.data.objects[obj].location.z * self.exp_Scale))
            + ","
            + str(round(-bpy.data.objects[obj].location.y * self.exp_Scale))
            + " },\n"
            + "\t{ "
            + str(
                round(
                    -(degrees(bpy.data.objects[obj].rotation_euler.x) - 90) / 360 * 4096
                )
            )
            + ","
            + str(round(degrees(bpy.data.objects[obj].rotation_euler.z) / 360 * 4096))
            + ","
            + str(
                round(-(degrees(bpy.data.objects[obj].rotation_euler.y)) / 360 * 4096)
            )
            + " }\n"
            + "};\n\n"
        )
        self.level_symbols.append(
            "CAMPOS "
            + self.fileName
            + "_camPos_"
            + self.cleanName(bpy.data.objects[obj].name)
        )

    def populate_cam_path_points(self, cam_path_points: list, f: TextIO):
        # Write the CAMPATH structure
        if cam_path_points:
            # Populate with points found above
            # ~ camPathPoints = list(reversed(camPathPoints))
            for point in range(len(cam_path_points)):
                if point == 0:
                    f.write(
                        "CAMPATH "
                        + self.fileName
                        + "_camPath = {\n"
                        + "\t"
                        + str(len(cam_path_points))
                        + ",\n"
                        + "\t0,\n"
                        + "\t0,\n"
                        + "\t{\n"
                    )
                    self.level_symbols.append("CAMPATH " + self.fileName + "_camPath")
                f.write(
                    "\t\t{ "
                    + str(
                        round(
                            -bpy.data.objects[cam_path_points[point]].location.x
                            * self.exp_Scale
                        )
                    )
                    + ","
                    + str(
                        round(
                            bpy.data.objects[cam_path_points[point]].location.z
                            * self.exp_Scale
                        )
                    )
                    + ","
                    + str(
                        round(
                            -bpy.data.objects[cam_path_points[point]].location.y
                            * self.exp_Scale
                        )
                    )
                    + " }"
                )
                if point != len(cam_path_points) - 1:
                    f.write(",\n")
            f.write("\n\t}\n};\n\n")
        else:
            # If no camera path points are found, use default
            f.write(
                "CAMPATH "
                + self.fileName
                + "_camPath = {\n"
                + "\t0,\n"
                + "\t0,\n"
                + "\t0,\n"
                + "\t{0}\n"
                + "};\n\n"
            )
            self.level_symbols.append("CAMPATH " + self.fileName + "_camPath")

    def populate_lights(self, lamp_objects: dict, f: TextIO):
        # Light sources will be similar to Blender's sunlamp
        # A maximum of 3 light sources will be used
        # LLM : Local Light Matrix
        if len(lamp_objects) > 0:
            cnt = 0
            # ~ pad = 3 - len( lmpObjects ) if ( len( lmpObjects ) < 3 ) else 0
            f.write("MATRIX " + self.fileName + "_lgtmat = {\n")
            for light in sorted(lamp_objects):
                # Get rid of orphans
                if bpy.data.lamps[light].users == 0:
                    continue
                # PSX can only use 3 light sources
                if cnt < 3:
                    # Lightsource energy
                    energy = int(bpy.data.lamps[light].energy * 4096)
                    # ~ energy = int( light.energy * 4096 )
                    # Get lightsource's world orientation
                    lightdir = bpy.data.objects[
                        lamp_objects[light]
                    ].matrix_world * Vector((0, 0, -1, 0))
                    f.write(
                        "\t"
                        + str(int(lightdir.x * energy))
                        + ", "
                        + str(int(-lightdir.z * energy))
                        + ", "
                        + str(int(lightdir.y * energy))
                    )
                    if cnt < 2:
                        f.write(",")
                    f.write(" // L" + str(cnt + 1) + "\n")
                    cnt += 1
            # If less than 3 light sources exist in blender, fill the matrix with 0s.
            # ~ if pad:
            while cnt < 3:
                f.write("\t0, 0, 0")
                if cnt < 2:
                    f.write(",")
                f.write("\n")
                cnt += 1
            f.write("\t};\n\n")
            self.level_symbols.append("MATRIX " + self.fileName + "_lgtmat")
            # LCM : Local Color Matrix
            f.write("MATRIX " + self.fileName + "_cmat = {\n")
            LCM = []
            cnt = 0
            # If more than 3 light sources exists, use the 3 first in alphabetic order (same as in Blender's outliner)
            for light in sorted(lamp_objects):
                # If orphan, get on with it
                if bpy.data.lamps[light].users == 0:
                    continue
                if cnt < 3:
                    LCM.append(
                        str(
                            int(bpy.data.lamps[light].color.r * 4096)
                            if bpy.data.lamps[light].color.r
                            else 0
                        )
                    )
                    LCM.append(
                        str(
                            int(bpy.data.lamps[light].color.g * 4096)
                            if bpy.data.lamps[light].color.g
                            else 0
                        )
                    )
                    LCM.append(
                        str(
                            int(bpy.data.lamps[light].color.b * 4096)
                            if bpy.data.lamps[light].color.b
                            else 0
                        )
                    )
                    cnt += 1
            if len(LCM) < 9:
                while len(LCM) < 9:
                    LCM.append("0")
            # Write LC matrix
            f.write(
                "//   L1   L2   L3\n"
                "\t"
                + LCM[0]
                + ", "
                + LCM[3]
                + ", "
                + LCM[6]
                + ", // R\n"
                + "\t"
                + LCM[1]
                + ", "
                + LCM[4]
                + ", "
                + LCM[7]
                + ", // G\n"
                + "\t"
                + LCM[2]
                + ", "
                + LCM[5]
                + ", "
                + LCM[8]
                + "  // B\n"
            )
            f.write("\t};\n\n")
            self.level_symbols.append("MATRIX " + self.fileName + "_cmat")

    def populate_mesh(self, mesh: bpy.types.Object, f: TextIO):
        # Store vertices coordinates by axis to find max/min coordinates
        Xvals = []
        Yvals = []
        Zvals = []
        cleanName = self.cleanName(mesh.name)
        f.write()
        f.write("SVECTOR " + self.fileName + "_model" + cleanName + "_mesh[] = {\n")
        self.level_symbols.append("SVECTOR " + "model" + cleanName + "_mesh[]")
        for i in range(len(mesh.vertices)):
            v = mesh.vertices[i].co
            # Append vertex coords to lists
            Xvals.append(v.x)
            Yvals.append(v.y)
            Zvals.append(-v.z)
            f.write(
                "\t{ "
                + str(ceil(v.x * self.exp_Scale))
                + ","
                + str(ceil(-v.z * self.exp_Scale))
                + ","
                + str(ceil(v.y * self.exp_Scale))
                + ",0 }"
            )
            if i != len(mesh.vertices) - 1:
                f.write(",")
            f.write("\n")
        f.write / ("};\n\n")

        # write normals vectors
        f.write("SVECTOR " + self.fileName + "_model" + cleanName + "_normal[] = {\n")
        self.level_symbols.append(
            "SVECTOR " + self.fileName + "_model" + cleanName + "_normal[]"
        )
        for i in range(len(mesh.vertices)):
            poly = mesh.vertices[i]
            f.write(
                "\t"
                + str(round(-poly.normal.x * 4096))
                + ","
                + str(round(poly.normal.z * 4096))
                + ","
                + str(round(-poly.normal.y * 4096))
                + ", 0"
            )
            if i != len(mesh.vertices) - 1:
                f.write(",")
            f.write("\n")
        f.write("};\n\n")

        # Write UV textures coordinates
        if len(mesh.uv_textures) != None:
            for t in range(len(mesh.uv_textures)):
                if mesh.uv_textures[t].data[0].image != None:
                    f.write(
                        "SVECTOR "
                        + self.fileName
                        + "_model"
                        + cleanName
                        + "_uv[] = {\n"
                    )
                    self.level_symbols.append(
                        "SVECTOR " + self.fileName + "_model" + cleanName + "_uv[]"
                    )
                    texture_image = mesh.uv_textures[t].data[0].image
                    tex_width = texture_image.size[0]
                    tex_height = texture_image.size[1]
                    uv_layer = mesh.uv_layers[0].data
                    for i in range(len(uv_layer)):
                        u = uv_layer[i].uv
                        ux = u.x * tex_width
                        uy = u.y * tex_height
                        # Clamp values to 0-255 to avoid tpage overflow
                        f.write(
                            "\t"
                            + str(max(0, min(round(ux), 255)))
                            + ","
                            + str(max(0, min(round(tex_height - uy), 255)))
                            + ", 0, 0"
                        )
                        if i != len(uv_layer) - 1:
                            f.write(",")
                        f.write("\n")
                    f.write("};\n\n")
                    # Save UV texture to a file in ./TEX
                    # It will have to be converted to a tim file
                    if texture_image.filepath == "":
                        # ~ os.makedirs(dirpath, exist_ok = 1)
                        texture_image.filepath_raw = (
                            self.textureFolder
                            + os.sep
                            + self.cleanName(texture_image.name)
                            + "."
                            + texture_image.file_format
                        )
                    texture_image.save()

        # Write vertex colors vectors
        f.write("CVECTOR " + self.fileName + "_model" + cleanName + "_color[] = {\n")
        self.level_symbols.append(
            "CVECTOR " + self.fileName + "_model" + cleanName + "_color[]"
        )

        # If vertex colors exist, use them
        if len(mesh.vertex_colors) != 0:
            colors = mesh.vertex_colors[0].data
            for i in range(len(colors)):
                f.write(
                    "\t"
                    + str(int(colors[i].color.r * 255))
                    + ","
                    + str(int(colors[i].color.g * 255))
                    + ","
                    + str(int(colors[i].color.b * 255))
                    + ", 0"
                )
                if i != len(colors) - 1:
                    f.write(",")
                f.write("\n")
        # If no vertex colors, default to 2 whites, 1 grey
        else:
            for i in range(len(mesh.polygons) * 3):
                if i % 3 == 0:
                    f.write("\t80, 80, 80, 0")
                else:
                    f.write("\t128, 128, 128, 0")
                if i != (len(mesh.polygons) * 3) - 1:
                    f.write(",")
                f.write("\n")
        f.write("};\n\n")

        # Get object's custom properties
        # Set defaults values
        chkProp = {
            "isAnim": 0,
            "isProp": 0,
            "isRigidBody": 0,
            "isStaticBody": 0,
            "isRound": 0,
            "isPrism": 0,
            "isActor": 0,
            "isLevel": 0,
            "isWall": 0,
            "isBG": 0,
            "isSprite": 0,
            "mass": 10,
            "restitution": 0,
        }
        # Get real values from object
        for prop in chkProp:
            if mesh.get(prop) is not None:
                chkProp[prop] = mesh.get(prop)
        # put isBG back to 0 if using precalculated BGs
        if not self.exp_Precalc:
            chkProp["isBG"] = 0
        if mesh.get("isActor"):
            actorPtr = mesh.name
        if mesh.get("isLevel"):
            levelPtr = cleanName
        if mesh.get("isProp"):
            propPtr = cleanName
        if chkProp["mass"] == 0:
            chkProp["mass"] = 1

        # MESH WORLD TRANSFORM SETUP
        ## Mesh world transform setup
        # Write object matrix, rot and pos vectors
        f.write(
            "BODY "
            + self.fileName
            + "_model"
            + cleanName
            + "_body = {\n"
            + "\t{0, 0, 0, 0},\n"
            + "\t"
            + str(
                round(
                    bpy.data.objects[self.mshObjects[mesh.name]].location.x
                    * self.exp_Scale
                )
            )
            + ","
            + str(
                round(
                    -bpy.data.objects[self.mshObjects[mesh.name]].location.z
                    * self.exp_Scale
                )
            )
            + ","
            + str(
                round(
                    bpy.data.objects[self.mshObjects[mesh.name]].location.y
                    * self.exp_Scale
                )
            )
            + ", 0,\n"
            + "\t"
            + str(
                round(
                    degrees(
                        bpy.data.objects[self.mshObjects[mesh.name]].rotation_euler.x
                    )
                    / 360
                    * 4096
                )
            )
            + ","
            + str(
                round(
                    degrees(
                        -bpy.data.objects[self.mshObjects[mesh.name]].rotation_euler.z
                    )
                    / 360
                    * 4096
                )
            )
            + ","
            + str(
                round(
                    degrees(
                        bpy.data.objects[self.mshObjects[mesh.name]].rotation_euler.y
                    )
                    / 360
                    * 4096
                )
            )
            + ", 0,\n"
            + "\t"
            + str(int(chkProp["mass"]))
            + ",\n"
            + "\tONE/"
            + str(int(chkProp["mass"]))
            + ",\n"
            +
            # write min and max values of AABBs on each axis
            "\t"
            + str(round(min(Xvals) * self.exp_Scale))
            + ","
            + str(round(min(Zvals) * self.exp_Scale))
            + ","
            + str(round(min(Yvals) * self.exp_Scale))
            + ", 0,\n"
            + "\t"
            + str(round(max(Xvals) * self.exp_Scale))
            + ","
            + str(round(max(Zvals) * self.exp_Scale))
            + ","
            + str(round(max(Yvals) * self.exp_Scale))
            + ", 0,\n"
            + "\t"
            + str(int(chkProp["restitution"]))
            + ",\n"
            +
            # ~ "\tNULL\n" +
            "\t};\n\n"
        )
        self.level_symbols.append(
            "BODY  " + self.fileName + "_model" + cleanName + "_body"
        )
        # Write TMESH struct
        f.write("TMESH " + self.fileName + "_model" + cleanName + " = {\n")
        f.write("\t" + self.fileName + "_model" + cleanName + "_mesh,\n")
        f.write("\t" + self.fileName + "_model" + cleanName + "_normal,\n")
        self.level_symbols.append("TMESH " + self.fileName + "_model" + cleanName)
        # ~ level_symbols.append( "model" + cleanName + "_mesh"  )
        # ~ level_symbols.append( "model" + cleanName + "_normal" )
        if len(mesh.uv_textures) != 0:
            for t in range(len(mesh.uv_textures)):
                if mesh.uv_textures[0].data[0].image != None:
                    f.write("\t" + self.fileName + "_model" + cleanName + "_uv,\n")
                    # ~ level_symbols.append( "model" + cleanName + "_uv" )
                else:
                    f.write("\t0,\n")
        else:
            f.write("\t0,\n")
        f.write("\t" + self.fileName + "_model" + cleanName + "_color, \n")
        # According to libgte.h, TMESH.len should be # of vertices. Meh...
        f.write("\t" + str(len(mesh.polygons)) + "\n")
        f.write("};\n\n")
        # Write texture binary name and declare TIM_IMAGE
        # By default, loads the file from the ./TIM folder
        if len(mesh.uv_textures) != None:
            for t in range(len(mesh.uv_textures)):
                if mesh.uv_textures[0].data[0].image != None:
                    tex_name = texture_image.name
                    # extension defaults to the image file format
                    tex_ext = texture_image.file_format.lower()
                    prefix = str.partition(tex_name, ".")[0].replace("-", "_")
                    prefix = self.cleanName(prefix)
                    # Add Tex name to list if it's not in there already
                    if prefix in self.timList:
                        break
                    else:
                        # Convert PNG to TIM
                        # If filename contains a dot, separate name and extension
                        if tex_name.find(".") != -1:
                            # store extension
                            tex_ext = tex_name[tex_name.rfind(".") + 1 :]
                            # store name
                            tex_name = tex_name[: tex_name.rfind(".")]
                        # ~ filePathWithExt = textureFolder + os.sep + self.cleanName( tex_name ) + "." + texture_image.file_format.lower()
                        filePathWithExt = (
                            self.textureFolder
                            + os.sep
                            + self.cleanName(tex_name)
                            + "."
                            + tex_ext
                        )
                        if not self.VramIsFull(bpy.context.scene.render.resolution_x):
                            self.convertBGtoTIM(
                                filePathWithExt,
                                bpp=self.TIMbpp,
                                timX=nextTpage,
                                timY=tpageY,
                                clutY=self.nextClutSlot,
                            )
                            self.setNextTimPos(texture_image)
                        elif (
                            self.VramIsFull(bpy.context.scene.render.resolution_x)
                            and tpageY == 0
                        ):
                            tpageY = 256
                            nextTpage = 320
                            if not self.VramIsFull(
                                bpy.context.scene.render.resolution_x
                            ):
                                self.convertBGtoTIM(
                                    filePathWithExt,
                                    bpp=self.TIMbpp,
                                    timX=nextTpage,
                                    timY=tpageY,
                                    clutY=self.nextClutSlot,
                                )
                                self.setNextTimPos(texture_image)
                            else:
                                self.report({"ERROR"}, "Not enough space in VRam !")
                        else:
                            self.report({"ERROR"}, "Not enough space in VRam !")
                        # Write corresponding TIM declaration
                        f.write(
                            "extern unsigned long "
                            + "_binary_TIM_"
                            + prefix
                            + "_tim_start[];\n"
                        )
                        f.write(
                            "extern unsigned long "
                            + "_binary_TIM_"
                            + prefix
                            + "_tim_end[];\n"
                        )
                        f.write(
                            "extern unsigned long "
                            + "_binary_TIM_"
                            + prefix
                            + "_tim_length;\n\n"
                        )
                        f.write(
                            "TIM_IMAGE " + self.fileName + "_tim_" + prefix + ";\n\n"
                        )
                        self.level_symbols.append(
                            "unsigned long " + "_binary_TIM_" + prefix + "_tim_start[]"
                        )
                        self.level_symbols.append(
                            "unsigned long " + "_binary_TIM_" + prefix + "_tim_end[]"
                        )
                        self.level_symbols.append(
                            "unsigned long " + "_binary_TIM_" + prefix + "_tim_length"
                        )
                        self.level_symbols.append(
                            "TIM_IMAGE " + self.fileName + "_tim_" + prefix
                        )
                        self.timList.append(prefix)
        f.write(
            "MESH "
            + self.fileName
            + "_mesh"
            + cleanName
            + " = {\n"
            + "\t"
            + str(self.totalVerts)
            + ",\n"
            + "\t&"
            + self.fileName
            + "_model"
            + cleanName
            + ",\n"
            + "\t"
            + self.fileName
            + "_model"
            + cleanName
            + "_index,\n"
        )
        if len(mesh.uv_textures) != 0:
            for t in range(len(mesh.uv_textures)):
                if mesh.uv_textures[0].data[0].image != None:
                    tex_name = texture_image.name
                    prefix = str.partition(tex_name, ".")[0].replace("-", "_")
                    prefix = self.cleanName(prefix)
                    f.write("\t&" + self.fileName + "_tim_" + prefix + ",\n")
                    f.write("\t_binary_TIM_" + prefix + "_tim_start,\n")
                else:
                    f.write("\t0,\n" + "\t0,\n")
        else:
            f.write("\t0,\n" + "\t0,\n")

        # Find out if object as animations

        # FIXME: for some reason we had obj.data.name instead of mesh.data.name
        # I have no idea how this would have worked in the first place

        # fuck this has to be the worst python code I've ever worked on
        symbol_name = (
            "MESH_ANIMS_TRACKS "
            + self.fileName
            + "_model"
            + self.cleanName(mesh.data.name)
            + "_anims"
        )
        if symbol_name in self.level_symbols:
            symbol_name = (
                "&"
                + self.fileName
                + "_model"
                + self.cleanName(mesh.data.name)
                + "_anims"
            )
        else:
            symbol_name = "0"
        f.write(
            "\t{0}, // Matrix\n"
            + "\t{"
            + str(
                round(
                    bpy.data.objects[self.mshObjects[mesh.name]].location.x
                    * self.exp_Scale
                )
            )
            + ","
            + str(
                round(
                    -bpy.data.objects[self.mshObjects[mesh.name]].location.z
                    * self.exp_Scale
                )
            )
            + ","
            + str(
                round(
                    bpy.data.objects[self.mshObjects[mesh.name]].location.y
                    * self.exp_Scale
                )
            )
            + ", 0}, // position\n"
            + "\t{"
            + str(
                round(
                    degrees(
                        bpy.data.objects[self.mshObjects[mesh.name]].rotation_euler.x
                    )
                    / 360
                    * 4096
                )
            )
            + ","
            + str(
                round(
                    degrees(
                        -bpy.data.objects[self.mshObjects[mesh.name]].rotation_euler.z
                    )
                    / 360
                    * 4096
                )
            )
            + ","
            + str(
                round(
                    degrees(
                        bpy.data.objects[self.mshObjects[mesh.name]].rotation_euler.y
                    )
                    / 360
                    * 4096
                )
            )
            + ", 0}, // rotation\n"
            + "\t"
            + str(int(chkProp["isProp"]))
            + ", // isProp\n"
            + "\t"
            + str(int(chkProp["isRigidBody"]))
            + ", // isRigidBody\n"
            + "\t"
            + str(int(chkProp["isStaticBody"]))
            + ", // isStaticBody\n"
            + "\t"
            + str(int(chkProp["isRound"]))
            + ", // isRound \n"
            + "\t"
            + str(int(chkProp["isPrism"]))
            + ", // isPrism\n"
            + "\t"
            + str(int(chkProp["isAnim"]))
            + ", // isAnim\n"
            + "\t"
            + str(int(chkProp["isActor"]))
            + ", // isActor\n"
            + "\t"
            + str(int(chkProp["isLevel"]))
            + ", // isLevel\n"
            + "\t"
            + str(int(chkProp["isWall"]))
            + ", // isWall\n"
            + "\t"
            + str(int(chkProp["isBG"]))
            + ", // isBG\n"
            + "\t"
            + str(int(chkProp["isSprite"]))
            + ", // isSprite\n"
            + "\t0, // p\n"
            + "\t0, // otz\n"
            + "\t&"
            + self.fileName
            + "_model"
            + cleanName
            + "_body,\n"
            + "\t"
            + symbol_name
            + ", // Mesh anim tracks\n"
            + "\t0, // Current VANIM\n"
            + "\t"
            + "subs_"
            + self.cleanName(mesh.name)
            + ",\n"
            + "\t0 // Screen space coordinates\n"
            + "};\n\n"
        )
        self.level_symbols.append("MESH " + self.fileName + "_mesh" + cleanName)

    def populate_portals(self, portal_list: List[bpy.types.Object], f: TextIO):
        for k in range(len(portal_list)):
            cleanName = self.cleanName(portal_list[k].name)
            f.write("\t&" + self.fileName + "_mesh" + cleanName)
            if k != len(portal_list) - 1:
                f.write(",\n")

        f.write("\n}; \n\n")
        f.write(
            "int "
            + self.fileName
            + "_meshes_length = "
            + str(len(portal_list))
            + ";\n\n"
        )

        self.level_symbols.append(
            "MESH * " + self.fileName + "_meshes[" + str(len(self.portal_list)) + "]"
        )
        self.level_symbols.append("int " + self.fileName + "_meshes_length")

    def populate_cam_angle(
        self, camera: bpy.types.Camera, actorPtr: str, portal_name_list: list, f: TextIO
    ):
        scene = bpy.context.scene
        # List of portals
        visiblePortal = []
        for portal in portal_name_list:
            if self.isInFrame(scene, camera, portal):
                # Get normalized direction vector between camera and portal
                dirToTarget = portal.location - camera.location
                dirToTarget.normalize()
                # Cast a ray from camera to body to determine visibility
                result, location, normal, index, hitObject, matrix = scene.ray_cast(
                    camera.location, dirToTarget
                )
                # If hitObject is portal, nothing is obstructing it's visibility
                if hitObject is not None:
                    if hitObject in portal_name_list:
                        if hitObject == portal:
                            visiblePortal.append(hitObject)
        # If more than one portal is visible, only keep the two closest ones
        if len(visiblePortal) > 2:
            # Store the tested portals distance to camera
            testDict = {}
            for tested in visiblePortal:
                # Get distance between cam and tested portal
                distToTested = sqrt(
                    (tested.location - camera.location)
                    * (tested.location - camera.location)
                )
                # Store distance
                testDict[distToTested] = tested
            # If dictionary has more than 2 portals, remove the farthest ones
            while len(testDict) > 2:
                del testDict[max(testDict)]
            # Reset visible portal
            visiblePortal.clear()
            # Get the portals stored in the dict and store them in the list
            for Dportal in testDict:
                visiblePortal.append(testDict[Dportal])
            # Revert to find original order back
            visiblePortal.reverse()
        # List of target found visible
        visibleTarget = []
        for target in self.rayTargets:
            # Chech object is in view frame
            if self.isInFrame(scene, camera, target):
                # Get normalized direction vector between camera and object
                dirToTarget = target.location - camera.location
                dirToTarget.normalize()
                # Cast ray from camera to object
                # Unpack results into several variables.
                # We're only interested in 'hitObject' though
                result, hitLocation, normal, index, hitObject, matrix = scene.ray_cast(
                    camera.location, dirToTarget
                )
                # If hitObject is the same as target, nothing is obstructing it's visibility
                if hitObject is not None:
                    # If hit object is a portal, cast a new ray from hit location to target
                    if hitObject.data.get("isPortal"):
                        # Find out if we're left or right of portal
                        # Get vertices world coordinates
                        v0 = hitObject.matrix_world * hitObject.data.vertices[0].co
                        v1 = hitObject.matrix_world * hitObject.data.vertices[1].co
                        # Check side :
                        #               'back' : portal in on the right of the cam, cam is on left of portal
                        #               'front' : portal in on the left of the cam, cam is on right of portal
                        side = self.checkLine(
                            v0.x,
                            v0.y,
                            v1.x,
                            v1.y,
                            camera.location.x,
                            camera.location.y,
                            camera.location.x,
                            camera.location.y,
                        )
                        if side == "front":
                            # we're on the right of the portal, origin.x must be > hitLocation.x
                            offset = [1.001, 0.999, 0.999]
                        else:
                            # we're on the left of the portal, origin.x must be < hitLocation.x
                            offset = [0.999, 1.001, 1.001]
                        # Add offset to hitLocation, so that the new ray won't hit the same portal
                        origin = Vector(
                            (
                                hitLocation.x * offset[0],
                                hitLocation.y * offset[1],
                                hitLocation.z * offset[2],
                            )
                        )
                        (
                            result,
                            hitLocationPort,
                            normal,
                            index,
                            hitObjectPort,
                            matrix,
                        ) = scene.ray_cast(origin, dirToTarget)
                        if hitObjectPort is not None:
                            if hitObjectPort in self.rayTargets:
                                visibleTarget.append(target)
                    # If hitObject is not a portal, just add it
                    elif hitObject in self.rayTargets:
                        visibleTarget.append(target)
        if bpy.data.objects[actorPtr] not in visibleTarget:
            visibleTarget.append(bpy.data.objects[actorPtr])
        # If visiblePortal length is under 2, this means there's only one portal
        # Empty strings to be populated depending on portal position (left/right of screen)
        before = ""
        after = ""
        if len(visiblePortal) < 2:
            # Find wich side of screen the portal is on. left side : portal == bw, right side : portal == fw
            screenCenterX = int(scene.render.resolution_x / 2)
            screenY = int(scene.render.resolution_y)
            # Get vertices screen coordinates
            s = self.objVertWtoS(scene, camera, visiblePortal[0])
            # Check line
            side = self.checkLine(
                screenCenterX, 0, screenCenterX, screenY, s[1].x, s[1].y, s[3].x, s[3].y
            )
            # If front == right of screen : fw
            if side == "front":
                before = "\t{\n\t\t{ 0, 0, 0, 0 },\n\t\t{ 0, 0, 0, 0 },\n\t\t{ 0, 0, 0, 0 },\n\t\t{ 0, 0, 0, 0 }\n\t},\n"
            # If back == left of screen : bw
            else:
                after = "\t{\n\t\t{ 0, 0, 0, 0 },\n\t\t{ 0, 0, 0, 0 },\n\t\t{ 0, 0, 0, 0 },\n\t\t{ 0, 0, 0, 0 }\n\t},\n"
        prefix = self.cleanName(camera.name)
        # Include Tim data
        f.write(
            "extern unsigned long " + "_binary_TIM_bg_" + prefix + "_tim_start[];\n"
        )
        f.write("extern unsigned long " + "_binary_TIM_bg_" + prefix + "_tim_end[];\n")
        f.write(
            "extern unsigned long " + "_binary_TIM_bg_" + prefix + "_tim_length;\n\n"
        )
        # Write corresponding TIM_IMAGE struct
        f.write("TIM_IMAGE tim_bg_" + prefix + ";\n\n")
        # Write corresponding CamAngle struct
        f.write(
            "CAMANGLE "
            + self.fileName
            + "_camAngle_"
            + prefix
            + " = {\n"
            + "\t&"
            + self.fileName
            + "_camPos_"
            + prefix
            + ",\n"
            + "\t&tim_bg_"
            + prefix
            + ",\n"
            + "\t_binary_TIM_bg_"
            + prefix
            + "_tim_start,\n"
            + "\t// Write quad NW, NE, SE, SW\n"
        )
        f.write(before)
        # Feed to level_symbols
        self.level_symbols.append(
            "unsigned long " + "_binary_TIM_bg_" + prefix + "_tim_start[]"
        )
        self.level_symbols.append(
            "unsigned long " + "_binary_TIM_bg_" + prefix + "_tim_end[]"
        )
        self.level_symbols.append(
            "unsigned long " + "_binary_TIM_bg_" + prefix + "_tim_length"
        )
        self.level_symbols.append("TIM_IMAGE tim_bg_" + prefix)
        self.level_symbols.append("CAMANGLE " + self.fileName + "_camAngle_" + prefix)
        for portal in visiblePortal:
            w = self.objVertLtoW(portal)
            # ~ f.write("\t// " + str(portal) + "\n" )
            # Write portal'vertices world coordinates NW, NE, SE, SW
            f.write(
                "\t{\n\t\t"
                + "{ "
                + str(int(w[3].x))
                + ", "
                + str(int(w[3].y))
                + ", "
                + str(int(w[3].z))
                + ", 0 },\n\t\t"
                + "{ "
                + str(int(w[2].x))
                + ", "
                + str(int(w[2].y))
                + ", "
                + str(int(w[2].z))
                + ", 0 },\n\t\t"
                + "{ "
                + str(int(w[0].x))
                + ", "
                + str(int(w[0].y))
                + ", "
                + str(int(w[0].z))
                + ", 0 },\n\t\t"
                + "{ "
                + str(int(w[1].x))
                + ", "
                + str(int(w[1].y))
                + ", "
                + str(int(w[1].z))
                + ", 0 }\n"
                + "\t},\n"
            )
        f.write(after)
        # UNUSED : Screen coords
        # ~ s = objVertWtoS( scene, camera, portal )
        # ~ f.write("\t{\n\t\t" +
        # ~ "{ " + str( int (s[3].x ) ) + ", " + str( int (s[3].y ) ) + ", " + str( int (s[3].z ) ) + ", 0 },\n\t\t" +
        # ~ "{ " + str( int (s[2].x ) ) + ", " + str( int (s[2].y ) ) + ", " + str( int (s[2].z ) ) + ", 0 },\n\t\t" +
        # ~ "{ " + str( int (s[0].x ) ) + ", " + str( int (s[0].y ) ) + ", " + str( int (s[0].z ) ) + ", 0 },\n\t\t" +
        # ~ "{ " + str( int (s[1].x ) ) + ", " + str( int (s[1].y ) ) + ", " + str( int (s[1].z ) ) + ", 0 }\n" +
        # ~ "\t},\n" )
        f.write("\t" + str(len(visibleTarget)) + ",\n" + "\t{\n")
        for target in range(len(visibleTarget)):
            f.write(
                "\t\t&"
                + self.fileName
                + "_mesh"
                + self.cleanName(visibleTarget[target].name)
            )
            if target < len(visibleTarget) - 1:
                f.write(",\n")
        f.write("\n\t}\n" + "};\n\n")

    def populate_cam_angles(self, actorPtr: str, portal_name_list: list, f: TextIO):
        for camera in self.camAngles:
            # Get current scene
            self.populate_cam_angle(
                camera=camera, actorPtr=actorPtr, portal_name_list=portal_name_list, f=f
            )

        # Write camera angles in an array for loops
        f.write(
            "CAMANGLE * "
            + self.fileName
            + "_camAngles["
            + str(len(self.camAngles))
            + "] = {\n"
        )
        for camera in self.camAngles:
            prefix = self.cleanName(camera.name)
            f.write("\t&" + self.fileName + "_camAngle_" + prefix + ",\n")
        f.write("};\n\n")
        # Feed to self.level_symbols
        self.level_symbols.append(
            "CAMANGLE * "
            + self.fileName
            + "_camAngles["
            + str(len(self.camAngles))
            + "]"
        )

    def populate_horizon_color(self, f: TextIO):
        # world horizon color
        BGr = str(
            round(self.linearToRGB(bpy.data.worlds[0].horizon_color.r) * 192) + 63
        )
        BGg = str(
            round(self.linearToRGB(bpy.data.worlds[0].horizon_color.g) * 192) + 63
        )
        BGb = str(
            round(self.linearToRGB(bpy.data.worlds[0].horizon_color.b) * 192) + 63
        )
        horizon_color_str = (
            f"CVECTOR {self.fileName}_BGc = {{ {BGr}, {BGg}, {BGb}, 0 }};\n"
        )
        f.write(horizon_color_str)
        self.level_symbols.append(f"CVECTOR {self.fileName}_BGc")

    def populate_ambient_color(self, f: TextIO):
        # ambient color
        BKr = str(
            round(self.linearToRGB(bpy.data.worlds[0].ambient_color.r) * 192) + 63
        )
        BKg = str(
            round(self.linearToRGB(bpy.data.worlds[0].ambient_color.g) * 192) + 63
        )
        BKb = str(
            round(self.linearToRGB(bpy.data.worlds[0].ambient_color.b) * 192) + 63
        )
        ambient_color_str = (
            f"VECTOR {self.fileName}_BKc = {{ {BKr}, {BKg}, {BKb}, 0 }};\n\n"
        )
        f.write(ambient_color_str)
        self.level_symbols.append(f"VECTOR {self.fileName}_BKc")

    def populate_spatial_partitioning(self, actorPtr: str, propPtr: str, f: TextIO):
        # Planes in the level - dict of strings
        LvlPlanes = {}
        # Objects in the level  - dict of strings
        LvlObjects = {}
        # Link objects to their respective plane
        PlanesObjects = defaultdict(dict)
        PlanesRigidBodies = defaultdict(dict)
        # List of objects that can travel ( actor , moveable props...)
        Moveables = []
        # Store starting plane for moveables
        PropPlane = defaultdict(dict)
        # Store XY1, XY2 values
        Xvalues = []
        Yvalues = []
        # Find planes and objects bounding boxes
        # Planes first
        for obj in bpy.data.objects:
            # If orphan, ignore
            if obj.users == 0:
                continue
            # Only loop through meshes
            if obj.type == "MESH" and not obj.data.get("isPortal"):
                # Get Level planes coordinates
                if obj.data.get("isLevel"):
                    # World matrix is used to convert local to global coordinates
                    mw = obj.matrix_world
                    for v in bpy.data.objects[obj.name].data.vertices:
                        # Convert local to global coords
                        Xvalues.append((mw * v.co).x)
                        Yvalues.append((mw * v.co).y)
                    LvlPlanes[obj.name] = {
                        "x1": min(Xvalues),
                        "y1": min(Yvalues),
                        "x2": max(Xvalues),
                        "y2": max(Yvalues),
                    }
                    # Clear X/Y lists for next iteration
                    Xvalues = []
                    Yvalues = []
                # For each object not a plane, get its coordinates
                if not obj.data.get("isLevel"):
                    # World matrix is used to convert local to global coordinates
                    mw = obj.matrix_world
                    for v in bpy.data.objects[obj.name].data.vertices:
                        # Convert local to global coords
                        Xvalues.append((mw * v.co).x)
                        Yvalues.append((mw * v.co).y)
                    LvlObjects[obj.name] = {
                        "x1": min(Xvalues),
                        "y1": min(Yvalues),
                        "x2": max(Xvalues),
                        "y2": max(Yvalues),
                    }
                    # Clear X/Y lists for next iteration
                    Xvalues = []
                    Yvalues = []
                    # Add objects that can travel to the
                    if obj.data.get("isRigidBody"):
                        Moveables.append(o)
        # Declare LvlPlanes nodes to avoid declaration dependency issues
        # ~ for k in LvlPlanes.keys():
        # ~ f.write("NODE node" + CleanName(k) + ";\n\n")
        # Sides of the plane to check
        checkSides = [["N", "S"], ["S", "N"], ["W", "E"], ["E", "W"]]
        # Generate a dict :
        # ~ {
        # ~     'S' : []
        # ~     'N' : [] list of planes connected to this plane, and side they're on
        # ~     'W' : []
        # ~     'E' : []
        # ~     'objects' : [] list of objects on this plane
        # ~     ''
        # ~ }
        overlappingObject = []
        for p in LvlPlanes:
            # Find objects on plane
            for o in LvlObjects:
                # If object is overlapping between several planes
                if self.isInPlane(LvlPlanes[p], LvlObjects[o]) > 1:
                    # Object not actor
                    if o != actorPtr:
                        # Object not in list
                        if o not in overlappingObject:
                            overlappingObject.append(o)
                        else:
                            overlappingObject.remove(o)
                            # Add this object to the plane's list
                            if "objects" in PlanesObjects[p]:
                                PlanesObjects[p]["objects"].append(o)
                            else:
                                PlanesObjects[p] = {"objects": [o]}
                # If object is above plane
                if self.isInPlane(LvlPlanes[p], LvlObjects[o]) == 1:
                    # Add all objects but the actor
                    if o != actorPtr:
                        # Add this object to the plane's list
                        if "objects" in PlanesObjects[p]:
                            PlanesObjects[p]["objects"].append(o)
                        else:
                            PlanesObjects[p] = {"objects": [o]}
                    else:
                        # If actor is on this plane, use it as starting node
                        levelPtr = p
                        nodePtr = p
            # Add moveable objects in every plane
            for moveable in Moveables:
                # If moveable is not actor
                if moveable.data.get("isProp"):
                    # If is in current plane, add it to the list
                    if self.isInPlane(LvlPlanes[p], LvlObjects[moveable.name]):
                        PropPlane[moveable] = self.cleanName(p)
                        # ~ PropPlane[moveable] = self.cleanName(bpy.data.objects[p].data.name)
                if "rigidbodies" in PlanesRigidBodies[p]:
                    if moveable.name not in PlanesRigidBodies[p]["rigidbodies"]:
                        # ~ PlanesRigidBodies[ p ][ 'rigidbodies' ].append(self.cleanName( moveable.name ) )
                        PlanesRigidBodies[p]["rigidbodies"].append(moveable.name)
                else:
                    PlanesRigidBodies[p] = {"rigidbodies": [moveable.name]}
            # Find surrounding planes
            for op in LvlPlanes:
                # Loop on other planes
                if op is not p:
                    # Check each side
                    for s in checkSides:
                        # If connected ('connected') plane exists...
                        if self.checkLine(
                            self.getSepLine(p, s[0])[0],
                            self.getSepLine(p, s[0])[1],
                            self.getSepLine(p, s[0])[2],
                            self.getSepLine(p, s[0])[3],
                            self.getSepLine(op, s[1])[0],
                            self.getSepLine(op, s[1])[1],
                            self.getSepLine(op, s[1])[2],
                            self.getSepLine(op, s[1])[3],
                        ) == "connected" and (
                            self.isInPlane(LvlPlanes[p], LvlPlanes[op])
                        ):
                            # ... add it to the list
                            if "siblings" not in PlanesObjects[p]:
                                PlanesObjects[p]["siblings"] = {}
                            # If more than one plane is connected on the same side of the plane,
                            # add it to the corresponding list
                            if s[0] in PlanesObjects[p]["siblings"]:
                                PlanesObjects[p]["siblings"][s[0]].append(op)
                            else:
                                PlanesObjects[p]["siblings"][s[0]] = [op]
            pName = self.cleanName(p)
            # Write SIBLINGS structure
            nSiblings = 0
            if "siblings" in PlanesObjects[p]:
                if "S" in PlanesObjects[p]["siblings"]:
                    nSiblings += len(PlanesObjects[p]["siblings"]["S"])
                if "N" in PlanesObjects[p]["siblings"]:
                    nSiblings += len(PlanesObjects[p]["siblings"]["N"])
                if "E" in PlanesObjects[p]["siblings"]:
                    nSiblings += len(PlanesObjects[p]["siblings"]["E"])
                if "W" in PlanesObjects[p]["siblings"]:
                    nSiblings += len(PlanesObjects[p]["siblings"]["W"])
            f.write(
                "SIBLINGS "
                + self.fileName
                + "_node"
                + pName
                + "_siblings = {\n"
                + "\t"
                + str(nSiblings)
                + ",\n"
                + "\t{\n"
            )
            if "siblings" in PlanesObjects[p]:
                i = 0
                for side in PlanesObjects[p]["siblings"]:
                    for sibling in PlanesObjects[p]["siblings"][side]:
                        f.write(
                            "\t\t&" + self.fileName + "_node" + self.cleanName(sibling)
                        )
                        if i < (nSiblings - 1):
                            f.write(",")
                        i += 1
                        f.write("\n")
            else:
                f.write("\t\t0\n")
            f.write("\t}\n" + "};\n\n")
            # Feed to level_symbols
            self.level_symbols.append(
                "SIBLINGS " + self.fileName + "_node" + pName + "_siblings"
            )
            # Write CHILDREN static objects structure
            f.write("CHILDREN " + self.fileName + "_node" + pName + "_objects = {\n")
            if "objects" in PlanesObjects[p]:
                f.write("\t" + str(len(PlanesObjects[p]["objects"])) + ",\n" + "\t{\n")
                i = 0
                for obj in PlanesObjects[p]["objects"]:
                    f.write(
                        "\t\t&"
                        + self.fileName
                        + "_mesh"
                        + self.cleanName(bpy.data.objects[obj].data.name)
                    )
                    if i < len(PlanesObjects[p]["objects"]) - 1:
                        f.write(",")
                    i += 1
                    f.write("\n")
            else:
                f.write("\t0,\n" + "\t{\n\t\t0\n")
            f.write("\t}\n" + "};\n\n")
            # Feed to self.level_symbols
            self.level_symbols.append(
                "CHILDREN " + self.fileName + "_node" + pName + "_objects"
            )
            # Write CHILDREN rigidbodies structure
            f.write(
                "CHILDREN " + self.fileName + "_node" + pName + "_rigidbodies = {\n"
            )
            if "rigidbodies" in PlanesRigidBodies[p]:
                f.write(
                    "\t"
                    + str(len(PlanesRigidBodies[p]["rigidbodies"]))
                    + ",\n"
                    + "\t{\n"
                )
                i = 0
                for obj in PlanesRigidBodies[p]["rigidbodies"]:
                    # ~ f.write( "\t\t&" + self.fileName + "_mesh" + self.cleanName(obj))
                    f.write(
                        "\t\t&"
                        + self.fileName
                        + "_mesh"
                        + self.cleanName(bpy.data.objects[obj].data.name)
                    )
                    if i < len(PlanesRigidBodies[p]["rigidbodies"]) - 1:
                        f.write(",")
                    i += 1
                    f.write("\n")
            else:
                f.write("\t0,\n" + "\t{\n\t\t0\n")
            f.write("\t}\n" + "};\n\n")
            # Feed to self.level_symbols
            self.level_symbols.append(
                "CHILDREN " + self.fileName + "_node" + pName + "_rigidbodies"
            )
            # Write NODE structure
            f.write(
                "NODE "
                + self.fileName
                + "_node"
                + pName
                + " = {\n"
                + "\t&"
                + self.fileName
                + "_mesh"
                + self.cleanName(bpy.data.objects[p].data.name)
                + ",\n"
                + "\t&"
                + self.fileName
                + "_node"
                + pName
                + "_siblings,\n"
                + "\t&"
                + self.fileName
                + "_node"
                + pName
                + "_objects,\n"
                + "\t&"
                + self.fileName
                + "_node"
                + pName
                + "_rigidbodies\n"
                + "};\n\n"
            )
            # Feed to self.level_symbols
            self.level_symbols.append("NODE " + self.fileName + "_node" + pName)
        f.write(
            "MESH * "
            + self.fileName
            + "_actorPtr = &"
            + self.fileName
            + "_mesh"
            + self.cleanName(actorPtr)
            + ";\n"
        )
        # ~ f.write("MESH * " + self.fileName + "_levelPtr = &" + self.fileName + "_mesh" + self.cleanName(levelPtr) + ";\n")
        f.write(
            "MESH * "
            + self.fileName
            + "_levelPtr = &"
            + self.fileName
            + "_mesh"
            + self.cleanName(bpy.data.objects[levelPtr].data.name)
            + ";\n"
        )
        f.write(
            "MESH * "
            + self.fileName
            + "_propPtr  = &"
            + self.fileName
            + "_mesh"
            + propPtr
            + ";\n\n"
        )
        f.write(
            "CAMANGLE * "
            + self.fileName
            + "_camPtr =  &"
            + self.fileName
            + "_camAngle_"
            + self.cleanName(self.defaultCam)
            + ";\n\n"
        )
        f.write(
            "NODE * "
            + self.fileName
            + "_curNode =  &"
            + self.fileName
            + "_node"
            + self.cleanName(nodePtr)
            + ";\n\n"
        )
        # Feed to self.level_symbols
        self.level_symbols.append("MESH * " + self.fileName + "_actorPtr")
        self.level_symbols.append("MESH * " + self.fileName + "_levelPtr")
        self.level_symbols.append("MESH * " + self.fileName + "_propPtr")
        self.level_symbols.append("CAMANGLE * " + self.fileName + "_camPtr")
        self.level_symbols.append("NODE * " + self.fileName + "_curNode")

    def write_level_c_h(self):
        lvlNbr = self.exp_LvlNbr
        self.fileName = "level" + str(lvlNbr)
        # level files go in ./levels/
        if not os.path.exists(self.expFolder + os.sep + "levels"):
            os.mkdir(self.expFolder + os.sep + "levels")

        levels_folder = self.expFolder + os.sep + "levels" + os.sep
        level_h = levels_folder + self.fileName + ".h"
        level_c = levels_folder + self.fileName + ".c"

        self.level_symbols.append("LEVEL " + self.fileName)
        f = open(os.path.normpath(level_c), "w+")
        f.write('#include "' + self.fileName + '.h"\n\n' + "NODE_DECLARATION\n")

        # Horizon and Ambient colors
        self.populate_horizon_color(f=f)
        self.populate_ambient_color(f=f)

        lamp_objects = {}
        cam_path_points = []
        # TODO: check if obj is used as str or as actual object
        for obj in bpy.data.objects:
            if obj.type == "SPEAKER" and obj.data.sound is not None:
                self.parse_speaker(obj)
            if obj.type == "LAMP":
                lamp_objects[obj.data.name] = obj.name
            if obj.type == "MESH":
                self.parse_mesh(obj)
            if obj.type == "CAMERA":
                self.parse_cam(obj, f)
                # Find camera path points and append them to camPathPoints[]
                if obj.name.startswith("camPath") and not obj.data.get("exclude"):
                    cam_path_points.append(obj.name)

        # export anim tracks and strips
        for obj in self.objAnims:
            self.parse_anim(obj, f)

        self.populate_cam_path_points(cam_path_points=cam_path_points, f=f)
        self.populate_lights(lamp_objects=lamp_objects, f=f)

        meshes_no_orphans = [mesh for mesh in bpy.data.meshes if mesh.users > 0]
        mesh_list = [mesh for mesh in meshes_no_orphans if not mesh.get("isPortal")]
        portal_list = [mesh for mesh in meshes_no_orphans if mesh.get("isPortal")]
        portal_name_list = [mesh.name for mesh in portal_list]
        for mesh in mesh_list:
            self.populate_mesh(mesh, f)

        self.populate_portals(portal_list=portal_list, f=f)

        # Define first mesh. Will be used as default if no properties are found in meshes
        first_mesh = self.cleanName(bpy.data.meshes[0].name)
        actorPtr = first_mesh
        levelPtr = first_mesh
        propPtr = first_mesh
        nodePtr = first_mesh
        self.timList = []

        self.populate_cam_angles(
            actorPtr=actorPtr, portal_name_list=portal_name_list, f=f
        )
        self.populate_spatial_partitioning(actorPtr=actorPtr, propPtr=propPtr, f=f)
        ## Sound
        # Use dict generated earlier
        # Default values
        XAFiles = "0"
        VAGBank = "0"
        level_sounds = "0"
        # If sound objects in scene
        if self.soundFiles:
            # Deal with VAGs
            VAGBank = self.writeVAGbank(f, self.soundFiles, self.level_symbols)
            if VAGBank and VAGBank != "0":
                VAGBank = "&" + self.fileName + "_VAGBank"
            # Deal with XA
            XAlist = self.writeXAbank(f, self.writeXAbank, self.level_symbols)
            self.writeXAfiles(f, XAlist, self.fileName)
            if XAlist:
                self.XAmanifest(XAlist)
                self.XAinterleave(XAlist)
                # Update mkpsxiso config file if it exists
                configFile = self.expFolder + os.sep + os.path.relpath(self.exp_isoCfg)
                self.addXAtoISO(XAlist, configFile)
                XAFiles = len(XAlist)
            if XAFiles and XAFiles != "0":
                XAFiles = "&" + self.fileName + "_XAFiles"
            # Write Sound obj
            level_sounds = self.writeSoundObj(f, self.writeXAbank, self.level_symbols)
            if level_sounds and level_sounds != "0":
                level_sounds = "&" + self.fileName + "_sounds"

        # Write LEVEL struct
        f.write(
            "LEVEL "
            + self.fileName
            + " = {\n"
            + "\t&"
            + self.fileName
            + "_BGc,\n"
            + "\t&"
            + self.fileName
            + "_BKc,\n"
            + "\t&"
            + self.fileName
            + "_cmat,\n"
            + "\t&"
            + self.fileName
            + "_lgtmat,\n"
            + "\t(MESH **)&"
            + self.fileName
            + "_meshes,\n"
            + "\t&"
            + self.fileName
            + "_meshes_length,\n"
            + "\t&"
            + self.fileName
            + "_mesh"
            + self.cleanName(actorPtr)
            + ",\n"
            + "\t&"
            + self.fileName
            + "_mesh"
            + self.cleanName(bpy.data.objects[levelPtr].data.name)
            + ",\n"
            + "\t&"
            + self.fileName
            + "_mesh"
            + propPtr
            + ",\n"
            + "\t&"
            + self.fileName
            + "_camAngle_"
            + self.cleanName(self.defaultCam)
            + ",\n"
            + "\t&"
            + self.fileName
            + "_camPath,\n"
            + "\t(CAMANGLE **)&"
            + self.fileName
            + "_camAngles,\n"
            + "\t&"
            + self.fileName
            + "_node"
            + self.cleanName(nodePtr)
            + ",\n"
            + "\t"
            + level_sounds
            + ",\n"
            + "\t"
            + VAGBank
            + ",\n"
            + "\t"
            + XAFiles
            + "\n"
            + "};\n\n"
        )
        # Set default camera back in Blender
        if self.defaultCam != "NULL":
            bpy.context.scene.camera = bpy.data.objects[self.defaultCam]
        f.close()
        # Using a UGLY method here , sorry !
        # We're re-opening the file we just closed to substracts some values that were not available
        # Fill in node in MESH structs
        # Get the file content
        f = open(os.path.normpath(level_c), "r")
        filedata = f.read()
        f.close()
        # Declare LvlPlanes nodes to avoid declaration dependency issues
        # Constuct and store the new string
        Node_declaration = ""
        for k in self.LvlPlanes.keys():
            Node_declaration += (
                "NODE " + self.fileName + "_node" + self.cleanName(k) + ";\n\n"
            )
            self.level_symbols.append(
                "NODE " + self.fileName + "_node" + self.cleanName(k)
            )
        # Do the substitution only once
        newdata = filedata.replace("NODE_DECLARATION\n", Node_declaration, 1)
        newdata = filedata.replace("NODE_DECLARATION\n", "")
        # Now substitute mesh name for corresponding plane's NODE
        for moveable in self.PropPlane:
            newdata = newdata.replace(
                "subs_" + self.cleanName(moveable.name),
                "&" + self.fileName + "_node" + self.PropPlane[moveable],
            )
        # Subsitute mesh name with 0 in the other MESH structs
        newdata = sub("(?m)^\tsubs_.*$", "\t0,", newdata)
        # Open and write file
        f = open(os.path.normpath(level_c), "w")
        f.write(newdata)
        f.close()

        ## Level forward declarations (level.h)
        h = open(os.path.normpath(level_h), "w+")
        h.write(
            "#pragma once\n"
            + '#include "../custom_types.h"\n'
            + '#include "../include/defines.h"\n\n'
        )
        for symbol in self.level_symbols:
            h.write("extern " + symbol + ";\n")
        h.close()
        return {"FINISHED"}

    def write_output_files(self):
        # Stolen from Lameguy64 : https://github.com/Lameguy64/Blender-RSD-Plugin/blob/b3b6fd4475aed4ca38587ca83d34000f60b68a47/io_export_rsd.py#L68
        filepath = self.filepath
        filepath = filepath.replace(self.filename_ext, "")

        # We're writing a few files:
        #  - custom_types.h contains the 'engine' 's specific struct definitions
        #  - level.h        contains the forward declaration of the level's variables
        #  - level.c        contains the initialization and data of those variables
        # 'custom_types.h' goes in export folder

        self.write_custom_types_h()
        self.write_level_c_h()

    def setup_paths(self) -> None:
        """
        Sets up paths for the exporter.
        """
        # Export directory path
        if self.exp_expMode:
            self.filepath = bpy.data.filepath

        self.expFolder = os.path.dirname(
            bpy.path.abspath(self.filepath)
        )  # TODO: do it with pathlib

        # if the file wasn't saved before, expFolder will be empty. Default to current directory in that case
        if self.expFolder == "":
            self.expFolder = os.getcwd()

        # Texture folder path
        self.textureFolder = os.path.join(self.expFolder, self.exp_CustomTexFolder)
        if not os.path.exists(self.textureFolder):
            os.mkdir(self.textureFolder)
        # defaults to TEX folder

        # TIM folder path
        self.timFolder = os.path.join(self.expFolder, "TIM")
        if not os.path.exists(self.timFolder):
            os.mkdir(self.timFolder)

    @staticmethod
    def set_context_area() -> None:
        """Set context area to 3D view"""
        previousAreaType = 0
        if bpy.context.mode != "OBJECT":
            previousAreaType = bpy.context.area.type
            bpy.context.area.type = "VIEW_3D"
            if bpy.context.object is None:
                # select the first object in the scene
                bpy.context.scene.objects.active = bpy.context.scene.objects[0]
            # Leave edit mode if we are in it
            bpy.ops.object.mode_set(mode="OBJECT")
            # Restore previous area type
            bpy.context.area.type = previousAreaType

    @staticmethod
    def set_resolution(self, mode: ResMode = ResMode.NTSC):
        """Set the render resolution of the scene"""
        bpy.context.scene.render.resolution_x = mode[0]
        bpy.context.scene.render.resolution_y = mode[1]

    @staticmethod
    def triangulate_object(obj: bpy.types.Object) -> None:
        """Triangulate an object's mesh
        Source : https://blender.stackexchange.com/questions/45698/triangulate-mesh-in-python/45722#45722
        Args:
            obj (bpy.types.Object): object
        """
        me = obj.data
        # Get a BMesh representation
        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method=0, ngon_method=0)
        # Finish up, write the bmesh back to the mesh
        bm.to_mesh(me)
        bm.free()

    def psxLoc(self, location):
        return round(location * self.exp_Scale)

    def triangulate_all_objects(self):
        for obj in bpy.data.objects:
            if obj.type == "MESH":
                self.triangulate_object(obj)

    def export_backgrounds(self) -> None:
        """Export precalculated backgrounds"""

        # Get background TIM size depending on mode
        timSize = bpy.context.scene.render.resolution_x >> self.TIMshift
        timSizeInCell = ceil(timSize / 64)

        # Set file format
        bpy.context.scene.render.image_settings.file_format = "PNG"
        bpy.context.scene.render.image_settings.color_depth = 8
        bpy.context.scene.render.image_settings.color_mode = "RGB"

        # Get active cam
        scene = bpy.context.scene
        cam = scene.camera

        # FInd default cam and cameras in camPath
        for obj in bpy.data.objects:
            # ignore orphans
            if obj.users == 0:
                continue

            if obj.type == "CAMERA":
                if obj.name == "Default":
                    self.defaultCam = obj.name
                elif obj.name.startswith("camPath"):
                    filepath = self.textureFolder + os.sep
                    filename = "bg_" + self.cleanName(obj.name)
                    fileext = (
                        "."
                        + str(
                            bpy.context.scene.render.image_settings.file_format
                        ).lower()
                    )

                    # Set camera as active
                    bpy.context.scene.camera = obj

                    # Render image
                    bpy.ops.render.render()

                    # Save the image
                    bpy.data.images["Render Result"].save_render(
                        filepath + filename + fileext
                    )

                    # Convert PNG to TIM
                    if not self.VramIsFull(bpy.context.scene.render.resolution_x):
                        self.convertBGtoTIM(
                            filePathWithExt=filepath + filename + fileext,
                            bpp=self.TIMbpp,
                            timX=self.nextTpage,
                            timY=self.tpageY,
                            clutY=self.nextClutSlot,
                            transparency="nonblack",
                        )
                    else:
                        self.tpageY = 256
                        self.nextTpage = 320
                        if not self.VramIsFull(bpy.context.scene.resolution_x):
                            self.convertBGtoTIM(
                                filePathWithExt=filepath + filename + fileext,
                                bpp=self.TIMbpp,
                                timX=self.nextTpage,
                                timY=self.tpageY,
                                clutY=self.nextClutSlot,
                                transparency="nonblack",
                            )
                    # add camera object to camAngles
                    self.camAngles.append(obj)

        self.nextTpage += timSizeInCell * 64
        self.freeTpage -= timSizeInCell
        self.nextClutSlot += 1
        self.freeClutSlot -= 1

    @staticmethod
    def cleanName(strName: str) -> str:
        """Remove special characters, dots and space from string

        Args:
            strName (str): input string name

        Returns:
            str: delaned name
        """
        name = strName.replace(" ", "_")
        name = name.replace(".", "_")
        name = unicodedata.normalize("NFKD", name).encode("ASCII", "ignore").decode()
        return name

    @staticmethod
    def isInFrame(
        scene: bpy.types.Scene, cam: bpy.types.Object, target: mathutils.Vector
    ) -> bool:
        """Checks if an object is in view frame

        Args:
            scene (bpy.types.Scene): scene
            cam (bpy.types.Object): camera
            target (mathutils.Vector): world space location

        Returns:
            bool: is in frame
        """
        # Checks if an object is in view frame
        position = world_to_camera_view(scene, cam, target.location)
        if (
            (position.x < 0 or position.x > 1)
            or (position.y < 0 or position.y > 1)
            or (position.z < 0)
        ):
            return False
        else:
            return True

    @staticmethod
    def isInPlane(plane: dict, obj: dict) -> int:
        """Checks if 'obj' has its coordinates contained between the plane's coordinate.

        Args:
            plane (dict): plane {x1, x2, y1, y2} dict
            obj (dict): obj {x1, x2, y1, y2} dict

        Returns:
            int:
                1 if 'object' is contained
                if partially contained, returns which side
                (S == 2, W == 4, N == 8, E == 6)
                0 if not contained
        """
        if (plane["x1"] <= obj["x1"] and plane["x2"] >= obj["x2"]) and (
            plane["y1"] <= obj["y1"] and plane["y2"] >= obj["y2"]
        ):
            return 1
        # Overlap on the West side of the plane
        if (plane["x1"] >= obj["x1"] and plane["x1"] <= obj["x2"]) and (
            plane["y1"] <= obj["y2"] and plane["y2"] >= obj["y1"]
        ):
            return 4
        # Overlap on the East side of the plane
        if (plane["x2"] <= obj["x2"] and plane["x2"] >= obj["x1"]) and (
            plane["y1"] <= obj["y2"] and plane["y2"] >= obj["y1"]
        ):
            return 6
        # Overlap on the North side of the plane
        if (plane["y2"] <= obj["y2"] and plane["y2"] >= obj["y1"]) and (
            plane["x1"] <= obj["x1"] and plane["x2"] >= obj["x2"]
        ):
            return 8
        # Overlap on the South side of the plane
        if (plane["y1"] >= obj["y1"] and plane["y1"] <= obj["y2"]) and (
            plane["x1"] <= obj["x1"] and plane["x2"] >= obj["x2"]
        ):
            return 2
        else:
            return 0

    def getSepLine(self, plane: str, side: str) -> List[int, int, int]:
        """Construct the line used for BSP generation from 'plane' 's coordinates, on specified side (S, W, N, E)
        Returns a list of three values

        Args:
            plane (str): key for self.LvLPlanes
            side (str): 'N', 'S', 'W', 'E'

        Returns:
            list: [x0, y0, x1, y1]
        """
        # Construct the line used for BSP generation from 'plane' 's coordinates, on specified side (S, W, N, E)
        # Returns an array of 4 values
        if side == "N":
            return [
                self.LvLPlanes[plane]["x1"],
                self.LvlPlanes[plane]["y2"],
                self.LvlPlanes[plane]["x2"],
                self.LvlPlanes[plane]["y2"],
            ]
        if side == "S":
            return [
                self.LvLPlanes[plane]["x1"],
                self.LvLPlanes[plane]["y1"],
                self.LvLPlanes[plane]["x2"],
                self.LvLPlanes[plane]["y1"],
            ]
        if side == "W":
            return [
                self.LvLPlanes[plane]["x1"],
                self.LvLPlanes[plane]["y1"],
                self.LvLPlanes[plane]["x1"],
                self.LvLPlanes[plane]["y2"],
            ]
        if side == "E":
            return [
                self.LvLPlanes[plane]["x2"],
                self.LvLPlanes[plane]["y1"],
                self.LvLPlanes[plane]["x2"],
                self.LvLPlanes[plane]["y2"],
            ]

    @staticmethod
    def checkLine(
        lineX1: int,
        lineY1: int,
        lineX2: int,
        lineY2: int,
        objX1: int,
        objY1: int,
        objX2: int,
        objY2: int,
    ) -> str:
        """Returns wether object spanning from objXY1 to objXY2 is Back, Front, Same or Intersecting the line

        Args:
            lineX1, lineY1, lineX2, lineY2 (int): line coords
            objX1, objY1, objX2, objY2 (int): obj coords

        Returns:
            str: "front", "back", "connected", "intersect"
        """
        # Returns wether object spanning from objXY1 to objXY2 is Back, Front, Same or Intersecting the line
        # defined by points (lineXY1, lineXY2)
        val1 = (objX1 - lineX1) * (lineY2 - lineY1) - (objY1 - lineY1) * (
            lineX2 - lineX1
        )
        # Rounding to avoid false positives
        val1 = round(val1, 4)
        val2 = (objX2 - lineX1) * (lineY2 - lineY1) - (objY2 - lineY1) * (
            lineX2 - lineX1
        )
        val2 = round(val2, 4)
        if (val1 > 0) and (val2 > 0):
            return "front"
        elif (val1 < 0) and (val2 < 0):
            return "back"
        elif (val1 == 0) and (val2 == 0):
            return "connected"
        elif ((val1 > 0) and (val2 == 0)) or ((val1 == 0) and (val2 > 0)):
            return "front"
        elif ((val1 < 0) and (val2 == 0)) or ((val1 == 0) and (val2 < 0)):
            return "back"
        elif ((val1 < 0) and (val2 > 0)) or ((val1 > 0) and (val2 < 0)):
            return "intersect"

    def objVertLtoW(self, target: bpy.types.Object) -> list:
        """Returns global vertex coordiantes from object with local coords

        Args:
            target (bpy.types.Object): input object

        Returns:
            list: list of vertex global coords
        """
        # Converts an object's vertices coordinates from local to global
        worldPos = []
        mw = target.matrix_world
        mesh = bpy.data.meshes[target.name]
        for vertex in mesh.vertices:
            worldPos.append(mw * vertex.co * self.exp_Scale)
        return worldPos

    @staticmethod
    def objVertWtoS(
        scene: bpy.types.Scene,
        cam: bpy.types.Object,
        target: bpy.types.Object,
        toScale: bool = True,
    ) -> list:
        """Convert an object's vertices coords from local to screen coordinates

        Args:
            scene (bpy.types.Scene): current scene
            cam (bpy.types.Object): camera
            target (bpy.types.Object): target obj
            toScale (bool, optional): scale values. Defaults to True.

        Returns:
            list: list of vertex screen coordinates
        """
        # Converts an object's vertices coordinates from local to screen coordinates
        screenPos = []
        # Get objects world matrix
        mw = target.matrix_world
        # Get object's mesh
        mesh = bpy.data.meshes[target.name]
        # For each vertex in mesh, get screen coordinates
        for vertex in mesh.vertices:
            # Get meshes world coordinates
            screenPos.append(world_to_camera_view(scene, cam, (mw * vertex.co)))
        if toScale:
            # Get current scene rsolution
            resX = scene.render.resolution_x
            resY = scene.render.resolution_y
            # Scale values
            for vector in screenPos:
                # ~ vector.x = int( resX * vector.x ) < 0 ? 0 : int( resX * vector.x ) > 320 ? 320 : int( resX * vector.x )
                vector.x = max(0, min(resX, int(resX * vector.x)))
                vector.y = resY - max(0, min(resY, int(resY * vector.y)))
                vector.z = int(vector.z)
        return screenPos

    # TEXTURE UTILS

    def convertBGtoTIM(
        self,
        filePathWithExt: str,
        colors: int = 256,
        bpp: int = 8,
        timX: int = 640,
        timY: int = 0,
        clutX: int = 0,
        clutY: int = 480,
        transparency: str = "alpha",
    ) -> None:
        """Convert background to TIM
        By default, converts a RGB to 8bpp, 256 colors indexed PNG, then to a 8bpp TIM image
        Args:
            filePathWithExt (str): filepath with extension
            colors (int, optional): n of colors. Defaults to 256.
            bpp (int, optional): bits per pixel. Defaults to 8.
            timX (int, optional): tim x. Defaults to 640.
            timY (int, optional): tim y. Defaults to 0.
            clutX (int, optional): clut x. Defaults to 0.
            clutY (int, optional): clut y. Defaults to 480.
            transparency (str, optional): transparency mode in "alpha", "black", "nonblack" Defaults to 'alpha'.
        """
        # TODO: pyhtonize the shit out of this

        # By default, converts a RGB to 8bpp, 256 colors indexed PNG, then to a 8bpp TIM image
        filePathWithoutExt = filePathWithExt[: filePathWithExt.rfind(".")]
        ext = filePathWithExt[filePathWithExt.rfind(".") + 1 :]
        fileBaseName = os.path.basename(filePathWithoutExt)
        # For windows users, add '.exe' to the command
        exe = ""
        if os.name == "nt":
            exe = ".exe"
        # 8bpp TIM needs < 256 colors
        if bpp == 8:
            # Clamp number of colors to 256
            colors = min(255, colors)
        elif bpp == 4:
            # 4bpp TIM needs < 16 colors
            # Clamp number of colors to 16
            colors = min(16, colors)
        if transparency == "alpha":
            transpMethod = "-usealpha"
        elif transparency == "black":
            transpMethod = "-b"
        elif transparency == "nonblack":
            transpMethod = "-t"
        # Image magick's convert can be used alternatively ( https://imagemagick.org/ )
        if self.exp_useIMforTIM:
            # ImageMagick alternative
            subprocess.call(
                [
                    "convert" + exe,
                    filePathWithExt,
                    "-colors",
                    str(colors),
                    filePathWithoutExt + ".png",
                ]
            )
            filePathWithExt = filePathWithoutExt + ".png"
            print("Using IM on " + filePathWithExt)
        else:
            if self.exp_convTexToPNG:
                if ext != "png" or ext != "PNG":
                    # Convert images to PNG
                    subprocess.call(
                        [
                            "convert" + exe,
                            filePathWithExt,
                            "-colors",
                            str(colors),
                            filePathWithoutExt + ".png",
                        ]
                    )
                    filePathWithExt = filePathWithoutExt + ".png"
            # Quantization of colors with pngquant ( https://pngquant.org/ )
            subprocess.run(
                [
                    "pngquant" + exe,
                    "-v",
                    "--force",
                    str(colors),
                    filePathWithExt,
                    "--ext",
                    ".pngq",
                ]
            )
        # Convert to tim with img2tim ( https://github.com/Lameguy64/img2tim )
        subprocess.call(
            [
                "img2tim" + exe,
                transpMethod,
                "-bpp",
                str(bpp),
                "-org",
                str(timX),
                str(timY),
                "-plt",
                str(clutX),
                str(clutY),
                "-o",
                self.timFolder + os.sep + fileBaseName + ".tim",
                filePathWithExt + "q",
            ]
        )

    def VramIsFull(self, size: int) -> bool:
        """Check if VRAM is full for a given size

        Args:
            size (int): size in bytes

        Returns:
            bool: True if there's space in VRAM, False otherwise
        """

        # Returns True if not enough space in Vram for image
        # Transpose bpp to bitshift value
        if self.TIMbpp == 8:
            shift = 1
        elif self.TIMbpp == 4:
            shift = 2
        else:
            shift = 0
        # Get image width in vram
        imageWidth = size >> shift

        # Divide by cell width ( 64 pixels )
        imageWidthInTPage = ceil(imageWidth / 64)
        if (
            self.tpageY == 0
            and self.nextTpage + (imageWidthInTPage * 64) < 1024
            and self.freeTpage - imageWidthInTPage > 0
        ):
            return False
        elif (
            self.tpageY == 256
            and self.nextTpage + (imageWidthInTPage * 64) < 960
            and self.freeTpage - imageWidthInTPage > 1
        ):
            return False
        else:
            return True

    def setNextTimPos(self, imageWidth: int) -> None:
        """set nextTpage, freeTpage, tpageY and nextClutSlot, freeClutSlot to next free space in VRAM

        Args:
            imageWidth (int): image width in pixels
        """
        # Sets nextTpage, freeTpage, tpageY, nextClutSlot, freeClutSlot to next free space in Vram
        # Transpose bpp to bitshift value
        if self.TIMbpp == 8:
            shift = 1
        elif self.TIMbpp == 4:
            shift = 2
        else:
            shift = 0
        # Get image width in vram
        imageWidth = imageWidth >> shift
        # Divide by cell width ( 64 pixels )
        imageWidthInTPage = ceil(imageWidth / 64)
        if (
            tpageY == 0
            and self.nextTpage + (imageWidthInTPage * 64) < 1024
            and self.freeTpage - imageWidthInTPage > 0
        ):
            self.nextTpage += imageWidthInTPage * 64
            self.freeTpage -= imageWidthInTPage
            self.nextClutSlot += 1
            self.freeClutSlot -= 1
        elif (
            tpageY == 256
            and self.nextTpage + (imageWidthInTPage * 64) < 960
            and self.freeTpage - imageWidthInTPage > 1
        ):
            self.nextTpage += imageWidthInTPage * 64
            self.freeTpage -= imageWidthInTPage
            self.nextClutSlot += 1
            self.freeClutSlot -= 1
        else:
            tpageY = 256
            self.nextTpage = 320
            self.nextClutSlot += 1
            self.freeClutSlot -= 1

    @staticmethod
    def linearToRGB(component: float) -> float:
        """Convert a linear component to an RGB component.
        according to specification https://www.color.org/bgsrgb.pdf
        """
        # Convert linear Color in range 0.0-1.0 to range 0-255
        # https://www.color.org/bgsrgb.pdf
        a = 0.055
        if component <= 0.0031308:
            linear = component * 12.92
        else:
            linear = (1 + a) * pow(component, 1 / 2.4) - a
        return linear

    # ANIMATION UTILS
    @staticmethod
    def rmEmptyNLA(obj: bpy.types.Object) -> None:
        """Remove empty NLA tracks from an object"""
        # Remove lna_tracks with no strips
        if obj.animation_data.nla_tracks:
            for track in obj.animation_data.nla_tracks:
                if not track.strips:
                    obj.animation_data.nla_tracks.remove(track)

    @classmethod
    def bakeActionToNLA(cls, obj: bpy.types.Object) -> bool:
        """Bake action to NLA track

        Args:
            obj (bpy.types.Object): object to bake action to

        Returns:
            bool: has animation
        """
        # Bake action to nla_track
        # Converting an action to nla_track makes it timeline independant.
        hasAnim = False
        if obj.animation_data:
            # Get action
            objectAction = obj.animation_data.action
            # If action exists
            if objectAction:
                # Create new nla_track
                nlaTrack = obj.animation_data.nla_tracks.new()
                # Create new strip from action
                nlaTrack.strips.new(
                    objectAction.name, objectAction.frame_range[0], objectAction
                )
                # Remove action
                obj.animation_data.action = None
            hasAnim = True
            cls.rmEmptyNLA(obj)
        return hasAnim

    def getTrackList(self, obj: bpy.types.Object, parent: bpy.types.Object) -> None:
        """Build a dictionary of object's nla tracks and strips

        Dict data structure is like so:
        objDict[ <bpy_struct, Object("Object")> ][ <bpy_struct, NlaTrack("Track")> ][ <bpy_struct, NlaStrip("Action")> ]

        objAnims is a defaultdict(dict)

        Args:
            obj (bpy.types.Object): object to get track dictionary for
            parent (bpy.types.Object): parent of object
        """
        # Build a dictionary of object's nla tracks and strips
        # Dict data structure is like so:
        # objDict[ <bpy_struct, Object("Object")> ][ <bpy_struct, NlaTrack("Track")> ][ <bpy_struct, NlaStrip("Action")> ]
        # objAnims is a defaultdict(dict)
        if obj.animation_data:
            # Get nla tracks
            objTracks = obj.animation_data.nla_tracks
            for track in objTracks:
                for strip in track.strips:
                    # If track struct exists in objAnims[parent], add strip to list
                    if track in self.objAnims[parent]:
                        if strip not in self.objAnims[parent][track]:
                            self.objAnims[parent][track].append(strip)
                    # If it doesn't, create dict item 'track' and initialize it to a list that contains the current strip
                    else:
                        self.objAnims[parent][track] = [strip]

    @staticmethod
    def getStripsTotal(objList: List[bpy.types.Object]) -> int:
        """Get total number of strips in all objects in objList

        Args:
            objList (List[bpy.types.Object]): list of objects to get total number of strips for

        Returns:
            int: total number of strips in all objects in objList
        """
        stripsTotal = []
        for track in objList:
            for strip in objList[track]:
                stripsTotal.append(strip)
        return stripsTotal

    @staticmethod
    def findOverlappingTrack(obj: bpy.types.Object) -> dict:
        """Find overlapping strips through all tracks in an object's nla tracks

        Args:
            obj (bpy.types.Object): object to find overlapping tracks for

        Returns:
            dict: dictionary of overlapping tracks
        """
        # Find overlapping strips through all the tracks
        # Get all strips
        tmpStrips = []
        overlappingStrips = defaultdict(dict)
        for track in obj:
            for strip in obj[track]:
                tmpStrips.append(strip)
        # Check each strip for overlapping
        for tmpStrip in tmpStrips:
            # Find other strips
            otherStrips = [
                otherStrip for otherStrip in tmpStrips if otherStrip is not tmpStrip
            ]
            for otherStrip in otherStrips:
                # If strips are overlapping
                if otherStrip.frame_start < tmpStrip.frame_end:
                    if otherStrip.frame_end > tmpStrip.frame_start:
                        # Add to list, unless already there
                        if otherStrip in overlappingStrips:
                            if tmpStrip not in overlappingStrips:
                                overlappingStrips[otherStrip].append(tmpStrip)
                        else:
                            if tmpStrip not in overlappingStrips:
                                overlappingStrips[otherStrip] = [tmpStrip]
        return overlappingStrips

    @classmethod
    def writeMESH_ANIMS(
        cls, f: TextIO, obj: bpy.types.Object, stripList: list, fileName: str
    ) -> str:
        """Write MESH_ANIMS to file

        Args:
            f (_type_): file to write to
            obj (_type_): object to write to file
            stripList (_type_): list of strips to write to file
            fileName (_type_): name of file to write to

        Returns:
            str : name of file written to
        """
        stripsTotal = len(stripList)
        symbolName = fileName + "_model" + cls.cleanName(obj.data.name) + "_anims"
        f.write(
            "MESH_ANIMS_TRACKS "
            + symbolName
            + " = {\n"
            + "\t"
            + str(stripsTotal)
            + ",\n"
            + "\t{\n"
        )
        i = 0
        for strip in stripList:
            f.write(
                "\t\t&"
                + fileName
                + "_model"
                + cls.CleanName(obj.data.name)
                + "_anim_"
                + cls.CleanName(strip.name)
            )
            if i < stripsTotal - 1:
                f.write(",\n")
            else:
                f.write("\n")
            i += 1
        f.write("\t}\n};\n\n")
        return str("MESH_ANIMS_TRACKS " + symbolName)

    def writeVANIM(
        self,
        f: TextIO,
        obj: bpy.types.Object,
        strip: bpy.types.NlaStrip,
        fileName: str,
        strip_start: int,
        strip_end: int,
        compress=False,
    ) -> str:
        """Write the VANIM part of a MESH_ANIMS struct declaration"""
        # write the VANIM portion of a MESH_ANIMS struct declaration
        # Get strip total length
        # ~ print(strip.name)
        strip_len = strip_end - strip_start
        # Iteration counter
        i = 0
        # Store temporary mesh in list for cleaning later
        tmp_mesh = []
        frameList = []
        for frame in range(int(strip_start), int(strip_end)):
            # Set current frame
            bpy.context.scene.frame_set(frame)
            # Update scene view
            bpy.context.scene.update()
            # Create a copy of the mesh with modifiers applied
            objMod = obj.to_mesh(bpy.context.scene, True, "PREVIEW")
            # Get isLerp flag
            lerp = 0
            if "isLerp" in obj.data:
                lerp = obj.data["isLerp"]
            # Write VANIM struct
            symbolName = (
                fileName
                + "_model"
                + self.CleanName(obj.data.name)
                + "_anim_"
                + self.CleanName(strip.name)
            )
            if frame == strip_start:
                f.write(
                    "VANIM  "
                    + symbolName
                    + " = {\n"
                    + "\t"
                    + str(int(strip_len))
                    + ", // number of frames e.g   20\n"
                    + "\t"
                    + str(len(objMod.vertices))
                    + ", // number of vertices e.g 21\n"
                    + "\t-1, // anim cursor : -1 means not playing back\n"
                    + "\t0,  // lerp cursor\n"
                    + "\t0,  // loop : if -1 , infinite loop, if n > 0, loop n times\n"
                    + "\t1,  // playback direction (1 or -1)\n"
                    + "\t0,  // ping pong animation (A>B>A)\n"
                    + "\t"
                    + str(lerp)
                    + ", // use lerp to interpolate keyframes\n"
                    + "\t{   // vertex pos as BVECTORs e.g 20 * 21 BVECTORS\n"
                )
            # Add an empty list to the frame list
            frameList.append([])
            currentFrameNbr = int(frame - strip_start)
            currentFrameItem = frameList[currentFrameNbr]
            if currentFrameNbr > 0:
                previousFrameItem = frameList[currentFrameNbr - 1]
            else:
                # If first iteration, use currentFrameItem
                previousFrameItem = currentFrameItem
            # Get vertices coordinates as a VECTORs
            for vertIndex in range(len(objMod.vertices)):
                # Store current vertex coords
                currentVertex = Vector(
                    (
                        round(objMod.vertices[vertIndex].co.x * self.exp_Scale),
                        round(-objMod.vertices[vertIndex].co.z * self.exp_Scale),
                        round(objMod.vertices[vertIndex].co.y * self.exp_Scale),
                    )
                )
                # Add current vertex to current frame item
                currentFrameItem.append(currentVertex)
                # If compressing anim
                if self.exp_CompressAnims:
                    # Find delta between current frame and previous frame
                    delta = currentFrameItem[vertIndex] - previousFrameItem[vertIndex]
                    currentVertex = delta
                # Readability : if first vertex of the frame, write frame number as a comment
                if vertIndex == 0:
                    f.write("\t\t//Frame " + str(currentFrameNbr) + "\n")
                # Write vertex coordinates x,z,y
                f.write(
                    "\t\t{ "
                    + str(int(currentVertex.x))
                    + ","
                    + str(int(currentVertex.y))
                    + ","
                    + str(int(currentVertex.z))
                    + " }"
                )
                # If vertex is not the last in the list, write a comma
                if i != (len(objMod.vertices) * (strip_len) * 3) - 3:
                    f.write(",\n")
                # Readability : If vertex is the last in frame, insert a blank line
                if vertIndex == len(objMod.vertices) - 1:
                    f.write("\n")
                # Increment counter
                i += 3
            # Add temporary mesh to the cleaning list
            tmp_mesh.append(objMod)
        # Close anim declaration
        f.write("\t}\n};\n\n")
        # ~ print(frameList)
        # Remove temporary meshes
        for o in tmp_mesh:
            bpy.data.meshes.remove(o)
        return str("VANIM " + symbolName)

    def sound2XA(
        self,
        soundPath: str,
        soundName: str,
        soundFolder: str = "XA",
        bpp: int = 4,
        XAfile: int = 0,
        XAchannel: int = 0,
    ) -> str:
        """Convert a sound to XA format

        Args:
            soundPath (str): sound path
            soundName (str): sound name
            soundFolder (str, optional): sound folder. Defaults to "XA".
            bpp (int, optional): bitdepth. Defaults to 4.
            XAfile (int, optional): XAfile. Defaults to 0.
            XAchannel (int, optional): XAchannel. Defaults to 0.
        Returns:
            str: XA file path
        """
        # Convert sound file to XA
        # exports in ./XA by default
        # ffmpeg -i input.mp3 -acodec pcm_s16le -ac 2 -ar 44100 output.wav
        # psxavenc -f 37800 -t xa -b 4 -c 2 -F 1 -C 0 "../hello_cdda/audio/beach.wav" "xa/beach.xa"
        exe = ""
        if os.name == "nt":
            exe = ".exe"
        # find export folder
        filepath = self.filepath
        # ~ filepath = bpy.data.filepath
        expFolder = (
            os.path.dirname(bpy.path.abspath(filepath)) + os.sep + soundFolder + os.sep
        )
        # create if non-existent
        if not os.path.exists(expFolder):
            os.mkdir(expFolder)
        # find file base name
        basename = soundName.split(".")[0]
        exportPath = expFolder + basename + ".xa"
        # Convert to 16-B WAV
        subprocess.call(
            [
                "ffmpeg" + exe,
                "-i",
                soundPath,
                "-acodec",
                "pcm_s16le",
                "-ac",
                "2",
                "-ar",
                "44100",
                "-y",
                "/tmp/tmp.wav",
            ]
        )
        # Convert WAV to XA
        subprocess.call(
            [
                "psxavenc" + exe,
                "-f",
                "37800",
                "-t",
                "xa",
                "-b",
                str(bpp),
                "-c",
                "2",
                "-F",
                str(XAfile),
                "-C",
                str(XAchannel),
                "/tmp/tmp.wav",
                exportPath,
            ]
        )
        return exportPath

    def XAmanifest(
        self, XAlist: List[Sound], soundFolder: str = "XA", XAchannels: int = 8
    ) -> None:
        """Create XA manifest file

        Args:
            XAlist (list): list of XA files
            soundFolder (str, optional): sound folder. Defaults to "XA".
            XAchannels (int, optional): XA channels. Defaults to 8.
        """
        # generate manifest file
        # find export folder
        filepath = self.filepath
        expFolder = (
            os.path.dirname(bpy.path.abspath(filepath)) + os.sep + soundFolder + os.sep
        )
        XAfiles = []
        for file_index in range(len(XAlist)):
            manifestFile = open(
                os.path.normpath(expFolder + "inter_" + str(file_index) + ".txt"), "w+"
            )
            # ~ print("\nFile_" + str(file_index) + " :")
            lines = XAchannels
            for xa in XAlist[file_index]:
                manifestFile.write(
                    str(self.exp_XAmode)
                    + " xa "
                    + xa.convertedSoundPath
                    + " "
                    + str(xa.XAfile)
                    + " "
                    + str(xa.XAchannel)
                    + "\n"
                )
                lines -= 1
            while lines:
                manifestFile.write(str(self.exp_XAmode) + " null\n")
                lines -= 1
            manifestFile.close()

    @staticmethod
    def writeIsoCfg(configFile: str, insertString: str) -> None:
        """Write insertString to mkpsxiso xml config file

        Args:
            configFile (str): config file path
            insertString (str): insert string
        """
        # Write insertString one line above searchString
        print(configFile)
        print(insertString)
        searchString = "<dummy sectors"
        if os.path.exists(configFile):
            with open(configFile, "r+") as fd:
                content = fd.readlines()
                for index, line in enumerate(content):
                    if insertString in content[index]:
                        break
                    if (
                        searchString in line
                        and insertString not in content[index]
                        and insertString not in content[index - 1]
                    ):
                        content.insert(index, insertString)
                        break
                fd.seek(0)
                fd.writelines(content)
        else:
            print("No mkpsxiso config file were found.")

    def addXAtoISO(
        self, XAinterList: List[str], configFile: str, soundFolder: str = "XA"
    ) -> None:
        """Add XA files to ISO config file

        Args:
            XAinterList (List[str]): list of XA files
            configFile (str): config file path
            soundFolder (str, optional): sound folder. Defaults to "XA".
        """
        # Add XA file to mkpsxiso config file if it existsd
        filepath = self.filepath
        expFolder = (
            os.path.dirname(bpy.path.abspath(filepath)) + os.sep + soundFolder + os.sep
        )
        # for xa in range(len(XAlist)):
        for xa in range(len(XAinterList)):  # TODO: verify if this is correct
            XAfilePath = expFolder + "inter_" + str(xa) + ".xa"
            insertString = (
                '\t\t\t<file name="INTER_'
                + str(xa)
                + '.XA" type="xa" source="'
                + XAfilePath
                + '"/>\n'
            )
            self.writeIsoCfg(configFile, insertString)

    def XAinterleave(self, XAlist: List[str]) -> None:
        # Generate interleaved XA files from existing XA files referenced in soundFiles
        exe = ""
        if os.name == "nt":
            exe = ".exe"
        # find export folder
        filepath = self.filepath
        for xa in range(len(XAlist)):
            manifestFile = self.expFolder + "inter_" + str(xa) + ".txt"
            outputFile = self.expFolder + "inter_" + str(xa) + ".xa"
            subprocess.call(
                ["xainterleave" + exe, str(self.exp_XAmode), manifestFile, outputFile]
            )

    def sound2VAG(
        self, soundPath: str, soundName: str, soundFolder: str = "VAG"
    ) -> str:
        """Convert sound to VAG file
        Args:
            soundPath (str): sound path
            soundName (str): sound name
            soundFolder (str, optional): sound folder. Defaults to "VAG".
        """
        # Convert sound file to VAG
        # exports in ./VAG by default
        # For windows users, add '.exe' to the command
        exe = ""
        if os.name == "nt":
            exe = ".exe"
        # find export folder
        filepath = self.filepath
        # ~ filepath = bpy.data.filepath
        expFolder = (
            os.path.dirname(bpy.path.abspath(filepath)) + os.sep + soundFolder + os.sep
        )
        # create if non-existent
        if not os.path.exists(expFolder):
            os.mkdir(expFolder)
        # find file base name
        basename = soundName.split(".")[0]
        exportPath = expFolder + basename + ".vag"
        # Convert to RAW WAV data
        subprocess.call(
            [
                "ffmpeg" + exe,
                "-i",
                soundPath,
                "-f",
                "s16le",
                "-ac",
                "1",
                "-ar",
                "44100",
                "-y",
                "/tmp/tmp.dat",
            ]
        )
        # Convert WAV to VAG
        subprocess.call(
            ["wav2vag" + exe, "/tmp/tmp.dat", exportPath, "-sraw16", "-freq=44100"]
        )
        return exportPath

    def writeExtList(f: TextIO, soundName: str) -> None:
        """Write sound name to extern file list

        Args:
            f (TextIO): file object
            soundName (str): sound name
        """
        soundName = soundName.split(".")[0]
        f.write("extern u_char _binary_VAG_" + soundName + "_vag_start;\n")

    def writeVAGbank(
        self, f: TextIO, soundList: List[Sound], level_symbols: List[str]
    ) -> int:
        """Write VAG bank to extern file list

        Args:
            f (TextIO): file object
            soundList (List[Sound]): list of sounds
            level_symbols (List[str]): list of level symbols

        Returns:
            int: number of sounds
        """
        index = 0
        SPU = 0
        dups = []
        for file_index in range(len(soundList)):
            if soundList[file_index].XAsize == -1:
                if soundList[file_index] not in dups:
                    self.writeExtList(f, soundList[file_index].soundName, level_symbols)
                    dups.append(soundList[file_index])
                index += 1
        f.write(
            "\nVAGbank "
            + self.fileName
            + "_VAGBank = {\n"
            + "\t"
            + str(index)
            + ",\n"
            + "\t{\n"
        )
        for sound in soundList:
            if sound.XAsize == -1:
                f.write(
                    "\t\t{ &_binary_VAG_"
                    + sound.soundName.split(".")[0]
                    + "_vag_start, SPU_0"
                    + str(SPU)
                    + "CH, 0 }"
                )
                if SPU < index - 1:
                    f.write(",\n")
                sound.index = SPU
                SPU += 1
        f.write("\n\t}\n};\n\n")
        level_symbols.append("VAGbank " + self.fileName + "_VAGBank")
        # If SPU, we're using VAGs
        return SPU

    def writeXAbank(
        self, f: TextIO, XAfiles: List[Sound], level_symbols: List[str]
    ) -> int:
        """Write XA bank to extern file list

        Args:
            f (TextIO): file object
            XAfiles (List[Sound]): list of sounds
            level_symbols (List[str]): list of level symbols

        Returns:
            int: number of sounds
        """
        index = 0
        XAinter = []
        # ~ soundName = objName.split('.')[0]
        for file_index in range(len(XAfiles)):
            if XAfiles[file_index].XAsize != -1:
                index += 1
                if XAfiles[file_index].XAfile not in range(len(XAinter)):
                    XAinter.append(list())
                XAinter[XAfiles[file_index].XAfile].append(XAfiles[file_index])
        for XAlistIndex in range(len(XAinter)):
            f.write(
                "XAbank "
                + self.fileName
                + "_XABank_"
                + str(XAlistIndex)
                + " = {\n"
                + '\t"\\\\INTER_'
                + str(XAlistIndex)
                + '.XA;1",\n'
                + "\t"
                + str(len(XAinter[XAlistIndex]))
                + ",\n"
                + "\t0,\n"
                + "\t{\n"
            )
            index = 0
            for sound in XAinter[XAlistIndex]:
                if sound.XAsize != -1:
                    f.write(
                        "\t\t{ "
                        + str(index)
                        + ", "
                        + str(sound.XAsize)
                        + ", "
                        + str(sound.XAfile)
                        + ", "
                        + str(sound.XAchannel)
                        + ", 0, "
                        + str(sound.XAend)
                        + " * XA_CHANNELS, -1 },\n"
                    )
                    sound.index = index
                    index += 1
            f.write("\t}\n};\n")
            level_symbols.append(
                "XAbank " + self.fileName + "_XABank_" + str(XAlistIndex)
            )
        return XAinter

    def writeXAfiles(self, f: TextIO, XAlist: List[Sound], fileName: str) -> None:
        """Write XA files to extern file list

        Args:
            f (TextIO): file object
            XAlist (List[Sound]): list of sounds
            fileName (str): file name
        """
        # Write XAFiles struct
        f.write(
            "XAfiles "
            + fileName
            + "_XAFiles = {\n"
            + "\t"
            + str(len(XAlist))
            + ",\n"
            + "\t{\n"
        )
        if XAlist:
            for xa in range(len(XAlist)):
                f.write("\t\t&" + fileName + "_XABank_" + str(xa))
                if xa < len(XAlist) - 1:
                    f.write(",")
        else:
            f.write("\t\t0")
        f.write("\n\t}\n};\n")
        self.level_symbols.append("XAfiles " + fileName + "_XAFiles")

    def writeSoundObj(
        self, f: TextIO, soundFiles: List[bpy.types.Object], level_symbols: List[str]
    ) -> None:
        index = 0
        # Write SOUND_OBJECT structures
        for obj in soundFiles:
            f.write(
                "SOUND_OBJECT "
                + self.fileName
                + "_"
                + obj.objName.replace(".", "_")
                + " = {\n"
                + "\t{"
                + str(self.psxLoc(obj.location.x))
                + ","
                + str(self.psxLoc(obj.location.y))
                + ","
                + str(self.psxLoc(obj.location.z))
                + "},\n"
                + "\t"
                + str(obj.volume * 0x3FFF)
                + ", "
                + str(obj.volume * 0x3FFF)
                + ", "
                + str(obj.volume_min * 0x3FFF)
                + ", "
                + str(obj.volume_max * 0x3FFF)
                + ",\n"
            )
            if obj.XAsize == -1:
                f.write(
                    "\t&"
                    + self.fileName
                    + "_VAGBank.samples["
                    + str(obj.index)
                    + "],\n"
                    + "\t0,\n"
                )
            else:
                f.write(
                    "\t0,\n"
                    + "\t&"
                    + self.fileName
                    + "_XABank_"
                    + str(obj.XAfile)
                    + ".samples["
                    + str(obj.index)
                    + "],\n"
                )
            if obj.parent:
                f.write(
                    "\t&"
                    + self.fileName
                    + "_mesh"
                    + self.cleanName(obj.parent.name)
                    + "\n"
                )
            else:
                f.write("\t0\n")
            f.write("};\n\n")
            index += 1
            level_symbols.append(
                "SOUND_OBJECT " + self.fileName + "_" + obj.objName.replace(".", "_")
            )
        f.write(
            "LEVEL_SOUNDS "
            + self.fileName
            + "_sounds = {\n"
            + "\t"
            + str(index)
            + ",\n"
            + "\t{\n"
        )
        for obj in range(len(soundFiles)):
            f.write(
                "\t\t&"
                + self.fileName
                + "_"
                + soundFiles[obj].objName.replace(".", "_")
            )
            if obj < len(soundFiles) - 1:
                f.write(",\n")
        f.write("\n\t}\n};\n\n")
        level_symbols.append("LEVEL_SOUNDS " + self.fileName + "_sounds")
        return index
