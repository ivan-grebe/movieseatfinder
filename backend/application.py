import base64
import hashlib
import ipaddress
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import requests
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from limits import RateLimitItemPerSecond
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from pydantic import StringConstraints

from .location import (
    ZipNotFoundError,
    filter_theatres_within_radius,
    http_session,
    resolve_search_location,
)
from .seat_matching import (
    parse_seat_grid,
    showtime_seat_match,
)

# The Mozilla prefix matters: Fandango's WAF rejects obviously non-browser UAs.
FANDANGO_USER_AGENT = "Mozilla/5.0 MovieSeatFinder/1.0"
FANDANGO_ORIGIN = "https://www.fandango.com"
SITE_NAME = "Movie Seat Finder"
SITE_DESCRIPTION = (
    "Find real Fandango showtimes with reserved seating and preview live seat maps before you buy movie tickets."
)
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "frontend"
INLINE_STYLES = (STATIC_DIR / "styles.bundle.css").read_text(encoding="utf-8")
INLINE_STYLE_HASH = base64.b64encode(hashlib.sha256(INLINE_STYLES.encode("utf-8")).digest()).decode("ascii")
VERSIONED_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
VERSIONED_ASSET_SUFFIXES = {".js", ".png", ".svg"}
# The only frontend files the site serves; source modules stay private and the
# raw index.html template is only reachable through the rendered "/" route.
PUBLIC_ASSETS = {"app.bundle.js", "favicon.svg", "og-image.png"}
# Content-derived ?v= values, so cached assets roll over automatically on deploy
# instead of relying on hand-bumped version strings.
ASSET_VERSIONS = {
    name: hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:12] for name in ("app.bundle.js", "favicon.svg")
}
# Static tokens are substituted once at import; only the origin-dependent SEO
# tokens vary per request.
INDEX_TEMPLATE = (
    (STATIC_DIR / "index.html")
    .read_text(encoding="utf-8")
    .replace("__INLINE_STYLES__", INLINE_STYLES)
    .replace("__BUNDLE_VERSION__", ASSET_VERSIONS["app.bundle.js"])
    .replace("__FAVICON_VERSION__", ASSET_VERSIONS["favicon.svg"])
)
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
MAX_DATE_RANGE_DAYS = 14
MAX_TEXT_PARAM_LENGTH = 120
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=MAX_TEXT_PARAM_LENGTH),
]
RequiredShortText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_TEXT_PARAM_LENGTH,
    ),
]
TimeText = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"),
]
Radius = Annotated[float, Query(ge=1, le=100)]
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
AdjacentSeatCount = Annotated[int, Query(ge=1, le=10)]
SearchSort = Literal["earliest", "latest", "nearest"]

RATE_LIMITS = {
    "/api/events/ticket-click": RateLimitItemPerSecond(60, 60),
    # Sort changes and pagination each re-run a full search, so a normal
    # browsing session can issue a burst of these; upstream fetches are cached.
    "/api/search": RateLimitItemPerSecond(30, 60),
    "/api/formats": RateLimitItemPerSecond(30, 60),
    "/api/movies": RateLimitItemPerSecond(30, 60),
    "/api/theatres": RateLimitItemPerSecond(30, 60),
}
RATE_LIMITER = MovingWindowRateLimiter(MemoryStorage())
# Errors that mean an upstream service failed rather than a bug in this app.
UPSTREAM_ERRORS = (requests.RequestException, KeyError)
LOGGER = logging.getLogger(__name__)
if not LOGGER.handlers:
    LOGGER.addHandler(logging.StreamHandler(sys.stdout))
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


def log_cache_hit(cache_name, age_seconds):
    """Log cache usage without including a request's search data."""
    LOGGER.info(
        "event=cache_hit cache=%s age_ms=%d",
        cache_name,
        max(0, round(age_seconds * 1000)),
    )


class TtlCache:
    """Thread-safe TTL + LRU cache for upstream payloads."""

    def __init__(self, name, ttl_seconds, max_entries):
        self.name = name
        self._entries = TTLCache(maxsize=max_entries, ttl=ttl_seconds)
        self._lock = threading.Lock()

    def get(self, key):
        now = time.monotonic()
        with self._lock:
            try:
                stored_at, value = self._entries[key]
            except KeyError:
                return None
        log_cache_hit(self.name, now - stored_at)
        return value

    def set(self, key, value):
        with self._lock:
            self._entries[key] = (time.monotonic(), value)


