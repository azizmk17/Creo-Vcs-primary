"""Plot saved native-Python median results for Nexus 4.4.0 and 4.5.0."""
import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.plot_runtime_comparison import Canvas


OLD = "#667085"
NEW = "#2864dc"


def create_chart(summary_path: Path, png_path: Path, svg_path: Path) -> None:
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = list(data["metrics"].items())
    canvas = Canvas()
    canvas.text(70, 54, "Nexus · Native Python version comparison", size="title")
    canvas.text(
        72, 113,
        "4.4.0 vs 4.5.0  ·  Python 3.10.19  ·  10,000 parts + 1,000 assemblies  ·  saved-run medians",
        size="regular", color="#475467",
    )

    canvas.point(90, 176, OLD, "circle")
    canvas.text(112, 164, "4.4.0 native Python", size="medium", color=OLD)
    canvas.point(355, 176, NEW, "square")
    canvas.text(377, 164, "4.5.0 native Python", size="medium", color=NEW)
    canvas.line(700, 176, 755, 176, "#475467", 3)
    canvas.line(700, 169, 700, 183, "#475467", 2)
    canvas.line(755, 169, 755, 183, "#475467", 2)
    canvas.text(770, 164, "min–max; marker is median", size="medium", color="#475467")

    x0, plot_width = 520, 830
    domain_min, domain_max = 0.15, 2.5
    log_span = math.log(domain_max) - math.log(domain_min)
    def xpos(value):
        value = min(domain_max, max(domain_min, value))
        return x0 + plot_width * (math.log(value) - math.log(domain_min)) / log_span

    native_x, current_x, change_x = 1400, 1680, 1980
    canvas.text(70, 230, "Median elapsed time by component", size="bold")
    canvas.text(native_x, 236, "4.4.0 · MEDIAN [RANGE]", size="small", color=OLD)
    canvas.text(current_x, 236, "4.5.0 · MEDIAN [RANGE]", size="small", color=NEW)
    canvas.text(change_x, 236, "4.5.0 CHANGE", size="small", color="#344054")

    top, gap = 300, 83
    axis_y = top + len(metrics) * gap - 7
    ticks = (0.2, 0.3, 0.5, 1.0, 2.0)
    for tick in ticks:
        x = xpos(tick)
        canvas.line(x, top-12, x, axis_y, "#d8dee8", 1)
        canvas.text(x, axis_y+10, f"{tick:g}", size="small", color="#475467", anchor="ma")

    def plot_measure(y, item, color, shape):
        median = item["median_seconds"]
        low, high = item["range_seconds"]
        left, right, center = xpos(low), xpos(high), xpos(median)
        canvas.line(left, y, right, y, color, 3)
        canvas.line(left, y-7, left, y+7, color, 2)
        canvas.line(right, y-7, right, y+7, color, 2)
        canvas.point(center, y, color, shape)

    for index, (key, record) in enumerate(metrics):
        y = top + index * gap
        previous, current = record["4.4.0"], record["4.5.0"]
        canvas.line(65, y+32, 2130, y+32, "#e8ecf2", 1)
        canvas.text(70, y-13, record["label"], size="regular")
        plot_measure(y-10, previous, OLD, "circle")
        plot_measure(y+11, current, NEW, "square")
        canvas.text(native_x, y-20, f"{previous['median_seconds']:.3f}s  [{previous['range_seconds'][0]:.3f}–{previous['range_seconds'][1]:.3f}]  n={previous['runs']}", size="small", color=OLD)
        canvas.text(current_x, y+1, f"{current['median_seconds']:.3f}s  [{current['range_seconds'][0]:.3f}–{current['range_seconds'][1]:.3f}]  n={current['runs']}", size="small", color=NEW)
        delta = record["4.5_vs_4.4_speed_change_percent"]
        canvas.text(change_x, y-5, f"{delta:+.1f}%", size="medium", color=NEW if delta >= 0 else "#b42318")

    canvas.text(x0 + plot_width/2, axis_y+36, "Median elapsed time (seconds) · log scale · lower is faster", size="small", color="#475467", anchor="ma")
    canvas.text(70, 1115, "How to read the change", size="bold")
    canvas.text(70, 1164, "+% means 4.5.0 took less time than 4.4.0. Negative means it took longer.", size="regular", color="#344054")
    canvas.text(70, 1210, "4.5.0 was faster in startup, service/BOM setup, row rendering and file sync; its working-directory integrity check was slower.", size="regular", color="#344054")
    canvas.text(70, 1270, "Source medians: 4.4.0 n=3 component runs; 4.5.0 n=9. Startup medians use 3 runs per version.", size="small", color="#475467")
    canvas.text(70, 1300, "Existing reports only. Saved batches used different benchmark revisions/cache states; treat comparisons as indicative, not controlled release qualification.", size="small", color="#475467")
    canvas.text(70, 1390, f"Fixture: {data['dataset_id']}  ·  project {data['project_id']}  ·  all plotted runs are native Python", size="small", color="#475467")
    canvas.save(png_path, svg_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=Path("docs/benchmarks/runtime_comparison_4_4_vs_4_5_native.json"))
    parser.add_argument("--png", type=Path, default=Path("docs/benchmarks/runtime_comparison_4_4_vs_4_5_native.png"))
    parser.add_argument("--svg", type=Path, default=Path("docs/benchmarks/runtime_comparison_4_4_vs_4_5_native.svg"))
    args = parser.parse_args()
    create_chart(args.summary, args.png, args.svg)
