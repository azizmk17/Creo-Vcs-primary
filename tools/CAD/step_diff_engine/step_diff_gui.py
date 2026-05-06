from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import tkinter as tk
from tkinter import filedialog, messagebox

from OCC.Display.SimpleGui import init_display
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.gp import gp_Trsf, gp_Vec
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import Face

from .diff_engine import compare_models
from .geometry_fingerprint import fingerprint_model
from .step_parser import ModelGeometry, StepParseError, parse_step_file


@dataclass(slots=True)
class LoadedModel:
    path: str
    commit_id: str
    model: ModelGeometry
    shape: object
    faces: list[object]


def _load_shape(step_path: str) -> object:
    reader = STEPControl_Reader()
    status = reader.ReadFile(step_path)
    if status != IFSelect_RetDone:
        raise StepParseError(f"Failed to read STEP file: {step_path}")
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        raise StepParseError(f"No geometry loaded from STEP file: {step_path}")
    return shape


def _extract_faces(shape: object) -> list[object]:
    result: list[object] = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        result.append(Face(exp.Current()))
        exp.Next()
    return result


def _load_model(step_path: str, commit_id: str) -> LoadedModel:
    model = parse_step_file(step_path, commit_id=commit_id)
    shape = _load_shape(step_path)
    faces = _extract_faces(shape)
    return LoadedModel(path=step_path, commit_id=commit_id, model=model, shape=shape, faces=faces)


def _bbox_width_x(shape_a: object, shape_b: object) -> float:
    box = Bnd_Box()
    brepbndlib.Add(shape_a, box)
    brepbndlib.Add(shape_b, box)
    xmin, _, _, xmax, _, _ = box.Get()
    width = float(xmax - xmin)
    if width <= 0:
        return 100.0
    return width


def _transformed(shape: object, trsf: gp_Trsf) -> object:
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def _index_set(items: list[dict], key: str = "index") -> set[int]:
    indices: set[int] = set()
    for item in items:
        face = item.get("face", {})
        idx = face.get(key)
        if isinstance(idx, int):
            indices.add(idx)
    return indices


def _build_modified_index_sets(model_a: ModelGeometry, model_b: ModelGeometry, modified_items: list[dict]) -> tuple[set[int], set[int]]:
    mod_a: set[int] = set()
    mod_b: set[int] = set()

    fp_a = fingerprint_model(model_a)
    fp_b = fingerprint_model(model_b)

    by_fp_a: dict[str, list[int]] = {}
    by_fp_b: dict[str, list[int]] = {}

    for it in fp_a:
        by_fp_a.setdefault(it.fingerprint, []).append(it.face.index)
    for it in fp_b:
        by_fp_b.setdefault(it.fingerprint, []).append(it.face.index)

    for item in modified_items:
        before_fp = item.get("before_fingerprint")
        after_fp = item.get("after_fingerprint")
        if isinstance(before_fp, str):
            for idx in by_fp_a.get(before_fp, []):
                mod_a.add(idx)
        if isinstance(after_fp, str):
            for idx in by_fp_b.get(after_fp, []):
                mod_b.add(idx)

    return mod_a, mod_b


def visualize_diff(model_a: LoadedModel, model_b: LoadedModel, common_transparency: float = 0.80) -> None:
    common_transparency = max(0.0, min(0.98, float(common_transparency)))

    display, start_display, _, _ = init_display()

    common_shape = None
    removed_shape = None
    added_shape = None

    try:
        common_op = BRepAlgoAPI_Common(model_a.shape, model_b.shape)
        if common_op.IsDone():
            common_shape = common_op.Shape()
    except Exception:
        common_shape = None

    try:
        removed_op = BRepAlgoAPI_Cut(model_a.shape, model_b.shape)
        if removed_op.IsDone():
            removed_shape = removed_op.Shape()
    except Exception:
        removed_shape = None

    try:
        added_op = BRepAlgoAPI_Cut(model_b.shape, model_a.shape)
        if added_op.IsDone():
            added_shape = added_op.Shape()
    except Exception:
        added_shape = None

    def _valid_shape(shape_obj) -> bool:
        return shape_obj is not None and (not hasattr(shape_obj, "IsNull") or not shape_obj.IsNull())

    drawn_any = False

    if _valid_shape(common_shape):
        display.DisplayShape(common_shape, color="GRAY", transparency=common_transparency, update=False)
        drawn_any = True

    if _valid_shape(removed_shape):
        display.DisplayShape(removed_shape, color="RED", transparency=0.0, update=False)
        drawn_any = True

    if _valid_shape(added_shape):
        display.DisplayShape(added_shape, color="GREEN", transparency=0.0, update=False)
        drawn_any = True

    if not drawn_any:
        diff = compare_models(model_a.model, model_b.model)
        removed_a = _index_set(diff.removed_surfaces)
        added_b = _index_set(diff.added_surfaces)
        modified_a, modified_b = _build_modified_index_sets(
            model_a.model,
            model_b.model,
            [m.__dict__ for m in diff.modified_surfaces],
        )

        for idx, face in enumerate(model_a.faces):
            if idx in (set(removed_a) | set(modified_a)):
                display.DisplayShape(face, color="RED", transparency=0.0, update=False)

        for idx, face in enumerate(model_b.faces):
            if idx in (set(added_b) | set(modified_b)):
                display.DisplayShape(face, color="GREEN", transparency=0.0, update=False)

    display.FitAll()
    start_display()


