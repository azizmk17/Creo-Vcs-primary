"""Render the 4.5.0 native-Python vs frozen-EXE comparison chart."""
import argparse
from html import escape
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 2200, 1460
BG = "#fbfcfe"
INK = "#101828"
MUTED = "#475467"
GRID = "#d8dee8"
SOURCE = "#2864dc"
FROZEN = "#d35b26"


class Canvas:
    def __init__(self):
        self.image = Image.new("RGB", (WIDTH, HEIGHT), BG)
        self.draw = ImageDraw.Draw(self.image)
        self.svg = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
            f'<rect width="100%" height="100%" fill="{BG}"/>',
            '<style>text{font-family:"Segoe UI",Arial,sans-serif}</style>',
        ]
        self.regular = ImageFont.truetype(r"C:\Windows\Fonts\segoeui.ttf", 18)
        self.small = ImageFont.truetype(r"C:\Windows\Fonts\segoeui.ttf", 16)
        self.medium = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 20)
        self.bold = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 30)
        self.title = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 40)

    def text(self, x, y, value, *, size="regular", color=INK, anchor="la"):
        font = getattr(self, size)
        self.draw.text((x, y), value, font=font, fill=color, anchor=anchor)
        font_size = {"small": 16, "regular": 18, "medium": 20, "bold": 30, "title": 40}[size]
        self.svg.append(
            f'<text x="{x}" y="{y}" fill="{color}" font-size="{font_size}" '
            f'text-anchor="{("middle" if anchor == "mm" else "end" if "r" in anchor else "start")}" '
            f'dominant-baseline="{("middle" if "m" in anchor else "hanging")}">{escape(value)}</text>'
        )

    def line(self, x1, y1, x2, y2, color=GRID, width=1):
        self.draw.line((x1, y1, x2, y2), fill=color, width=width)
        self.svg.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}"/>'
        )

    def point(self, x, y, color, shape):
        radius = 8
        if shape == "circle":
            self.draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color)
            self.svg.append(f'<circle cx="{x}" cy="{y}" r="{radius}" fill="{color}"/>')
        else:
            self.draw.rectangle((x-radius, y-radius, x+radius, y+radius), fill=color)
            self.svg.append(f'<rect x="{x-radius}" y="{y-radius}" width="{2*radius}" height="{2*radius}" fill="{color}"/>')

    def range_mark(self, x0, width, xmax, y, summary, color, shape):
        median = summary["median_seconds"]
        low, high = summary["range_seconds"]
        left = x0 + width * low / xmax
        right = x0 + width * high / xmax
        center = x0 + width * median / xmax
        self.line(left, y, right, y, color, 3)
        self.line(left, y-7, left, y+7, color, 2)
        self.line(right, y-7, right, y+7, color, 2)
        self.point(center, y, color, shape)

    def save(self, png: Path, svg: Path):
        self.svg.append("</svg>")
        png.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(png, dpi=(180, 180), optimize=True)
        svg.write_text("\n".join(self.svg), encoding="utf-8")


