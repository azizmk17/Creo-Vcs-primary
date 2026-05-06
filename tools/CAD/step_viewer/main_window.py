"""
Advanced STEP / CAD Viewer — main_window.py
============================================
A professional‑grade 3‑D CAD viewer built on **PyQt5 + pythonOCC**.

Features
--------
* Open / drag‑drop STEP / IGES / BREP files
* Interactive 3‑D viewport (rotate / pan / zoom)
* Model (shape) tree  — toggle visibility per solid
* Properties panel     — face count, volume, bbox, surface area
* Display modes        — Shaded / Wireframe / Shaded+Edges / Transparent / Hidden‑Line
* Standard views       — Front / Back / Top / Bottom / Left / Right / Isometric
* Cross‑section        — interactive clipping plane along X / Y / Z with slider
* Measurement tools    — point‑to‑point distance, face area, edge length
* Annotations          — add text labels anchored to the model
* Screenshot / Export  — save viewport as PNG, export to STL / IGES
* Diff overlay mode    — boolean‑volume comparison (common=gray, removed=red, added=green)
* Dark / Light theme toggle
* Status bar with live cursor coordinates
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Optional

# ── PyQt5 ────────────────────────────────────────────────────────────────
from PyQt5.QtWidgets import (
    QAction, QActionGroup, QApplication, QCheckBox, QColorDialog, QComboBox,
    QDialog, QDialogButtonBox, QDockWidget, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMenuBar,
    QMessageBox, QPushButton, QScrollArea, QSlider, QSplitter, QStatusBar,
    QStyle, QTabWidget, QTextEdit, QToolBar, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget, QSpinBox,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QSize, QPoint
from PyQt5.QtGui import (
    QColor, QCursor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap,
)

# ── pythonOCC ────────────────────────────────────────────────────────────
from OCC.Display.backend import load_backend

load_backend("pyqt5")

from OCC.Display.qtDisplay import qtViewer3d

from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.BRepTools import breptools
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.GCPnts import GCPnts_AbscissaPoint
from OCC.Core.GProp import GProp_GProps
from OCC.Core.Graphic3d import Graphic3d_ClipPlane
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Quantity import (
    Quantity_Color, Quantity_TOC_RGB,
)
from OCC.Core.STEPControl import STEPControl_Reader, STEPControl_Writer, STEPControl_AsIs
from OCC.Core.StlAPI import StlAPI_Writer
from OCC.Core.IGESControl import IGESControl_Reader, IGESControl_Writer
from OCC.Core.TopAbs import (
    TopAbs_COMPOUND, TopAbs_COMPSOLID, TopAbs_EDGE, TopAbs_FACE,
    TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX, TopAbs_WIRE,
)
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import topods, TopoDS_Shape
from OCC.Core.V3d import (
    V3d_TypeOfOrientation_Zup_Front,
    V3d_TypeOfOrientation_Zup_Back,
    V3d_TypeOfOrientation_Zup_Top,
    V3d_TypeOfOrientation_Zup_Bottom,
    V3d_TypeOfOrientation_Zup_Left,
    V3d_TypeOfOrientation_Zup_Right,
    V3d_XposYposZpos,
)
from OCC.Core.gp import gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec, gp_XYZ

# ── Face lifecycle ───────────────────────────────────────────────────────
from .face_lifecycle import get_face_lifecycle

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_SHAPE_TYPE_NAMES = {
    TopAbs_COMPOUND: "Compound",
    TopAbs_COMPSOLID: "CompSolid",
    TopAbs_SOLID: "Solid",
    TopAbs_SHELL: "Shell",
    TopAbs_FACE: "Face",
    TopAbs_WIRE: "Wire",
    TopAbs_EDGE: "Edge",
    TopAbs_VERTEX: "Vertex",
}


def _shape_type_name(shape: TopoDS_Shape) -> str:
    return _SHAPE_TYPE_NAMES.get(shape.ShapeType(), "Unknown")


def _count_sub(shape: TopoDS_Shape, sub_type) -> int:
    n = 0
    exp = TopExp_Explorer(shape, sub_type)
    while exp.More():
        n += 1
        exp.Next()
    return n


def _shape_volume(shape: TopoDS_Shape) -> float:
    props = GProp_GProps()
    try:
        brepgprop.VolumeProperties(shape, props)
    except Exception:
        from OCC.Core.BRepGProp import brepgprop_VolumeProperties
        brepgprop_VolumeProperties(shape, props)
    return float(props.Mass())


def _shape_surface_area(shape: TopoDS_Shape) -> float:
    props = GProp_GProps()
    try:
        brepgprop.SurfaceProperties(shape, props)
    except Exception:
        from OCC.Core.BRepGProp import brepgprop_SurfaceProperties
        brepgprop_SurfaceProperties(shape, props)
    return float(props.Mass())


def _shape_center_of_mass(shape: TopoDS_Shape) -> tuple[float, float, float]:
    props = GProp_GProps()
    try:
        brepgprop.VolumeProperties(shape, props)
    except Exception:
        from OCC.Core.BRepGProp import brepgprop_VolumeProperties
        brepgprop_VolumeProperties(shape, props)
    c = props.CentreOfMass()
    return (float(c.X()), float(c.Y()), float(c.Z()))


def _shape_bbox(shape: TopoDS_Shape) -> tuple[tuple, tuple]:
    box = Bnd_Box()
    try:
        brepbndlib.Add(shape, box)
    except Exception:
        from OCC.Core.BRepBndLib import brepbndlib_Add
        brepbndlib_Add(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return (
        (round(xmin, 4), round(ymin, 4), round(zmin, 4)),
        (round(xmax, 4), round(ymax, 4), round(zmax, 4)),
    )


def _face_area(face) -> float:
    props = GProp_GProps()
    try:
        brepgprop.SurfaceProperties(face, props)
    except Exception:
        from OCC.Core.BRepGProp import brepgprop_SurfaceProperties
        brepgprop_SurfaceProperties(face, props)
    return float(props.Mass())


def _edge_length(edge) -> float:
    adaptor = BRepAdaptor_Curve(edge)
    try:
        length = GCPnts_AbscissaPoint.Length(adaptor)
    except Exception:
        length = 0.0
    return float(length)


def _surface_type_name(face) -> str:
    from OCC.Core.GeomAbs import (
        GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere,
        GeomAbs_Torus, GeomAbs_BezierSurface, GeomAbs_BSplineSurface,
        GeomAbs_SurfaceOfRevolution, GeomAbs_SurfaceOfExtrusion,
        GeomAbs_OffsetSurface, GeomAbs_OtherSurface,
    )
    adaptor = BRepAdaptor_Surface(face, True)
    mapping = {
        GeomAbs_Plane: "Plane", GeomAbs_Cylinder: "Cylinder",
        GeomAbs_Cone: "Cone", GeomAbs_Sphere: "Sphere",
        GeomAbs_Torus: "Torus", GeomAbs_BezierSurface: "Bézier",
        GeomAbs_BSplineSurface: "B‑Spline",
        GeomAbs_SurfaceOfRevolution: "Revolution",
        GeomAbs_SurfaceOfExtrusion: "Extrusion",
        GeomAbs_OffsetSurface: "Offset", GeomAbs_OtherSurface: "Other",
    }
    return mapping.get(adaptor.GetType(), "Unknown")


def _point_distance(p1: gp_Pnt, p2: gp_Pnt) -> float:
    return p1.Distance(p2)


# ---------------------------------------------------------------------------
#  File I/O
# ---------------------------------------------------------------------------

_FILE_FILTER = (
    "All CAD (*.step *.stp *.iges *.igs *.brep *.brp);;"
    "STEP (*.step *.stp);;"
    "IGES (*.iges *.igs);;"
    "BREP (*.brep *.brp);;"
    "All Files (*.*)"
)


def load_cad_file(filepath: str) -> TopoDS_Shape:
    """Load a CAD file and return the top‑level TopoDS_Shape."""
    ext = Path(filepath).suffix.lower()
    if ext in (".step", ".stp"):
        reader = STEPControl_Reader()
        status = reader.ReadFile(filepath)
        if status != IFSelect_RetDone:
            raise RuntimeError(f"Cannot read STEP file: {filepath}")
        reader.TransferRoots()
        return reader.OneShape()
    elif ext in (".iges", ".igs"):
        reader = IGESControl_Reader()
        status = reader.ReadFile(filepath)
        if status != IFSelect_RetDone:
            raise RuntimeError(f"Cannot read IGES file: {filepath}")
        reader.TransferRoots()
        return reader.OneShape()
    elif ext in (".brep", ".brp"):
        from OCC.Core.BRep import BRep_Builder
        shape = TopoDS_Shape()
        builder = BRep_Builder()
        ok = breptools.Read(shape, filepath, builder)
        if not ok:
            raise RuntimeError(f"Cannot read BREP file: {filepath}")
        return shape
    else:
        raise RuntimeError(f"Unsupported file format: {ext}")


# ---------------------------------------------------------------------------
#  Model Tree Widget
# ---------------------------------------------------------------------------

class ModelTreeWidget(QDockWidget):
    """Hierarchical shape explorer — toggle visibility, select sub‑shapes."""

    shape_selected = pyqtSignal(object)          # emits TopoDS_Shape
    visibility_changed = pyqtSignal(object, bool) # shape, visible

    def __init__(self, parent=None):
        super().__init__("Model Tree", parent)
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.setMinimumWidth(220)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        # search
        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍  Filter shapes…")
        self._search.textChanged.connect(self._filter)
        layout.addWidget(self._search)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Shape", "Type", "Faces"])
        self._tree.header().setStretchLastSection(False)
        self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tree.setColumnWidth(1, 80)
        self._tree.setColumnWidth(2, 50)
        self._tree.itemClicked.connect(self._on_click)
        self._tree.itemChanged.connect(self._on_check)
        layout.addWidget(self._tree)

        self.setWidget(container)
        self._items: list[tuple[QTreeWidgetItem, TopoDS_Shape]] = []

    # ── public ───────────────────────────────────────────────────────────
    def populate(self, shape: TopoDS_Shape, name: str = "Model"):
        self._tree.clear()
        self._items.clear()
        root = self._add_node(None, shape, name)
        self._tree.expandAll()

    def clear(self):
        self._tree.clear()
        self._items.clear()

    # ── private ──────────────────────────────────────────────────────────
    def _add_node(self, parent, shape: TopoDS_Shape, label: str) -> QTreeWidgetItem:
        stype = _shape_type_name(shape)
        nfaces = _count_sub(shape, TopAbs_FACE)
        item = QTreeWidgetItem()
        item.setText(0, label)
        item.setText(1, stype)
        item.setText(2, str(nfaces))
        item.setCheckState(0, Qt.Checked)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)

        if parent is None:
            self._tree.addTopLevelItem(item)
        else:
            parent.addChild(item)

        self._items.append((item, shape))

        # expand children for compounds / solids
        if shape.ShapeType() in (TopAbs_COMPOUND, TopAbs_COMPSOLID):
            exp = TopExp_Explorer(shape, TopAbs_SOLID)
            idx = 0
            while exp.More():
                child = exp.Current()
                self._add_node(item, child, f"Solid_{idx}")
                idx += 1
                exp.Next()
        elif shape.ShapeType() == TopAbs_SOLID:
            # faces
            exp = TopExp_Explorer(shape, TopAbs_FACE)
            idx = 0
            while exp.More():
                child = exp.Current()
                self._add_node(item, child, f"Face_{idx}")
                idx += 1
                exp.Next()

        return item

    def _on_click(self, item: QTreeWidgetItem, col: int):
        for tw_item, shape in self._items:
            if tw_item is item:
                self.shape_selected.emit(shape)
                return

    def _on_check(self, item: QTreeWidgetItem, col: int):
        if col != 0:
            return
        visible = item.checkState(0) == Qt.Checked
        for tw_item, shape in self._items:
            if tw_item is item:
                self.visibility_changed.emit(shape, visible)
                return

    def _filter(self, text: str):
        text = text.lower()
        for tw_item, _ in self._items:
            match = text in tw_item.text(0).lower() or text in tw_item.text(1).lower()
            tw_item.setHidden(not match and bool(text))


# ---------------------------------------------------------------------------
#  Properties Panel
# ---------------------------------------------------------------------------

class PropertiesPanel(QDockWidget):
    """Shows geometric properties of the loaded / selected shape."""

    def __init__(self, parent=None):
        super().__init__("Properties", parent)
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.setMinimumWidth(260)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(4)
        scroll.setWidget(container)
        self.setWidget(scroll)

        self._info_label = QLabel("No model loaded")
        self._info_label.setWordWrap(True)
        self._info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._layout.addWidget(self._info_label)
        self._layout.addStretch()

    def show_shape_info(self, shape: TopoDS_Shape, label: str = "Model"):
        lines: list[str] = []
        lines.append(f"<h3>{label}</h3>")
        lines.append(f"<b>Type:</b> {_shape_type_name(shape)}")
        lines.append(f"<b>Solids:</b> {_count_sub(shape, TopAbs_SOLID)}")
        lines.append(f"<b>Shells:</b> {_count_sub(shape, TopAbs_SHELL)}")
        lines.append(f"<b>Faces:</b> {_count_sub(shape, TopAbs_FACE)}")
        lines.append(f"<b>Edges:</b> {_count_sub(shape, TopAbs_EDGE)}")
        lines.append(f"<b>Vertices:</b> {_count_sub(shape, TopAbs_VERTEX)}")

        try:
            vol = _shape_volume(shape)
            lines.append(f"<b>Volume:</b> {vol:,.4f}")
        except Exception:
            lines.append("<b>Volume:</b> N/A")

        try:
            sa = _shape_surface_area(shape)
            lines.append(f"<b>Surface Area:</b> {sa:,.4f}")
        except Exception:
            lines.append("<b>Surface Area:</b> N/A")

        try:
            cx, cy, cz = _shape_center_of_mass(shape)
            lines.append(f"<b>Center of Mass:</b> ({cx:.4f}, {cy:.4f}, {cz:.4f})")
        except Exception:
            pass

        try:
            (xn, yn, zn), (xx, yx, zx) = _shape_bbox(shape)
            lines.append(f"<b>BBox min:</b> ({xn}, {yn}, {zn})")
            lines.append(f"<b>BBox max:</b> ({xx}, {yx}, {zx})")
            lines.append(f"<b>BBox size:</b> ({xx-xn:.4f} × {yx-yn:.4f} × {zx-zn:.4f})")
        except Exception:
            pass

        self._info_label.setText("<br>".join(lines))

    def show_face_info(self, face):
        lines: list[str] = []
        lines.append("<h3>Selected Face</h3>")
        lines.append(f"<b>Surface:</b> {_surface_type_name(face)}")
        try:
            area = _face_area(face)
            lines.append(f"<b>Area:</b> {area:,.6f}")
        except Exception:
            pass
        try:
            props = GProp_GProps()
            brepgprop.SurfaceProperties(face, props)
            c = props.CentreOfMass()
            lines.append(f"<b>Centroid:</b> ({c.X():.4f}, {c.Y():.4f}, {c.Z():.4f})")
        except Exception:
            pass
        lines.append(f"<b>Edges:</b> {_count_sub(face, TopAbs_EDGE)}")
        lines.append(f"<b>Vertices:</b> {_count_sub(face, TopAbs_VERTEX)}")
        self._info_label.setText("<br>".join(lines))

    def show_edge_info(self, edge):
        lines: list[str] = []
        lines.append("<h3>Selected Edge</h3>")
        try:
            length = _edge_length(edge)
            lines.append(f"<b>Length:</b> {length:,.6f}")
        except Exception:
            pass
        lines.append(f"<b>Vertices:</b> {_count_sub(edge, TopAbs_VERTEX)}")
        self._info_label.setText("<br>".join(lines))

    def show_measurement(self, text: str):
        self._info_label.setText(f"<h3>Measurement</h3>{text}")

    def clear(self):
        self._info_label.setText("No model loaded")


# ---------------------------------------------------------------------------
#  Clipping (Cross‑Section) Panel
# ---------------------------------------------------------------------------

class ClippingPanel(QDockWidget):
    """Interactive clipping‑plane controls along X / Y / Z axes."""

    clip_changed = pyqtSignal()  # emitted when any clip value changes

    def __init__(self, parent=None):
        super().__init__("Cross‑Section", parent)
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.setMinimumWidth(240)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)

        self._enabled_cb = QCheckBox("Enable clipping")
        self._enabled_cb.stateChanged.connect(lambda: self.clip_changed.emit())
        layout.addWidget(self._enabled_cb)

        self._axis_combo = QComboBox()
        self._axis_combo.addItems(["X Axis", "Y Axis", "Z Axis"])
        self._axis_combo.currentIndexChanged.connect(lambda: self.clip_changed.emit())
        layout.addWidget(QLabel("Clip axis:"))
        layout.addWidget(self._axis_combo)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(-1000, 1000)
        self._slider.setValue(0)
        self._slider.valueChanged.connect(lambda: self.clip_changed.emit())
        layout.addWidget(QLabel("Position:"))
        layout.addWidget(self._slider)

        self._pos_label = QLabel("0.00")
        layout.addWidget(self._pos_label)

        self._flip_cb = QCheckBox("Flip direction")
        self._flip_cb.stateChanged.connect(lambda: self.clip_changed.emit())
        layout.addWidget(self._flip_cb)

        self._cap_cb = QCheckBox("Show capping")
        self._cap_cb.setChecked(True)
        self._cap_cb.stateChanged.connect(lambda: self.clip_changed.emit())
        layout.addWidget(self._cap_cb)

        layout.addStretch()
        self.setWidget(container)

    def set_range(self, vmin: float, vmax: float):
        self._slider.setRange(int(vmin * 10), int(vmax * 10))
        self._slider.setValue(int((vmin + vmax) / 2 * 10))

    @property
    def enabled(self) -> bool:
        return self._enabled_cb.isChecked()

    @property
    def axis_index(self) -> int:
        return self._axis_combo.currentIndex()

    @property
    def position(self) -> float:
        val = self._slider.value() / 10.0
        self._pos_label.setText(f"{val:.1f}")
        return val

    @property
    def flipped(self) -> bool:
        return self._flip_cb.isChecked()

    @property
    def capping(self) -> bool:
        return self._cap_cb.isChecked()


# ---------------------------------------------------------------------------
#  Lifecycle Panel (face → commit history)
# ---------------------------------------------------------------------------

class LifecyclePanel(QDockWidget):
    """Shows the commit lifecycle of a selected geometric face."""

    def __init__(self, parent=None):
        super().__init__("Face Lifecycle", parent)
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.setMinimumWidth(300)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._title = QLabel("<i>Select a face to see its lifecycle</i>")
        self._title.setWordWrap(True)
        layout.addWidget(self._title)

        self._list = QListWidget()
        self._list.setWordWrap(True)
        self._list.setAlternatingRowColors(True)
        layout.addWidget(self._list)

        self._detail_label = QLabel("")
        self._detail_label.setWordWrap(True)
        self._detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._detail_label)

        self.setWidget(container)

    def show_lifecycle(self, fingerprint: str, events: list[dict]):
        """Populate the panel with lifecycle events for a face."""
        short_fp = fingerprint[:12] + "…" if len(fingerprint) > 12 else fingerprint
        self._title.setText(f"<h4>Face Lifecycle</h4><b>Fingerprint:</b> <code>{short_fp}</code>")

        self._list.clear()
        self._detail_label.setText("")

        if not events:
            item = QListWidgetItem("No lifecycle events found for this face.")
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            self._list.addItem(item)
            return

        _EVENT_ICONS = {
            "baseline": "🏁",
            "added": "🟢",
            "removed": "🔴",
            "modified (before)": "🟡",
            "modified (after)": "🔵",
            "present": "⚪",
        }

        for ev in events:
            event_type = ev.get("event", "unknown")
            icon = _EVENT_ICONS.get(event_type, "⚪")
            commit_id = ev.get("commit_id", "")
            short_cid = commit_id[:8] if commit_id else "?"
            message = ev.get("message", "")
            date = ev.get("committed_at", "")
            designer = ev.get("designer", "")
            details = ev.get("details", "")

            display_text = f"{icon} {event_type.upper()} in commit {short_cid}"
            if message:
                display_text += f"\n   {message}"
            if date:
                display_text += f"\n   📅 {date}"
            if designer:
                display_text += f"  👤 {designer}"

            list_item = QListWidgetItem(display_text)
            list_item.setToolTip(
                f"Commit: {commit_id}\n"
                f"Event: {event_type}\n"
                f"Message: {message}\n"
                f"Date: {date}\n"
                f"Designer: {designer}\n"
                f"Details: {details}"
            )
            self._list.addItem(list_item)

        total = len(events)
        added = sum(1 for e in events if e.get("event") == "added")
        removed = sum(1 for e in events if e.get("event") == "removed")
        modified = sum(1 for e in events if "modified" in (e.get("event") or ""))
        baseline = sum(1 for e in events if e.get("event") == "baseline")

        summary = (
            f"<b>Summary:</b> {total} event(s) — "
            f"🏁 {baseline} baseline, 🟢 {added} added, "
            f"🔴 {removed} removed, 🟡 {modified} modified"
        )
        self._detail_label.setText(summary)

    def clear(self):
        self._title.setText("<i>Select a face to see its lifecycle</i>")
        self._list.clear()
        self._detail_label.setText("")


# ---------------------------------------------------------------------------
#  Measurement Tool State
# ---------------------------------------------------------------------------

class MeasurementState:
    """Tracks measurement mode and picked points."""

    NONE = 0
    DISTANCE = 1
    FACE_AREA = 2
    EDGE_LENGTH = 3
    ANGLE = 4

    def __init__(self):
        self.mode = self.NONE
        self.points: list[gp_Pnt] = []

    def reset(self):
        self.mode = self.NONE
        self.points.clear()


# ---------------------------------------------------------------------------
#  Main Window
# ---------------------------------------------------------------------------

class AdvancedCADViewer(QMainWindow):
    """Full‑featured CAD viewer with professional tooling."""

    def __init__(self, parent=None, *,
                 part_id: int | None = None,
                 project_id: int | None = None,
                 db_path: str | None = None,
                 face_map_path: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Advanced CAD Viewer")
        self.resize(1400, 900)
        self.setMinimumSize(900, 600)

        # lifecycle tracking params
        self._part_id = part_id
        self._project_id = project_id
        self._db_path = db_path
        self._face_map_path = face_map_path
        self._lifecycle_enabled = (part_id is not None and project_id is not None and db_path is not None)

        # state
        self._shape: Optional[TopoDS_Shape] = None
        self._filepath: Optional[str] = None
        self._ais_shapes: list = []
        self._face_index_map: dict[int, dict] = {}   # face hash → face_map entry
        self._occ_face_list: list = []                 # ordered OCC face objects from TopExp_Explorer
        self._clip_plane: Optional[Graphic3d_ClipPlane] = None
        self._measurement = MeasurementState()
        self._annotations: list[dict] = []
        self._dark_theme = True
        self._recent_files: list[str] = []

        # build UI
        self._build_viewer()
        self._build_docks()
        self._build_menus()
        self._build_toolbars()
        self._build_statusbar()
        self._apply_theme()

        # Register default face‑selection callback for lifecycle
        self._display.register_select_callback(self._on_face_selected)

    # ═══════════════════════════════════════════════════════════════════════
    #  UI Construction
    # ═══════════════════════════════════════════════════════════════════════

    def _build_viewer(self):
        """Create the central 3D OpenGL canvas."""
        self._canvas = qtViewer3d(self)
        self.setCentralWidget(self._canvas)
        self._canvas.InitDriver()
        self._display = self._canvas._display

        # background gradient
        self._display.set_bg_gradient_color(
            [30, 35, 46],    # dark top
            [56, 62, 77],    # slightly lighter bottom
        )

        self._display.SetSelectionModeFace()
        self._display.display_triedron()

    def _build_docks(self):
        """Create side panels."""
        # Model Tree
        self._model_tree = ModelTreeWidget(self)
        self._model_tree.shape_selected.connect(self._on_tree_select)
        self._model_tree.visibility_changed.connect(self._on_visibility)
        self.addDockWidget(Qt.LeftDockWidgetArea, self._model_tree)

        # Properties
        self._props = PropertiesPanel(self)
        self.addDockWidget(Qt.RightDockWidgetArea, self._props)

        # Clipping
        self._clip_panel = ClippingPanel(self)
        self._clip_panel.clip_changed.connect(self._apply_clipping)
        self.addDockWidget(Qt.RightDockWidgetArea, self._clip_panel)
        self._clip_panel.hide()

        # Lifecycle
        self._lifecycle_panel = LifecyclePanel(self)
        self.addDockWidget(Qt.RightDockWidgetArea, self._lifecycle_panel)
        if not self._lifecycle_enabled:
            self._lifecycle_panel.hide()

    def _build_menus(self):
        """Create the full menu bar."""
        mb = self.menuBar()

        # ── File ─────────────────────────────────────────────────────────
        file_menu = mb.addMenu("&File")

        open_act = QAction("&Open…", self)
        open_act.setShortcut(QKeySequence.Open)
        open_act.triggered.connect(self._open_file)
        file_menu.addAction(open_act)

        file_menu.addSeparator()

        export_menu = file_menu.addMenu("Export")
        exp_png = QAction("Screenshot (PNG)…", self)
        exp_png.triggered.connect(self._export_png)
        export_menu.addAction(exp_png)

        exp_stl = QAction("Export as STL…", self)
        exp_stl.triggered.connect(self._export_stl)
        export_menu.addAction(exp_stl)

        exp_iges = QAction("Export as IGES…", self)
        exp_iges.triggered.connect(self._export_iges)
        export_menu.addAction(exp_iges)

        exp_step = QAction("Export as STEP…", self)
        exp_step.triggered.connect(self._export_step)
        export_menu.addAction(exp_step)

        file_menu.addSeparator()

        close_act = QAction("Close Model", self)
        close_act.triggered.connect(self._close_model)
        file_menu.addAction(close_act)

        file_menu.addSeparator()

        quit_act = QAction("&Quit", self)
        quit_act.setShortcut(QKeySequence.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        # ── View ─────────────────────────────────────────────────────────
        view_menu = mb.addMenu("&View")

        # display modes
        dm_group = QActionGroup(self)
        dm_group.setExclusive(True)

        for label, mode_id in [
            ("Shaded", 0), ("Wireframe", 1), ("Shaded + Edges", 2),
            ("Transparent", 3), ("Hidden‑Line", 4),
        ]:
            act = QAction(label, self, checkable=True)
            act.setData(mode_id)
            act.triggered.connect(lambda checked, m=mode_id: self._set_display_mode(m))
            dm_group.addAction(act)
            view_menu.addAction(act)
            if mode_id == 0:
                act.setChecked(True)

        view_menu.addSeparator()

        # standard views
        views_menu = view_menu.addMenu("Standard Views")
        for label, orient in [
            ("Front", V3d_TypeOfOrientation_Zup_Front),
            ("Back", V3d_TypeOfOrientation_Zup_Back),
            ("Top", V3d_TypeOfOrientation_Zup_Top),
            ("Bottom", V3d_TypeOfOrientation_Zup_Bottom),
            ("Left", V3d_TypeOfOrientation_Zup_Left),
            ("Right", V3d_TypeOfOrientation_Zup_Right),
            ("Isometric", V3d_XposYposZpos),
        ]:
            act = QAction(label, self)
            act.triggered.connect(lambda checked, o=orient: self._set_view(o))
            views_menu.addAction(act)

        view_menu.addSeparator()

        fitall_act = QAction("Fit All", self)
        fitall_act.setShortcut(QKeySequence("F"))
        fitall_act.triggered.connect(self._fit_all)
        view_menu.addAction(fitall_act)

        view_menu.addSeparator()

        clip_act = QAction("Cross‑Section Panel", self, checkable=True)
        clip_act.triggered.connect(lambda checked: self._clip_panel.setVisible(checked))
        view_menu.addAction(clip_act)

        lifecycle_act = QAction("Face Lifecycle Panel", self, checkable=True)
        lifecycle_act.setChecked(self._lifecycle_enabled)
        lifecycle_act.triggered.connect(lambda checked: self._lifecycle_panel.setVisible(checked))
        view_menu.addAction(lifecycle_act)

        view_menu.addSeparator()

        theme_act = QAction("Toggle Dark / Light Theme", self)
        theme_act.triggered.connect(self._toggle_theme)
        view_menu.addAction(theme_act)

        # ── Measure ──────────────────────────────────────────────────────
        measure_menu = mb.addMenu("&Measure")

        dist_act = QAction("Point‑to‑Point Distance", self)
        dist_act.setShortcut(QKeySequence("D"))
        dist_act.triggered.connect(lambda: self._start_measurement(MeasurementState.DISTANCE))
        measure_menu.addAction(dist_act)

        face_area_act = QAction("Face Area", self)
        face_area_act.triggered.connect(lambda: self._start_measurement(MeasurementState.FACE_AREA))
        measure_menu.addAction(face_area_act)

        edge_len_act = QAction("Edge Length", self)
        edge_len_act.triggered.connect(lambda: self._start_measurement(MeasurementState.EDGE_LENGTH))
        measure_menu.addAction(edge_len_act)

        measure_menu.addSeparator()

        clear_meas = QAction("Clear Measurements", self)
        clear_meas.triggered.connect(self._clear_measurements)
        measure_menu.addAction(clear_meas)

        # ── Diff ─────────────────────────────────────────────────────────
        diff_menu = mb.addMenu("&Diff")

        diff_act = QAction("Open Diff (compare two files)…", self)
        diff_act.triggered.connect(self._open_diff_dialog)
        diff_menu.addAction(diff_act)

        # ── Help ─────────────────────────────────────────────────────────
        help_menu = mb.addMenu("&Help")

        about_act = QAction("About", self)
        about_act.triggered.connect(self._show_about)
        help_menu.addAction(about_act)

        shortcuts_act = QAction("Keyboard Shortcuts", self)
        shortcuts_act.triggered.connect(self._show_shortcuts)
        help_menu.addAction(shortcuts_act)

    def _build_toolbars(self):
        """Create the main toolbar strip."""
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(Qt.TopToolBarArea, tb)

        # file ops
        tb.addAction(self._make_text_action("📂 Open", self._open_file, "Ctrl+O"))
        tb.addAction(self._make_text_action("📷 Screenshot", self._export_png, "Ctrl+Shift+S"))
        tb.addSeparator()

        # display modes
        tb.addAction(self._make_text_action("Shaded", lambda: self._set_display_mode(0)))
        tb.addAction(self._make_text_action("Wire", lambda: self._set_display_mode(1)))
        tb.addAction(self._make_text_action("Edges", lambda: self._set_display_mode(2)))
        tb.addAction(self._make_text_action("Transp", lambda: self._set_display_mode(3)))
        tb.addSeparator()

        # views
        tb.addAction(self._make_text_action("Front", lambda: self._set_view(V3d_TypeOfOrientation_Zup_Front)))
        tb.addAction(self._make_text_action("Top", lambda: self._set_view(V3d_TypeOfOrientation_Zup_Top)))
        tb.addAction(self._make_text_action("Right", lambda: self._set_view(V3d_TypeOfOrientation_Zup_Right)))
        tb.addAction(self._make_text_action("Iso", lambda: self._set_view(V3d_XposYposZpos)))
        tb.addAction(self._make_text_action("Fit", self._fit_all, "F"))
        tb.addSeparator()

        # measurements
        tb.addAction(self._make_text_action("📏 Distance", lambda: self._start_measurement(MeasurementState.DISTANCE), "D"))
        tb.addAction(self._make_text_action("📐 Face Area", lambda: self._start_measurement(MeasurementState.FACE_AREA)))
        tb.addAction(self._make_text_action("📏 Edge Len", lambda: self._start_measurement(MeasurementState.EDGE_LENGTH)))
        tb.addSeparator()

        # cross-section
        tb.addAction(self._make_text_action("✂ Section", self._toggle_clip_panel))
        tb.addSeparator()

        # diff
        tb.addAction(self._make_text_action("⚡ Diff", self._open_diff_dialog))
        tb.addSeparator()

        # lifecycle
        if self._lifecycle_enabled:
            tb.addAction(self._make_text_action("🔬 Lifecycle", self._toggle_lifecycle_panel))
            tb.addSeparator()

        # color
        tb.addAction(self._make_text_action("🎨 Color", self._pick_model_color))
        tb.addSeparator()

        # theme
        tb.addAction(self._make_text_action("🌓 Theme", self._toggle_theme))

    def _build_statusbar(self):
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._coord_label = QLabel("Ready")
        self._status.addPermanentWidget(self._coord_label)

    def _make_text_action(self, text: str, slot, shortcut: str = None) -> QAction:
        act = QAction(text, self)
        act.triggered.connect(slot)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        return act

    # ═══════════════════════════════════════════════════════════════════════
    #  File Operations
    # ═══════════════════════════════════════════════════════════════════════

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open CAD File", "", _FILE_FILTER)
        if path:
            self.load_file(path)

    def load_file(self, filepath: str):
        """Public entry — load and display a CAD file."""
        self._status.showMessage(f"Loading {filepath}…")
        QApplication.processEvents()
        try:
            shape = load_cad_file(filepath)
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", str(exc))
            self._status.showMessage("Load failed")
            return

        self._close_model()
        self._shape = shape
        self._filepath = filepath

        # mesh it
        BRepMesh_IncrementalMesh(shape, 0.5, False, 0.25, True)

        # Build ordered face list (same order as TopExp_Explorer = same as parser)
        self._occ_face_list = []
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        while exp.More():
            # Use topods.Face() to get a proper persistent copy of the face
            self._occ_face_list.append(topods.Face(exp.Current()))
            exp.Next()
        print(f"[CAD Viewer] Built OCC face list: {len(self._occ_face_list)} faces")

        # Load face map for lifecycle tracking
        self._face_index_map.clear()
        if self._lifecycle_enabled and self._face_map_path:
            try:
                import json as _json
                with open(self._face_map_path, "r", encoding="utf-8") as fh:
                    face_map_data = _json.load(fh)
                for entry in face_map_data:
                    idx = entry.get("index", -1)
                    self._face_index_map[idx] = entry
                self._status.showMessage(
                    f"Loaded face map: {len(self._face_index_map)} faces with fingerprints"
                )
            except Exception as exc:
                self._status.showMessage(f"Face map load failed: {exc}")

        # display
        self._display_shape(shape)
        self._display.FitAll()

        # Re-activate face selection mode AFTER shape is displayed
        # (SetSelectionModeFace in __init__ only applies to shapes that existed
        #  at that point; newly displayed shapes need it re-applied)
        self._display.SetSelectionModeFace()
        print(f"[CAD Viewer] Face selection mode activated (post-display)")

        # panels
        name = Path(filepath).name
        self._model_tree.populate(shape, name)
        self._props.show_shape_info(shape, name)
        title = f"Advanced CAD Viewer — {name}"
        if self._lifecycle_enabled:
            title += f"  (Part #{self._part_id} — lifecycle tracking ON)"
        self.setWindowTitle(title)

        # clipping range
        try:
            (xn, yn, zn), (xx, yx, zx) = _shape_bbox(shape)
            max_range = max(xx - xn, yx - yn, zx - zn)
            self._clip_panel.set_range(-max_range, max_range * 2)
        except Exception:
            pass

        self._status.showMessage(f"Loaded: {name}  |  {_count_sub(shape, TopAbs_FACE)} faces, {_count_sub(shape, TopAbs_EDGE)} edges")

    def _display_shape(self, shape: TopoDS_Shape, color=None, transparency: float = 0.0):
        """Add a shape to the AIS context."""
        ais_shape = self._display.DisplayShape(
            shape,
            color=color,
            transparency=transparency,
            update=True,
        )
        if ais_shape:
            if isinstance(ais_shape, (list, tuple)):
                self._ais_shapes.extend(ais_shape)
            else:
                self._ais_shapes.append(ais_shape)

    def _close_model(self):
        """Remove everything from the display."""
        self._display.EraseAll()
        self._ais_shapes.clear()
        self._occ_face_list.clear()
        self._face_index_map.clear()
        self._shape = None
        self._filepath = None
        self._model_tree.clear()
        self._props.clear()
        self._lifecycle_panel.clear()
        self._remove_clip()
        self._measurement.reset()
        self._annotations.clear()
        self.setWindowTitle("Advanced CAD Viewer")
        self._status.showMessage("Ready")

    # ── Exports ──────────────────────────────────────────────────────────

    def _export_png(self):
        if self._shape is None:
            QMessageBox.warning(self, "Export", "No model loaded")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Screenshot", "screenshot.png", "PNG (*.png)")
        if path:
            self._display.ExportToImage(path)
            self._status.showMessage(f"Screenshot saved: {path}")

    def _export_stl(self):
        if self._shape is None:
            QMessageBox.warning(self, "Export", "No model loaded")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export STL", "model.stl", "STL (*.stl)")
        if not path:
            return
        try:
            BRepMesh_IncrementalMesh(self._shape, 0.1, False, 0.1, True)
            writer = StlAPI_Writer()
            writer.Write(self._shape, path)
            self._status.showMessage(f"STL exported: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    def _export_iges(self):
        if self._shape is None:
            QMessageBox.warning(self, "Export", "No model loaded")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export IGES", "model.iges", "IGES (*.iges *.igs)")
        if not path:
            return
        try:
            writer = IGESControl_Writer()
            writer.AddShape(self._shape)
            writer.Write(path)
            self._status.showMessage(f"IGES exported: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    def _export_step(self):
        if self._shape is None:
            QMessageBox.warning(self, "Export", "No model loaded")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export STEP", "model.step", "STEP (*.step *.stp)")
        if not path:
            return
        try:
            writer = STEPControl_Writer()
            writer.Transfer(self._shape, STEPControl_AsIs)
            writer.Write(path)
            self._status.showMessage(f"STEP exported: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    # ═══════════════════════════════════════════════════════════════════════
    #  Display Modes
    # ═══════════════════════════════════════════════════════════════════════

    def _set_display_mode(self, mode_id: int):
        if mode_id == 0:  # Shaded
            self._display.SetModeShaded()
        elif mode_id == 1:  # Wireframe
            self._display.SetModeWireFrame()
        elif mode_id == 2:  # Shaded + Edges
            self._display.SetModeShaded()
        elif mode_id == 3:  # Transparent
            self._display.SetModeShaded()
            # Apply transparency to all displayed shapes
            ctx = self._display.GetContext()
            for ais in self._ais_shapes:
                try:
                    ctx.SetTransparency(ais, 0.6, False)
                except Exception:
                    pass
            ctx.UpdateCurrentViewer()
        elif mode_id == 4:  # HLR
            self._display.SetModeHLR()

        # Remove transparency for non-transparent modes
        if mode_id != 3:
            ctx = self._display.GetContext()
            for ais in self._ais_shapes:
                try:
                    ctx.SetTransparency(ais, 0.0, False)
                except Exception:
                    pass
            ctx.UpdateCurrentViewer()

        self._status.showMessage(
            f"Display mode: {['Shaded','Wireframe','Shaded+Edges','Transparent','Hidden-Line'][mode_id]}"
        )

    # ═══════════════════════════════════════════════════════════════════════
    #  Views
    # ═══════════════════════════════════════════════════════════════════════

    def _set_view(self, orientation):
        # Map orientation enums to display convenience methods
        _view_map = {
            V3d_TypeOfOrientation_Zup_Front: "View_Front",
            V3d_TypeOfOrientation_Zup_Back: "View_Rear",
            V3d_TypeOfOrientation_Zup_Top: "View_Top",
            V3d_TypeOfOrientation_Zup_Bottom: "View_Bottom",
            V3d_TypeOfOrientation_Zup_Left: "View_Left",
            V3d_TypeOfOrientation_Zup_Right: "View_Right",
            V3d_XposYposZpos: "View_Iso",
        }
        method_name = _view_map.get(orientation, "View_Iso")
        getattr(self._display, method_name, self._display.View_Iso)()
        self._display.FitAll()

    def _fit_all(self):
        self._display.FitAll()

    # ═══════════════════════════════════════════════════════════════════════
    #  Clipping / Cross‑Section
    # ═══════════════════════════════════════════════════════════════════════

    def _toggle_clip_panel(self):
        self._clip_panel.setVisible(not self._clip_panel.isVisible())

    def _toggle_lifecycle_panel(self):
        self._lifecycle_panel.setVisible(not self._lifecycle_panel.isVisible())

    def _apply_clipping(self):
        self._remove_clip()
        if not self._clip_panel.enabled or self._shape is None:
            self._display.GetView().Redraw()
            return

        axis_idx = self._clip_panel.axis_index
        pos = self._clip_panel.position
        flipped = self._clip_panel.flipped

        dirs = [gp_Dir(1, 0, 0), gp_Dir(0, 1, 0), gp_Dir(0, 0, 1)]
        d = dirs[axis_idx]
        if flipped:
            d = gp_Dir(-d.X(), -d.Y(), -d.Z())

        plane = gp_Pln(gp_Pnt(
            pos if axis_idx == 0 else 0,
            pos if axis_idx == 1 else 0,
            pos if axis_idx == 2 else 0,
        ), d)

        self._clip_plane = Graphic3d_ClipPlane(plane)
        self._clip_plane.SetCapping(self._clip_panel.capping)
        self._clip_plane.SetOn(True)

        self._display.GetView().AddClipPlane(self._clip_plane)
        self._display.GetView().Redraw()
        self._status.showMessage(f"Clipping: axis={'XYZ'[axis_idx]}  pos={pos:.1f}")

    def _remove_clip(self):
        if self._clip_plane is not None:
            try:
                self._display.GetView().RemoveClipPlane(self._clip_plane)
            except Exception:
                pass
            self._clip_plane = None

    # ═══════════════════════════════════════════════════════════════════════
    #  Measurement
    # ═══════════════════════════════════════════════════════════════════════

    def _start_measurement(self, mode: int):
        self._measurement.reset()
        self._measurement.mode = mode

        # Set appropriate selection mode
        if mode == MeasurementState.FACE_AREA:
            self._display.SetSelectionModeFace()
        elif mode == MeasurementState.EDGE_LENGTH:
            self._display.SetSelectionModeEdge()
        elif mode == MeasurementState.DISTANCE:
            self._display.SetSelectionModeVertex()

        labels = {
            MeasurementState.DISTANCE: "Click two points / vertices to measure distance",
            MeasurementState.FACE_AREA: "Click a face to measure its area",
            MeasurementState.EDGE_LENGTH: "Click an edge to measure its length",
        }
        self._status.showMessage(labels.get(mode, "Measurement active"))

        # The default _on_face_selected callback delegates to _on_measurement_select
        # when self._measurement.mode is active, so no need to re-register.

    def _on_measurement_select(self, shapes, *args):
        """Handle shape selection during measurement."""
        if self._measurement.mode == MeasurementState.NONE:
            return
        if not shapes:
            return

        shape = shapes[0]

        if self._measurement.mode == MeasurementState.FACE_AREA:
            try:
                if shape.ShapeType() == TopAbs_FACE:
                    face = topods.Face(shape)
                    area = _face_area(face)
                    self._props.show_measurement(f"<b>Face Area:</b> {area:,.6f}")
                    self._status.showMessage(f"Face area: {area:,.6f}")
                else:
                    self._status.showMessage("Please click a face (switch to face selection mode)")
            except Exception as e:
                self._status.showMessage(f"Cannot read face: {e}")

        elif self._measurement.mode == MeasurementState.EDGE_LENGTH:
            try:
                if shape.ShapeType() == TopAbs_EDGE:
                    edge = topods.Edge(shape)
                    length = _edge_length(edge)
                    self._props.show_measurement(f"<b>Edge Length:</b> {length:,.6f}")
                    self._status.showMessage(f"Edge length: {length:,.6f}")
                else:
                    self._status.showMessage("Please click an edge (use View → Selection Mode → Edge)")
            except Exception as e:
                self._status.showMessage(f"Cannot read edge: {e}")

        elif self._measurement.mode == MeasurementState.DISTANCE:
            try:
                # Compute centroid of the selected sub-shape as a representative point
                props = GProp_GProps()
                if shape.ShapeType() == TopAbs_VERTEX:
                    pt = BRep_Tool.Pnt(topods.Vertex(shape))
                else:
                    brepgprop.LinearProperties(shape, props)
                    c = props.CentreOfMass()
                    pt = gp_Pnt(c.X(), c.Y(), c.Z())

                self._measurement.points.append(pt)

                if len(self._measurement.points) == 1:
                    self._status.showMessage(
                        f"Point 1: ({pt.X():.4f}, {pt.Y():.4f}, {pt.Z():.4f}) — Click second point"
                    )
                elif len(self._measurement.points) >= 2:
                    p1 = self._measurement.points[0]
                    p2 = self._measurement.points[1]
                    dist = _point_distance(p1, p2)
                    dx = abs(p2.X() - p1.X())
                    dy = abs(p2.Y() - p1.Y())
                    dz = abs(p2.Z() - p1.Z())
                    text = (
                        f"<b>Distance:</b> {dist:,.6f}<br>"
                        f"<b>ΔX:</b> {dx:,.6f}<br>"
                        f"<b>ΔY:</b> {dy:,.6f}<br>"
                        f"<b>ΔZ:</b> {dz:,.6f}<br>"
                        f"<b>P1:</b> ({p1.X():.4f}, {p1.Y():.4f}, {p1.Z():.4f})<br>"
                        f"<b>P2:</b> ({p2.X():.4f}, {p2.Y():.4f}, {p2.Z():.4f})"
                    )
                    self._props.show_measurement(text)
                    self._status.showMessage(f"Distance: {dist:,.6f}")
                    self._measurement.reset()
            except Exception as e:
                self._status.showMessage(f"Distance error: {e}")

    def _clear_measurements(self):
        self._measurement.reset()
        self._display.SetSelectionModeFace()  # restore default selection mode
        # Re-register default callback
        self._display.register_select_callback(self._on_face_selected)
        if self._shape:
            self._props.show_shape_info(self._shape, Path(self._filepath).name if self._filepath else "Model")
        self._status.showMessage("Measurements cleared")

    # ═══════════════════════════════════════════════════════════════════════
    #  Model Tree Callbacks
    # ═══════════════════════════════════════════════════════════════════════

    def _on_tree_select(self, shape: TopoDS_Shape):
        """When user clicks a shape in the tree → highlight + show properties."""
        try:
            if shape.ShapeType() == TopAbs_FACE:
                self._props.show_face_info(shape)
            elif shape.ShapeType() == TopAbs_EDGE:
                self._props.show_edge_info(shape)
            else:
                self._props.show_shape_info(shape)
        except Exception:
            pass

    def _on_visibility(self, shape: TopoDS_Shape, visible: bool):
        """Toggle shape visibility from tree checkbox."""
        ctx = self._display.GetContext()
        for ais in self._ais_shapes:
            try:
                if ais.Shape().IsEqual(shape):
                    if visible:
                        ctx.Display(ais, True)
                    else:
                        ctx.Erase(ais, True)
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════════════════
    #  Face Selection → Properties + Lifecycle
    # ═══════════════════════════════════════════════════════════════════════

    def _on_face_selected(self, shapes, *args):
        """Default callback: when a face is clicked, show properties and lifecycle."""
        print(f"[CAD Viewer] Selection callback fired: {len(shapes)} shape(s)")

        # If a measurement is active, delegate to measurement handler instead
        if self._measurement.mode != MeasurementState.NONE:
            self._on_measurement_select(shapes, *args)
            return

        if not shapes:
            self._status.showMessage("Click on a face to select it")
            return

        shape = shapes[0]

        try:
            stype = shape.ShapeType()
            type_name = _shape_type_name(shape)
            print(f"[CAD Viewer] Selected shape type: {type_name} ({stype})")

            if stype == TopAbs_FACE:
                face = topods.Face(shape)
                self._props.show_face_info(face)
                self._query_face_lifecycle(face)
            elif stype == TopAbs_EDGE:
                self._props.show_edge_info(topods.Edge(shape))
            elif stype in (TopAbs_SOLID, TopAbs_SHELL, TopAbs_COMPOUND, TopAbs_COMPSOLID):
                self._props.show_shape_info(shape)
            else:
                self._props.show_shape_info(shape)
        except Exception as exc:
            print(f"[CAD Viewer] Selection callback error: {exc}")
            import traceback; traceback.print_exc()
            self._status.showMessage(f"Selection error: {exc}")

    def _query_face_lifecycle(self, face):
        """Look up the face's index in the OCC face list, find its
        pre-computed fingerprint from the face map, then query lifecycle."""
        if not self._lifecycle_enabled:
            return

        if not self._face_index_map:
            self._lifecycle_panel.clear()
            self._status.showMessage(
                "Face lifecycle: no face map loaded — commit a STEP file first"
            )
            return

        # Determine face index by matching the OCC face object
        face_idx = self._find_face_index(face)
        if face_idx < 0:
            self._lifecycle_panel.clear()
            self._status.showMessage("Face lifecycle: could not identify face index")
            return

        entry = self._face_index_map.get(face_idx)
        if not entry:
            self._lifecycle_panel.clear()
            self._status.showMessage(
                f"Face lifecycle: face #{face_idx} not found in face map"
            )
            return

        fingerprint = entry.get("fingerprint", "")
        if not fingerprint:
            self._lifecycle_panel.clear()
            return

        try:
            events = get_face_lifecycle(
                fingerprint=fingerprint,
                part_id=self._part_id,
                project_id=self._project_id,
                db_path=self._db_path,
            )
            self._lifecycle_panel.show_lifecycle(fingerprint, events)
            self._lifecycle_panel.show()

            stype = entry.get("surface_type", "")
            if events:
                n = len(events)
                self._status.showMessage(
                    f"Face #{face_idx} ({stype}): {n} lifecycle event(s)"
                )
            else:
                self._status.showMessage(
                    f"Face #{face_idx} ({stype}): no changes — present since baseline"
                )
        except Exception as exc:
            self._status.showMessage(f"Lifecycle query error: {exc}")

    def _find_face_index(self, face) -> int:
        """Return the index of *face* in the OCC face list (-1 if not found).

        Tries IsSame() first (geometry + location identity, ignoring
        orientation), then IsEqual() (full topological identity)."""
        for idx, occ_face in enumerate(self._occ_face_list):
            try:
                if face.IsSame(occ_face):
                    return idx
            except Exception:
                pass
        # Fallback: try IsEqual
        for idx, occ_face in enumerate(self._occ_face_list):
            try:
                if face.IsEqual(occ_face):
                    return idx
            except Exception:
                pass
        return -1

    # ═══════════════════════════════════════════════════════════════════════
    #  Model Color Picker
    # ═══════════════════════════════════════════════════════════════════════

    def _pick_model_color(self):
        if not self._ais_shapes:
            QMessageBox.warning(self, "Color", "No model loaded")
            return
        color = QColorDialog.getColor(Qt.white, self, "Pick Model Color")
        if color.isValid():
            occ_color = Quantity_Color(
                color.redF(), color.greenF(), color.blueF(), Quantity_TOC_RGB
            )
            ctx = self._display.GetContext()
            for ais in self._ais_shapes:
                try:
                    ctx.SetColor(ais, occ_color, False)
                except Exception:
                    pass
            ctx.UpdateCurrentViewer()

    # ═══════════════════════════════════════════════════════════════════════
    #  Diff Overlay
    # ═══════════════════════════════════════════════════════════════════════

    def _open_diff_dialog(self):
        """Open two files and show boolean‑volume diff overlay."""
        dlg = DiffDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return

        path_a = dlg.path_a
        path_b = dlg.path_b
        transparency = dlg.common_transparency

        self._status.showMessage("Computing diff…")
        QApplication.processEvents()

        try:
            shape_a = load_cad_file(path_a)
            shape_b = load_cad_file(path_b)
        except Exception as exc:
            QMessageBox.critical(self, "Diff Error", f"Cannot load files:\n{exc}")
            return

        self._close_model()
        self._shape = shape_a  # reference shape for properties

        # Boolean diff
        common_shape = removed_shape = added_shape = None
        try:
            op = BRepAlgoAPI_Common(shape_a, shape_b)
            if op.IsDone():
                common_shape = op.Shape()
        except Exception:
            pass
        try:
            op = BRepAlgoAPI_Cut(shape_a, shape_b)
            if op.IsDone():
                removed_shape = op.Shape()
        except Exception:
            pass
        try:
            op = BRepAlgoAPI_Cut(shape_b, shape_a)
            if op.IsDone():
                added_shape = op.Shape()
        except Exception:
            pass

        def _ok(s):
            return s is not None and (not hasattr(s, "IsNull") or not s.IsNull())

        if _ok(common_shape):
            self._display_shape(common_shape, color="GRAY", transparency=transparency)
        if _ok(removed_shape):
            self._display_shape(removed_shape, color="RED", transparency=0.0)
        if _ok(added_shape):
            self._display_shape(added_shape, color="GREEN", transparency=0.0)

        self._display.FitAll()

        # Populate tree with diff info
        name_a = Path(path_a).name
        name_b = Path(path_b).name
        self._model_tree.populate(shape_a, f"Diff: {name_a} vs {name_b}")

        # Properties: diff summary
        lines = [f"<h3>Diff: {name_a} → {name_b}</h3>"]
        try:
            vol_a = _shape_volume(shape_a)
            vol_b = _shape_volume(shape_b)
            lines.append(f"<b>Volume A:</b> {vol_a:,.4f}")
            lines.append(f"<b>Volume B:</b> {vol_b:,.4f}")
            lines.append(f"<b>ΔVolume:</b> {vol_b - vol_a:,.4f}")
        except Exception:
            pass

        try:
            faces_a = _count_sub(shape_a, TopAbs_FACE)
            faces_b = _count_sub(shape_b, TopAbs_FACE)
            lines.append(f"<b>Faces A:</b> {faces_a}")
            lines.append(f"<b>Faces B:</b> {faces_b}")
            lines.append(f"<b>ΔFaces:</b> {faces_b - faces_a}")
        except Exception:
            pass

        lines.append("<br><b>Legend:</b>")
        lines.append("<span style='color:gray'>■</span> Common geometry")
        lines.append("<span style='color:red'>■</span> Removed (in A, not in B)")
        lines.append("<span style='color:green'>■</span> Added (in B, not in A)")

        self._props._info_label.setText("<br>".join(lines))
        self.setWindowTitle(f"Advanced CAD Viewer — Diff: {name_a} vs {name_b}")
        self._status.showMessage("Diff complete — gray=common, red=removed, green=added")

    # ═══════════════════════════════════════════════════════════════════════
    #  Theme
    # ═══════════════════════════════════════════════════════════════════════

    def _toggle_theme(self):
        self._dark_theme = not self._dark_theme
        self._apply_theme()

    def _apply_theme(self):
        if self._dark_theme:
            self.setStyleSheet(_DARK_QSS)
            bg1 = [30, 35, 46]
            bg2 = [56, 62, 77]
        else:
            self.setStyleSheet(_LIGHT_QSS)
            bg1 = [217, 224, 235]
            bg2 = [255, 255, 255]

        try:
            self._display.set_bg_gradient_color(bg1, bg2)
            self._display.Repaint()
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════════════
    #  About / Help
    # ═══════════════════════════════════════════════════════════════════════

    def _show_about(self):
        QMessageBox.about(
            self, "About Advanced CAD Viewer",
            "<h2>Advanced CAD Viewer</h2>"
            "<p>Professional 3‑D viewer for STEP / IGES / BREP files.</p>"
            "<p>Built with <b>PyQt5</b> + <b>pythonOCC</b></p>"
            "<hr>"
            "<p><b>Features:</b></p>"
            "<ul>"
            "<li>Interactive 3D viewport</li>"
            "<li>Model tree explorer</li>"
            "<li>Properties panel</li>"
            "<li>5 display modes</li>"
            "<li>7 standard views</li>"
            "<li>Cross‑section clipping plane</li>"
            "<li>Measurement tools</li>"
            "<li>Boolean volume diff</li>"
            "<li>Export to PNG / STL / IGES / STEP</li>"
            "<li>Face lifecycle tracking (commit history per face)</li>"
            "<li>Dark / Light theme</li>"
            "</ul>"
        )

    def _show_shortcuts(self):
        QMessageBox.information(
            self, "Keyboard Shortcuts",
            "<h3>Keyboard Shortcuts</h3>"
            "<table>"
            "<tr><td><b>Ctrl+O</b></td><td>Open file</td></tr>"
            "<tr><td><b>F</b></td><td>Fit all</td></tr>"
            "<tr><td><b>D</b></td><td>Distance measurement</td></tr>"
            "<tr><td><b>Ctrl+Shift+S</b></td><td>Screenshot</td></tr>"
            "<tr><td><b>Scroll</b></td><td>Zoom</td></tr>"
            "<tr><td><b>Middle drag</b></td><td>Rotate</td></tr>"
            "<tr><td><b>Right drag</b></td><td>Pan</td></tr>"
            "</table>"
        )

    # ═══════════════════════════════════════════════════════════════════════
    #  Drag & Drop
    # ═══════════════════════════════════════════════════════════════════════

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self.load_file(path)
                break

    # ═══════════════════════════════════════════════════════════════════════
    #  Public API (for subprocess / CLI)
    # ═══════════════════════════════════════════════════════════════════════

    def load_and_show(self, filepath: str):
        """Convenience for CLI: load file after window shows."""
        QTimer.singleShot(100, lambda: self.load_file(filepath))

    def load_diff_and_show(self, path_a: str, path_b: str,
                           commit_a: str = "", commit_b: str = "",
                           transparency: float = 0.80):
        """Convenience for CLI: open in diff mode after window shows."""
        def _run():
            self._status.showMessage("Computing diff…")
            QApplication.processEvents()
            try:
                shape_a = load_cad_file(path_a)
                shape_b = load_cad_file(path_b)
            except Exception as exc:
                QMessageBox.critical(self, "Diff Error", f"Cannot load files:\n{exc}")
                return

            self._close_model()
            self._shape = shape_a

            common_shape = removed_shape = added_shape = None
            try:
                op = BRepAlgoAPI_Common(shape_a, shape_b)
                if op.IsDone():
                    common_shape = op.Shape()
            except Exception:
                pass
            try:
                op = BRepAlgoAPI_Cut(shape_a, shape_b)
                if op.IsDone():
                    removed_shape = op.Shape()
            except Exception:
                pass
            try:
                op = BRepAlgoAPI_Cut(shape_b, shape_a)
                if op.IsDone():
                    added_shape = op.Shape()
            except Exception:
                pass

            def _ok(s):
                return s is not None and (not hasattr(s, "IsNull") or not s.IsNull())

            if _ok(common_shape):
                self._display_shape(common_shape, color="GRAY", transparency=transparency)
            if _ok(removed_shape):
                self._display_shape(removed_shape, color="RED", transparency=0.0)
            if _ok(added_shape):
                self._display_shape(added_shape, color="GREEN", transparency=0.0)

            self._display.FitAll()

            na = Path(path_a).name
            nb = Path(path_b).name
            title_a = commit_a or na
            title_b = commit_b or nb
            self._model_tree.populate(shape_a, f"Diff: {title_a} vs {title_b}")
            self.setWindowTitle(f"Advanced CAD Viewer — Diff: {title_a} vs {title_b}")

            # summary
            lines = [f"<h3>Diff: {title_a} → {title_b}</h3>"]
            try:
                va = _shape_volume(shape_a)
                vb = _shape_volume(shape_b)
                lines.append(f"<b>Volume A:</b> {va:,.4f}")
                lines.append(f"<b>Volume B:</b> {vb:,.4f}")
                lines.append(f"<b>ΔVolume:</b> {vb - va:,.4f}")
            except Exception:
                pass
            lines.append("<br><b>Legend:</b>")
            lines.append("<span style='color:gray'>■</span> Common")
            lines.append("<span style='color:red'>■</span> Removed")
            lines.append("<span style='color:green'>■</span> Added")
            self._props._info_label.setText("<br>".join(lines))

            self._status.showMessage("Diff complete")

        QTimer.singleShot(100, _run)


# ---------------------------------------------------------------------------
#  Diff Dialog
# ---------------------------------------------------------------------------

class DiffDialog(QDialog):
    """Simple dialog to pick two CAD files + transparency for diff."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Compare Two CAD Files")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._path_a = QLineEdit()
        self._path_a.setPlaceholderText("Select File A (before)…")
        btn_a = QPushButton("Browse")
        btn_a.clicked.connect(lambda: self._browse(self._path_a))
        row_a = QHBoxLayout()
        row_a.addWidget(self._path_a)
        row_a.addWidget(btn_a)
        form.addRow("File A:", row_a)

        self._path_b = QLineEdit()
        self._path_b.setPlaceholderText("Select File B (after)…")
        btn_b = QPushButton("Browse")
        btn_b.clicked.connect(lambda: self._browse(self._path_b))
        row_b = QHBoxLayout()
        row_b.addWidget(self._path_b)
        row_b.addWidget(btn_b)
        form.addRow("File B:", row_b)

        self._trans_slider = QSlider(Qt.Horizontal)
        self._trans_slider.setRange(0, 95)
        self._trans_slider.setValue(80)
        form.addRow("Common transparency:", self._trans_slider)

        layout.addLayout(form)

        legend = QLabel(
            "<small>Gray = Common volume | Red = Removed (A−B) | Green = Added (B−A)</small>"
        )
        legend.setAlignment(Qt.AlignCenter)
        layout.addWidget(legend)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self, line_edit: QLineEdit):
        path, _ = QFileDialog.getOpenFileName(self, "Select CAD File", "", _FILE_FILTER)
        if path:
            line_edit.setText(path)

    def _validate(self):
        if not Path(self._path_a.text().strip()).is_file():
            QMessageBox.warning(self, "Invalid", "File A does not exist.")
            return
        if not Path(self._path_b.text().strip()).is_file():
            QMessageBox.warning(self, "Invalid", "File B does not exist.")
            return
        self.accept()

    @property
    def path_a(self) -> str:
        return self._path_a.text().strip()

    @property
    def path_b(self) -> str:
        return self._path_b.text().strip()

    @property
    def common_transparency(self) -> float:
        return self._trans_slider.value() / 100.0


