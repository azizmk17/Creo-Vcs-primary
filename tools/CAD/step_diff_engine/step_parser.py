from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class FaceGeometry:
    """Extracted geometric information for one face."""

    index: int
    surface_type: str
    area: float
    center: tuple[float, float, float]
    normal: tuple[float, float, float] | None = None
    radius: float | None = None
    axis_direction: tuple[float, float, float] | None = None
    axis_location: tuple[float, float, float] | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelGeometry:
    """Model-level geometry snapshot extracted from STEP."""

    commit_id: str
    step_path: str
    volume: float
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    faces: list[FaceGeometry]


class StepParseError(RuntimeError):
    """Raised when STEP geometry cannot be parsed."""


def _surface_type_name(type_code: int) -> str:
    """Map OCC GeomAbs surface type to string."""
    from OCC.Core.GeomAbs import (
        GeomAbs_BSplineSurface,
        GeomAbs_BezierSurface,
        GeomAbs_Cone,
        GeomAbs_Cylinder,
        GeomAbs_OffsetSurface,
        GeomAbs_OtherSurface,
        GeomAbs_Plane,
        GeomAbs_Sphere,
        GeomAbs_SurfaceOfExtrusion,
        GeomAbs_SurfaceOfRevolution,
        GeomAbs_Torus,
    )

    mapping = {
        GeomAbs_Plane: "plane",
        GeomAbs_Cylinder: "cylinder",
        GeomAbs_Cone: "cone",
        GeomAbs_Sphere: "sphere",
        GeomAbs_Torus: "torus",
        GeomAbs_BezierSurface: "bezier_surface",
        GeomAbs_BSplineSurface: "bspline_surface",
        GeomAbs_SurfaceOfRevolution: "surface_of_revolution",
        GeomAbs_SurfaceOfExtrusion: "surface_of_extrusion",
        GeomAbs_OffsetSurface: "offset_surface",
        GeomAbs_OtherSurface: "other_surface",
    }
    return mapping.get(type_code, f"unknown_{type_code}")


def _vector3(gp_pnt_or_dir: Any) -> tuple[float, float, float]:
    return (float(gp_pnt_or_dir.X()), float(gp_pnt_or_dir.Y()), float(gp_pnt_or_dir.Z()))


def _extract_face_normal(face: Any) -> tuple[float, float, float] | None:
    """Estimate a representative face normal at UV midpoint."""
    try:
        from OCC.Core.BRep import BRep_Tool
        from OCC.Core.BRepTools import breptools
        from OCC.Core.GeomLProp import GeomLProp_SLProps

        surf = BRep_Tool.Surface(face)
        try:
            u_min, u_max, v_min, v_max = breptools.UVBounds(face)
        except Exception:
            from OCC.Core.BRepTools import breptools_UVBounds

            u_min, u_max, v_min, v_max = breptools_UVBounds(face)
        u_mid = 0.5 * (float(u_min) + float(u_max))
        v_mid = 0.5 * (float(v_min) + float(v_max))

        props = GeomLProp_SLProps(surf, u_mid, v_mid, 1, 1e-7)
        if not props.IsNormalDefined():
            return None
        return _vector3(props.Normal())
    except Exception:
        return None


def _extract_surface_specific_parameters(adaptor: Any) -> tuple[float | None, tuple[float, float, float] | None, tuple[float, float, float] | None, dict[str, Any]]:
    """Extract radius/axis and shape-specific parameters for fingerprinting and reporting."""
    params: dict[str, Any] = {}
    radius: float | None = None
    axis_dir: tuple[float, float, float] | None = None
    axis_loc: tuple[float, float, float] | None = None

    try:
        stype = adaptor.GetType()
        stype_name = _surface_type_name(stype)

        if stype_name == "cylinder":
            cyl = adaptor.Cylinder()
            radius = float(cyl.Radius())
            axis = cyl.Axis()
            axis_dir = _vector3(axis.Direction())
            axis_loc = _vector3(axis.Location())

        elif stype_name == "cone":
            cone = adaptor.Cone()
            radius = float(cone.RefRadius())
            axis = cone.Axis()
            axis_dir = _vector3(axis.Direction())
            axis_loc = _vector3(axis.Location())
            params["semi_angle"] = float(cone.SemiAngle())

        elif stype_name == "sphere":
            sph = adaptor.Sphere()
            radius = float(sph.Radius())
            params["center"] = _vector3(sph.Location())

        elif stype_name == "torus":
            tor = adaptor.Torus()
            radius = float(tor.MinorRadius())
            params["major_radius"] = float(tor.MajorRadius())
            axis = tor.Axis()
            axis_dir = _vector3(axis.Direction())
            axis_loc = _vector3(axis.Location())
    except Exception:
        pass

    return radius, axis_dir, axis_loc, params