def create_chart(summary_path: Path, png_path: Path, svg_path: Path) -> None:
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    components = data["project_load_components"]
    startup = data["startup_to_first_login_paint"]
    rows = [
        ("First login paint", startup["native_python"], startup["frozen_exe"]),
        ("Service setup", components["service_setup_seconds"]["native_python"], components["service_setup_seconds"]["frozen_exe"]),
        ("EBOM structure worker", components["structure_worker_seconds"]["native_python"], components["structure_worker_seconds"]["frozen_exe"]),
        ("BOM widget setup", components["bom_widget_setup_seconds"]["native_python"], components["bom_widget_setup_seconds"]["frozen_exe"]),
        ("Initial BOM row rendering", components["initial_row_creation_seconds"]["native_python"], components["initial_row_creation_seconds"]["frozen_exe"]),
        ("BOM refresh rendering", components["bom_refresh_update_seconds"]["native_python"], components["bom_refresh_update_seconds"]["frozen_exe"]),
        ("File sync · 11,000 Items", components["file_sync_seconds"]["native_python"], components["file_sync_seconds"]["frozen_exe"]),
        ("Working-directory check", components["working_directory_check_seconds"]["native_python"], components["working_directory_check_seconds"]["frozen_exe"]),
    ]
    canvas = Canvas()
    canvas.text(70, 54, "Nexus 4.5.0 · Native Python vs frozen EXE", size="title")
    canvas.text(
        72, 113,
        "Python 3.10.19  ·  10,000 parts + 1,000 assemblies  ·  local warm-cache runs  ·  medians with min–max ranges",
        size="regular", color=MUTED,
    )

    canvas.point(88, 176, SOURCE, "circle")
    canvas.text(110, 164, "Native Python", size="medium", color=SOURCE)
    canvas.point(300, 176, FROZEN, "square")
    canvas.text(322, 164, "Frozen EXE", size="medium", color=FROZEN)
    canvas.line(520, 176, 575, 176, MUTED, 3)
    canvas.line(520, 169, 520, 183, MUTED, 2)
    canvas.line(575, 169, 575, 183, MUTED, 2)
    canvas.text(590, 164, "min–max range; marker is median", size="medium", color=MUTED)

    plot_x, plot_w, plot_max = 535, 960, 1.6
    native_text_x, frozen_text_x = 1545, 1865
    canvas.text(70, 230, "Startup, project checks and BOM GUI", size="bold")
    canvas.text(native_text_x, 236, "NATIVE · MEDIAN [RANGE]", size="small", color=SOURCE)
    canvas.text(frozen_text_x, 236, "FROZEN · MEDIAN [RANGE]", size="small", color=FROZEN)

    top_y, row_gap = 294, 75
    axis_y = top_y + len(rows) * row_gap - 6
    for tick in (0, 0.4, 0.8, 1.2, 1.6):
        x = plot_x + plot_w * tick / plot_max
        canvas.line(x, top_y-12, x, axis_y, GRID, 1)
        canvas.text(x, axis_y+11, f"{tick:.1f}", size="small", color=MUTED, anchor="ma")
    for index, (label, native, frozen) in enumerate(rows):
        center_y = top_y + index * row_gap
        canvas.line(65, center_y+30, 2115, center_y+30, "#e8ecf2", 1)
        canvas.text(70, center_y-14, label, size="regular")
        native_y, frozen_y = center_y-10, center_y+11
        canvas.range_mark(plot_x, plot_w, plot_max, native_y, native, SOURCE, "circle")
        canvas.range_mark(plot_x, plot_w, plot_max, frozen_y, frozen, FROZEN, "square")
        canvas.text(native_text_x, native_y-10, f"{native['median_seconds']:.3f}s  [{native['range_seconds'][0]:.3f}–{native['range_seconds'][1]:.3f}]", size="small", color=SOURCE)
        canvas.text(frozen_text_x, frozen_y-10, f"{frozen['median_seconds']:.3f}s  [{frozen['range_seconds'][0]:.3f}–{frozen['range_seconds'][1]:.3f}]", size="small", color=FROZEN)
    canvas.text(plot_x + plot_w/2, axis_y+36, "Elapsed time (seconds) · lower is faster", size="small", color=MUTED, anchor="ma")

    metadata = components["metadata_update_seconds"]
    panel_y = 1010
    canvas.text(70, panel_y, "Forced metadata update · 11,000 rows · disposable database", size="bold")
    canvas.text(native_text_x, panel_y+7, "NATIVE · MEDIAN [RANGE]", size="small", color=SOURCE)
    canvas.text(frozen_text_x, panel_y+7, "FROZEN · MEDIAN [RANGE]", size="small", color=FROZEN)
    meta_x, meta_w, meta_max = plot_x, plot_w, 21.0
    meta_top, meta_gap = panel_y+96, 62
    meta_axis_y = meta_top + 2*meta_gap
    for tick in (0, 5, 10, 15, 20):
        x = meta_x + meta_w * tick / meta_max
        canvas.line(x, meta_top-20, x, meta_axis_y, GRID, 1)
        canvas.text(x, meta_axis_y+8, str(tick), size="small", color=MUTED, anchor="ma")
    for i, (label, key, color, shape, x_text) in enumerate([
        ("Native Python", "native_python", SOURCE, "circle", native_text_x),
        ("Frozen EXE", "frozen_exe", FROZEN, "square", frozen_text_x),
    ]):
        y = meta_top + i*meta_gap
        measure = metadata[key]
        canvas.text(70, y-10, label, size="regular", color=color)
        canvas.range_mark(meta_x, meta_w, meta_max, y, measure, color, shape)
        canvas.text(x_text, y-10, f"{measure['median_seconds']:.3f}s  [{measure['range_seconds'][0]:.3f}–{measure['range_seconds'][1]:.3f}]", size="small", color=color)
    canvas.text(meta_x + meta_w/2, meta_axis_y+32, "Elapsed time (seconds) · lower is faster", size="small", color=MUTED, anchor="ma")
    canvas.text(70, 1390, "Normal sync: 11,000 checked · 0 writes. Forced-write runs varied substantially; ranges are shown.", size="regular", color=MUTED)
    canvas.text(70, 1420, f"Bundle: 898.5 MB  ·  Fixture: {data['dataset_id']}  ·  Native runs: 9  ·  Frozen runs: 7", size="small", color=MUTED)
    canvas.save(png_path, svg_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=Path("docs/benchmarks/runtime_comparison_4_5_0.json"))
    parser.add_argument("--png", type=Path, default=Path("docs/benchmarks/runtime_comparison_4_5_0.png"))
    parser.add_argument("--svg", type=Path, default=Path("docs/benchmarks/runtime_comparison_4_5_0.svg"))
    args = parser.parse_args()
    create_chart(args.summary, args.png, args.svg)
