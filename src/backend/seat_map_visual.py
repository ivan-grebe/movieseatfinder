"""Render the website seat-map layout as a dark MCP image."""

import base64
import math
import threading
from html import escape
from pathlib import Path

from resvg import render, usvg

OUTPUT_SCALE = 2
CANVAS_WIDTH = 642
CONTENT_WIDTH = 620
WRAPPER_PADDING = 10
ACCESSIBLE_SEAT_TYPES = {"wheelchair", "companion"}
_FONT_PATH = Path(__file__).with_name("assets") / "inter-variable.ttf"
_RENDER_STATE = threading.local()
TITLE_TEXT_STYLE = (
    "display:inline;visibility:visible;opacity:1;fill:#f1f5f9;"
    "font-family:'Inter Variable',sans-serif;font-size:11px;font-weight:850"
)
COUNT_TEXT_STYLE = (
    "display:inline;visibility:visible;opacity:1;fill:#aebacd;"
    "font-family:'Inter Variable',sans-serif;font-size:11px;font-weight:750;font-variant-numeric:tabular-nums"
)
LEGEND_TEXT_STYLE = (
    "display:inline;visibility:visible;opacity:1;fill:#aebacd;"
    "font-family:'Inter Variable',sans-serif;font-size:11px;font-weight:400"
)
SCREEN_TEXT_STYLE = (
    "display:inline;visibility:visible;opacity:1;fill:#e8b4bd;"
    "font-family:'Inter Variable',sans-serif;font-size:9px;font-weight:900;letter-spacing:1.44px"
)


def _render_options():
    options = getattr(_RENDER_STATE, "options", None)
    if options is None:
        options = usvg.Options.default()
        setattr(options, "font_family", "Inter Variable")
        options.load_font_file(str(_FONT_PATH))
        _RENDER_STATE.options = options
    return options


def _number(value, fallback=0.0):
    try:
        parsed = float(value)
    except TypeError, ValueError:
        return fallback
    return parsed if math.isfinite(parsed) else fallback


def _text(value):
    return escape(str(value or ""), quote=True)


def _embedded_svg(svg):
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _seat_svg(seat, stroke_width, accessible_seats_excluded):
    x = _number(seat.get("x"))
    y = _number(seat.get("y"))
    width = max(_number(seat.get("width")), 1)
    height = max(_number(seat.get("height")), 1)
    top_radius = min(3 * stroke_width, width / 2, height / 2)
    bottom_radius = min(5 * stroke_width, width / 2, height / 2)
    source_available = seat.get("status") == "A"
    accessible = str(seat.get("type") or "").lower() in ACCESSIBLE_SEAT_TYPES
    excluded = source_available and accessible and accessible_seats_excluded
    available = source_available and not excluded
    matched = bool(seat.get("matched"))

    if matched and accessible:
        fill = "url(#matched-accessible)"
        stroke = "url(#matched-accessible-border)"
        glow_fill = "url(#matched-accessible-glow)"
    elif matched:
        fill = "#ef4444"
        stroke = "#fca5a5"
        glow_fill = "rgba(239,68,68,.66)"
    elif available and accessible:
        fill = "#3b82f6"
        stroke = "#93c5fd"
        glow_fill = "rgba(59,130,246,.66)"
    elif available:
        fill = "#f8fafc"
        stroke = "#cbd5e1"
        glow_fill = ""
    else:
        fill = "#475569"
        stroke = "#64748b"
        glow_fill = ""

    right = x + width
    bottom = y + height
    seat_path = (
        f"M{x + top_radius:g},{y:g} H{right - top_radius:g} "
        f"Q{right:g},{y:g} {right:g},{y + top_radius:g} "
        f"V{bottom - bottom_radius:g} Q{right:g},{bottom:g} {right - bottom_radius:g},{bottom:g} "
        f"H{x + bottom_radius:g} Q{x:g},{bottom:g} {x:g},{bottom - bottom_radius:g} "
        f"V{y + top_radius:g} Q{x:g},{y:g} {x + top_radius:g},{y:g} Z"
    )
    description = " - ".join(
        value
        for value in [
            str(seat.get("id") or "Seat"),
            "available" if source_available else "unavailable",
            str(seat.get("type") or "") if accessible else "",
            "excluded by filter" if excluded else "",
            "matching" if matched else "",
        ]
        if value
    )
    glow = (
        f'<path d="{seat_path}" fill="{glow_fill}" stroke="{glow_fill}" stroke-width="{stroke_width * 4:g}" '
        f'filter="url(#seat-glow)"/>'
        if glow_fill
        else ""
    )
    return (
        glow + f'<path d="{seat_path}" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="{stroke_width:g}"><title>{_text(description)}</title></path>'
    )


def _legend_item(x, label, fill, stroke):
    return (
        f'<rect x="{x:g}" y="0" width="12" height="12" rx="3" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
        + f'<text x="{x + 17:g}" y="10" style="{LEGEND_TEXT_STYLE}">{_text(label)}</text>'
    )


def _legend_svg(accessible_seats_excluded):
    items = [("Available", "#f8fafc", "#cbd5e1")]
    if not accessible_seats_excluded:
        items.append(("Accessible", "#3b82f6", "#93c5fd"))
    items.extend(
        [
            (
                "Unavailable / excluded" if accessible_seats_excluded else "Unavailable",
                "#475569",
                "#64748b",
            ),
            ("Matches", "#ef4444", "#fca5a5"),
        ]
    )
    text_widths = [len(label) * 6.1 for label, _, _ in items]
    item_widths = [17 + width for width in text_widths]
    gap = 7
    total_width = sum(item_widths) + gap * (len(items) - 1)
    cursor = WRAPPER_PADDING + 1
    nodes = []
    for (label, fill, stroke), item_width in zip(items, item_widths):
        nodes.append(_legend_item(cursor, label, fill, stroke))
        cursor += item_width + gap
    start = WRAPPER_PADDING + 1
    return (
        f'<g id="seat-map-legend" data-start="{start:g}" data-width="{total_width:g}">'
        f"{''.join(nodes)}</g>"
    )


