"""Pure seat-map normalization and matching rules."""

import requests

def seat_band(position, bands):
    for limit, name in bands:
        if position <= limit:
            return name
    return bands[-1][1]


def human_zone(value):
    return value.replace("-", " ")


def seat_zone_labels(seat, min_row, max_row, min_x, max_x, row_edges):
    labels = set()
    seat_type = seat.get("type")
    if seat_type in ("wheelchair", "companion"):
        labels.add("accessible")
        labels.add(seat_type)

    row_span = max(max_row - min_row, 1)
    row_position = (seat.get("row", 0) - min_row) / row_span
    x_span = max(max_x - min_x, 1)
    x_position = (seat.get("x", 0) - min_x) / x_span

    depth = seat_band(row_position, [
        (0.20, "very-front"),
        (0.40, "front"),
        (0.60, "middle"),
        (0.80, "back"),
        (1.00, "very-back"),
    ])
    side = seat_band(x_position, [
        (0.20, "far-left"),
        (0.40, "left"),
        (0.60, "center"),
        (0.80, "right"),
        (1.00, "far-right"),
    ])

    labels.add(depth)
    labels.add(side)
    labels.add(f"{depth}-{side}")

    if depth == "middle" and side == "center":
        labels.add("middle-center")
        labels.add("center-middle")
        labels.add("center")
    if side == "center":
        labels.add(f"{depth}-center")
    if depth == "middle":
        labels.add(f"middle-{side}")

    row_min, row_max = row_edges.get(seat.get("row", 0), (None, None))
    column = seat.get("column")
    if column == row_min:
        labels.add("left-aisle")
        labels.add("aisle")
    if column == row_max:
        labels.add("right-aisle")
        labels.add("aisle")

    return labels


def seat_matches_filter(labels, requested_area):
    requested = (requested_area or "any").lower()
    if requested == "any":
        return True
    aliases = {
        "center-center": "middle-center",
        "center-middle": "middle-center",
        "aisle-left": "left-aisle",
        "aisle-right": "right-aisle",
    }
    return aliases.get(requested, requested) in labels


def parse_seat_grid(value):
    cells = []
    for part in (value or "").split(","):
        if not part:
            continue
        pieces = part.split(":")
        if len(pieces) != 2:
            continue
        try:
            row = int(pieces[0])
            col = int(pieces[1])
        except ValueError:
            continue
        if 0 <= row < 15 and 0 <= col < 15:
            cells.append((row, col))
    return cells


def seat_matches_grid(row_position, x_position, selected_cells):
    if not selected_cells:
        return True
    row = min(14, max(0, int(row_position * 15)))
    col = min(14, max(0, int(x_position * 15)))
    return (row, col) in selected_cells


def primary_zone(labels, requested_area):
    requested = (requested_area or "any").lower()
    if requested != "any":
        return human_zone(requested)
    for candidate in [
        "middle-center",
        "front-center",
        "back-center",
        "very-front-center",
        "very-back-center",
        "middle-left",
        "middle-right",
        "accessible",
        "left-aisle",
        "right-aisle",
    ]:
        if candidate in labels:
            return human_zone(candidate)
    exact = sorted(label for label in labels if "-" in label)
    return human_zone(exact[0]) if exact else human_zone(sorted(labels)[0])


def normalized_seat_layout(data, matching_blocks):
    seats = data.get("seats") or []
    if not seats:
        return None

    matched_ids = {
        seat_id
        for block in matching_blocks
        for seat_id in block.get("seats", [])
    }
    background_svg = data.get("backgroundSvg") or ""
    background_width = data.get("backgroundWidth")
    background_height = data.get("backgroundHeight")
    if background_svg and background_width and background_height:
        map_offset_x = data.get("mapOffsetX", 0) or 0
        map_offset_y = data.get("mapOffsetY", 0) or 0
        return {
            "width": max(float(background_width), 1),
            "height": max(float(background_height), 1),
            "backgroundSvg": background_svg,
            "seats": [{
                "id": seat.get("id", ""),
                "row": seat.get("row"),
                "column": seat.get("column"),
                "type": seat.get("type", "standard"),
                "status": seat.get("status", ""),
                "x": seat.get("x", 0) + map_offset_x,
                "y": seat.get("y", 0) + map_offset_y,
                "width": seat.get("width", 0),
                "height": seat.get("height", 0),
                "matched": seat.get("id", "") in matched_ids,
            } for seat in seats],
        }

    min_left = min((seat.get("x", 0) for seat in seats), default=0)
    min_top = min((seat.get("y", 0) for seat in seats), default=0)
    max_right = max((seat.get("x", 0) + seat.get("width", 0) for seat in seats), default=0)
    max_bottom = max((seat.get("y", 0) + seat.get("height", 0) for seat in seats), default=0)
    seat_widths = [seat.get("width", 0) for seat in seats if seat.get("width", 0)]
    seat_heights = [seat.get("height", 0) for seat in seats if seat.get("height", 0)]
    content_width = max(max_right - min_left, 1)
    content_height = max(max_bottom - min_top, 1)
    average_seat_size = max(
        sum(seat_widths) / len(seat_widths) if seat_widths else 0,
        sum(seat_heights) / len(seat_heights) if seat_heights else 0,
    )
    padding = max(average_seat_size * 1.75, min(content_width, content_height) * 0.035, 8)
    width = content_width + padding * 2
    height = content_height + padding * 2

    return {
        "width": width,
        "height": height,
        "seats": [{
            "id": seat.get("id", ""),
            "row": seat.get("row"),
            "column": seat.get("column"),
            "type": seat.get("type", "standard"),
            "status": seat.get("status", ""),
            "x": seat.get("x", 0) - min_left + padding,
            "y": seat.get("y", 0) - min_top + padding,
            "width": seat.get("width", 0),
            "height": seat.get("height", 0),
            "matched": seat.get("id", "") in matched_ids,
        } for seat in seats],
    }


