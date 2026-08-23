"""Render the canonical seat-map visual shared by the website and MCP."""

import base64
import math
import threading
from html import escape

from resvg import render, usvg

CANVAS_WIDTH = 1800
CANVAS_HEIGHT = 1200
ACCESSIBLE_SEAT_TYPES = {"wheelchair", "companion"}
_RENDER_STATE = threading.local()


def _render_options():
    options = getattr(_RENDER_STATE, "options", None)
    if options is None:
        options = usvg.Options.default()
        setattr(options, "font_family", "Arial")
        options.load_system_fonts()
        _RENDER_STATE.options = options
    return options


def _number(value, fallback=0.0):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
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
    top_radius = max(min(width, height) * 0.18, stroke_width)
    bottom_radius = max(min(width, height) * 0.30, stroke_width)
    source_available = seat.get("status") == "A"
    accessible = str(seat.get("type") or "").lower() in ACCESSIBLE_SEAT_TYPES
    excluded = source_available and accessible and accessible_seats_excluded
    available = source_available and not excluded
    matched = bool(seat.get("matched"))

    if matched and accessible:
        fill = "url(#matched-accessible)"
        stroke = "url(#matched-accessible-border)"
        glow = ' filter="url(#match-glow)"'
    elif matched:
        fill = "#c93a3a"
        stroke = "#8f1f26"
        glow = ' filter="url(#match-glow)"'
    elif available and accessible:
        fill = "#2563c7"
        stroke = "#174a97"
        glow = ' filter="url(#accessible-glow)"'
    elif available:
        fill = "#ffffff"
        stroke = "#9fabbb"
        glow = ""
    else:
        fill = "#c7ced8"
        stroke = "#adb5c0"
        glow = ""

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
    return (
        f'<path d="{seat_path}" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="{stroke_width:g}"{glow}><title>{_text(description)}</title></path>'
    )


def _legend_item(x, label, fill, stroke):
    return (
        f'<rect x="{x:g}" y="1112" width="28" height="28" rx="6" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
        + f'<text x="{x + 42}" y="1133" class="legend">{_text(label)}</text>'
    )


def _legend_svg(accessible_seats_excluded):
    items = [("Available", "#ffffff", "#9fabbb")]
    if not accessible_seats_excluded:
        items.extend([
            ("Accessible", "#2563c7", "#174a97"),
            ("Accessible match", "url(#matched-accessible)", "url(#matched-accessible-border)"),
        ])
    items.extend([
        ("Unavailable / excluded" if accessible_seats_excluded else "Unavailable", "#c7ced8", "#adb5c0"),
        ("Matches", "#c93a3a", "#8f1f26"),
    ])
    text_widths = [max(78, len(label) * 11.5) for label, _, _ in items]
    item_widths = [42 + width for width in text_widths]
    gap = 44
    total_width = sum(item_widths) + gap * (len(items) - 1)
    cursor = (CANVAS_WIDTH - total_width) / 2
    nodes = []
    for (label, fill, stroke), item_width in zip(items, item_widths):
        nodes.append(_legend_item(cursor, label, fill, stroke))
        cursor += item_width + gap
    start = (CANVAS_WIDTH - total_width) / 2
    return (
        f'<g id="seat-map-legend" data-start="{start:g}" data-width="{total_width:g}">'
        f'{"".join(nodes)}</g>'
    )