# ---------------------------------------------------------------------------
#  Stylesheets
# ---------------------------------------------------------------------------

_DARK_QSS = """
QMainWindow, QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
}
QMenuBar {
    background-color: #181825;
    color: #cdd6f4;
    border-bottom: 1px solid #313244;
}
QMenuBar::item:selected {
    background-color: #313244;
}
QMenu {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #313244;
}
QMenu::item:selected {
    background-color: #45475a;
}
QToolBar {
    background-color: #181825;
    border-bottom: 1px solid #313244;
    spacing: 4px;
    padding: 2px;
}
QToolBar QToolButton, QToolBar QPushButton {
    background-color: transparent;
    color: #cdd6f4;
    border: none;
    padding: 4px 8px;
    border-radius: 4px;
}
QToolBar QToolButton:hover, QToolBar QPushButton:hover {
    background-color: #313244;
}
QDockWidget {
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
    color: #cdd6f4;
    font-weight: bold;
}
QDockWidget::title {
    background-color: #181825;
    padding: 6px;
    border-bottom: 1px solid #313244;
}
QTreeWidget {
    background-color: #11111b;
    color: #cdd6f4;
    border: 1px solid #313244;
    alternate-background-color: #181825;
}
QTreeWidget::item:selected {
    background-color: #45475a;
}
QTreeWidget::item:hover {
    background-color: #313244;
}
QListWidget {
    background-color: #11111b;
    color: #cdd6f4;
    border: 1px solid #313244;
    alternate-background-color: #181825;
}
QListWidget::item {
    padding: 4px 6px;
}
QListWidget::item:selected {
    background-color: #45475a;
}
QListWidget::item:hover {
    background-color: #313244;
}
QHeaderView::section {
    background-color: #181825;
    color: #a6adc8;
    border: none;
    padding: 4px;
    border-bottom: 1px solid #313244;
}
QLineEdit {
    background-color: #11111b;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 4px;
    padding: 4px 8px;
}
QLineEdit:focus {
    border-color: #89b4fa;
}
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 6px 14px;
}
QPushButton:hover {
    background-color: #45475a;
    border-color: #89b4fa;
}
QPushButton:pressed {
    background-color: #585b70;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #313244;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 14px;
    height: 14px;
    margin: -4px 0;
    background: #89b4fa;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: #89b4fa;
    border-radius: 3px;
}
QStatusBar {
    background-color: #181825;
    color: #a6adc8;
    border-top: 1px solid #313244;
}
QLabel {
    color: #cdd6f4;
}
QCheckBox {
    color: #cdd6f4;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #45475a;
    border-radius: 3px;
    background-color: #11111b;
}
QCheckBox::indicator:checked {
    background-color: #89b4fa;
    border-color: #89b4fa;
}
QComboBox {
    background-color: #11111b;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 4px;
    padding: 4px 8px;
}
QComboBox::drop-down {
    border: none;
}
QScrollArea {
    border: none;
}
QGroupBox {
    border: 1px solid #313244;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 12px;
    color: #cdd6f4;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
}
QTabWidget::pane {
    border: 1px solid #313244;
    background-color: #1e1e2e;
}
QTabBar::tab {
    background-color: #181825;
    color: #a6adc8;
    padding: 6px 14px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border-bottom: 2px solid #89b4fa;
}
"""