def parse_step_file(step_path: str | Path, commit_id: str) -> ModelGeometry:
    """Parse STEP file and return model + face geometry records.

    Args:
        step_path: Path to `.stp` / `.step` file.
        commit_id: Commit identifier associated with this snapshot.

    Raises:
        StepParseError: If file is invalid or OCC parsing fails.
    """
    path = Path(step_path)
    if not path.exists() or not path.is_file():
        raise StepParseError(f"STEP file not found: {path}")

    try:
        from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
        from OCC.Core.BRepGProp import brepgprop
        from OCC.Core.BRepTools import breptools
        from OCC.Core.Bnd import Bnd_Box
        from OCC.Core.BRepBndLib import brepbndlib
        from OCC.Core.GProp import GProp_GProps
        from OCC.Core.STEPControl import STEPControl_Reader
        from OCC.Core.IFSelect import IFSelect_RetDone
        from OCC.Core.TopAbs import TopAbs_FACE
        from OCC.Core.TopExp import TopExp_Explorer
        from OCC.Core.TopoDS import Face
    except Exception as exc:
        raise StepParseError(
            "pythonOCC is required for STEP parsing. Install `pythonocc-core`."
        ) from exc

    reader = STEPControl_Reader()
    status = reader.ReadFile(str(path))
    if status != IFSelect_RetDone:
        raise StepParseError(f"Failed to read STEP file: {path}")

    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        raise StepParseError(f"No geometry loaded from STEP file: {path}")

    volume_props = GProp_GProps()
    try:
        brepgprop.VolumeProperties(shape, volume_props)
    except Exception:
        from OCC.Core.BRepGProp import brepgprop_VolumeProperties

        brepgprop_VolumeProperties(shape, volume_props)
    volume = float(volume_props.Mass())

    box = Bnd_Box()
    try:
        brepbndlib.Add(shape, box)
    except Exception:
        from OCC.Core.BRepBndLib import brepbndlib_Add

        brepbndlib_Add(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()

    faces: list[FaceGeometry] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    idx = 0
    while explorer.More():
        face = Face(explorer.Current())
        adaptor = BRepAdaptor_Surface(face, True)

        surface_type = _surface_type_name(adaptor.GetType())

        props = GProp_GProps()
        try:
            brepgprop.SurfaceProperties(face, props)
        except Exception:
            from OCC.Core.BRepGProp import brepgprop_SurfaceProperties

            brepgprop_SurfaceProperties(face, props)
        area = float(props.Mass())
        center = _vector3(props.CentreOfMass())
        normal = _extract_face_normal(face)

        radius, axis_dir, axis_loc, params = _extract_surface_specific_parameters(adaptor)

        # Include UV extents for extra stability/debug in advanced workflows.
        try:
            try:
                u_min, u_max, v_min, v_max = breptools.UVBounds(face)
            except Exception:
                from OCC.Core.BRepTools import breptools_UVBounds

                u_min, u_max, v_min, v_max = breptools_UVBounds(face)
            params.update(
                {
                    "u_min": float(u_min),
                    "u_max": float(u_max),
                    "v_min": float(v_min),
                    "v_max": float(v_max),
                }
            )
        except Exception:
            pass

        faces.append(
            FaceGeometry(
                index=idx,
                surface_type=surface_type,
                area=area,
                center=center,
                normal=normal,
                radius=radius,
                axis_direction=axis_dir,
                axis_location=axis_loc,
                parameters=params,
            )
        )

        idx += 1
        explorer.Next()

    return ModelGeometry(
        commit_id=commit_id,
        step_path=str(path),
        volume=volume,
        bbox_min=(float(xmin), float(ymin), float(zmin)),
        bbox_max=(float(xmax), float(ymax), float(zmax)),
        faces=faces,
    )
