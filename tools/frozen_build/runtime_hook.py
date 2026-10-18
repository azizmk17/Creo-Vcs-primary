"""Resolve native CAD resources only from the installed portable bundle.

PyInstaller executes this before importing OCC or application modules. DLL
directory handles must remain alive; otherwise Python removes those directories
from the Windows loader search path when the handles are collected.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def configure_frozen_runtime() -> None:
    bundle_path = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or not bundle_path:
        return
    bundle = Path(bundle_path).resolve()
    resources = bundle / "occ-resources"
    os.environ["OCCT_ESSENTIALS_ROOT"] = str(bundle)
    os.environ["CASROOT"] = str(resources)
    directories = {
        "CSF_ShadersDirectory": "Shaders",
        "CSF_MDTVTexturesDirectory": "Textures",
        "CSF_XSMessage": "XSMessage",
        "CSF_SHMessage": "SHMessage",
        "CSF_StandardDefaults": "StdResource",
        "CSF_StandardLiteDefaults": "StdResource",
        "CSF_PluginDefaults": "StdResource",
        "CSF_XCAFDefaults": "StdResource",
        "CSF_TObjDefaults": "StdResource",
        "CSF_IGESDefaults": "XSTEPResource",
        "CSF_STEPDefaults": "XSTEPResource",
        "CSF_XSDefaults": "XSTEPResource",
        "CSF_XmlOcafResource": "XmlOcafResource",
        "CSF_UnitsDefinition": "UnitsAPI/Units.dat",
        "CSF_UnitsLexicon": "UnitsAPI/Lexi_Expr.dat",
    }
    for variable, relative in directories.items():
        target = resources / relative
        # Old OCCT releases use Lexi_Expr.dat; newer ones no longer ship it.
        # Clear host overrides when this bundle does not contain the resource.
        os.environ.pop(variable, None)
        if target.exists():
            os.environ[variable] = str(target)
    handles = getattr(sys, "_nexus_dll_directory_handles", [])
    if hasattr(os, "add_dll_directory"):
        for relative in (".", "Library/bin", "OCC", "OCC/Core", "PyQt5/Qt5/bin"):
            directory = bundle / relative
            if directory.is_dir():
                handles.append(os.add_dll_directory(str(directory)))
    sys._nexus_dll_directory_handles = handles


configure_frozen_runtime()
