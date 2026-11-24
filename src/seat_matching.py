"""Pure seat-map normalization and matching rules."""

GRID_SIZE = 15

ACCESSIBLE_SEAT_TYPES = {"wheelchair", "companion"}
NEIGHBOR_KEYS = ("leftNeighbor", "rightNeighbor")


def parse_seat_grid(value):
    if not value:
        return []
    return [tuple(int(piece) for piece in part.split(":")) for part in value.split(",")]


def seat_matches_grid(row_position, x_position, selected_cells):
    if not selected_cells:
        return True
    row = min(GRID_SIZE - 1, int(row_position * GRID_SIZE))
    col = min(GRID_SIZE - 1, int(x_position * GRID_SIZE))
    return (row, col) in selected_cells


def normalized_seat_layout(data, matching_blocks):
    seats = data["seats"]
    matched_ids = {seat_id for block in matching_blocks for seat_id in block}
    background_svg = data.get("backgroundSvg")
    if background_svg:
        width = data["backgroundWidth"]
        height = data["backgroundHeight"]
        offset_x = data["mapOffsetX"]
        offset_y = data["mapOffsetY"]
    else:
        min_left = min(seat["x"] for seat in seats)
        min_top = min(seat["y"] for seat in seats)
        content_width = max(seat["x"] + seat["width"] for seat in seats) - min_left
        content_height = max(seat["y"] + seat["height"] for seat in seats) - min_top
        average_seat_size = max(
            sum(seat["width"] for seat in seats) / len(seats),
            sum(seat["height"] for seat in seats) / len(seats),
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
                "id": seat["id"],
                "type": seat["type"],
                "status": seat["status"],
                "x": seat["x"] + offset_x,
                "y": seat["y"] + offset_y,
                "width": seat["width"],
                "height": seat["height"],
                "matched": seat["id"] in matched_ids,
            }
            for seat in seats
        ],
    }
    if background_svg:
        layout["backgroundSvg"] = background_svg
    return layout


def seat_positioner(seats):
    """Return a function giving a seat's (row, x) position, each scaled to 0..1 across the map."""
    rows = [seat["row"] for seat in seats]
    xs = [seat["x"] for seat in seats]
    min_row, row_span = min(rows), max(max(rows) - min(rows), 1)
    min_x, x_span = min(xs), max(max(xs) - min(xs), 1)

    def position(seat):
        return (seat["row"] - min_row) / row_span, (seat["x"] - min_x) / x_span

    return position


def neighbor_ids(seat):
    return {seat[key] for key in NEIGHBOR_KEYS}


def seats_touch(left, right, use_links):
    """Whether `right` sits directly to the right of `left` in the same row.

    Some seat maps number columns straight across an aisle; only their neighbor links show the gap.
    """
    if right["column"] != left["column"] + 1:
        return False
    if not use_links:
        return True
    return left["id"] in neighbor_ids(right) or right["id"] in neighbor_ids(left)


def adjacent_blocks(seats, min_adjacent, selected_cells, exclude_accessible):
    """Return runs of adjacent available seat ids, one list per run."""
    available = [
        seat
        for seat in seats
        if seat["status"] == "A"
        and not (exclude_accessible and seat["type"] in ACCESSIBLE_SEAT_TYPES)
    ]
    # Maps without any neighbor links fall back to consecutive columns alone.
    use_links = any(seat[key] for seat in seats for key in NEIGHBOR_KEYS)
    position = seat_positioner(seats)
    by_row = {}
    for seat in available:
        row_position, x_position = position(seat)
        if not seat_matches_grid(row_position, x_position, selected_cells):
            continue
        by_row.setdefault(seat["row"], []).append(seat)

    blocks = []
    for row_seats in by_row.values():
        row_seats.sort(key=lambda seat: seat["column"])
        current = []
        for seat in row_seats:
            if current and not seats_touch(current[-1], seat, use_links):
                if len(current) >= min_adjacent:
                    blocks.append(current)
                current = []
            current.append(seat)
        if len(current) >= min_adjacent:
            blocks.append(current)

    return [[seat["id"] for seat in block] for block in blocks]


def ranked_adjacent_groups(seats, blocks, group_size, selected_cells):
    """Return exact-size adjacent groups, closest to the requested region first."""
    seat_by_id = {seat["id"]: seat for seat in seats}
    position = seat_positioner(seats)

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
            positions = [position(seat_by_id[seat_id]) for seat_id in group]
            row_position = sum(row for row, _ in positions) / group_size
            x_position = sum(x for _, x in positions) / group_size
            distance = (row_position - target_row) ** 2 + (x_position - target_x) ** 2
            groups.append((distance, group))

    groups.sort()
    return [group for _, group in groups]


def seat_map_match(data, min_adjacent, selected_cells, exclude_accessible):
    if not data["seats"]:
        return None
    blocks = adjacent_blocks(data["seats"], min_adjacent, selected_cells, exclude_accessible)
    if not blocks:
        # Most seat maps don't match; skip building a layout nobody will see.
        return None
    return seat_map_preview(data, blocks, min_adjacent, selected_cells)


def seat_map_preview(data, blocks, min_adjacent, selected_cells):
    """`blocks` are the seat map's `adjacent_blocks` for this search."""
    matching_groups = ranked_adjacent_groups(data["seats"], blocks, min_adjacent, selected_cells)
    return {
        "availableSeatCount": data["totalAvailableSeatCount"],
        "totalSeatCount": data["totalSeatCount"],
        "matchingGroups": matching_groups,
        "bestGroup": matching_groups[0] if matching_groups else [],
        "layout": normalized_seat_layout(data, blocks),
    }