ACCESSIBLE_SEAT_TYPES = {"wheelchair", "companion"}


def adjacent_blocks(seats, min_adjacent, requested_area, selected_cells=None, exclude_accessible=False):
    selected_cells = selected_cells or []
    available = [
        seat for seat in seats
        if seat.get("status") == "A"
        and not (exclude_accessible and seat.get("type") in ACCESSIBLE_SEAT_TYPES)
    ]
    if not available:
        return []

    rows = [seat.get("row", 0) for seat in seats]
    xs = [seat.get("x", 0) for seat in seats]
    min_row, max_row = min(rows), max(rows)
    min_x, max_x = min(xs), max(xs)
    row_edges = {}
    for seat in seats:
        row = seat.get("row", 0)
        column = seat.get("column")
        if column is None:
            continue
        current_min, current_max = row_edges.get(row, (column, column))
        row_edges[row] = (min(current_min, column), max(current_max, column))

    by_row = {}
    for seat in available:
        row_span = max(max_row - min_row, 1)
        row_position = (seat.get("row", 0) - min_row) / row_span
        x_span = max(max_x - min_x, 1)
        x_position = (seat.get("x", 0) - min_x) / x_span
        if not seat_matches_grid(row_position, x_position, selected_cells):
            continue
        labels = seat_zone_labels(seat, min_row, max_row, min_x, max_x, row_edges)
        if not selected_cells and not seat_matches_filter(labels, requested_area):
            continue
        by_row.setdefault(seat.get("row", 0), []).append({
            **seat,
            "area": "selected grid" if selected_cells else primary_zone(labels, requested_area),
            "zoneLabels": sorted(labels),
        })

    blocks = []
    for row, row_seats in by_row.items():
        row_seats.sort(key=lambda seat: seat.get("column", 0))
        current = []
        previous_col = None
        for seat in row_seats:
            column = seat.get("column", 0)
            if previous_col is None or column == previous_col + 1:
                current.append(seat)
            else:
                if len(current) >= min_adjacent:
                    blocks.append(current)
                current = [seat]
            previous_col = column
        if len(current) >= min_adjacent:
            blocks.append(current)

    return [{
        "row": block[0].get("id", "")[:1] or str(block[0].get("row", "")),
        "seats": [seat.get("id", "") for seat in block],
        "count": len(block),
        "area": block[0].get("area", requested_area),
        "zones": sorted(set(zone for seat in block for zone in seat.get("zoneLabels", []))),
    } for block in blocks]


def showtime_seat_match(
    showtime,
    min_adjacent,
    requested_area,
    selected_cells=None,
    exclude_accessible=False,
    seat_map_loader=None,
):
    try:
        if seat_map_loader is None:
            return None
        data = seat_map_loader(showtime.get("showtimeHashCode"))
        if not data:
            return None
        seats = data.get("seats") or []
        blocks = adjacent_blocks(seats, min_adjacent, requested_area, selected_cells, exclude_accessible)
        if not blocks:
            return None
        available_count = data.get("totalAvailableSeatCount")
        total_count = data.get("totalSeatCount")
        if available_count is None:
            available_count = len([seat for seat in seats if seat.get("status") == "A"])
        if total_count is None:
            total_count = len(seats)
        return {
            "availableSeatCount": available_count,
            "totalSeatCount": total_count,
            "layout": normalized_seat_layout(data, blocks),
        }
    except (requests.RequestException, TimeoutError, KeyError, ValueError):
        return None