def render_seat_map_svg(layout, available_count=None, total_count=None, accessible_seats_excluded=True):
    """Create the canonical seat-map visual shared by the website and MCP."""
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

    region_y = 214 if background_svg else 284
    max_stage_width = 1640
    max_stage_height = 810 if background_svg else 740
    layout_aspect = layout_width / layout_height
    stage_width = max_stage_width
    stage_height = stage_width / layout_aspect
    if stage_height > max_stage_height:
        stage_height = max_stage_height
        stage_width = stage_height * layout_aspect
    stage_x = (CANVAS_WIDTH - stage_width) / 2
    stage_y = region_y + (max_stage_height - stage_height) / 2
    inner_x, inner_y = stage_x, stage_y
    inner_width, inner_height = stage_width, stage_height
    stroke_width = max(min(layout_width, layout_height) / 700, 0.45)
    seat_nodes = "".join(
        _seat_svg(seat, stroke_width, accessible_seats_excluded)
        for seat in seats[:2500]
    )
    background_node = (
        f'<image x="0" y="0" width="{layout_width:g}" height="{layout_height:g}" '
        f'preserveAspectRatio="none" href="{_embedded_svg(background_svg)}" opacity="0.92"/>'
        if background_svg else ""
    )
    screen_y = max(196, stage_y - 58)
    screen_node = "" if background_svg else (
        f'<g><rect x="390" y="{screen_y:g}" width="1020" height="38" rx="19" fill="url(#screen)"/>'
        f'<text x="900" y="{screen_y + 25:g}" class="screen-label" text-anchor="middle">SCREEN</text></g>'
    )

    legend = _legend_svg(accessible_seats_excluded)
    glow_blur = max(stroke_width * 1.35, 0.5)

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_WIDTH}" height="{CANVAS_HEIGHT}" viewBox="0 0 {CANVAS_WIDTH} {CANVAS_HEIGHT}">
      <defs>
        <linearGradient id="canvas" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#f5f9fd"/>
        </linearGradient>
        <linearGradient id="stage" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#f8fbff"/><stop offset="1" stop-color="#edf4fb"/>
        </linearGradient>
        <linearGradient id="screen" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#fffafa"/><stop offset="1" stop-color="#ffd7d2"/>
        </linearGradient>
        <linearGradient id="matched-accessible" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0.49" stop-color="#c93a3a"/><stop offset="0.51" stop-color="#2563c7"/>
        </linearGradient>
        <linearGradient id="matched-accessible-border" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0.49" stop-color="#8f1f26"/><stop offset="0.51" stop-color="#174a97"/>
        </linearGradient>
        <filter id="match-glow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="{glow_blur:g}" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
        <filter id="accessible-glow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="{glow_blur:g}" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
        <clipPath id="stage-clip"><rect x="{stage_x:g}" y="{stage_y:g}" width="{stage_width:g}" height="{stage_height:g}" rx="28"/></clipPath>
        <style>
          text {{ font-family: Arial, sans-serif; }}
          .title {{ fill:#405069; font-size:38px; font-weight:800; }}
          .count {{ fill:#667085; font-size:23px; font-weight:700; font-variant-numeric:tabular-nums; }}
          .legend {{ fill:#667085; font-size:22px; font-weight:650; }}
          .screen-label {{ fill:#5a2730; font-size:16px; font-weight:900; letter-spacing:5px; }}
        </style>
      </defs>
      <rect width="1800" height="1200" fill="url(#canvas)"/>
      <rect x="24" y="24" width="1752" height="1152" rx="34" fill="none" stroke="#6882a4" stroke-opacity="0.18"/>
      <text x="80" y="92" class="title">{_text(title)}</text>
      <text x="1720" y="92" class="count" text-anchor="end">{int(available_count)} available / {int(total_count)} total</text>
      <rect x="{stage_x:g}" y="{stage_y:g}" width="{stage_width:g}" height="{stage_height:g}" rx="28" fill="url(#stage)" stroke="#6882a4" stroke-opacity="0.24"/>
      {screen_node}
      <g clip-path="url(#stage-clip)">
        <svg x="{inner_x:g}" y="{inner_y:g}" width="{inner_width:g}" height="{inner_height:g}" viewBox="0 0 {layout_width:g} {layout_height:g}" preserveAspectRatio="none" overflow="hidden">
          {background_node}{seat_nodes}
        </svg>
      </g>
      {legend}
    </svg>'''
    return svg


def render_svg_png(svg):
    """Rasterize an already-generated canonical seat-map SVG."""
    tree = usvg.Tree.from_str(svg, _render_options())
    # resvg expects a row-major 2x3 affine matrix, not SVG's transform ordering.
    return bytes(render(tree, (1, 0, 0, 0, 1, 0)))
