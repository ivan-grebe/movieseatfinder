from pathlib import Path
from urllib.parse import urlsplit
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict, defaultdict, deque
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
import html
import json
import os
import re
import requests
import threading
import time

from .location import (
    distance_miles,
    filter_theatres_within_radius,
    geocode_zip,
    reverse_geocode_zip,
    resolve_search_location,
    validate_coordinates,
)


USER_AGENT = "MovieSeatFinder/1.0 (local development app)"
FANDANGO_ORIGIN = "https://www.fandango.com"
SITE_NAME = "Movie Seat Finder"
SITE_DESCRIPTION = (
    "Find real Fandango showtimes with reserved seating and preview live seat maps "
    "before you buy movie tickets."
)
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "frontend"
INDEX_HTML = STATIC_DIR / "index.html"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
SEAT_MAP_CACHE = OrderedDict()
SEAT_MAP_CACHE_LOCK = threading.Lock()
SEAT_MAP_TTL_SECONDS = 300
SEAT_MAP_CACHE_MAX = 200

# Cache of theatre+showtime payloads keyed by (zip, radius, date). Showtimes do
# not change second to second, so a short TTL lets the theatres/movies/formats/
# search endpoints share one fetch instead of each re-downloading the same data.
THEATRES_CACHE = OrderedDict()
THEATRES_CACHE_LOCK = threading.Lock()
THEATRES_TTL_SECONDS = 300
THEATRES_CACHE_MAX = 240
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
MAX_DATE_RANGE_DAYS = 14
MAX_TEXT_PARAM_LENGTH = 120
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")
RATE_LIMIT_MAX_KEYS = 1000

RATE_LIMITS = {
    "/api/search": (10, 60),
    "/api/formats": (30, 60),
    "/api/movies": (30, 60),
    "/api/theatres": (30, 60),
}
RATE_LIMIT_HISTORY = defaultdict(deque)
RATE_LIMIT_LOCK = threading.Lock()
HTTP_LOCAL = threading.local()