class StepDiffGui:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("STEP Diff Visualizer")
        self.root.geometry("760x260")

        self.path_a_var = tk.StringVar()
        self.path_b_var = tk.StringVar()
        self.commit_a_var = tk.StringVar(value="step_a")
        self.commit_b_var = tk.StringVar(value="step_b")
        self.common_transparency_var = tk.DoubleVar(value=0.80)

        self._build_ui()

    def _build_ui(self) -> None:
        frm = tk.Frame(self.root, padx=12, pady=12)
        frm.pack(fill=tk.BOTH, expand=True)

        tk.Label(frm, text="STEP A").grid(row=0, column=0, sticky="w")
        tk.Entry(frm, textvariable=self.path_a_var, width=72).grid(row=1, column=0, sticky="we")
        tk.Button(frm, text="Browse", command=self._pick_a).grid(row=1, column=1, padx=(8, 0))

        tk.Label(frm, text="STEP B").grid(row=2, column=0, sticky="w", pady=(10, 0))
        tk.Entry(frm, textvariable=self.path_b_var, width=72).grid(row=3, column=0, sticky="we")
        tk.Button(frm, text="Browse", command=self._pick_b).grid(row=3, column=1, padx=(8, 0))

        meta = tk.Frame(frm)
        meta.grid(row=4, column=0, columnspan=2, sticky="we", pady=(12, 0))

        tk.Label(meta, text="Commit A").grid(row=0, column=0, sticky="w")
        tk.Entry(meta, textvariable=self.commit_a_var, width=24).grid(row=0, column=1, padx=(8, 20), sticky="w")
        tk.Label(meta, text="Commit B").grid(row=0, column=2, sticky="w")
        tk.Entry(meta, textvariable=self.commit_b_var, width=24).grid(row=0, column=3, padx=(8, 0), sticky="w")

        tk.Label(meta, text="Common Gray Transparency").grid(row=1, column=0, sticky="w", pady=(8, 0))
        tk.Scale(
            meta,
            from_=0,
            to=95,
            orient=tk.HORIZONTAL,
            variable=self.common_transparency_var,
            resolution=1,
            length=220,
        ).grid(row=1, column=1, columnspan=3, sticky="w", pady=(8, 0))

        tk.Button(
            frm,
            text="Load + Highlight Differences",
            command=self._run,
            bg="#2d7ff9",
            fg="white",
            padx=12,
            pady=6,
        ).grid(row=5, column=0, columnspan=2, pady=(16, 0), sticky="we")

        legend = tk.Label(
            frm,
            text="Legend: Gray=Common volume, Red=Removed volume (A-B), Green=Added volume (B-A)",
            fg="#555",
        )
        legend.grid(row=6, column=0, columnspan=2, pady=(8, 0), sticky="w")

        frm.columnconfigure(0, weight=1)

    def _pick_a(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("STEP files", "*.step *.stp")])
        if path:
            self.path_a_var.set(path)

    def _pick_b(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("STEP files", "*.step *.stp")])
        if path:
            self.path_b_var.set(path)

    def _run(self) -> None:
        path_a = self.path_a_var.get().strip()
        path_b = self.path_b_var.get().strip()
        commit_a = self.commit_a_var.get().strip() or "step_a"
        commit_b = self.commit_b_var.get().strip() or "step_b"

        if not Path(path_a).is_file() or not Path(path_b).is_file():
            messagebox.showerror("Invalid input", "Select two valid STEP files first.")
            return

        try:
            model_a = _load_model(path_a, commit_a)
            model_b = _load_model(path_b, commit_b)
        except Exception as exc:
            messagebox.showerror("Load error", str(exc))
            return

        try:
            common_transparency = float(self.common_transparency_var.get()) / 100.0
            visualize_diff(model_a, model_b, common_transparency=common_transparency)
        except Exception as exc:
            messagebox.showerror("Visualization error", str(exc))

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description="STEP diff visualizer")
    parser.add_argument("--step-a", default="", help="Previous STEP path")
    parser.add_argument("--step-b", default="", help="Current STEP path")
    parser.add_argument("--commit-a", default="step_a", help="Commit label for STEP A")
    parser.add_argument("--commit-b", default="step_b", help="Commit label for STEP B")
    parser.add_argument(
        "--common-transparency",
        type=float,
        default=0.80,
        help="Gray transparency for common areas (0.0 opaque to 0.98 very transparent)",
    )
    args = parser.parse_args()

    if args.step_a and args.step_b:
        model_a = _load_model(args.step_a, args.commit_a or "step_a")
        model_b = _load_model(args.step_b, args.commit_b or "step_b")
        visualize_diff(model_a, model_b, common_transparency=args.common_transparency)
        return 0

    app = StepDiffGui()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
