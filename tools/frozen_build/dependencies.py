"""Dependency inputs for the shared Windows folder distribution.

The build runs in the environment that contains pythonocc-core.  PyInstaller's
PE analysis follows the imported OCC extensions into that environment's native
DLL directories; copying every DLL from the Conda environment would also ship
unrelated development libraries.
"""

from __future__ import annotations

from importlib.util import find_spec
import os
from pathlib import Path
import sys


# The viewer imports these wrappers directly or through OCC.Display.OCCViewer.
# Their regular Python imports bring in the corresponding native extensions.
# Avoid collecting OCC's examples, tests, wx/Tk viewers and unrelated toolkits.
OCC_CORE_MODULES = (
    "AIS", "Aspect", "BRep", "BRepAdaptor", "BRepAlgoAPI", "BRepBndLib",
    "BRepBuilderAPI", "BRepGProp", "BRepMesh", "BRepTools", "Bnd",
    "GCPnts", "GProp", "Geom", "Geom2d", "GeomAbs", "GeomLProp",
    "Graphic3d", "IFSelect", "IGESControl", "Prs3d", "Quantity",
    "STEPControl", "StlAPI", "TopAbs", "TopExp", "TopoDS", "V3d",
    "Visualization", "gp",
)
OCC_DISPLAY_MODULES = (
    "OCC.Display.backend", "OCC.Display.qtDisplay", "OCC.Display.OCCViewer",
)


def _available(module: str) -> bool:
    try:
        return find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def native_library_search_paths(prefix: str | Path | None = None) -> list[str]:
    """Directories Analysis must search for OCCT's transitive native imports."""
    environment = Path(prefix) if prefix is not None else Path(sys.prefix)
    candidates = (environment / "Library" / "bin", environment / "DLLs", environment)
    return [str(path.resolve()) for path in candidates if path.is_dir()]


def _occ_resources(environment: Path, package_path: Path) -> Path:
    candidates = (
        environment / "Library" / "share" / "opencascade" / "resources",
        environment / "share" / "opencascade" / "resources",
        package_path / "resources",
    )
    for candidate in candidates:
        if ((candidate / "Shaders").is_dir()
                and (candidate / "UnitsAPI" / "Units.dat").is_file()):
            return candidate.resolve()
    searched = ", ".join(str(path) for path in candidates)
    raise RuntimeError(
        "OpenCascade resources (Shaders and UnitsAPI/Units.dat) were not found. "
        "Build with the complete pythonocc-core/OCCT environment. Searched: " + searched
    )


def _unique_pairs(entries):
    result = []
    seen = set()
    for source, destination in entries:
        key = (os.path.normcase(os.path.abspath(source)), os.path.normcase(destination))
        if key not in seen:
            seen.add(key)
            result.append((source, destination))
    return result


def _is_native_library(path: str) -> bool:
    name = Path(path).name.casefold()
    return name.endswith((".dll", ".pyd", ".dylib", ".so")) or ".so." in name


def collect_runtime_dependencies(
    *, prefix: str | Path | None = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    """Return ``(datas, binaries, hiddenimports)`` for a PyInstaller Analysis.

    No dependencies are installed and no directory is modified. ``prefix`` is
    normally omitted; the build interpreter supplies its own environment.
    Add :func:`native_library_search_paths` to the build process's PATH before
    Analysis so imported extensions can resolve their OCCT DLL dependencies.
    """
    from PyInstaller.utils.hooks import (
        collect_data_files, collect_dynamic_libs, collect_submodules, get_package_paths,
    )

    if not _available("OCC"):
        raise RuntimeError("OCC is missing. Run the build with a pythonocc-core environment.")

    pdf_packages = [name for name in ("fitz", "pymupdf") if _available(name)]
    if not pdf_packages:
        raise RuntimeError("PyMuPDF is missing (neither fitz nor pymupdf is importable).")

    environment = Path(prefix) if prefix is not None else Path(sys.prefix)
    _, occ_package = get_package_paths("OCC")
    occ_package = Path(occ_package)
    datas = []
    binaries = []
    hidden = ["OCC", "OCC.Core"]
    hidden.extend("OCC.Core." + module for module in OCC_CORE_MODULES)
    hidden.extend(OCC_DISPLAY_MODULES)
    hidden.extend(("PyQt5.sip", "PyQt5.QtOpenGL", "PyQt5.QtPrintSupport"))

    for package in pdf_packages:
        package_data = collect_data_files(
            package, excludes=["**/tests/**", "**/test/**", "**/examples/**"],
        )
        # Some older fitz packages report mupdfcpp64.dll as ordinary data as
        # well. Collect it only as a binary so its own DLL imports are scanned.
        datas.extend(entry for entry in package_data if not _is_native_library(entry[0]))
        binaries.extend(collect_dynamic_libs(package))
        hidden.extend(collect_submodules(
            package,
            filter=lambda name: not any(
                component in {"tests", "test", "examples"} for component in name.split(".")
            ),
        ))
        hidden.append(package)

    # Wheels may carry DLLs inside OCC itself. Conda puts them in Library/bin;
    # Analysis resolves those from imported extensions instead of copying the
    # whole environment (which can contain hundreds of unrelated DLLs).
    binaries.extend(collect_dynamic_libs("OCC"))
    resources = _occ_resources(environment, occ_package)
    datas.append((str(resources), "occ-resources"))

    # OCCT selects the OpenGL driver by name at runtime. It need not appear in
    # a PE import table, so explicitly seed it; Analysis follows its imports.
    dll_directories = [Path(path) for path in native_library_search_paths(environment)]
    dll_directories.extend((occ_package, occ_package / "Core"))
    opengl_driver = next(
        (directory / "TKOpenGl.dll" for directory in dll_directories
         if (directory / "TKOpenGl.dll").is_file()),
        None,
    )
    if opengl_driver is None:
        opengl_driver = next(
            (Path(source) for source, _ in binaries
             if Path(source).name.casefold() == "tkopengl.dll"),
            None,
        )
    if opengl_driver is None:
        raise RuntimeError(
            "TKOpenGl.dll is missing from the build environment. "
            "A complete OpenCascade installation is required for the CAD viewer."
        )
    binaries.append((str(opengl_driver.resolve()), "."))

    return _unique_pairs(datas), _unique_pairs(binaries), list(dict.fromkeys(hidden))