def render_seat_map_svg(
    layout, available_count=None, total_count=None, accessible_seats_excluded=True
):
    """Create a dark MCP image using the website seat map's layout."""
    seats = layout.get("seats") or []
    if not seats:
        raise ValueError("This showtime does not have a seat map to display.")

    layout_width = max(
        _number(layout.get("width"), 1),
        max(_number(seat.get("x")) + _number(seat.get("width")) for seat in seats),
        1,
    )
    layout_height = max(
        _number(layout.get("height"), 1),
        max(_number(seat.get("y")) + _number(seat.get("height")) for seat in seats),
        1,
    )
    background_svg = str(layout.get("backgroundSvg") or "")
    title = "Live Fandango seat map"
    if available_count is None:
        available_count = sum(1 for seat in seats if seat.get("status") == "A")
    if total_count is None:
        total_count = len(seats)

    layout_aspect = layout_width / layout_height
    stage_width = CONTENT_WIDTH
    stage_height = max(stage_width / layout_aspect, 150)
    stage_x = WRAPPER_PADDING + 1
    screen_y = 29
    stage_y = 29 if background_svg else 57
    legend_y = stage_y + stage_height + 8
    canvas_height = math.ceil(legend_y + 12 + WRAPPER_PADDING + 1)
    stroke_width = max(layout_width / CONTENT_WIDTH, 0.01)
    seat_nodes = "".join(
        _seat_svg(seat, stroke_width, accessible_seats_excluded) for seat in seats[:2500]
    )
    background_node = (
        f'<image x="0" y="0" width="{layout_width:g}" height="{layout_height:g}" '
        f'preserveAspectRatio="none" href="{_embedded_svg(background_svg)}"/>'
        if background_svg
        else ""
    )
    screen_node = (
        ""
        if background_svg
        else (
            f'<g><rect x="91" y="{screen_y}" width="460" height="18" rx="9" fill="url(#screen)"/>'
            f'<text x="321" y="{screen_y + 12}" style="{SCREEN_TEXT_STYLE}" '
            f'text-anchor="middle">SCREEN</text></g>'
        )
    )

    legend = _legend_svg(accessible_seats_excluded)
    glow_blur = max(2 * layout_width / CONTENT_WIDTH, 0.5)
    output_width = CANVAS_WIDTH * OUTPUT_SCALE
    output_height = canvas_height * OUTPUT_SCALE
    stage_fill = "#0b1220" if background_svg else "url(#stage)"

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{output_width}" height="{output_height}" viewBox="0 0 {CANVAS_WIDTH} {canvas_height}">
      <defs>
        <linearGradient id="stage" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#182334"/><stop offset="1" stop-color="#0b1220"/>
        </linearGradient>
        <linearGradient id="screen" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#4c2730"/><stop offset="1" stop-color="#27171d"/>
        </linearGradient>
        <linearGradient id="matched-accessible" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0.49" stop-color="#ef4444"/><stop offset="0.51" stop-color="#3b82f6"/>
        </linearGradient>
        <linearGradient id="matched-accessible-border" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0.49" stop-color="#fca5a5"/><stop offset="0.51" stop-color="#93c5fd"/>
        </linearGradient>
        <linearGradient id="matched-accessible-glow" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0.49" stop-color="rgba(239,68,68,.66)"/><stop offset="0.51" stop-color="rgba(59,130,246,.66)"/>
        </linearGradient>
        <filter id="seat-glow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="{glow_blur:g}"/>
        </filter>
        <clipPath id="stage-clip"><rect x="{stage_x:g}" y="{stage_y:g}" width="{stage_width:g}" height="{stage_height:g}" rx="12"/></clipPath>
      </defs>
      <rect x="0.5" y="0.5" width="641" height="{canvas_height - 1}" rx="22" fill="#111827" stroke="#475569" stroke-opacity=".72"/>
      <text x="11" y="20" style="{TITLE_TEXT_STYLE}">{_text(title)}</text>
      <text x="631" y="20" style="{COUNT_TEXT_STYLE}" text-anchor="end">{int(available_count)} available / {int(total_count)} total</text>
      <rect x="{stage_x:g}" y="{stage_y:g}" width="{stage_width:g}" height="{stage_height:g}" rx="12" fill="{stage_fill}" stroke="#64748b" stroke-opacity=".5"/>
      {screen_node}
      <g clip-path="url(#stage-clip)">
        <svg x="{stage_x:g}" y="{stage_y:g}" width="{stage_width:g}" height="{stage_height:g}" viewBox="0 0 {layout_width:g} {layout_height:g}" preserveAspectRatio="none" overflow="hidden">
          {background_node}{seat_nodes}
        </svg>
      </g>
      <g transform="translate(0 {legend_y:g})">{legend}</g>
    </svg>'''
    return svg


def render_svg_png(svg):
    """Rasterize an MCP seat-map SVG."""
    tree = usvg.Tree.from_str(svg, _render_options())
    # resvg expects a row-major 2x3 affine matrix, not SVG's transform ordering.
    return bytes(render(tree, (1, 0, 0, 0, 1, 0)))