_LIGHT_QSS = """
QMainWindow, QWidget {
    background-color: #eff1f5;
    color: #4c4f69;
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
}
QMenuBar {
    background-color: #e6e9ef;
    color: #4c4f69;
    border-bottom: 1px solid #ccd0da;
}
QMenuBar::item:selected {
    background-color: #ccd0da;
}
QMenu {
    background-color: #eff1f5;
    color: #4c4f69;
    border: 1px solid #ccd0da;
}
QMenu::item:selected {
    background-color: #bcc0cc;
}
QToolBar {
    background-color: #e6e9ef;
    border-bottom: 1px solid #ccd0da;
    spacing: 4px;
    padding: 2px;
}
QToolBar QToolButton, QToolBar QPushButton {
    background-color: transparent;
    color: #4c4f69;
    border: none;
    padding: 4px 8px;
    border-radius: 4px;
}
QToolBar QToolButton:hover, QToolBar QPushButton:hover {
    background-color: #ccd0da;
}
QDockWidget {
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
    color: #4c4f69;
    font-weight: bold;
}
QDockWidget::title {
    background-color: #e6e9ef;
    padding: 6px;
    border-bottom: 1px solid #ccd0da;
}
QTreeWidget {
    background-color: #ffffff;
    color: #4c4f69;
    border: 1px solid #ccd0da;
    alternate-background-color: #f5f5f8;
}
QTreeWidget::item:selected {
    background-color: #bcc0cc;
}
QListWidget {
    background-color: #ffffff;
    color: #4c4f69;
    border: 1px solid #ccd0da;
    alternate-background-color: #f5f5f8;
}
QListWidget::item {
    padding: 4px 6px;
}
QListWidget::item:selected {
    background-color: #bcc0cc;
}
QListWidget::item:hover {
    background-color: #e6e9ef;
}
QHeaderView::section {
    background-color: #e6e9ef;
    color: #6c6f85;
    border: none;
    padding: 4px;
    border-bottom: 1px solid #ccd0da;
}
QLineEdit {
    background-color: #ffffff;
    color: #4c4f69;
    border: 1px solid #ccd0da;
    border-radius: 4px;
    padding: 4px 8px;
}
QLineEdit:focus {
    border-color: #1e66f5;
}
QPushButton {
    background-color: #ccd0da;
    color: #4c4f69;
    border: 1px solid #bcc0cc;
    border-radius: 4px;
    padding: 6px 14px;
}
QPushButton:hover {
    background-color: #bcc0cc;
    border-color: #1e66f5;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #ccd0da;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 14px;
    height: 14px;
    margin: -4px 0;
    background: #1e66f5;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: #1e66f5;
    border-radius: 3px;
}
QStatusBar {
    background-color: #e6e9ef;
    color: #6c6f85;
    border-top: 1px solid #ccd0da;
}
QCheckBox {
    color: #4c4f69;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #bcc0cc;
    border-radius: 3px;
    background-color: #ffffff;
}
QCheckBox::indicator:checked {
    background-color: #1e66f5;
    border-color: #1e66f5;
}
QComboBox {
    background-color: #ffffff;
    color: #4c4f69;
    border: 1px solid #ccd0da;
    border-radius: 4px;
    padding: 4px 8px;
}
QScrollArea {
    border: none;
}
QLabel {
    color: #4c4f69;
}
"""


