from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode, parse_qs, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import html
import json
import math
import re


USER_AGENT = "MovieSeatFinder/1.0 (local development app)"
FANDANGO_ORIGIN = "https://www.fandango.com"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
SEAT_MAP_CACHE = {}


def fetch_json(url, timeout=20):
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
      return json.loads(response.read().decode("utf-8"))


def fetch_text(url, timeout=20):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fandango_json(path, params=None, referer="https://www.fandango.com/movie-theaters", timeout=30):
    query = f"?{urlencode(params)}" if params else ""
    request = Request(
        f"{FANDANGO_ORIGIN}{path}{query}",
        headers={
            "User-Agent": "Mozilla/5.0 MovieSeatFinder/1.0",
            "Accept": "application/json",
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def distance_miles(lat1, lon1, lat2, lon2):
    radius = 3958.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geocode_zip(zip_code):
    data = fetch_json(f"https://api.zippopotam.us/us/{zip_code}")
    place = data["places"][0]
    return {
        "label": f'{place["place name"]}, {place["state abbreviation"]} {data["post code"]}',
        "lat": float(place["latitude"]),
        "lon": float(place["longitude"]),
    }


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_range(start, end):
    current = parse_date(start)
    final = parse_date(end)
    if final < current:
        raise ValueError("End date must be on or after start date.")
    if (final - current).days > 14:
        raise ValueError("Use a date range of 14 days or less.")
    while current <= final:
        yield current.isoformat()
        current += timedelta(days=1)


def overpass_theatres(lat, lon, radius_miles):
    radius_meters = int(float(radius_miles) * 1609.344)
    query = f"""
    [out:json][timeout:25];
    (
      node["amenity"="cinema"](around:{radius_meters},{lat},{lon});
      way["amenity"="cinema"](around:{radius_meters},{lat},{lon});
      relation["amenity"="cinema"](around:{radius_meters},{lat},{lon});
    );
    out center tags;
    """
    last_error = None
    data = None
    for endpoint in OVERPASS_ENDPOINTS:
        request = Request(
            endpoint,
            data=query.encode("utf-8"),
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except (HTTPError, URLError, TimeoutError) as error:
            last_error = error

    if data is None:
        raise last_error

    theatres = []
    seen = set()
    for element in data.get("elements", []):
        tags = element.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        element_lat = element.get("lat") or element.get("center", {}).get("lat")
        element_lon = element.get("lon") or element.get("center", {}).get("lon")
        if element_lat is None or element_lon is None:
            continue

        key = (name.lower(), round(float(element_lat), 4), round(float(element_lon), 4))
        if key in seen:
            continue
        seen.add(key)

        street = " ".join(part for part in [
            tags.get("addr:housenumber", ""),
            tags.get("addr:street", ""),
        ] if part).strip()
        city = tags.get("addr:city", "")
        state = tags.get("addr:state", "")
        address = ", ".join(part for part in [street, city, state] if part)

        theatres.append({
            "name": name,
            "address": address,
            "website": tags.get("website") or tags.get("contact:website") or "",
            "distanceMiles": distance_miles(lat, lon, float(element_lat), float(element_lon)),
            "source": "OpenStreetMap",
        })

    return sorted(theatres, key=lambda theatre: theatre["distanceMiles"])


def clean_title(value):
    return re.sub(r"\s+", " ", value or "").strip()


def normalized_text(value):
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def movie_matches(title, query):
    query = normalized_text(query)
    title = normalized_text(title)
    return query and (query in title or title in query)


def format_matches(format_name, amenity_text, requested):
    requested = (requested or "any").lower()
    if requested == "any":
        return True
    haystack = f"{format_name} {amenity_text}".lower()
    checks = {
        "standard": ["standard"],
        "imax": ["imax"],
        "imax70": ["imax 70", "imax 70mm", "imax 70 mm"],
        "dolby": ["dolby"],
        "screenx": ["screenx"],
        "4dx": ["4dx"],
        "35mm": ["35mm", "35 mm"],
        "70mm": ["70mm", "70 mm"],
    }
    return any(term in haystack for term in checks.get(requested, [requested]))


def fandango_theatres(zip_code, radius, show_date=None):
    radius_value = int(radius) if float(radius).is_integer() else radius
    params = {"zipCode": zip_code, "radius": radius_value, "limit": 100}
    if show_date:
        params["date"] = show_date
    data = fandango_json(
        "/napi/theaterswithshowtimes",
        params,
    )
    theatres = []
    for theatre in data.get("theaters", []):
        theatres.append({
            "name": theatre.get("name", ""),
            "address": theatre.get("fullAddress") or ", ".join(
                part for part in [
                    theatre.get("address1", ""),
                    theatre.get("city", ""),
                    theatre.get("state", ""),
                    theatre.get("zip", ""),
                ] if part
            ),
            "distanceMiles": float(theatre.get("distance") or 0),
            "website": f'{FANDANGO_ORIGIN}{theatre.get("theaterPageUrl", "")}',
            "source": "Fandango",
            "fandangoId": theatre.get("id", ""),
            "chainCode": theatre.get("chainCode", ""),
            "hasReservedSeating": bool(theatre.get("hasReservedSeating")),
            "hasShowtimes": bool(theatre.get("hasShowtimes")),
            "formats": theatre.get("formats") or [],
            "rawMovies": theatre.get("movies") or [],
        })
    return sorted(theatres, key=lambda item: item["distanceMiles"])


def movie_has_matching_format(movie, requested_format):
    for variant in movie.get("variants") or []:
        format_name = clean_title(variant.get("filmFormatHeader", "Standard")) or "Standard"
        for group in variant.get("amenityGroups") or []:
            amenity_text = clean_title(group.get("amenityString", ""))
            amenities = ", ".join(clean_title(item.get("name", "")) for item in group.get("amenities") or [])
            if format_matches(format_name, f"{amenity_text}, {amenities}", requested_format):
                return True
    return requested_format == "any"


def movies_from_dated_theatre_payloads(zip_code, radius, start_date, end_date, theatre_query=""):
    seen = set()
    movies = []
    for show_date in date_range(start_date, end_date):
        theatres = [
            theatre for theatre in fandango_theatres(zip_code, radius, show_date)
            if not theatre_query or theatre_query in theatre["name"].lower()
        ]
        for theatre in theatres:
            for movie in theatre.get("rawMovies", []):
                movie_id = str(movie.get("id") or "")
                title = clean_title(movie.get("title", ""))
                key = movie_id or normalized_text(title)
                if not title or key in seen:
                    continue
                seen.add(key)
                movies.append({
                    "title": title,
                    "fandangoId": movie_id,
                    "source": f"Fandango live showtimes {start_date} to {end_date}",
                })
    return sorted(movies, key=lambda movie: movie["title"])


def formats_from_dated_theatre_payloads(zip_code, radius, movie_query, start_date, end_date, theatre_query=""):
    formats = set()
    for show_date in date_range(start_date, end_date):
        theatres = [
            theatre for theatre in fandango_theatres(zip_code, radius, show_date)
            if not theatre_query or theatre_query in theatre["name"].lower()
        ]
        for theatre in theatres:
            for movie in theatre.get("rawMovies", []):
                if not movie_matches(movie.get("title", ""), movie_query):
                    continue
                for variant in movie.get("variants") or []:
                    format_name = clean_title(variant.get("filmFormatHeader", "Standard")) or "Standard"
                    formats.add(format_name)
                    for group in variant.get("amenityGroups") or []:
                        amenity_text = clean_title(group.get("amenityString", ""))
                        for amenity in amenity_text.split(","):
                            amenity = amenity.strip()
                            if any(term in amenity.lower() for term in ("imax", "dolby", "4dx", "screenx", "35mm", "70mm")):
                                formats.add(amenity)
                        for amenity in group.get("amenities") or []:
                            name = clean_title(amenity.get("name", ""))
                            if any(term in name.lower() for term in ("imax", "dolby", "4dx", "screenx", "35mm", "70mm")):
                                formats.add(name)
    return sorted(formats)


def normalize_movie_list_from_theatres(theatres):
    seen = set()
    movies = []
    for theatre in theatres:
        for movie in theatre.get("rawMovies", []):
            movie_id = str(movie.get("id") or "")
            title = clean_title(movie.get("title", ""))
            key = movie_id or normalized_text(title)
            if not title or key in seen:
                continue
            seen.add(key)
            movies.append({
                "title": title,
                "fandangoId": movie_id,
                "source": "Fandango live theatre showtimes",
            })
    return sorted(movies, key=lambda movie: movie["title"])


def movies_for_theatres_and_dates(theatres, start_date, end_date, theatre_query=""):
    seen = set()
    movies = []
    selected_theatres = [
        theatre for theatre in theatres
        if not theatre_query or theatre_query in theatre["name"].lower()
    ]
    for theatre in selected_theatres:
        for show_date in date_range(start_date, end_date):
            for movie in showtimes_for_theatre(theatre, show_date):
                movie_id = str(movie.get("id") or "")
                title = clean_title(movie.get("title", ""))
                key = movie_id or normalized_text(title)
                if not title or key in seen:
                    continue
                seen.add(key)
                movies.append({
                    "title": title,
                    "fandangoId": movie_id,
                    "source": f"Fandango live showtimes {start_date} to {end_date}",
                })
    return sorted(movies, key=lambda movie: movie["title"])


def showtimes_for_theatre(theatre, show_date):
    params = {
        "chainCode": theatre.get("chainCode") or "",
        "startDate": show_date,
        "isdesktop": "true",
        "partnerRestrictedTicketing": "false",
    }
    referer = theatre.get("website") or "https://www.fandango.com/movie-theaters"
    data = fandango_json(
        f"/napi/theaterMovieShowtimes/{theatre['fandangoId']}",
        params,
        referer=referer,
    )
    return data.get("viewModel", {}).get("movies", [])


def normalize_showtimes(theatre, show_date):
    normalized = []
    for movie in showtimes_for_theatre(theatre, show_date):
        movie_title = clean_title(movie.get("title", ""))
        for variant in movie.get("variants") or []:
            format_name = clean_title(variant.get("filmFormatHeader", "Standard")) or "Standard"
            for group in variant.get("amenityGroups") or []:
                amenity_text = clean_title(group.get("amenityString", ""))
                amenities = [clean_title(item.get("name", "")) for item in group.get("amenities") or []]
                if not amenity_text:
                    amenity_text = ", ".join(item for item in amenities if item)
                for showtime in group.get("showtimes") or []:
                    if showtime.get("type") != "available" or showtime.get("expired"):
                        continue
                    ticketing_date = showtime.get("ticketingDate") or ""
                    if "+" in ticketing_date:
                        show_date_part, show_time_part = ticketing_date.split("+", 1)
                    else:
                        show_date_part, show_time_part = show_date, ""
                    normalized.append({
                        "theatre": theatre,
                        "movieTitle": movie_title,
                        "movieId": movie.get("id"),
                        "date": show_date_part,
                        "time": show_time_part,
                        "screenReaderTime": showtime.get("screenReaderTime") or showtime.get("date") or show_time_part,
                        "format": format_name,
                        "amenities": amenity_text,
                        "reservedSeating": bool(group.get("hasReservedSeating")),
                        "showtimeHashCode": showtime.get("showtimeHashCode"),
                        "ticketUrl": showtime.get("ticketingJumpPageURL"),
                    })
    return normalized


def normalize_showtimes_from_movies(theatre, movies, show_date):
    normalized = []
    for movie in movies or []:
        movie_title = clean_title(movie.get("title", ""))
        for variant in movie.get("variants") or []:
            format_name = clean_title(variant.get("filmFormatHeader", "Standard")) or "Standard"
            for group in variant.get("amenityGroups") or []:
                amenity_text = clean_title(group.get("amenityString", ""))
                amenities = [clean_title(item.get("name", "")) for item in group.get("amenities") or []]
                if not amenity_text:
                    amenity_text = ", ".join(item for item in amenities if item)
                for showtime in group.get("showtimes") or []:
                    if showtime.get("type") != "available" or showtime.get("expired"):
                        continue
                    ticketing_date = showtime.get("ticketingDate") or ""
                    if "+" in ticketing_date:
                        show_date_part, show_time_part = ticketing_date.split("+", 1)
                    else:
                        show_date_part, show_time_part = show_date, ""
                    normalized.append({
                        "theatre": theatre,
                        "movieTitle": movie_title,
                        "movieId": movie.get("id"),
                        "date": show_date_part,
                        "time": show_time_part,
                        "screenReaderTime": showtime.get("screenReaderTime") or showtime.get("date") or show_time_part,
                        "format": format_name,
                        "amenities": amenity_text,
                        "reservedSeating": bool(group.get("hasReservedSeating")),
                        "showtimeHashCode": showtime.get("showtimeHashCode"),
                        "ticketUrl": showtime.get("ticketingJumpPageURL"),
                    })
    return normalized


def seat_map(showtime_hash):
    if not showtime_hash:
        return None
    if showtime_hash not in SEAT_MAP_CACHE:
        SEAT_MAP_CACHE[showtime_hash] = fandango_json(
            f"/napi/seatMap/{showtime_hash}",
            referer="https://www.fandango.com/",
        )
    return SEAT_MAP_CACHE[showtime_hash]


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
    max_right = max((seat.get("x", 0) + seat.get("width", 0) for seat in seats), default=0)
    max_bottom = max((seat.get("y", 0) + seat.get("height", 0) for seat in seats), default=0)
    width = data.get("totalWidth") or data.get("backgroundWidth") or max_right or 1
    height = data.get("totalHeight") or data.get("backgroundHeight") or max_bottom or 1

    return {
        "width": width,
        "height": height,
        "seats": [{
            "id": seat.get("id", ""),
            "row": seat.get("row"),
            "column": seat.get("column"),
            "type": seat.get("type", "standard"),
            "status": seat.get("status", ""),
            "x": seat.get("x", 0),
            "y": seat.get("y", 0),
            "width": seat.get("width", 0),
            "height": seat.get("height", 0),
            "matched": seat.get("id", "") in matched_ids,
        } for seat in seats],
    }


def adjacent_blocks(seats, min_adjacent, requested_area, selected_cells=None):
    selected_cells = selected_cells or []
    available = [seat for seat in seats if seat.get("status") == "A"]
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


def showtime_seat_match(showtime, min_adjacent, requested_area, selected_cells=None):
    try:
        data = seat_map(showtime.get("showtimeHashCode"))
        if not data:
            return None
        seats = data.get("seats") or []
        blocks = adjacent_blocks(seats, min_adjacent, requested_area, selected_cells)
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
            "matchingBlocks": blocks,
            "layout": normalized_seat_layout(data, blocks),
        }
    except (HTTPError, URLError, TimeoutError, KeyError, ValueError):
        return None


def title_from_slug(slug):
    words = slug.split("-")
    if words and re.fullmatch(r"20\d\d", words[-1]):
        words = words[:-1]
    small_words = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
    title_words = []
    for index, word in enumerate(words):
        if index > 0 and word in small_words:
            title_words.append(word)
        else:
            title_words.append(word.capitalize())
    return " ".join(title_words)


def fandango_movies():
    page = fetch_text("https://www.fandango.com/movies-in-theaters")
    seen = set()
    movies = []
    pattern = re.compile(r'href="/(?P<slug>[a-z0-9-]+)-(?P<id>\d+)/movie-overview"', re.IGNORECASE)
    for match in pattern.finditer(page):
        movie_id = match.group("id")
        if movie_id in seen:
            continue
        seen.add(movie_id)
        slug = html.unescape(match.group("slug"))
        movies.append({
            "title": title_from_slug(slug),
            "fandangoId": movie_id,
            "source": "Fandango public movies page",
        })
    return sorted(movies, key=lambda movie: movie["title"])


class Handler(SimpleHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/theatres":
            self.handle_theatres(parsed)
            return
        if parsed.path == "/api/movies":
            self.handle_movies()
            return
        if parsed.path == "/api/formats":
            self.handle_formats(parsed)
            return
        if parsed.path == "/api/search":
            self.handle_search(parsed)
            return
        super().do_GET()

    def handle_theatres(self, parsed):
        try:
            params = parse_qs(parsed.query)
            zip_code = params.get("zip", [""])[0].strip()
            radius = float(params.get("radius", ["10"])[0])
            if not re.fullmatch(r"\d{5}", zip_code):
                self.send_json(400, {"error": "Enter a valid 5 digit US ZIP code."})
                return
            if radius < 1 or radius > 100:
                self.send_json(400, {"error": "Radius must be between 1 and 100 miles."})
                return

            try:
                theatres = fandango_theatres(zip_code, radius)
                place = f"ZIP {zip_code}"
                limited_radius = radius
            except (HTTPError, URLError, TimeoutError, KeyError):
                location = geocode_zip(zip_code)
                try:
                    theatres = overpass_theatres(location["lat"], location["lon"], radius)
                    limited_radius = radius
                except (HTTPError, URLError, TimeoutError):
                    if radius <= 3:
                        raise
                    limited_radius = 3
                    theatres = overpass_theatres(location["lat"], location["lon"], limited_radius)
                place = location["label"]

            self.send_json(200, {
                "place": place,
                "requestedRadius": radius,
                "searchedRadius": limited_radius,
                "theatres": theatres
            })
        except (HTTPError, URLError, TimeoutError, KeyError, ValueError) as error:
            self.send_json(502, {"error": f"Could not load real theatre data: {error}"})

    def handle_movies(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            zip_code = params.get("zip", [""])[0].strip()
            radius = float(params.get("radius", ["25"])[0])
            start_date = params.get("startDate", [date.today().isoformat()])[0]
            end_date = params.get("endDate", [start_date])[0]
            theatre_query = params.get("theatre", [""])[0].strip().lower()
            if re.fullmatch(r"\d{5}", zip_code):
                movies = movies_from_dated_theatre_payloads(zip_code, radius, start_date, end_date, theatre_query)
                if not movies:
                    theatres = fandango_theatres(zip_code, radius)
                    movies = normalize_movie_list_from_theatres(theatres)
                if movies:
                    self.send_json(200, {"movies": movies})
                    return
            self.send_json(200, {"movies": fandango_movies()})
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            self.send_json(502, {"error": f"Could not load real movie data: {error}"})

    def handle_formats(self, parsed):
        try:
            params = parse_qs(parsed.query)
            zip_code = params.get("zip", [""])[0].strip()
            radius = float(params.get("radius", ["25"])[0])
            movie_query = params.get("movie", [""])[0].strip()
            start_date = params.get("startDate", [date.today().isoformat()])[0]
            end_date = params.get("endDate", [start_date])[0]
            theatre_query = params.get("theatre", [""])[0].strip().lower()
            if not movie_query:
                self.send_json(200, {"formats": []})
                return
            formats = formats_from_dated_theatre_payloads(
                zip_code,
                radius,
                movie_query,
                start_date,
                end_date,
                theatre_query,
            )
            self.send_json(200, {"formats": sorted(formats)})
        except (HTTPError, URLError, TimeoutError, KeyError, ValueError) as error:
            self.send_json(502, {"error": f"Could not load real format data: {error}"})

    def handle_search(self, parsed):
        try:
            params = parse_qs(parsed.query)
            zip_code = params.get("zip", [""])[0].strip()
            radius = float(params.get("radius", ["25"])[0])
            theatre_query = params.get("theatre", [""])[0].strip().lower()
            movie_query = params.get("movie", [""])[0].strip()
            requested_format = params.get("format", ["any"])[0]
            start_date = params.get("startDate", [date.today().isoformat()])[0]
            end_date = params.get("endDate", [start_date])[0]
            start_time = params.get("startTime", ["00:00"])[0]
            end_time = params.get("endTime", ["23:59"])[0]
            min_adjacent = int(params.get("adjacentSeats", ["1"])[0])
            seat_filter = params.get("seatArea", ["any"])[0]
            selected_cells = parse_seat_grid(params.get("seatGrid", [""])[0])

            if not re.fullmatch(r"\d{5}", zip_code):
                self.send_json(400, {"error": "Enter a valid 5 digit US ZIP code."})
                return
            if not movie_query:
                self.send_json(400, {"error": "Enter a movie title."})
                return

            matches = []
            candidates = []

            for show_date in date_range(start_date, end_date):
                dated_theatres = [
                    theatre for theatre in fandango_theatres(zip_code, radius, show_date)
                    if not theatre_query or theatre_query in theatre["name"].lower()
                ]
                for theatre in dated_theatres:
                    has_candidate_movie = any(
                        movie_matches(movie.get("title", ""), movie_query)
                        and movie_has_matching_format(movie, requested_format)
                        for movie in theatre.get("rawMovies", [])
                    )
                    if not has_candidate_movie:
                        continue
                    for showtime in normalize_showtimes_from_movies(theatre, theatre.get("rawMovies", []), show_date):
                        if not movie_matches(showtime["movieTitle"], movie_query):
                            continue
                        if not format_matches(showtime["format"], showtime["amenities"], requested_format):
                            continue
                        if showtime["time"] < start_time or showtime["time"] > end_time:
                            continue
                        candidates.append((theatre, showtime))

            def check_candidate(candidate):
                theatre, showtime = candidate
                seat_match = showtime_seat_match(showtime, min_adjacent, seat_filter, selected_cells)
                if not seat_match:
                    return None
                return {
                    "theatre": {
                        "name": theatre["name"],
                        "address": theatre["address"],
                        "distanceMiles": theatre["distanceMiles"],
                        "website": theatre["website"],
                        "source": theatre["source"],
                    },
                    "movieTitle": showtime["movieTitle"],
                    "date": showtime["date"],
                    "time": showtime["time"],
                    "displayTime": showtime["screenReaderTime"],
                    "format": showtime["format"],
                    "amenities": showtime["amenities"],
                    "ticketUrl": showtime["ticketUrl"],
                    "seatMap": seat_match,
                }

            if candidates:
                worker_count = min(16, max(4, len(candidates)))
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    future_map = {executor.submit(check_candidate, candidate): candidate for candidate in candidates}
                    for future in as_completed(future_map):
                        result = future.result()
                        if result:
                            matches.append(result)

            matches.sort(key=lambda item: (
                item["theatre"]["distanceMiles"],
                item["date"],
                item["time"],
                item["movieTitle"],
            ))

            self.send_json(200, {
                "matches": matches,
                "checkedShowtimes": len(candidates),
                "checkedSeatMaps": len(candidates),
                "source": "Fandango NAPI",
            })
        except (HTTPError, URLError, TimeoutError, KeyError, ValueError) as error:
            self.send_json(502, {"error": f"Could not search real showtimes/seats: {error}"})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 4173), Handler)
    print("Serving Movie Seat Finder at http://127.0.0.1:4173/index.html")
    server.serve_forever()
