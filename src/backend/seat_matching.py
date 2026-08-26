"""Pure seat-map normalization and matching rules."""

# Must match GRID_SIZE in src/frontend/scripts/seat-grid.js.
GRID_SIZE = 15

ACCESSIBLE_SEAT_TYPES = {"wheelchair", "companion"}


def parse_seat_grid(value):
    cells = []
    for part in (value or "").split(","):
        pieces = part.split(":")
        if len(pieces) != 2:
            continue
        try:
            row, col = int(pieces[0]), int(pieces[1])
        except ValueError:
            continue
        if 0 <= row < GRID_SIZE and 0 <= col < GRID_SIZE:
            cells.append((row, col))
    return cells


def seat_matches_grid(row_position, x_position, selected_cells):
    if not selected_cells:
        return True
    row = min(GRID_SIZE - 1, int(row_position * GRID_SIZE))
    col = min(GRID_SIZE - 1, int(x_position * GRID_SIZE))
    return (row, col) in selected_cells


def normalized_seat_layout(data, matching_blocks):
    seats = data["seats"]
    matched_ids = {seat_id for block in matching_blocks for seat_id in block}
    background_svg = data.get("backgroundSvg") or ""
    background_width = data.get("backgroundWidth")
    background_height = data.get("backgroundHeight")
    if background_svg and background_width and background_height:
        width = max(float(background_width), 1)
        height = max(float(background_height), 1)
        offset_x = data.get("mapOffsetX", 0) or 0
        offset_y = data.get("mapOffsetY", 0) or 0
    else:
        background_svg = ""
        min_left = min(seat.get("x", 0) for seat in seats)
        min_top = min(seat.get("y", 0) for seat in seats)
        max_right = max(seat.get("x", 0) + seat.get("width", 0) for seat in seats)
        max_bottom = max(seat.get("y", 0) + seat.get("height", 0) for seat in seats)
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
        offset_x = padding - min_left
        offset_y = padding - min_top

    layout = {
        "width": width,
        "height": height,
        "seats": [
            {
                "id": seat.get("id", ""),
                "type": seat.get("type", "standard"),
                "status": seat.get("status", ""),
                "x": seat.get("x", 0) + offset_x,
                "y": seat.get("y", 0) + offset_y,
                "width": seat.get("width", 0),
                "height": seat.get("height", 0),
                "matched": seat.get("id", "") in matched_ids,
            }
            for seat in seats
        ],
    }
    if background_svg:
        layout["backgroundSvg"] = background_svg
    return layout


def adjacent_blocks(seats, min_adjacent, selected_cells, exclude_accessible):
    """Return runs of adjacent available seat ids, one list per run."""
    available = [
        seat
        for seat in seats
        if seat.get("status") == "A"
        and not (exclude_accessible and seat.get("type") in ACCESSIBLE_SEAT_TYPES)
    ]
    if not available:
        return []

    rows = [seat.get("row", 0) for seat in seats]
    xs = [seat.get("x", 0) for seat in seats]
    min_row, row_span = min(rows), max(max(rows) - min(rows), 1)
    min_x, x_span = min(xs), max(max(xs) - min(xs), 1)

    by_row = {}
    for seat in available:
        row_position = (seat.get("row", 0) - min_row) / row_span
        x_position = (seat.get("x", 0) - min_x) / x_span
        if not seat_matches_grid(row_position, x_position, selected_cells):
            continue
        by_row.setdefault(seat.get("row", 0), []).append(seat)

    blocks = []
    for row_seats in by_row.values():
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

    return [[seat.get("id", "") for seat in block] for block in blocks]


def ranked_adjacent_groups(seats, blocks, group_size, selected_cells):
    """Return exact-size adjacent groups, closest to the requested region first."""
    seat_by_id = {seat.get("id", ""): seat for seat in seats if seat.get("id")}
    rows = [seat.get("row", 0) for seat in seats]
    xs = [seat.get("x", 0) for seat in seats]
    min_row, row_span = min(rows), max(max(rows) - min(rows), 1)
    min_x, x_span = min(xs), max(max(xs) - min(xs), 1)

    if selected_cells:
        target_row = sum((row + 0.5) / GRID_SIZE for row, _ in selected_cells) / len(selected_cells)
        target_x = sum((column + 0.5) / GRID_SIZE for _, column in selected_cells) / len(
            selected_cells
        )
    else:
        target_row = target_x = 0.5

    groups = []
    for block in blocks:
        for start in range(len(block) - group_size + 1):
            group = block[start : start + group_size]
            group_seats = [seat_by_id[seat_id] for seat_id in group if seat_id in seat_by_id]
            if len(group_seats) != group_size:
                continue
            row_position = (
                sum((seat.get("row", 0) - min_row) / row_span for seat in group_seats) / group_size
            )
            x_position = (
                sum((seat.get("x", 0) - min_x) / x_span for seat in group_seats) / group_size
            )
            distance = (row_position - target_row) ** 2 + (x_position - target_x) ** 2
            groups.append((distance, group))

    groups.sort(key=lambda item: (item[0], item[1]))
    return [group for _, group in groups]


def showtime_seat_match(
    showtime, min_adjacent, selected_cells, exclude_accessible, seat_map_loader
):
    data = seat_map_loader(showtime.get("showtimeHashCode"))
    if not data:
        return None
    seats = data.get("seats") or []
    blocks = adjacent_blocks(seats, min_adjacent, selected_cells, exclude_accessible)
    if not blocks:
        return None
    matching_groups = ranked_adjacent_groups(seats, blocks, min_adjacent, selected_cells)
    available_count = data.get("totalAvailableSeatCount")
    if available_count is None:
        available_count = sum(1 for seat in seats if seat.get("status") == "A")
    total_count = data.get("totalSeatCount")
    if total_count is None:
        total_count = len(seats)
    layout = normalized_seat_layout(data, blocks)
    return {
        "availableSeatCount": available_count,
        "totalSeatCount": total_count,
        "matchingGroups": matching_groups,
        "bestGroup": matching_groups[0],
        "layout": layout,
    }