def http_session():
    session = getattr(HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        HTTP_LOCAL.session = session
    return session


def fetch_json(url, timeout=20):
    response = http_session().get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_text(url, timeout=20):
    response = http_session().get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    return response.text


def fandango_json(path, params=None, referer="https://www.fandango.com/movie-theaters", timeout=30):
    response = http_session().get(
        f"{FANDANGO_ORIGIN}{path}",
        params=params,
        headers={
            "User-Agent": "Mozilla/5.0 MovieSeatFinder/1.0",
            "Accept": "application/json",
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_range(start, end):
    current = parse_date(start)
    final = parse_date(end)
    if final < current:
        raise ValueError("End date must be on or after start date.")
    if (final - current).days > MAX_DATE_RANGE_DAYS:
        raise ValueError(f"Date range must be {MAX_DATE_RANGE_DAYS} days or fewer.")
    while current <= final:
        yield current.isoformat()
        current += timedelta(days=1)


def validate_radius(radius):
    if radius is None:
        raise ValueError("Enter a search radius.")
    if radius < 1 or radius > 100:
        raise ValueError("Radius must be between 1 and 100 miles.")
    return radius


def validate_short_text(value, field_name):
    value = (value or "").strip()
    if len(value) > MAX_TEXT_PARAM_LENGTH:
        raise ValueError(f"{field_name} must be {MAX_TEXT_PARAM_LENGTH} characters or fewer.")
    return value


def validate_time(value, field_name):
    if not TIME_PATTERN.fullmatch(value or ""):
        raise ValueError(f"{field_name} must be in HH:MM format.")
    hours, minutes = [int(part) for part in value.split(":")]
    if hours > 23 or minutes > 59:
        raise ValueError(f"{field_name} must be a valid time.")
    return value


def safe_fandango_url(value):
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme != "https":
        return ""
    if parts.netloc not in {"www.fandango.com", "tickets.fandango.com"}:
        return ""
    return value


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
        try:
            response = http_session().post(
                endpoint,
                data=query.encode("utf-8"),
                headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            break
        except (requests.RequestException, TimeoutError) as error:
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
    requested = normalized_text(requested or "any")
    if requested == "any":
        return True

    values = [
        normalized_text(value)
        for value in [format_name, *(amenity_text or "").split(",")]
        if normalized_text(value)
    ]
    value_set = set(values)
    exact_checks = {
        "standard": {"standard"},
        "imax": {"imax"},
        "imax 70": {"imax 70", "imax 70mm", "imax 70 mm"},
        "imax 70mm": {"imax 70", "imax 70mm", "imax 70 mm"},
        "imax70": {"imax 70", "imax 70mm", "imax 70 mm"},
        "imax with laser": {"imax with laser", "imax laser"},
        "dolby": {"dolby", "dolby cinema"},
        "screenx": {"screenx"},
        "4dx": {"4dx"},
        "35mm": {"35mm", "35 mm"},
        "35 mm": {"35mm", "35 mm"},
        "70mm": {"70mm", "70 mm"},
        "70 mm": {"70mm", "70 mm"},
    }
    if requested == "imax" and "imax" in value_set:
        return True
    if requested == "imax" and any(value.startswith("imax ") for value in values):
        return False
    combined = normalized_text(f"{format_name} {amenity_text}")
    if requested in ("35mm", "35 mm"):
        return bool(re.search(r"\b35\s*mm\b|\b35mm\b", combined))
    if requested in ("70mm", "70 mm"):
        return bool(re.search(r"\b70\s*mm\b|\b70mm\b", combined))
    return bool(value_set & exact_checks.get(requested, {requested}))


def fandango_theatres(zip_code, radius, show_date=None, origin=None):
    origin_key = tuple(round(value, 5) for value in origin) if origin else ()
    key = (str(zip_code), str(radius), show_date or "", origin_key)
    now = time.monotonic()
    with THEATRES_CACHE_LOCK:
        entry = THEATRES_CACHE.get(key)
        if entry is not None and now - entry[0] < THEATRES_TTL_SECONDS:
            THEATRES_CACHE.move_to_end(key)
            return entry[1]
        if entry is not None:
            THEATRES_CACHE.pop(key, None)
    # Fandango accepts a ZIP rather than coordinates. For a browser-location
    # search, get a broad candidate set and enforce the exact circle locally.
    # Fandango caps reliable ZIP searches below its nominal 100-mile maximum.
    # A 25–50 mile candidate set covers ZIP-boundary cases, then the exact
    # coordinate filter below enforces the user's requested radius.
    fetch_radius = min(max(radius * 2, 25), 50) if origin else radius
    theatres = _fetch_fandango_theatres(zip_code, fetch_radius, show_date)
    if origin:
        theatres = filter_theatres_within_radius(theatres, origin[0], origin[1], radius)
    with THEATRES_CACHE_LOCK:
        THEATRES_CACHE[key] = (now, theatres)
        THEATRES_CACHE.move_to_end(key)
        while len(THEATRES_CACHE) > THEATRES_CACHE_MAX:
            THEATRES_CACHE.popitem(last=False)
    return theatres


def fandango_theatres_by_date(zip_code, radius, dates, origin=None):
    """Fetch (and cache) theatre+showtime payloads for many dates in parallel."""
    results = {}
    unique_dates = list(dict.fromkeys(dates))
    if not unique_dates:
        return results
    workers = min(8, len(unique_dates))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(fandango_theatres, zip_code, radius, show_date, origin): show_date
            for show_date in unique_dates
        }
        for future in as_completed(future_map):
            show_date = future_map[future]
            try:
                results[show_date] = future.result()
            except (requests.RequestException, TimeoutError, KeyError, ValueError):
                results[show_date] = []
    return results


def _fetch_fandango_theatres(zip_code, radius, show_date=None):
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
            "latitude": float(theatre["geo"]["latitude"]) if theatre.get("geo", {}).get("latitude") is not None else None,
            "longitude": float(theatre["geo"]["longitude"]) if theatre.get("geo", {}).get("longitude") is not None else None,
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


def should_list_amenity_format(name, visible_terms):
    normalized_name = normalized_text(name)
    visible = {normalized_text(term) for term in visible_terms if normalized_text(term)}
    if normalized_name == "imax" and any(term.startswith("imax ") for term in visible):
        return False
    return True


def movies_from_dated_theatre_payloads(zip_code, radius, start_date, end_date, theatre_query="", origin=None):
    seen = set()
    movies = []
    dates = list(date_range(start_date, end_date))
    theatres_by_date = fandango_theatres_by_date(zip_code, radius, dates, origin)
    for show_date in dates:
        theatres = [
            theatre for theatre in theatres_by_date.get(show_date, [])
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


def formats_from_dated_theatre_payloads(zip_code, radius, movie_query, start_date, end_date, theatre_query="", origin=None):
    formats = set()
    dates = list(date_range(start_date, end_date))
    theatres_by_date = fandango_theatres_by_date(zip_code, radius, dates, origin)
    for show_date in dates:
        theatres = [
            theatre for theatre in theatres_by_date.get(show_date, [])
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
                        visible_terms = [format_name]
                        for amenity in amenity_text.split(","):
                            amenity = amenity.strip()
                            if any(term in amenity.lower() for term in ("imax", "dolby", "4dx", "screenx", "35mm", "70mm")):
                                formats.add(amenity)
                                visible_terms.append(amenity)
                        for amenity in group.get("amenities") or []:
                            name = clean_title(amenity.get("name", ""))
                            if (
                                any(term in name.lower() for term in ("imax", "dolby", "4dx", "screenx", "35mm", "70mm"))
                                and should_list_amenity_format(name, visible_terms)
                            ):
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


def poster_url(movie, size="300"):
    poster = movie.get("poster")
    if isinstance(poster, dict):
        sizes = poster.get("size")
        if isinstance(sizes, dict):
            return sizes.get(size) or sizes.get("200") or sizes.get("full") or ""
    return ""


def format_runtime(minutes):
    try:
        total = int(minutes)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    hours, mins = divmod(total, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    return f"{hours}h" if hours else f"{mins}m"


def format_rating(rating):
    value = (rating or "").strip()
    return {"PG13": "PG-13", "NC17": "NC-17"}.get(value, value)


def movie_meta(movie):
    return {
        "poster": poster_url(movie),
        "rating": format_rating(movie.get("rating")),
        "runtime": format_runtime(movie.get("runtime")),
        "genres": [genre for genre in (movie.get("genres") or []) if genre][:2],
    }


def normalize_showtimes(theatre, movies, show_date):
    normalized = []
    for movie in movies or []:
        movie_title = clean_title(movie.get("title", ""))
        meta = movie_meta(movie)
        for variant in movie.get("variants") or []:
            format_name = clean_title(variant.get("filmFormatHeader", "Standard")) or "Standard"
            for group in variant.get("amenityGroups") or []:
                amenity_text = clean_title(group.get("amenityString", ""))
                amenities = [clean_title(item.get("name", "")) for item in group.get("amenities") or []]
                if not amenity_text:
                    amenity_text = ", ".join(item for item in amenities if item)
                format_tags = ", ".join(
                    dict.fromkeys(
                        [part.strip() for part in amenity_text.split(",") if part.strip()] +
                        [item for item in amenities if item]
                    )
                )
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
                        "formatTags": format_tags,
                        "reservedSeating": bool(group.get("hasReservedSeating")),
                        "showtimeHashCode": showtime.get("showtimeHashCode"),
                        "ticketUrl": showtime.get("ticketingJumpPageURL"),
                        **meta,
                    })
    return normalized


def seat_map(showtime_hash):
    if not showtime_hash:
        return None
    now = time.monotonic()
    with SEAT_MAP_CACHE_LOCK:
        cached = SEAT_MAP_CACHE.get(showtime_hash)
        if cached and now - cached[0] < SEAT_MAP_TTL_SECONDS:
            SEAT_MAP_CACHE.move_to_end(showtime_hash)
            return cached[1]
        if cached:
            SEAT_MAP_CACHE.pop(showtime_hash, None)

    data = fandango_json(
        f"/napi/seatMap/{showtime_hash}",
        referer="https://www.fandango.com/",
    )
    with SEAT_MAP_CACHE_LOCK:
        SEAT_MAP_CACHE[showtime_hash] = (now, data)
        SEAT_MAP_CACHE.move_to_end(showtime_hash)
        while len(SEAT_MAP_CACHE) > SEAT_MAP_CACHE_MAX:
            SEAT_MAP_CACHE.popitem(last=False)
    return data


from .seat_matching import (
    adjacent_blocks,
    human_zone,
    normalized_seat_layout,
    parse_seat_grid,
    primary_zone,
    seat_band,
    seat_matches_filter,
    seat_matches_grid,
    seat_zone_labels,
    showtime_seat_match,
)
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


app = FastAPI(title="Movie Seat Finder")


def site_origin(request):
    configured_url = os.environ.get("SITE_URL", "").strip().rstrip("/")
    if configured_url:
        return configured_url
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_proto and forwarded_host:
        return f"{forwarded_proto.split(',')[0]}://{forwarded_host.split(',')[0]}".rstrip("/")
    return str(request.base_url).rstrip("/")


def seo_context(request):
    origin = site_origin(request)
    return {
        "__SITE_NAME__": SITE_NAME,
        "__SITE_DESCRIPTION__": SITE_DESCRIPTION,
        "__SITE_URL__": origin,
        "__CANONICAL_URL__": f"{origin}/",
        "__OG_IMAGE_URL__": f"{origin}/og-image.svg",
    }


def render_index(request):
    markup = INDEX_HTML.read_text(encoding="utf-8")
    for token, value in seo_context(request).items():
        markup = markup.replace(token, value)
    return markup


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data: https://images.fandango.com; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.get("/", include_in_schema=False)
def index(request: Request):
    return HTMLResponse(render_index(request))


@app.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    origin = site_origin(request)
    return PlainTextResponse(
        "\n".join([
            "User-agent: *",
            "Allow: /",
            "",
            f"Sitemap: {origin}/sitemap.xml",
        ]) + "\n"
    )


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    origin = site_origin(request)
    today = date.today().isoformat()
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{origin}/</loc>
    <lastmod>{today}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>
"""
    return Response(content=xml, media_type="application/xml")


@app.get("/site.webmanifest", include_in_schema=False)
def webmanifest():
    return JSONResponse({
        "name": SITE_NAME,
        "short_name": "Seat Finder",
        "description": SITE_DESCRIPTION,
        "start_url": "/",
        "display": "standalone",
        "background_color": "#e7e4dd",
        "theme_color": "#b23b34",
        "icons": [{
            "src": "/favicon.svg",
            "sizes": "any",
            "type": "image/svg+xml",
        }],
    })


def enforce_rate_limit(request, path):
    limit, window = RATE_LIMITS[path]
    client_host = request.client.host if request.client else "unknown"
    key = (client_host, path)
    now = time.monotonic()
    with RATE_LIMIT_LOCK:
        if len(RATE_LIMIT_HISTORY) > RATE_LIMIT_MAX_KEYS:
            stale_keys = [
                history_key for history_key, history in RATE_LIMIT_HISTORY.items()
                if not history or now - history[-1] > window
            ]
            for history_key in stale_keys:
                RATE_LIMIT_HISTORY.pop(history_key, None)
                if len(RATE_LIMIT_HISTORY) <= RATE_LIMIT_MAX_KEYS:
                    break
        history = RATE_LIMIT_HISTORY[key]
        while history and now - history[0] > window:
            history.popleft()
        if len(history) >= limit:
            raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment and try again.")
        history.append(now)


def upstream_error(message, error):
    raise HTTPException(status_code=502, detail=f"{message}: {error}")


@app.get("/api/theatres")
def api_theatres(request: Request, zip: str = "", radius: float | None = None, lat: float | None = None, lon: float | None = None):
    enforce_rate_limit(request, "/api/theatres")
    try:
        zip_code = zip.strip()
        radius = validate_radius(radius)
        try:
            search_zip, origin, place = resolve_search_location(zip_code, lat, lon)
        except (requests.RequestException, TimeoutError, KeyError):
            raise HTTPException(
                status_code=400,
                detail="We could not determine a nearby ZIP for your location. Enter a ZIP code instead.",
            )

        try:
            theatres = fandango_theatres(search_zip, radius, origin=origin)
            limited_radius = radius
        except (requests.RequestException, TimeoutError, KeyError):
            try:
                theatres = overpass_theatres(origin[0], origin[1], radius)
                limited_radius = radius
            except (requests.RequestException, TimeoutError):
                if radius <= 3:
                    raise
                limited_radius = 3
                theatres = overpass_theatres(origin[0], origin[1], limited_radius)

        return {
            "place": place,
            "requestedRadius": radius,
            "searchedRadius": limited_radius,
            "theatres": theatres,
        }
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except (requests.RequestException, TimeoutError, KeyError) as error:
        upstream_error("Could not load real theatre data", error)


@app.get("/api/movies")
def api_movies(
    request: Request,
    zip: str = "",
    radius: float | None = None,
    startDate: str = "",
    endDate: str = "",
    theatre: str = "",
    lat: float | None = None,
    lon: float | None = None,
):
    enforce_rate_limit(request, "/api/movies")
    try:
        zip_code = zip.strip()
        radius = validate_radius(radius)
        start_date = startDate or date.today().isoformat()
        end_date = endDate or start_date
        theatre_query = validate_short_text(theatre, "Theatre").lower()
        list(date_range(start_date, end_date))
        try:
            search_zip, origin, _ = resolve_search_location(zip_code, lat, lon)
            movies = movies_from_dated_theatre_payloads(search_zip, radius, start_date, end_date, theatre_query, origin)
            if not movies:
                theatres = fandango_theatres(search_zip, radius, origin=origin)
                movies = normalize_movie_list_from_theatres(theatres)
            if movies:
                return {"movies": movies}
        except (requests.RequestException, TimeoutError, KeyError):
            pass
        return {"movies": fandango_movies()}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except (requests.RequestException, TimeoutError) as error:
        upstream_error("Could not load real movie data", error)


@app.get("/api/formats")
def api_formats(
    request: Request,
    zip: str = "",
    radius: float | None = None,
    movie: str = "",
    startDate: str = "",
    endDate: str = "",
    theatre: str = "",
    lat: float | None = None,
    lon: float | None = None,
):
    enforce_rate_limit(request, "/api/formats")
    try:
        zip_code = zip.strip()
        radius = validate_radius(radius)
        movie_query = validate_short_text(movie, "Movie")
        start_date = startDate or date.today().isoformat()
        end_date = endDate or start_date
        theatre_query = validate_short_text(theatre, "Theatre").lower()
        if not movie_query:
            return {"formats": []}
        search_zip, origin, _ = resolve_search_location(zip_code, lat, lon)
        formats = formats_from_dated_theatre_payloads(
            search_zip,
            radius,
            movie_query,
            start_date,
            end_date,
            theatre_query,
            origin,
        )
        return {"formats": sorted(formats)}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except (requests.RequestException, TimeoutError, KeyError) as error:
        upstream_error("Could not load real format data", error)


@app.get("/api/search")
def api_search(
    request: Request,
    zip: str = "",
    radius: float | None = None,
    theatre: str = "",
    movie: str = "",
    format: str = "any",
    startDate: str = "",
    endDate: str = "",
    startTime: str = "00:00",
    endTime: str = "23:59",
    adjacentSeats: int = 1,
    seatArea: str = "any",
    seatGrid: str = "",
    excludeAccessible: str = "0",
    page: int = 1,
    pageSize: int = DEFAULT_PAGE_SIZE,
    lat: float | None = None,
    lon: float | None = None,
):
    enforce_rate_limit(request, "/api/search")
    try:
        zip_code = zip.strip()
        radius = validate_radius(radius)
        theatre_query = validate_short_text(theatre, "Theatre").lower()
        movie_query = validate_short_text(movie, "Movie")
        requested_format = validate_short_text(format, "Format") or "any"
        start_date = startDate or date.today().isoformat()
        end_date = endDate or start_date
        start_time = validate_time(startTime, "Start time")
        end_time = validate_time(endTime, "End time")
        min_adjacent = min(max(adjacentSeats, 1), 10)
        seat_filter = validate_short_text(seatArea, "Seat area") or "any"
        selected_cells = parse_seat_grid(seatGrid)
        exclude_accessible = excludeAccessible.lower() in ("1", "true", "yes", "on")
        page = max(page, 1)
        page_size = min(max(pageSize, 1), MAX_PAGE_SIZE)
        page_start = (page - 1) * page_size
        page_end = page_start + page_size

        search_zip, origin, _ = resolve_search_location(zip_code, lat, lon)
        if not movie_query:
            raise HTTPException(status_code=400, detail="Enter a movie title.")

        matches = []
        candidates = []
        dates = list(date_range(start_date, end_date))
        theatres_by_date = fandango_theatres_by_date(search_zip, radius, dates, origin)
        for show_date in dates:
            dated_theatres = [
                theatre_item for theatre_item in theatres_by_date.get(show_date, [])
                if not theatre_query or theatre_query in theatre_item["name"].lower()
            ]
            for theatre_item in dated_theatres:
                has_candidate_movie = any(
                    movie_matches(movie_item.get("title", ""), movie_query)
                    for movie_item in theatre_item.get("rawMovies", [])
                )
                if not has_candidate_movie:
                    continue
                for showtime in normalize_showtimes(theatre_item, theatre_item.get("rawMovies", []), show_date):
                    if not movie_matches(showtime["movieTitle"], movie_query):
                        continue
                    if not format_matches(showtime["format"], showtime.get("formatTags", showtime["amenities"]), requested_format):
                        continue
                    if showtime["time"] < start_time or showtime["time"] > end_time:
                        continue
                    candidates.append((theatre_item, showtime))
        candidates.sort(key=lambda candidate: (
            candidate[0]["distanceMiles"],
            candidate[1]["date"],
            candidate[1]["time"],
            candidate[1]["movieTitle"],
        ))

        def check_candidate(candidate):
            theatre_item, showtime = candidate
            seat_match = showtime_seat_match(showtime, min_adjacent, seat_filter, selected_cells, exclude_accessible, seat_map_loader=seat_map)
            if not seat_match:
                return None
            return {
                "theatre": {
                    "name": theatre_item["name"],
                    "address": theatre_item["address"],
                    "distanceMiles": theatre_item["distanceMiles"],
                    "website": theatre_item["website"],
                    "source": theatre_item["source"],
                },
                "movieTitle": showtime["movieTitle"],
                "date": showtime["date"],
                "time": showtime["time"],
                "displayTime": showtime["screenReaderTime"],
                "format": showtime["format"],
                "amenities": showtime["amenities"],
                "ticketUrl": safe_fandango_url(showtime["ticketUrl"]),
                "poster": showtime.get("poster", ""),
                "rating": showtime.get("rating", ""),
                "runtime": showtime.get("runtime", ""),
                "genres": showtime.get("genres", []),
                "seatMap": seat_match,
            }

        checked_seat_maps = 0
        if candidates:
            worker_count = min(12, max(4, len(candidates)))
            batch_size = worker_count * 2
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                for offset in range(0, len(candidates), batch_size):
                    batch = candidates[offset:offset + batch_size]
                    future_map = {executor.submit(check_candidate, candidate): candidate for candidate in batch}
                    checked_seat_maps += len(batch)
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
                    if len(matches) > page_end:
                        break

        page_matches = matches[page_start:page_end]
        return {
            "matches": page_matches,
            "page": page,
            "pageSize": page_size,
            "hasPreviousPage": page > 1,
            "hasNextPage": len(matches) > page_end,
            "matchedThrough": min(len(matches), page_end),
            "checkedShowtimes": len(candidates),
            "checkedSeatMaps": checked_seat_maps,
            "source": "Fandango NAPI",
        }
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except (requests.RequestException, TimeoutError, KeyError) as error:
        upstream_error("Could not search real showtimes/seats", error)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
