"""Minimal SVG charting, written here rather than pulled in.

The repository has no runtime dependencies and a chart is a few hundred lines
of coordinate arithmetic. Colours are chosen to stay legible against both the
light and the dark GitHub themes, so the figures work wherever the README is
read.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

PALETTE = [
    "#2c6fbb",
    "#c8582a",
    "#3f8f5f",
    "#8b5cb8",
    "#b8933f",
    "#4aa3a3",
]

BACKGROUND = "#ffffff"
GRID = "#d8dee6"
AXIS = "#5a6472"
TEXT = "#1f2733"
MUTED = "#6b7684"

_FONT = "font-family='ui-sans-serif,-apple-system,Segoe UI,Helvetica,Arial,sans-serif'"


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _nice_ticks(low: float, high: float, count: int = 5) -> List[float]:
    if high <= low:
        high = low + 1.0
    span = high - low
    raw = span / max(count, 1)
    magnitude = 10.0 ** _floor_log10(raw)
    for step in (1.0, 2.0, 2.5, 5.0, 10.0):
        if raw <= step * magnitude:
            interval = step * magnitude
            break
    else:
        interval = 10.0 * magnitude
    start = interval * int(low / interval)
    if start > low:
        start -= interval
    ticks = []
    value = start
    while value <= high + interval * 0.5:
        ticks.append(round(value, 10))
        value += interval
    return ticks


def _floor_log10(value: float) -> int:
    exponent = 0
    if value <= 0:
        return 0
    while value < 1.0:
        value *= 10.0
        exponent -= 1
    while value >= 10.0:
        value /= 10.0
        exponent += 1
    return exponent


def _format(value: float) -> str:
    if abs(value) >= 1000:
        return "{0:,.0f}".format(value)
    if abs(value) >= 10:
        return "{0:.0f}".format(value)
    if abs(value) >= 1:
        return "{0:.1f}".format(value)
    if value == 0:
        return "0"
    return "{0:.3g}".format(value)


class Canvas:
    def __init__(self, width: int, height: int, title: str = "", subtitle: str = ""):
        self.width = width
        self.height = height
        self.parts: List[str] = []
        self.title = title
        self.subtitle = subtitle

    def add(self, markup: str) -> None:
        self.parts.append(markup)

    def render(self) -> str:
        head = [
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {0} {1}' "
            "width='{0}' height='{1}' role='img'>".format(self.width, self.height),
            "<rect width='{0}' height='{1}' fill='{2}'/>".format(
                self.width, self.height, BACKGROUND
            ),
        ]
        if self.title:
            head.append(
                "<text x='24' y='30' {0} font-size='16' font-weight='600' fill='{1}'>{2}</text>".format(
                    _FONT, TEXT, _escape(self.title)
                )
            )
        if self.subtitle:
            head.append(
                "<text x='24' y='50' {0} font-size='12' fill='{1}'>{2}</text>".format(
                    _FONT, MUTED, _escape(self.subtitle)
                )
            )
        return "\n".join(head + self.parts + ["</svg>"]) + "\n"


def line_chart(
    series: Sequence[Tuple[str, Sequence[float], Sequence[float]]],
    title: str = "",
    subtitle: str = "",
    x_label: str = "",
    y_label: str = "",
    width: int = 900,
    height: int = 420,
    bands: Optional[Sequence[Tuple[float, float, str]]] = None,
    hlines: Optional[Sequence[Tuple[float, str]]] = None,
) -> str:
    """One or more (name, xs, ys) traces on a shared pair of axes."""
    left, right, top, bottom = 70, 190, 72, 56
    plot_w = width - left - right
    plot_h = height - top - bottom
    canvas = Canvas(width, height, title, subtitle)

    xs_all = [x for _n, xs, _ys in series for x in xs]
    ys_all = [y for _n, _xs, ys in series for y in ys]
    if not xs_all or not ys_all:
        return canvas.render()
    x_lo, x_hi = min(xs_all), max(xs_all)
    y_lo, y_hi = min(ys_all), max(ys_all)
    if hlines:
        for value, _label in hlines:
            y_lo = min(y_lo, value)
            y_hi = max(y_hi, value)
    pad = (y_hi - y_lo) * 0.08 or 1.0
    y_lo, y_hi = y_lo - pad, y_hi + pad
    if x_hi <= x_lo:
        x_hi = x_lo + 1.0

    def sx(value: float) -> float:
        return left + (value - x_lo) / (x_hi - x_lo) * plot_w

    def sy(value: float) -> float:
        return top + plot_h - (value - y_lo) / (y_hi - y_lo) * plot_h

    for low, high, colour in bands or []:
        canvas.add(
            "<rect x='{0:.1f}' y='{1:.1f}' width='{2:.1f}' height='{3:.1f}' fill='{4}' "
            "opacity='0.10'/>".format(sx(low), top, sx(high) - sx(low), plot_h, colour)
        )

    for tick in _nice_ticks(y_lo, y_hi):
        if tick < y_lo or tick > y_hi:
            continue
        y = sy(tick)
        canvas.add(
            "<line x1='{0}' y1='{1:.1f}' x2='{2}' y2='{1:.1f}' stroke='{3}' "
            "stroke-width='1'/>".format(left, y, left + plot_w, GRID)
        )
        canvas.add(
            "<text x='{0}' y='{1:.1f}' {2} font-size='11' fill='{3}' "
            "text-anchor='end'>{4}</text>".format(left - 8, y + 4, _FONT, MUTED, _format(tick))
        )

    for tick in _nice_ticks(x_lo, x_hi, 6):
        if tick < x_lo or tick > x_hi:
            continue
        x = sx(tick)
        canvas.add(
            "<line x1='{0:.1f}' y1='{1}' x2='{0:.1f}' y2='{2}' stroke='{3}' "
            "stroke-width='1'/>".format(x, top, top + plot_h, GRID)
        )
        canvas.add(
            "<text x='{0:.1f}' y='{1}' {2} font-size='11' fill='{3}' "
            "text-anchor='middle'>{4}</text>".format(
                x, top + plot_h + 18, _FONT, MUTED, _format(tick)
            )
        )

    for value, label in hlines or []:
        y = sy(value)
        canvas.add(
            "<line x1='{0}' y1='{1:.1f}' x2='{2}' y2='{1:.1f}' stroke='{3}' "
            "stroke-width='1.5' stroke-dasharray='6 4'/>".format(left, y, left + plot_w, AXIS)
        )
        canvas.add(
            "<text x='{0}' y='{1:.1f}' {2} font-size='11' fill='{3}'>{4}</text>".format(
                left + 6, y - 6, _FONT, AXIS, _escape(label)
            )
        )

    canvas.add(
        "<rect x='{0}' y='{1}' width='{2}' height='{3}' fill='none' stroke='{4}' "
        "stroke-width='1'/>".format(left, top, plot_w, plot_h, AXIS)
    )

    for index, (name, xs, ys) in enumerate(series):
        colour = PALETTE[index % len(PALETTE)]
        points = " ".join("{0:.2f},{1:.2f}".format(sx(x), sy(y)) for x, y in zip(xs, ys))
        canvas.add(
            "<polyline points='{0}' fill='none' stroke='{1}' stroke-width='2' "
            "stroke-linejoin='round'/>".format(points, colour)
        )
        legend_y = top + 6 + index * 20
        canvas.add(
            "<line x1='{0}' y1='{1}' x2='{2}' y2='{1}' stroke='{3}' "
            "stroke-width='3'/>".format(left + plot_w + 16, legend_y, left + plot_w + 40, colour)
        )
        canvas.add(
            "<text x='{0}' y='{1}' {2} font-size='11' fill='{3}'>{4}</text>".format(
                left + plot_w + 46, legend_y + 4, _FONT, TEXT, _escape(name)
            )
        )

    if x_label:
        canvas.add(
            "<text x='{0:.0f}' y='{1}' {2} font-size='12' fill='{3}' "
            "text-anchor='middle'>{4}</text>".format(
                left + plot_w / 2, height - 14, _FONT, TEXT, _escape(x_label)
            )
        )
    if y_label:
        canvas.add(
            "<text transform='translate(18,{0:.0f}) rotate(-90)' {1} font-size='12' "
            "fill='{2}' text-anchor='middle'>{3}</text>".format(
                top + plot_h / 2, _FONT, TEXT, _escape(y_label)
            )
        )
    return canvas.render()


def grouped_bars(
    categories: Sequence[str],
    groups: Sequence[Tuple[str, Sequence[float]]],
    title: str = "",
    subtitle: str = "",
    y_label: str = "",
    width: int = 900,
    height: int = 420,
    value_format: str = "{0:,.0f}",
) -> str:
    left, right, top, bottom = 70, 190, 72, 60
    plot_w = width - left - right
    plot_h = height - top - bottom
    canvas = Canvas(width, height, title, subtitle)

    values = [v for _n, vs in groups for v in vs]
    if not values or not categories:
        return canvas.render()
    y_hi = max(values) * 1.15 or 1.0
    y_lo = min(0.0, min(values))

    def sy(value: float) -> float:
        return top + plot_h - (value - y_lo) / (y_hi - y_lo) * plot_h

    for tick in _nice_ticks(y_lo, y_hi):
        if tick < y_lo or tick > y_hi:
            continue
        y = sy(tick)
        canvas.add(
            "<line x1='{0}' y1='{1:.1f}' x2='{2}' y2='{1:.1f}' stroke='{3}' "
            "stroke-width='1'/>".format(left, y, left + plot_w, GRID)
        )
        canvas.add(
            "<text x='{0}' y='{1:.1f}' {2} font-size='11' fill='{3}' "
            "text-anchor='end'>{4}</text>".format(left - 8, y + 4, _FONT, MUTED, _format(tick))
        )

    slot = plot_w / len(categories)
    bar_w = slot * 0.72 / max(len(groups), 1)
    for c_index, category in enumerate(categories):
        base = left + slot * c_index + slot * 0.14
        for g_index, (_name, values) in enumerate(groups):
            if c_index >= len(values):
                continue
            value = values[c_index]
            colour = PALETTE[g_index % len(PALETTE)]
            x = base + g_index * bar_w
            y = sy(value)
            canvas.add(
                "<rect x='{0:.1f}' y='{1:.1f}' width='{2:.1f}' height='{3:.1f}' "
                "fill='{4}' rx='2'/>".format(x, y, bar_w * 0.9, sy(y_lo) - y, colour)
            )
            canvas.add(
                "<text x='{0:.1f}' y='{1:.1f}' {2} font-size='10' fill='{3}' "
                "text-anchor='middle'>{4}</text>".format(
                    x + bar_w * 0.45, y - 5, _FONT, TEXT, _escape(value_format.format(value))
                )
            )
        canvas.add(
            "<text x='{0:.1f}' y='{1}' {2} font-size='12' fill='{3}' "
            "text-anchor='middle'>{4}</text>".format(
                left + slot * c_index + slot / 2,
                top + plot_h + 20,
                _FONT,
                TEXT,
                _escape(category),
            )
        )

    for g_index, (name, _values) in enumerate(groups):
        colour = PALETTE[g_index % len(PALETTE)]
        legend_y = top + 6 + g_index * 20
        canvas.add(
            "<rect x='{0}' y='{1}' width='14' height='10' fill='{2}' rx='2'/>".format(
                left + plot_w + 16, legend_y - 8, colour
            )
        )
        canvas.add(
            "<text x='{0}' y='{1}' {2} font-size='11' fill='{3}'>{4}</text>".format(
                left + plot_w + 36, legend_y + 1, _FONT, TEXT, _escape(name)
            )
        )

    canvas.add(
        "<line x1='{0}' y1='{1:.1f}' x2='{2}' y2='{1:.1f}' stroke='{3}' "
        "stroke-width='1.5'/>".format(left, sy(y_lo), left + plot_w, AXIS)
    )
    if y_label:
        canvas.add(
            "<text transform='translate(18,{0:.0f}) rotate(-90)' {1} font-size='12' "
            "fill='{2}' text-anchor='middle'>{3}</text>".format(
                top + plot_h / 2, _FONT, TEXT, _escape(y_label)
            )
        )
    return canvas.render()


def phase_timeline(
    rows: Sequence[Tuple[str, Sequence[Tuple[str, float, float]]]],
    title: str = "",
    subtitle: str = "",
    width: int = 900,
    height: int = 300,
    phase_colours: Optional[Dict[str, str]] = None,
) -> str:
    """A Gantt-style comparison of where the time went in each cycle."""
    left, right, top, bottom = 150, 40, 78, 60
    plot_w = width - left - right
    canvas = Canvas(width, height, title, subtitle)
    span = max(
        (end for _label, bars in rows for _name, _start, end in bars),
        default=1.0,
    )
    row_h = (height - top - bottom) / max(len(rows), 1)
    colours: Dict[str, str] = dict(phase_colours or {})
    order: List[str] = []
    for _label, bars in rows:
        for name, _start, _end in bars:
            if name not in colours:
                colours[name] = PALETTE[len(order) % len(PALETTE)]
                order.append(name)
            elif name not in order:
                order.append(name)

    for r_index, (label, bars) in enumerate(rows):
        y = top + r_index * row_h + row_h * 0.18
        bar_h = row_h * 0.52
        canvas.add(
            "<text x='{0}' y='{1:.1f}' {2} font-size='12' fill='{3}' "
            "text-anchor='end'>{4}</text>".format(
                left - 12, y + bar_h * 0.7, _FONT, TEXT, _escape(label)
            )
        )
        for name, start, end in bars:
            x0 = left + start / span * plot_w
            x1 = left + end / span * plot_w
            canvas.add(
                "<rect x='{0:.1f}' y='{1:.1f}' width='{2:.1f}' height='{3:.1f}' fill='{4}' "
                "rx='2'/>".format(x0, y, max(x1 - x0, 1.0), bar_h, colours[name])
            )
        total = max((end for _n, _s, end in bars), default=0.0)
        canvas.add(
            "<text x='{0:.1f}' y='{1:.1f}' {2} font-size='11' fill='{3}'>{4:.0f} min</text>".format(
                left + total / span * plot_w + 8, y + bar_h * 0.7, _FONT, MUTED, total / 60.0
            )
        )

    axis_y = top + len(rows) * row_h + 6
    for tick in _nice_ticks(0.0, span / 60.0, 6):
        x = left + tick * 60.0 / span * plot_w
        if x > left + plot_w + 1:
            continue
        canvas.add(
            "<text x='{0:.1f}' y='{1:.1f}' {2} font-size='11' fill='{3}' "
            "text-anchor='middle'>{4:.0f}</text>".format(x, axis_y + 14, _FONT, MUTED, tick)
        )
    canvas.add(
        "<text x='{0:.0f}' y='{1}' {2} font-size='12' fill='{3}' "
        "text-anchor='middle'>minutes</text>".format(
            left + plot_w / 2, height - 12, _FONT, TEXT
        )
    )

    for index, name in enumerate(order):
        x = left + index * (plot_w / max(len(order), 1))
        canvas.add(
            "<rect x='{0:.1f}' y='{1}' width='12' height='9' fill='{2}' rx='2'/>".format(
                x, top - 22, colours[name]
            )
        )
        canvas.add(
            "<text x='{0:.1f}' y='{1}' {2} font-size='10' fill='{3}'>{4}</text>".format(
                x + 17, top - 14, _FONT, TEXT, _escape(name.replace("_", " ").title())
            )
        )
    return canvas.render()


def write(path: str, markup: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(markup)
