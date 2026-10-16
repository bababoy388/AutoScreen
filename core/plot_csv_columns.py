#!/usr/bin/env python3
"""Plot configured CSV columns with per-column scaling ratios.

Usage:
    python3 plot_csv_columns.py plot_config.json /path/to/input.csv

The config file controls the x-axis, subplots, selected columns, per-column
ratios, GUI display, and optional output image path. Set "x_column" to
"row_number" to plot by CSV data-row number. See plot_config.json for an
example.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot selected CSV columns using a JSON config file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python3 plot_csv_columns.py plot_config.json data.csv"
        ),
    )
    parser.add_argument(
        "config",
        type=Path,
        help="Path to JSON config file.",
    )
    parser.add_argument(
        "csv_path",
        type=Path,
        help="Path to CSV file to plot.",
    )
    return parser.parse_args()


def normalize_subplots(config: dict[str, Any]) -> None:
    """Apply defaults and validate the subplot references used by series."""
    config.setdefault("share_x", True)
    config.setdefault("line_width", 1.0)
    config.setdefault("alpha", 1.0)
    config.setdefault("legend", True)
    config.setdefault("legend_location", "best")
    config.setdefault("legend_draggable", True)
    config.setdefault("click_legend_to_toggle", True)
    config.setdefault("y_axis_mode", "separate")

    subplots = config.get("subplots")
    if subplots is None or subplots == []:
        # Keep the short config useful: series can declare subplot ids without a
        # separate "subplots" block, and those ids are expanded here.
        subplot_ids: list[str] = []
        for series in config["series"]:
            subplot_id = str(series.get("subplot", "main"))
            if subplot_id not in subplot_ids:
                subplot_ids.append(subplot_id)
        subplots = [{"id": subplot_id} for subplot_id in subplot_ids]
        config["subplots"] = subplots

    if not isinstance(subplots, list):
        raise ValueError("Config 'subplots' must be a list when provided.")

    seen_subplot_ids: set[str] = set()
    for index, subplot in enumerate(subplots, start=1):
        if not isinstance(subplot, dict):
            raise ValueError(f"Subplot item {index} must be an object.")
        subplot.setdefault("id", "main" if len(subplots) == 1 else f"subplot_{index}")
        subplot_id = str(subplot["id"])
        if subplot_id in seen_subplot_ids:
            raise ValueError(f"Duplicate subplot id: {subplot_id}")
        seen_subplot_ids.add(subplot_id)
        subplot["id"] = subplot_id
        subplot.setdefault("title", config["title"] if len(subplots) == 1 else subplot_id)
        subplot.setdefault("y_axis_mode", config["y_axis_mode"])
        subplot.setdefault("y_label", config["y_label"])
        subplot.setdefault("grid", config["grid"])
        subplot.setdefault("legend", config["legend"])
        subplot.setdefault("legend_location", config["legend_location"])

    if not subplots:
        raise ValueError("Config must include at least one subplot.")

    default_subplot_id = str(subplots[0]["id"])
    for index, series in enumerate(config["series"], start=1):
        # Store a canonical subplot id on every series so plotting code does not
        # need to repeat default handling.
        series.setdefault("subplot", default_subplot_id)
        subplot_id = str(series["subplot"])
        if subplot_id not in seen_subplot_ids:
            raise ValueError(
                f"Series item {index} references unknown subplot '{subplot_id}'."
            )
        series["subplot"] = subplot_id


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    if "series" not in config or not config["series"]:
        raise ValueError("Config must include a non-empty 'series' list.")

    for index, series in enumerate(config["series"], start=1):
        if "column" not in series:
            raise ValueError(f"Series item {index} must include 'column'.")
        series.setdefault("ratio", 1.0)
        series.setdefault("label", series["column"])

    config.setdefault("x_column", "row_number")
    config.setdefault("delimiter", "auto")
    config.setdefault("output_path", "plot.png")
    config.setdefault("save_plot", True)
    config.setdefault("show_plot", False)
    config.setdefault("title", "Selected CSV columns")
    config.setdefault("x_label", config["x_column"])
    config.setdefault("y_label", "Value")
    config.setdefault("figsize", [12, 6])
    config.setdefault("dpi", 150)
    config.setdefault("max_points", None)
    config.setdefault("grid", True)
    normalize_subplots(config)
    return config


def uses_row_number_x(x_column: str) -> bool:
    return x_column.lower() in {"row_number", "row", "__row_number__"}


def sniff_delimiter(csv_path: Path, configured_delimiter: str) -> str:
    if configured_delimiter != "auto":
        return configured_delimiter

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    return dialect.delimiter


def parse_number(value: str, column_name: str, row_number: int) -> float:
    text = value.strip()
    if text == "":
        return math.nan
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(
            f"Column '{column_name}' contains a non-numeric value at row "
            f"{row_number}: {value!r}"
        ) from exc


def parse_x_value(value: str) -> Any:
    text = value.strip()
    if text == "":
        return math.nan

    try:
        return float(text)
    except ValueError:
        pass

    iso_text = text.removesuffix("Z")
    try:
        return datetime.fromisoformat(iso_text)
    except ValueError:
        return text


def load_plot_data(
    config: dict[str, Any],
    csv_path: Path,
) -> tuple[list[Any], list[dict[str, Any]]]:
    csv_path = csv_path.expanduser()
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    delimiter = sniff_delimiter(csv_path, str(config["delimiter"]))
    x_column = str(config["x_column"])
    use_row_number_x = uses_row_number_x(x_column)
    columns = [str(item["column"]) for item in config["series"]]
    # A CSV column can be plotted more than once with different ratios/styles,
    # but header validation only needs to check each source column once.
    unique_columns = list(dict.fromkeys(columns))
    ratios = [float(item["ratio"]) for item in config["series"]]

    x_values: list[Any] = []
    series_values: list[list[float]] = [[] for _ in config["series"]]
    max_points = config.get("max_points")
    if max_points is not None:
        max_points = int(max_points)
        if max_points <= 0:
            raise ValueError("'max_points' must be a positive integer or null.")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if reader.fieldnames is None:
            raise ValueError("CSV file has no header row.")

        required_columns = (
            unique_columns if use_row_number_x else [x_column, *unique_columns]
        )
        missing = [name for name in required_columns if name not in reader.fieldnames]
        if missing:
            available = ", ".join(reader.fieldnames)
            raise ValueError(
                "CSV file is missing configured column(s): "
                f"{', '.join(missing)}. Available columns: {available}"
            )

        for data_row_number, row in enumerate(reader, start=1):
            if max_points is not None and len(x_values) >= max_points:
                break
            # csv_row_number is the physical file row number, including the
            # header, which makes validation errors easier to locate.
            csv_row_number = data_row_number + 1
            if use_row_number_x:
                x_values.append(data_row_number)
            else:
                x_values.append(parse_x_value(row[x_column]))
            for series_index, column in enumerate(columns):
                series_values[series_index].append(
                    parse_number(row[column], column, csv_row_number)
                    * ratios[series_index]
                )

    if not x_values:
        raise ValueError("No data rows were found in the CSV file.")

    series_data = [
        {"config": series_config, "values": values}
        for series_config, values in zip(config["series"], series_values)
    ]
    return x_values, series_data


def uses_separate_y_axes(subplot_config: dict[str, Any]) -> bool:
    mode = str(subplot_config.get("y_axis_mode", "separate")).lower()
    if mode in {"separate", "independent", "per_series", "per-series", "own"}:
        return True
    if mode in {"shared", "same", "single"}:
        return False
    raise ValueError(
        "Subplot 'y_axis_mode' must be 'separate' or 'shared', "
        f"got {mode!r}."
    )


def series_label(series_config: dict[str, Any]) -> str:
    label = str(series_config.get("label", series_config["column"]))
    ratio = float(series_config.get("ratio", 1.0))
    show_ratio = bool(series_config.get("show_ratio_in_legend", ratio != 1.0))
    if show_ratio and ratio != 1.0:
        return f"{label} x {ratio:g}"
    return label


def next_color(plt: Any, color_index: int) -> tuple[str, int]:
    colors = plt.rcParams["axes.prop_cycle"].by_key().get("color", ["C0"])
    return colors[color_index % len(colors)], color_index + 1


def plot_series_line(
    ax: Any,
    x_values: list[Any],
    values: list[float],
    series_config: dict[str, Any],
    config: dict[str, Any],
    color: str,
) -> Any:
    line_kwargs: dict[str, Any] = {
        "label": series_label(series_config),
        "linewidth": float(series_config.get("line_width", config["line_width"])),
        "alpha": float(series_config.get("alpha", config["alpha"])),
        "color": color,
    }
    for option in ("linestyle", "marker", "markersize"):
        if option in series_config:
            line_kwargs[option] = series_config[option]
    return ax.plot(x_values, values, **line_kwargs)[0]


def get_legend_handles(legend: Any) -> list[Any]:
    # Matplotlib renamed this attribute across versions; keep compatibility with
    # both old and new installations.
    handles = getattr(legend, "legend_handles", None)
    if handles is None:
        handles = getattr(legend, "legendHandles", None)
    if handles is None:
        handles = legend.get_lines()
    return list(handles)


def connect_clickable_legends(fig: Any, toggle_items: list[dict[str, Any]]) -> None:
    """Bind legend artists to their source lines for click-to-toggle behavior."""
    pick_map: dict[Any, dict[str, Any]] = {}
    for item in toggle_items:
        for artist in item["legend_artists"]:
            artist.set_picker(True)
            if hasattr(artist, "set_pickradius"):
                artist.set_pickradius(8)
            pick_map[artist] = item

    if not pick_map:
        return

    def on_pick(event: Any) -> None:
        item = pick_map.get(event.artist)
        if item is None:
            return

        visible = not item["line"].get_visible()
        item["line"].set_visible(visible)

        legend_alpha = 1.0 if visible else 0.25
        for artist in item["legend_artists"]:
            artist.set_alpha(legend_alpha)

        if item["hide_axis"]:
            axis = item["axis"]
            axis.yaxis.set_visible(visible)
            axis.spines[item["spine"]].set_visible(visible)

        fig.canvas.draw_idle()

    callback_id = fig.canvas.mpl_connect("pick_event", on_pick)
    # Keep strong references on the figure. Some GUI backends can drop callbacks
    # if the only remaining references live in local variables.
    fig._legend_toggle_callback_id = callback_id
    fig._legend_toggle_pick_map = pick_map


def configure_axis_color(ax: Any, spine: str, color: str) -> None:
    ax.yaxis.label.set_color(color)
    ax.tick_params(axis="y", colors=color)
    ax.spines[spine].set_color(color)


def create_extra_y_axis(base_ax: Any, axis_index: int) -> tuple[Any, str]:
    if axis_index == 0:
        base_ax.spines["right"].set_visible(False)
        return base_ax, "left"

    # Each extra series on the same subplot gets a twinx axis. Offset right-side
    # spines so tick labels do not overlap when there are several y-axes.
    ax = base_ax.twinx()
    spine_offset = 1.0 + 0.1 * (axis_index - 1)
    ax.spines["right"].set_position(("axes", spine_offset))
    ax.spines["right"].set_visible(True)
    ax.spines["left"].set_visible(False)
    ax.patch.set_visible(False)
    return ax, "right"


def group_series_by_subplot(
    config: dict[str, Any],
    series_data: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {
        str(subplot["id"]): [] for subplot in config["subplots"]
    }
    for item in series_data:
        grouped[str(item["config"]["subplot"])].append(item)
    return grouped


def make_plot(
    config: dict[str, Any],
    x_values: list[Any],
    series_data: list[dict[str, Any]],
) -> tuple[Path | None, bool]:
    save_plot = bool(config["save_plot"])
    show_plot = bool(config["show_plot"])
    if not save_plot and not show_plot:
        raise ValueError("At least one of 'save_plot' or 'show_plot' must be true.")

    output_path = Path(config["output_path"]).expanduser()
    cache_base = output_path.parent if save_plot else Path(__file__).resolve().parent
    mpl_config_dir = cache_base / ".matplotlib-cache"
    mpl_config_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_config_dir))

    try:
        if not show_plot:
            import matplotlib

            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError(
            "matplotlib is required for plotting. Install it with "
            "'python3 -m pip install matplotlib' and rerun the script."
        ) from exc

    if save_plot:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    subplots = config["subplots"]
    height_ratios = [float(subplot.get("height_ratio", 1.0)) for subplot in subplots]
    fig, axes_grid = plt.subplots(
        nrows=len(subplots),
        ncols=1,
        figsize=tuple(config["figsize"]),
        dpi=int(config["dpi"]),
        sharex=bool(config["share_x"]),
        squeeze=False,
        gridspec_kw={"height_ratios": height_ratios},
    )
    base_axes = [row[0] for row in axes_grid]

    if len(subplots) > 1 and config.get("title"):
        fig.suptitle(str(config["title"]))

    manager = getattr(fig.canvas, "manager", None)
    if manager is not None and hasattr(manager, "set_window_title"):
        manager.set_window_title(str(config.get("window_title", config["title"])))

    grouped_series = group_series_by_subplot(config, series_data)
    legend_toggle_items: list[dict[str, Any]] = []
    color_index = 0
    max_y_axes_on_subplot = 1

    for subplot_index, (subplot, base_ax) in enumerate(zip(subplots, base_axes)):
        subplot_id = str(subplot["id"])
        subplot_series = grouped_series[subplot_id]
        separate_y_axes = uses_separate_y_axes(subplot)
        max_y_axes_on_subplot = max(
            max_y_axes_on_subplot,
            len(subplot_series) if separate_y_axes else 1,
        )

        if subplot.get("title"):
            base_ax.set_title(str(subplot["title"]))
        base_ax.grid(bool(subplot["grid"]), alpha=0.3)

        is_bottom_subplot = subplot_index == len(subplots) - 1
        if is_bottom_subplot or not bool(config["share_x"]):
            base_ax.set_xlabel(str(config["x_label"]))

        if not subplot_series:
            base_ax.text(
                0.5,
                0.5,
                f"No series assigned to subplot '{subplot_id}'",
                ha="center",
                va="center",
                transform=base_ax.transAxes,
            )
            continue

        lines: list[Any] = []
        line_axes: list[tuple[Any, bool, str]] = []

        for series_index, item in enumerate(subplot_series):
            series_config = item["config"]
            color = series_config.get("color")
            if color is None:
                color, color_index = next_color(plt, color_index)

            if separate_y_axes:
                axis, spine = create_extra_y_axis(base_ax, series_index)
            else:
                axis, spine = base_ax, "left"

            line = plot_series_line(
                axis,
                x_values,
                item["values"],
                series_config,
                config,
                str(color),
            )
            lines.append(line)
            line_axes.append((axis, separate_y_axes, spine))

            if separate_y_axes:
                axis_label = str(series_config.get("y_label", line.get_label()))
                axis.set_ylabel(axis_label)
                configure_axis_color(axis, spine, line.get_color())

        if not separate_y_axes:
            base_ax.set_ylabel(str(subplot.get("y_label", config["y_label"])))

        if bool(subplot["legend"]) and lines:
            # Attach the legend to the topmost axis. With twinx(), the top axis
            # receives GUI pick events first; placing the legend there keeps
            # legend toggles working when a subplot has multiple y-axes.
            legend_axis = line_axes[-1][0]
            legend = legend_axis.legend(
                handles=lines,
                labels=[line.get_label() for line in lines],
                loc=str(subplot["legend_location"]),
            )
            legend.set_zorder(1000)
            if bool(config["legend_draggable"]):
                legend.set_draggable(True)

            if bool(config["click_legend_to_toggle"]):
                legend_handles = get_legend_handles(legend)
                legend_texts = legend.get_texts()
                for line, (axis, hide_axis, spine), handle, text in zip(
                    lines,
                    line_axes,
                    legend_handles,
                    legend_texts,
                ):
                    legend_toggle_items.append(
                        {
                            "line": line,
                            "axis": axis,
                            "spine": spine,
                            "hide_axis": hide_axis,
                            "legend_artists": [handle, text],
                        }
                    )

    if bool(config["click_legend_to_toggle"]):
        connect_clickable_legends(fig, legend_toggle_items)

    if x_values and isinstance(x_values[0], datetime):
        fig.autofmt_xdate()

    # Leave extra right margin for additional y-axes created with twinx().
    right_edge = max(0.62, 0.96 - 0.08 * max(0, max_y_axes_on_subplot - 2))
    top_edge = 0.94 if len(subplots) > 1 and config.get("title") else 1.0
    fig.tight_layout(rect=[0, 0, right_edge, top_edge])
    if save_plot:
        fig.savefig(output_path)
    if show_plot:
        plt.show()
    plt.close(fig)
    return output_path if save_plot else None, show_plot


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config)
        x_values, series_data = load_plot_data(config, args.csv_path)
        output_path, showed_plot = make_plot(config, x_values, series_data)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    row_count = len(x_values)
    plotted_columns = ", ".join(
        str(item["config"]["column"]) for item in series_data
    )
    print(f"Plotted {row_count:,} rows for columns: {plotted_columns}")
    if output_path is not None:
        print(f"Saved plot to: {output_path}")
    if showed_plot:
        print("Displayed plot in matplotlib GUI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
