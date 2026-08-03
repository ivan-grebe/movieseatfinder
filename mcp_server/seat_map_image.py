"""Render normalized live seat layouts as dependency-free PNG images."""

import math
import struct
import zlib

CANVAS_WIDTH = 1200
CANVAS_HEIGHT = 800

BACKGROUND = (245, 249, 253)
MAP_BACKGROUND = (248, 251, 255)
SCREEN = (255, 215, 210)
AVAILABLE = (255, 255, 255)
UNAVAILABLE = (199, 206, 216)
ACCESSIBLE = (37, 99, 199)
MATCHED = (201, 58, 58)
MATCHED_OUTLINE = (143, 31, 38)
SEAT_OUTLINE = (159, 171, 187)


def _fill_rect(pixels, width, height, x0, y0, x1, y1, color):
    left = max(0, min(width, int(x0)))
    top = max(0, min(height, int(y0)))
    right = max(left, min(width, int(math.ceil(x1))))
    bottom = max(top, min(height, int(math.ceil(y1))))
    if left == right or top == bottom:
        return
    row = bytes(color) * (right - left)
    for y in range(top, bottom):
        start = (y * width + left) * 3
        pixels[start:start + len(row)] = row


def _png_chunk(chunk_type, data):
    payload = chunk_type + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)


def _encode_png(width, height, pixels):
    stride = width * 3
    scanlines = b"".join(
        b"\x00" + bytes(pixels[offset:offset + stride])
        for offset in range(0, len(pixels), stride)
    )
    return b"".join((
        b"\x89PNG\r\n\x1a\n",
        _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
        _png_chunk(b"IDAT", zlib.compress(scanlines, level=9)),
        _png_chunk(b"IEND", b""),
    ))


def _number(value, fallback=0.0):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if math.isfinite(parsed) else fallback


def render_seat_map_png(layout):
    """Create a stable PNG preview from the application's normalized seat layout."""
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

    pixels = bytearray(BACKGROUND * (CANVAS_WIDTH * CANVAS_HEIGHT))
    map_left, map_top = 64, 112
    map_right, map_bottom = CANVAS_WIDTH - 64, CANVAS_HEIGHT - 92
    _fill_rect(pixels, CANVAS_WIDTH, CANVAS_HEIGHT, map_left, map_top, map_right, map_bottom, MAP_BACKGROUND)

    screen_left, screen_right = CANVAS_WIDTH * 0.24, CANVAS_WIDTH * 0.76
    _fill_rect(pixels, CANVAS_WIDTH, CANVAS_HEIGHT, screen_left, 48, screen_right, 64, (255, 250, 250))
    _fill_rect(pixels, CANVAS_WIDTH, CANVAS_HEIGHT, screen_left + 28, 64, screen_right - 28, 70, SCREEN)

    available_width = map_right - map_left - 48
    available_height = map_bottom - map_top - 40
    scale = min(available_width / layout_width, available_height / layout_height)
    drawn_width = layout_width * scale
    drawn_height = layout_height * scale
    origin_x = map_left + (map_right - map_left - drawn_width) / 2
    origin_y = map_top + (map_bottom - map_top - drawn_height) / 2

    for seat in seats[:2500]:
        x = origin_x + _number(seat.get("x")) * scale
        y = origin_y + _number(seat.get("y")) * scale
        seat_width = max(_number(seat.get("width")) * scale, 3)
        seat_height = max(_number(seat.get("height")) * scale, 3)
        is_available = seat.get("status") == "A"
        seat_type = str(seat.get("type") or "standard").lower()
        if seat.get("matched"):
            color = MATCHED
            outline = MATCHED_OUTLINE
        elif is_available and seat_type in {"wheelchair", "companion"}:
            color = ACCESSIBLE
            outline = SEAT_OUTLINE
        elif is_available:
            color = AVAILABLE
            outline = SEAT_OUTLINE
        else:
            color = UNAVAILABLE
            outline = SEAT_OUTLINE

        border = max(1, min(3, int(min(seat_width, seat_height) * 0.16)))
        _fill_rect(
            pixels,
            CANVAS_WIDTH,
            CANVAS_HEIGHT,
            x,
            y,
            x + seat_width,
            y + seat_height,
            outline,
        )
        _fill_rect(
            pixels,
            CANVAS_WIDTH,
            CANVAS_HEIGHT,
            x + border,
            y + border,
            x + seat_width - border,
            y + seat_height - border,
            color,
        )

    legend_y = CANVAS_HEIGHT - 54
    for index, color in enumerate((MATCHED, AVAILABLE, UNAVAILABLE, ACCESSIBLE)):
        left = 330 + index * 150
        _fill_rect(pixels, CANVAS_WIDTH, CANVAS_HEIGHT, left, legend_y, left + 28, legend_y + 20, SEAT_OUTLINE)
        _fill_rect(pixels, CANVAS_WIDTH, CANVAS_HEIGHT, left + 2, legend_y + 2, left + 26, legend_y + 18, color)

    return _encode_png(CANVAS_WIDTH, CANVAS_HEIGHT, pixels)
