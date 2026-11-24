"""Payloads shaped like Fandango's, with every field the app reads."""


def seat(seat_id, row, column, x, status="A", kind="standard", left="", right=""):
    return {
        "id": seat_id,
        "row": row,
        "column": column,
        "x": x,
        "y": row * 10,
        "width": 8,
        "height": 8,
        "status": status,
        "type": kind,
        "leftNeighbor": left,
        "rightNeighbor": right,
    }


def seat_map(seats):
    return {
        "seats": seats,
        "backgroundSvg": "<svg/>",
        "backgroundWidth": 100,
        "backgroundHeight": 100,
        "mapOffsetX": 0,
        "mapOffsetY": 0,
        "totalAvailableSeatCount": sum(seat["status"] == "A" for seat in seats),
        "totalSeatCount": len(seats),
    }


def showtime(code, time, film_formats=(), date="2026-07-20"):
    return {
        "type": "available",
        "expired": False,
        "ticketingDate": f"{date}+{time}",
        "showtimeHashCode": code,
        "ticketingJumpPageURL": f"https://tickets.fandango.com/{code}",
        "filmFormat": [{"filterName": name} for name in film_formats],
    }


def amenity_group(amenities, showtimes):
    return {
        "amenityString": ", ".join(amenities),
        "amenities": [{"name": name} for name in amenities],
        "showtimes": showtimes,
    }


def movie(title, format_header, groups):
    return {
        "title": title,
        "rating": "PG-13",
        "runtime": 120,
        "genres": ["Drama"],
        "poster": {"size": {"300": "https://images.fandango.com/poster.jpg"}},
        "variants": [{"filmFormatHeader": format_header, "amenityGroups": groups}],
    }


def theatre(name, latitude, longitude):
    return {
        "name": name,
        "fullAddress": f"1 Main St, {name}",
        "geo": {"latitude": latitude, "longitude": longitude},
    }