SEAT_MAP_CACHE = TtlCache("seat_map", ttl_seconds=300, max_entries=200)
# Theatre+showtime payloads keyed by (zip, radius, date, origin). Showtimes do
# not change second to second, so a short TTL lets the theatres/movies/formats/
# search endpoints share one fetch instead of each re-downloading the same data.
THEATRES_CACHE = TtlCache("theatres", ttl_seconds=300, max_entries=240)


def fandango_json(path, params=None, referer="https://www.fandango.com/movie-theaters", timeout=30):
    response = http_session().get(
        f"{FANDANGO_ORIGIN}{path}",
        params=params,
        headers={
            "User-Agent": FANDANGO_USER_AGENT,
            "Accept": "application/json",
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def date_range(start, end):
    if end < start:
        raise ValueError("End date must be on or after start date.")
    if (end - start).days > MAX_DATE_RANGE_DAYS:
        raise ValueError(f"Date range must be {MAX_DATE_RANGE_DAYS} days or fewer.")
    current = start
    while current <= end:
        yield current.isoformat()
        current += timedelta(days=1)


def search_sort_key(distance, show_date, show_time, theatre_name, sort_order):
    """Return a deterministic key for supported result sort orders.

    Date and time are fixed-width, so their digits concatenate into one
    integer that sorts chronologically and negates cleanly for "latest".
    """
    chronology = int(show_date.replace("-", "") + show_time.replace(":", ""))
    if sort_order == "nearest":
        return (distance, chronology, theatre_name)
    if sort_order == "latest":
        return (-chronology, distance, theatre_name)
    return (chronology, distance, theatre_name)


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


def clean_title(value):
    return re.sub(r"\s+", " ", value or "").strip()


def normalized_text(value):
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def movie_matches(title, query):
    query = normalized_text(query)
    title = normalized_text(title)
    return bool(title) and title == query


def format_matches(format_name, amenity_text, requested):
    if requested == "any":
        return True
    requested_formats = [normalized_text(value) for value in requested.split(",")]
    return any(
        format_matches_one(format_name, amenity_text, requested_format)
        for requested_format in requested_formats
        if requested_format
    )


# Requested formats whose match set is more than the literal requested value.
FORMAT_ALIASES = {
    "imax 70": {"imax 70", "imax 70mm", "imax 70 mm"},
    "imax 70mm": {"imax 70", "imax 70mm", "imax 70 mm"},
    "imax70": {"imax 70", "imax 70mm", "imax 70 mm"},
    "imax with laser": {"imax with laser", "imax laser"},
    "dolby": {"dolby", "dolby cinema"},
}


def format_matches_one(format_name, amenity_text, requested):
    values = [
        normalized_text(value) for value in [format_name, *(amenity_text or "").split(",")] if normalized_text(value)
    ]
    value_set = set(values)
    if requested == "imax":
        # Plain IMAX must not match the premium IMAX variants.
        return "imax" in value_set and not any(value.startswith("imax ") for value in values)
    combined = normalized_text(f"{format_name} {amenity_text}")
    if requested in ("35mm", "35 mm"):
        return bool(re.search(r"\b35\s*mm\b|\b35mm\b", combined))
    if requested in ("70mm", "70 mm"):
        return bool(re.search(r"\b70\s*mm\b|\b70mm\b", combined))
    return bool(value_set & FORMAT_ALIASES.get(requested, {requested}))


def fandango_theatres(zip_code, radius, origin, show_date=None):
    key = (zip_code, radius, show_date or "", origin)
    cached = THEATRES_CACHE.get(key)
    if cached is not None:
        return cached
    # Fandango accepts a ZIP rather than coordinates. For a browser-location
    # search, get a broad candidate set and enforce the exact circle locally.
    # Over-fetch around the ZIP used by Fandango so an exact coordinate search
    # still includes candidates near ZIP boundaries. Fandango accepts up to the
    # same 100-mile maximum exposed by this application.
    fetch_radius = min(max(radius * 2, 25), 100)
    theatres = _fetch_fandango_theatres(zip_code, fetch_radius, show_date)
    theatres = filter_theatres_within_radius(theatres, origin[0], origin[1], radius)
    THEATRES_CACHE.set(key, theatres)
    return theatres


def fandango_theatres_by_date(zip_code, radius, dates, origin):
    """Fetch (and cache) theatre+showtime payloads for many dates in parallel."""
    results = {}
    successful_dates = 0
    last_error = None
    with ThreadPoolExecutor(max_workers=min(8, len(dates))) as executor:
        future_map = {
            executor.submit(fandango_theatres, zip_code, radius, origin, show_date): show_date for show_date in dates
        }
        for future in as_completed(future_map):
            show_date = future_map[future]
            try:
                results[show_date] = future.result()
                successful_dates += 1
            except (*UPSTREAM_ERRORS, ValueError) as error:
                last_error = error
                results[show_date] = []
    if successful_dates == 0:
        raise last_error
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
        theatres.append(
            {
                "name": theatre.get("name", ""),
                "address": theatre.get("fullAddress")
                or ", ".join(
                    part
                    for part in [
                        theatre.get("address1", ""),
                        theatre.get("city", ""),
                        theatre.get("state", ""),
                        theatre.get("zip", ""),
                    ]
                    if part
                ),
                "distanceMiles": float(theatre.get("distance") or 0),
                "latitude": float(theatre["geo"]["latitude"])
                if theatre.get("geo", {}).get("latitude") is not None
                else None,
                "longitude": float(theatre["geo"]["longitude"])
                if theatre.get("geo", {}).get("longitude") is not None
                else None,
                "rawMovies": theatre.get("movies") or [],
            }
        )
    return sorted(theatres, key=lambda item: item["distanceMiles"])


def dated_theatres(zip_code, radius, start_date, end_date, theatre_query, origin):
    """Yield theatres for each fetched date, filtered by theatre name."""
    dates = list(date_range(start_date, end_date))
    theatres_by_date = fandango_theatres_by_date(zip_code, radius, dates, origin)
    for show_date in dates:
        for theatre in theatres_by_date[show_date]:
            if theatre_query and theatre_query not in theatre["name"].lower():
                continue
            yield theatre


def movies_from_dated_theatre_payloads(zip_code, radius, start_date, end_date, theatre_query, origin):
    seen = set()
    movies = []
    for theatre in dated_theatres(zip_code, radius, start_date, end_date, theatre_query, origin):
        for movie in theatre["rawMovies"]:
            title = clean_title(movie.get("title", ""))
            key = normalized_text(title)
            if not title or key in seen:
                continue
            seen.add(key)
            movies.append({"title": title})
    return sorted(movies, key=lambda movie: movie["title"])


PREMIUM_FORMAT_TERMS = ("imax", "dolby", "4dx", "screenx", "35mm", "70mm")


def should_list_amenity_format(name, visible_terms):
    normalized_name = normalized_text(name)
    visible = {normalized_text(term) for term in visible_terms if normalized_text(term)}
    return not (normalized_name == "imax" and any(term.startswith("imax ") for term in visible))


def group_formats(format_name, group):
    """Every format label a single amenity group exposes for its showtimes."""
    group_showtimes = group.get("showtimes") or [{}]
    labels = {showtime_format(format_name, group, showtime) for showtime in group_showtimes}
    visible = [format_name, *labels]
    for amenity in clean_title(group.get("amenityString", "")).split(","):
        amenity = amenity.strip()
        if any(term in amenity.lower() for term in PREMIUM_FORMAT_TERMS):
            labels.add(amenity)
            visible.append(amenity)
    for amenity in group.get("amenities") or []:
        name = clean_title(amenity.get("name", ""))
        if any(term in name.lower() for term in PREMIUM_FORMAT_TERMS) and should_list_amenity_format(name, visible):
            labels.add(name)
    return labels


def formats_from_dated_theatre_payloads(zip_code, radius, movie_query, start_date, end_date, theatre_query, origin):
    formats = set()
    for theatre in dated_theatres(zip_code, radius, start_date, end_date, theatre_query, origin):
        for movie in theatre["rawMovies"]:
            if not movie_matches(movie.get("title", ""), movie_query):
                continue
            for variant in movie.get("variants") or []:
                format_name = clean_title(variant.get("filmFormatHeader", "Standard")) or "Standard"
                for group in variant.get("amenityGroups") or []:
                    formats.update(group_formats(format_name, group))
    return sorted(formats)


def poster_url(movie):
    poster = movie.get("poster")
    if isinstance(poster, dict):
        sizes = poster.get("size")
        if isinstance(sizes, dict):
            return sizes.get("300") or sizes.get("200") or sizes.get("full") or ""
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


def display_showtime_time(value):
    """Turn Fandango's 24-hour ticketing time into a compact display time."""
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
    except (AttributeError, TypeError, ValueError):
        return value
    suffix = "AM" if hour < 12 else "PM"
    hour = hour % 12 or 12
    return f"{hour}:{minute:02d} {suffix}"


def showtime_format(format_header, group, showtime):
    """Prefer Fandango's showtime-specific format over broad category labels."""
    for format_item in showtime.get("filmFormat") or []:
        name = clean_title(format_item.get("filterName", ""))
        if name:
            return name

    header = clean_title(format_header)
    if normalized_text(header) not in {"premium format", "format", ""}:
        return header

    premium_terms = (
        "imax",
        "dolby",
        "4dx",
        "screenx",
        "rpx",
        "prime",
        "xl",
        "dbox",
        "d-box",
        "reald",
    )
    for amenity in group.get("amenities") or []:
        name = clean_title(amenity.get("name", ""))
        if name and any(term in normalized_text(name) for term in premium_terms):
            return name
    return header or "Standard"


def normalize_showtimes(movies):
    normalized = []
    for movie in movies:
        movie_title = clean_title(movie.get("title", ""))
        meta = movie_meta(movie)
        for variant in movie.get("variants") or []:
            format_name = clean_title(variant.get("filmFormatHeader", "Standard")) or "Standard"
            for group in variant.get("amenityGroups") or []:
                amenity_text = clean_title(group.get("amenityString", ""))
                amenities = [
                    name
                    for name in (clean_title(item.get("name", "")) for item in group.get("amenities") or [])
                    if name
                ]
                if not amenity_text:
                    amenity_text = ", ".join(amenities)
                for showtime in group.get("showtimes") or []:
                    if showtime.get("type") != "available" or showtime.get("expired"):
                        continue
                    ticketing_date = showtime.get("ticketingDate") or ""
                    if "+" not in ticketing_date:
                        # Without a ticketing date the showtime has no time and
                        # could never pass the search's time-window filter.
                        continue
                    show_date, show_time = ticketing_date.split("+", 1)
                    format_label = showtime_format(format_name, group, showtime)
                    format_tags = ", ".join(
                        dict.fromkeys(
                            [format_label]
                            + [part.strip() for part in amenity_text.split(",") if part.strip()]
                            + amenities
                        )
                    )
                    normalized.append(
                        {
                            "movieTitle": movie_title,
                            "date": show_date,
                            "time": show_time,
                            "displayTime": display_showtime_time(show_time),
                            "format": format_label,
                            "amenities": amenity_text,
                            "formatTags": format_tags,
                            "showtimeHashCode": showtime.get("showtimeHashCode"),
                            "ticketUrl": showtime.get("ticketingJumpPageURL"),
                            **meta,
                        }
                    )
    return normalized


def seat_map(showtime_hash):
    if not showtime_hash:
        return None
    cached = SEAT_MAP_CACHE.get(showtime_hash)
    if cached is not None:
        return cached
    data = fandango_json(
        f"/napi/seatMap/{showtime_hash}",
        referer="https://www.fandango.com/",
    )
    SEAT_MAP_CACHE.set(showtime_hash, data)
    return data


app = FastAPI(title="Movie Seat Finder")


def normalized_origin(value):
    try:
        parts = urlsplit((value or "").strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    if parts.username or parts.password or parts.path not in {"", "/"} or parts.query or parts.fragment:
        return None

    hostname = parts.hostname.rstrip(".")
    try:
        ipaddress.ip_address(hostname)
        rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    except ValueError:
        labels = hostname.split(".")
        if not labels or any(
            not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels
        ):
            return None
        rendered_host = hostname.lower()
    if port is not None:
        rendered_host = f"{rendered_host}:{port}"
    return f"{parts.scheme}://{rendered_host}"


def site_origin(request):
    configured_url = os.environ.get("SITE_URL", "").strip().rstrip("/")
    configured_origin = normalized_origin(configured_url)
    if configured_origin:
        return configured_origin
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_proto and forwarded_host:
        forwarded_origin = normalized_origin(
            f"{forwarded_proto.split(',')[0].strip()}://{forwarded_host.split(',')[0].strip()}"
        )
        if forwarded_origin:
            return forwarded_origin
    return normalized_origin(str(request.base_url)) or "http://localhost"


def seo_context(request):
    origin = site_origin(request)
    return {
        "__SITE_NAME__": SITE_NAME,
        "__SITE_DESCRIPTION__": SITE_DESCRIPTION,
        "__SITE_URL__": origin,
        "__CANONICAL_URL__": f"{origin}/",
        "__OG_IMAGE_URL__": f"{origin}/og-image.png",
    }


def render_index(request):
    markup = INDEX_TEMPLATE
    for token, value in seo_context(request).items():
        markup = markup.replace(token, value)
    return markup


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; "
        f"style-src 'self' 'sha256-{INLINE_STYLE_HASH}'; "
        "img-src 'self' data: https://images.fandango.com; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "require-trusted-types-for 'script'; trusted-types 'none'"
    )
    suffix = Path(request.url.path).suffix.lower()
    if response.status_code == 200 and request.query_params.get("v") and suffix in VERSIONED_ASSET_SUFFIXES:
        response.headers["Cache-Control"] = VERSIONED_ASSET_CACHE_CONTROL
        response.headers["CDN-Cache-Control"] = VERSIONED_ASSET_CACHE_CONTROL
        response.headers["Vercel-CDN-Cache-Control"] = VERSIONED_ASSET_CACHE_CONTROL
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(_request, exc):
    content = exc.detail if isinstance(exc.detail, dict) else {"error": exc.detail}
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_request, _exc):
    return JSONResponse(
        status_code=400,
        content={"error": "One of the search values is invalid. Adjust the form and try again."},
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(request, exc):
    LOGGER.error(
        "Unhandled request error for %s",
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(
        status_code=500,
        content={"error": "We could not complete that search. Please try again."},
    )


@app.get("/", include_in_schema=False)
def index(request: Request):
    return HTMLResponse(render_index(request))


@app.get("/index.html", include_in_schema=False)
def index_html():
    # The on-disk file is an unrendered template; never serve it raw.
    return RedirectResponse("/", status_code=308)


@app.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    origin = site_origin(request)
    return PlainTextResponse(
        "\n".join(
            [
                "User-agent: *",
                "Allow: /",
                "",
                f"Sitemap: {origin}/sitemap.xml",
            ]
        )
        + "\n"
    )


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    origin = site_origin(request)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{origin}/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>
"""
    return Response(content=xml, media_type="application/xml")


@app.get("/site.webmanifest", include_in_schema=False)
def webmanifest():
    return JSONResponse(
        {
            "name": SITE_NAME,
            "short_name": "Seat Finder",
            "description": SITE_DESCRIPTION,
            "start_url": "/",
            "display": "standalone",
            # Match the page's own light background and dark chrome tint.
            "background_color": "#fff7f6",
            "theme_color": "#12151c",
            "icons": [
                {
                    "src": f"/favicon.svg?v={ASSET_VERSIONS['favicon.svg']}",
                    "sizes": "any",
                    "type": "image/svg+xml",
                }
            ],
        }
    )


def rate_limit_client_key(request):
    """Key limits by the real client, not the fronting proxy.

    Behind Vercel/other proxies request.client.host is the proxy address, which
    would make every user share one bucket. The platform sets the caller as the
    first x-forwarded-for hop. Note the history still lives in process memory,
    so on serverless it only bounds each instance rather than global traffic.
    """
    forwarded_for = request.headers.get("x-forwarded-for", "")
    first_hop = forwarded_for.split(",")[0].strip()
    if first_hop:
        return first_hop
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request, path):
    if not RATE_LIMITER.hit(RATE_LIMITS[path], path, rate_limit_client_key(request)):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a moment and try again.",
        )


def upstream_error(message, error):
    raise HTTPException(status_code=502, detail=f"{message}: {error}") from error


def api_search_location(zip_code, lat, lon):
    """Resolve location while preserving which field needs attention."""
    try:
        return resolve_search_location(zip_code, lat, lon)
    except ZipNotFoundError as error:
        raise HTTPException(
            status_code=400,
            detail={"error": str(error), "code": "location"},
        ) from error
    except UPSTREAM_ERRORS as error:
        if lat is not None:
            message = "We couldn't determine a ZIP code for your location. Enter a ZIP code instead."
            status_code = 400
        else:
            message = "ZIP lookup is temporarily unavailable. Try again."
            status_code = 502
        raise HTTPException(
            status_code=status_code,
            detail={"error": message, "code": "location"},
        ) from error


@app.post("/api/events/ticket-click", status_code=204)
def ticket_click(request: Request):
    enforce_rate_limit(request, "/api/events/ticket-click")
    LOGGER.info("event=ticket_click")
    return Response(status_code=204)


@app.get("/api/theatres")
def api_theatres(
    request: Request,
    radius: Radius,
    zip_code: Annotated[ShortText, Query(alias="zip")] = "",
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
):
    enforce_rate_limit(request, "/api/theatres")
    try:
        search_zip, origin, place = api_search_location(zip_code, lat, lon)
        theatres = fandango_theatres(search_zip, radius, origin)
        # The UI only needs names for its theatre picker.
        return {
            "place": place,
            "theatres": [{"name": theatre["name"]} for theatre in theatres],
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except UPSTREAM_ERRORS as error:
        upstream_error("Could not load real theatre data", error)


@app.get("/api/movies")
def api_movies(
    request: Request,
    radius: Radius,
    zip_code: Annotated[ShortText, Query(alias="zip")] = "",
    startDate: date | None = None,
    endDate: date | None = None,
    theatre: ShortText = "",
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
):
    enforce_rate_limit(request, "/api/movies")
    try:
        start_date = startDate or date.today()
        end_date = endDate or start_date
        theatre_query = theatre.lower()
        search_zip, origin, _ = api_search_location(zip_code, lat, lon)
        movies = movies_from_dated_theatre_payloads(search_zip, radius, start_date, end_date, theatre_query, origin)
        return {"movies": movies}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except UPSTREAM_ERRORS as error:
        upstream_error("Could not load real movie data", error)


@app.get("/api/formats")
def api_formats(
    request: Request,
    radius: Radius,
    zip_code: Annotated[ShortText, Query(alias="zip")] = "",
    movie: ShortText = "",
    startDate: date | None = None,
    endDate: date | None = None,
    theatre: ShortText = "",
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
):
    enforce_rate_limit(request, "/api/formats")
    try:
        movie_query = movie
        start_date = startDate or date.today()
        end_date = endDate or start_date
        theatre_query = theatre.lower()
        if not movie_query:
            return {"formats": []}
        search_zip, origin, _ = api_search_location(zip_code, lat, lon)
        formats = formats_from_dated_theatre_payloads(
            search_zip,
            radius,
            movie_query,
            start_date,
            end_date,
            theatre_query,
            origin,
        )
        return {"formats": formats}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except UPSTREAM_ERRORS as error:
        upstream_error("Could not load real format data", error)


def collect_candidate_showtimes(
    search_zip,
    radius,
    start_date,
    end_date,
    theatre_query,
    movie_query,
    requested_format,
    start_time,
    end_time,
    origin,
):
    """Gather (theatre, showtime) pairs that pass every non-seat filter."""
    candidates = []
    for theatre in dated_theatres(search_zip, radius, start_date, end_date, theatre_query, origin):
        raw_movies = theatre["rawMovies"]
        if not any(movie_matches(movie.get("title", ""), movie_query) for movie in raw_movies):
            continue
        for showtime in normalize_showtimes(raw_movies):
            if not movie_matches(showtime["movieTitle"], movie_query):
                continue
            if not format_matches(showtime["format"], showtime["formatTags"], requested_format):
                continue
            if showtime["time"] < start_time or showtime["time"] > end_time:
                continue
            candidates.append((theatre, showtime))
    return candidates


def seat_checked_matches(candidates, page_end, check_candidate):
    """Seat-check candidates in parallel until one page past page_end is filled.

    Candidates must arrive pre-sorted. Matches keep the candidate order, so
    stopping early is safe: every unchecked candidate sorts after every
    checked one.
    """
    indexed_matches = []
    checked = 0
    if not candidates:
        return [], 0
    worker_count = min(12, max(4, len(candidates)))
    batch_size = worker_count * 2
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for offset in range(0, len(candidates), batch_size):
            batch = candidates[offset : offset + batch_size]
            checked += len(batch)
            future_map = {
                executor.submit(check_candidate, candidate): offset + position
                for position, candidate in enumerate(batch)
            }
            for future in as_completed(future_map):
                result = future.result()
                if result:
                    indexed_matches.append((future_map[future], result))
            if len(indexed_matches) > page_end:
                break
    indexed_matches.sort(key=lambda pair: pair[0])
    return [match for _, match in indexed_matches], checked


@app.get("/api/search")
def api_search(
    request: Request,
    radius: Radius,
    movie: RequiredShortText,
    zip_code: Annotated[ShortText, Query(alias="zip")] = "",
    theatre: ShortText = "",
    requested_format: Annotated[RequiredShortText, Query(alias="format")] = "any",
    startDate: date | None = None,
    endDate: date | None = None,
    startTime: TimeText = "00:00",
    endTime: TimeText = "23:59",
    adjacentSeats: AdjacentSeatCount = 1,
    seatGrid: str = "",
    excludeAccessible: bool = True,
    sort: SearchSort = "earliest",
    page: PageNumber = 1,
    pageSize: PageSize = DEFAULT_PAGE_SIZE,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
):
    enforce_rate_limit(request, "/api/search")
    try:
        theatre_query = theatre.lower()
        movie_query = movie
        start_date = startDate or date.today()
        end_date = endDate or start_date
        start_time = startTime
        end_time = endTime
        min_adjacent = adjacentSeats
        selected_cells = parse_seat_grid(seatGrid)
        exclude_accessible = excludeAccessible
        sort_order = sort
        page_size = pageSize
        page_start = (page - 1) * page_size
        page_end = page_start + page_size

        search_zip, origin, _ = api_search_location(zip_code, lat, lon)
        candidates = collect_candidate_showtimes(
            search_zip,
            radius,
            start_date,
            end_date,
            theatre_query,
            movie_query,
            requested_format,
            start_time,
            end_time,
            origin,
        )
        candidates.sort(
            key=lambda candidate: search_sort_key(
                candidate[0]["distanceMiles"],
                candidate[1]["date"],
                candidate[1]["time"],
                candidate[0]["name"],
                sort_order,
            )
        )

        def check_candidate(candidate):
            theatre_item, showtime = candidate
            try:
                seat_match = showtime_seat_match(showtime, min_adjacent, selected_cells, exclude_accessible, seat_map)
            except (*UPSTREAM_ERRORS, ValueError):
                return None
            if not seat_match:
                return None
            return {
                "theatre": {
                    "name": theatre_item["name"],
                    "address": theatre_item["address"],
                    "distanceMiles": theatre_item["distanceMiles"],
                },
                "movieTitle": showtime["movieTitle"],
                "date": showtime["date"],
                "displayTime": showtime["displayTime"],
                "format": showtime["format"],
                "amenities": showtime["amenities"],
                "ticketUrl": safe_fandango_url(showtime["ticketUrl"]),
                "poster": showtime["poster"],
                "rating": showtime["rating"],
                "runtime": showtime["runtime"],
                "genres": showtime["genres"],
                "seatMap": seat_match,
            }

        matches, checked_seat_maps = seat_checked_matches(candidates, page_end, check_candidate)

        return {
            "matches": matches[page_start:page_end],
            "page": page,
            "pageSize": page_size,
            "hasPreviousPage": page > 1,
            "hasNextPage": len(matches) > page_end,
            "checkedShowtimes": len(candidates),
            "checkedSeatMaps": checked_seat_maps,
            "accessibleSeatsExcluded": exclude_accessible,
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except UPSTREAM_ERRORS as error:
        upstream_error("Could not search real showtimes/seats", error)


class PublicAssetFiles(StaticFiles):
    """Serve only the allowlisted production assets, not frontend sources."""

    async def get_response(self, path, scope):
        if path.replace("\\", "/") not in PUBLIC_ASSETS:
            raise HTTPException(status_code=404, detail="Not found")
        return await super().get_response(path, scope)


app.mount("/", PublicAssetFiles(directory=STATIC_DIR), name="static")