# ---------------------------------------------------------------------------
#  CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Advanced CAD Viewer")
    parser.add_argument("file", nargs="?", default="", help="CAD file to open (STEP/IGES/BREP)")
    parser.add_argument("--step-a", default="", help="File A for diff mode")
    parser.add_argument("--step-b", default="", help="File B for diff mode")
    parser.add_argument("--commit-a", default="", help="Commit label for A")
    parser.add_argument("--commit-b", default="", help="Commit label for B")
    parser.add_argument("--common-transparency", type=float, default=0.80,
                        help="Common‑area transparency (0..0.98)")
    parser.add_argument("--part-id", type=int, default=None,
                        help="Part ID for face lifecycle tracking")
    parser.add_argument("--project-id", type=int, default=None,
                        help="Project ID for face lifecycle tracking")
    parser.add_argument("--db-path", default=None,
                        help="Path to SQLite database for lifecycle queries")
    parser.add_argument("--face-map-path", default=None,
                        help="Path to face_map.json for face-index-to-fingerprint lookup")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    viewer = AdvancedCADViewer(
        part_id=args.part_id,
        project_id=args.project_id,
        db_path=args.db_path,
        face_map_path=args.face_map_path,
    )
    viewer.show()

    # Decide mode
    if args.step_a and args.step_b:
        viewer.load_diff_and_show(
            args.step_a, args.step_b,
            commit_a=args.commit_a, commit_b=args.commit_b,
            transparency=args.common_transparency,
        )
    elif args.file:
        viewer.load_and_show(args.file)

    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
