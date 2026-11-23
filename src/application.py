"""Website routes and the showtime/seat search shared with the MCP server."""

import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

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
from pydantic import StringConstraints

from .fandango import (
    UPSTREAM_ERRORS,
    amenity_groups,
    clean_title,
    fandango_theatres,
    fandango_theatres_by_date,
    format_matches,
    group_formats,
    movie_matches,
    normalize_showtimes,
    normalized_text,
    safe_fandango_url,
    seat_map,
)
from .location import ZipNotFoundError, resolve_search_location
from .seat_matching import (
    parse_seat_grid,
    seat_map_preview,
    showtime_seat_match,
)
from .share_links import decode_share_link

SITE_NAME = "Movie Seat Finder"
SITE_DESCRIPTION = "Find real Fandango showtimes with reserved seating and preview live seat maps before you buy movie tickets."
PUBLIC_SITE_URL = "https://movieseatfinder.com"
SRC_DIR = Path(__file__).resolve().parent
STATIC_DIR = SRC_DIR / "dist"
TEMPLATE_DIR = SRC_DIR / "templates"
INDEX_TEMPLATE = (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
FAQ_TEMPLATE = (TEMPLATE_DIR / "faq.html").read_text(encoding="utf-8")
# Fandango lists U.S. theatres only; these territories use U.S. ZIP codes too.
US_COUNTRY_CODES = {"US", "PR", "GU", "VI", "AS", "MP"}
REGION_NOTICE_COOKIE = "regionNoticeDismissed"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
MAX_DATE_RANGE_DAYS = 14
MAX_DAYS_AHEAD = 180
US_TIMEZONE = timezone(timedelta(hours=-8))
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
# Fandango returns at most 100 theatres per search, so a filter never needs more names.
MAX_THEATRE_FILTERS = 100
IncludedTheatres = Annotated[
    list[ShortText], Query(alias="includeTheatre", max_length=MAX_THEATRE_FILTERS)
]
ExcludedTheatres = Annotated[
    list[ShortText], Query(alias="excludeTheatre", max_length=MAX_THEATRE_FILTERS)
]
SearchSort = Literal["earliest", "latest", "nearest"]

LOGGER = logging.getLogger("movieseatfinder")
if not LOGGER.handlers:
    LOGGER.addHandler(logging.StreamHandler(sys.stdout))
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


def us_today():
    return datetime.now(US_TIMEZONE).date()


def date_range(start, end):
    """Validate a requested date range and return its days as ISO strings."""
    if end > us_today() + timedelta(days=MAX_DAYS_AHEAD):
        raise ValueError(f"Dates must be within {MAX_DAYS_AHEAD} days from today.")
    if end < start:
        raise ValueError("End date must be on or after start date.")
    if (end - start).days > MAX_DATE_RANGE_DAYS:
        raise ValueError(f"End date must be within {MAX_DATE_RANGE_DAYS} days of the start date.")
    return [
        (start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)
    ]


def search_sort_key(distance, show_date, show_time, theatre_name, sort_order):
    """Return a deterministic key for supported result sort orders.

    Date and time digits concatenate into one integer, which negates for "latest".
    """
    chronology = int(show_date.replace("-", "") + show_time.replace(":", ""))
    if sort_order == "nearest":
        return (distance, chronology, theatre_name)
    if sort_order == "latest":
        return (-chronology, distance, theatre_name)
    return (chronology, distance, theatre_name)


def intent_query_matches(value, query):
    """Match a human hint without requiring an exact display string."""
    value_text = normalized_text(value)
    query_text = normalized_text(query)
    return not query_text or query_text in value_text


def dated_theatres(zip_code, radius, dates, theatre_query, origin, included=(), excluded=()):
    """Yield (date, theatre) pairs for every requested date, filtered by theatre name.

    `included` and `excluded` are exact theatre names (any case); an empty list means all.
    """
    theatres_by_date = fandango_theatres_by_date(zip_code, radius, dates, origin)
    included_names = {name.lower() for name in included}
    excluded_names = {name.lower() for name in excluded}
    for show_date in dates:
        for theatre in theatres_by_date[show_date]:
            name = theatre["name"].lower()
            if theatre_query and theatre_query not in name:
                continue
            if (included_names and name not in included_names) or name in excluded_names:
                continue
            yield show_date, theatre


def available_movies(zip_code, radius, dates, theatre_query, origin, included, excluded):
    seen = set()
    movies = []
    for _, theatre in dated_theatres(
        zip_code, radius, dates, theatre_query, origin, included, excluded
    ):
        for movie in theatre["rawMovies"]:
            title = clean_title(movie.get("title", ""))
            key = normalized_text(title)
            if not title or key in seen:
                continue
            seen.add(key)
            movies.append({"title": title})
    return sorted(movies, key=lambda movie: movie["title"])


def available_formats(
    zip_code, radius, dates, theatre_query, movie_query, origin, included, excluded
):
    formats = set()
    for _, theatre in dated_theatres(
        zip_code, radius, dates, theatre_query, origin, included, excluded
    ):
        for movie in theatre["rawMovies"]:
            if not movie_matches(movie.get("title", ""), movie_query):
                continue
            for format_name, group in amenity_groups(movie):
                formats.update(group_formats(format_name, group))
    return sorted(formats)


app = FastAPI(title="Movie Seat Finder")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data: https://images.fandango.com; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "require-trusted-types-for 'script'; trusted-types 'none'"
    )
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


def render_index(shared, show_region_notice=False):
    return HTMLResponse(
        INDEX_TEMPLATE.replace(
            "__SHARED_SEARCH_CLASS__", "shared-search shared-loading" if shared else ""
        )
        .replace("__INITIAL_SUMMARY__", "Loading shared showtime…" if shared else "")
        .replace("__REGION_NOTICE_OPEN__", " data-show" if show_region_notice else "")
    )


def should_show_region_notice(request):
    """Vercel sends the visitor's IP country; local servers send none and never show the notice."""
    country = request.headers.get("x-vercel-ip-country")
    if country is None or country in US_COUNTRY_CODES:
        return False
    return REGION_NOTICE_COOKIE not in request.cookies


@app.head("/", include_in_schema=False)
@app.get("/", include_in_schema=False)
def index(request: Request):
    return render_index(shared=False, show_region_notice=should_show_region_notice(request))


@app.get("/index.html", include_in_schema=False)
def index_html():
    return RedirectResponse("/", status_code=308)


@app.get("/s/{token}", include_in_schema=False)
def shared_showtime():
    return render_index(shared=True)


@app.get("/api/shared-showtime/{token}", include_in_schema=False)
def api_shared_showtime(token: str):
    with api_errors("Could not refresh this showtime’s seat map"):
        shared = decode_share_link(token)
        raw_map = seat_map(shared.showtime)
    if not raw_map or not raw_map.get("seats"):
        raise HTTPException(
            status_code=404, detail="This showtime’s seat map is no longer available."
        )
    preview = seat_map_preview(
        raw_map, shared.adjacent_seats, parse_seat_grid(shared.seat_grid), shared.exclude_accessible
    )
    poster = shared.poster
    if not poster.startswith("https://images.fandango.com/"):
        poster = ""
    match = {
        "movieTitle": shared.movie,
        "theatre": {"name": shared.theatre, "address": shared.address},
        "date": shared.show_date.isoformat(),
        "displayTime": shared.time,
        "format": shared.format,
        "showtimeHashCode": shared.showtime,
        "ticketUrl": safe_fandango_url(shared.ticket),
        "poster": poster,
        "rating": shared.rating,
        "runtime": shared.runtime,
        "genres": shared.genres.split(", ") if shared.genres else [],
        "amenities": shared.amenities,
        "seatMap": preview,
    }
    return {
        "matches": [match],
        "checkedShowtimes": 1,
        "checkedSeatMaps": 1,
        "failedSeatMaps": 0,
        "page": 1,
        "pageSize": 1,
        "hasNextPage": False,
        "hasPreviousPage": False,
        "accessibleSeatsExcluded": shared.exclude_accessible,
        "preferences": {
            "adjacentSeats": str(shared.adjacent_seats),
            "excludeAccessible": "1" if shared.exclude_accessible else "0",
            "seatGrid": shared.seat_grid,
        },
    }


@app.head("/faq", include_in_schema=False)
@app.get("/faq", include_in_schema=False)
def faq():
    return HTMLResponse(FAQ_TEMPLATE)


@app.get("/faq.html", include_in_schema=False)
def faq_html():
    return RedirectResponse("/faq", status_code=308)


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse(
        "\n".join(
            [
                "User-agent: *",
                "Allow: /",
                "",
                f"Sitemap: {PUBLIC_SITE_URL}/sitemap.xml",
            ]
        )
        + "\n"
    )


@app.get("/llms.txt", include_in_schema=False)
def llms_txt():
    llms_file = SRC_DIR / "content" / "llms.txt"
    return PlainTextResponse(llms_file.read_text(encoding="utf-8"))


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{PUBLIC_SITE_URL}/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>{PUBLIC_SITE_URL}/faq</loc>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
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
            "background_color": "#fff7f6",
            "theme_color": "#12151c",
            "icons": [
                {
                    "src": "/web-app-manifest-192x192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                },
                {
                    "src": "/web-app-manifest-512x512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                },
            ],
        }
    )


@contextmanager
def api_errors(upstream_message):
    """Report upstream failures as a logged 502 and invalid input as a 400."""
    try:
        yield
    except UPSTREAM_ERRORS as error:
        LOGGER.warning("Upstream error: %s: %r", upstream_message, error)
        raise HTTPException(
            status_code=502, detail=f"{upstream_message}. Please try again."
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


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
            message = (
                "We couldn't determine a ZIP code for your location. Enter a ZIP code instead."
            )
            status_code = 400
        else:
            message = "ZIP lookup is temporarily unavailable. Try again."
            status_code = 502
        raise HTTPException(
            status_code=status_code,
            detail={"error": message, "code": "location"},
        ) from error


def location_movie_info(
    radius,
    zip_code,
    start_date,
    end_date=None,
    theatre="",
    movie_query="",
    format_query="",
    lat=None,
    lon=None,
):
    """Return canonical live theatre, movie, and format values for agents."""
    with api_errors("Could not load real theatre and movie data"):
        end_date = end_date or start_date
        dates = date_range(start_date, end_date)
        search_zip, origin, place = api_search_location(zip_code, lat, lon)
        theatres = {}
        movies = {}

        for show_date, theatre_item in dated_theatres(
            search_zip, radius, dates, theatre.lower(), origin
        ):
            theatre_name = theatre_item["name"]
            theatres.setdefault(
                normalized_text(theatre_name),
                {
                    "name": theatre_name,
                    "address": theatre_item["address"],
                    "distanceMiles": theatre_item["distanceMiles"],
                },
            )
            for movie in theatre_item["rawMovies"]:
                title = clean_title(movie.get("title", ""))
                if not title:
                    continue
                movie_info = movies.setdefault(
                    normalized_text(title),
                    {
                        "title": title,
                        "dates": set(),
                        "formats": set(),
                        "formatTheatres": {},
                        "theatres": set(),
                    },
                )
                movie_info["dates"].add(show_date)
                movie_info["theatres"].add(theatre_name)
                for format_name, group in amenity_groups(movie):
                    for format_label in group_formats(format_name, group):
                        movie_info["formats"].add(format_label)
                        movie_info["formatTheatres"].setdefault(format_label, set()).add(
                            theatre_name
                        )

    filtered_movies = []
    relevant_theatres = set()
    for movie in movies.values():
        if movie_query and not intent_query_matches(movie["title"], movie_query):
            continue
        formats = sorted(
            value
            for value in movie["formats"]
            if not format_query or format_matches(value, format_query)
        )
        if format_query and not formats:
            continue
        movie_theatres = movie["theatres"]
        if format_query:
            movie_theatres = set().union(*(movie["formatTheatres"][value] for value in formats))
        relevant_theatres.update(movie_theatres)
        filtered_movies.append(
            {
                "title": movie["title"],
                "dates": sorted(movie["dates"]),
                "formats": formats,
                "theatres": sorted(movie_theatres),
            }
        )

    return {
        "place": place,
        "zipCode": search_zip,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "movieQuery": movie_query,
        "formatQuery": format_query,
        "theatres": sorted(
            (
                item
                for item in theatres.values()
                if not (movie_query or format_query) or item["name"] in relevant_theatres
            ),
            key=lambda item: (item["distanceMiles"], item["name"]),
        ),
        "movies": sorted(filtered_movies, key=lambda movie: movie["title"]),
    }


@app.post("/api/events/ticket-click", status_code=204)
def ticket_click():
    LOGGER.info("event=ticket_click")
    return Response(status_code=204)


@app.post("/api/events/shared-link-visit", status_code=204)
def shared_link_visit():
    LOGGER.info("event=shared_link_visit")
    return Response(status_code=204)


@app.get("/api/theatres")
def api_theatres(
    radius: Radius,
    zip_code: Annotated[ShortText, Query(alias="zip")] = "",
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
):
    with api_errors("Could not load real theatre data"):
        search_zip, origin, place = api_search_location(zip_code, lat, lon)
        theatres = fandango_theatres(search_zip, radius, origin)
    return {"place": place, "theatres": [{"name": theatre["name"]} for theatre in theatres]}


@app.get("/api/movies")
def api_movies(
    radius: Radius,
    zip_code: Annotated[ShortText, Query(alias="zip")] = "",
    startDate: date | None = None,
    endDate: date | None = None,
    theatre: ShortText = "",
    included_theatres: IncludedTheatres = [],
    excluded_theatres: ExcludedTheatres = [],
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
):
    with api_errors("Could not load real movie data"):
        start_date = startDate or us_today()
        dates = date_range(start_date, endDate or start_date)
        search_zip, origin, _ = api_search_location(zip_code, lat, lon)
        movies = available_movies(
            search_zip,
            radius,
            dates,
            theatre.lower(),
            origin,
            included_theatres,
            excluded_theatres,
        )
    return {"movies": movies}


@app.get("/api/formats")
def api_formats(
    radius: Radius,
    zip_code: Annotated[ShortText, Query(alias="zip")] = "",
    movie: ShortText = "",
    startDate: date | None = None,
    endDate: date | None = None,
    theatre: ShortText = "",
    included_theatres: IncludedTheatres = [],
    excluded_theatres: ExcludedTheatres = [],
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
):
    if not movie:
        return {"formats": []}
    with api_errors("Could not load real format data"):
        start_date = startDate or us_today()
        dates = date_range(start_date, endDate or start_date)
        search_zip, origin, _ = api_search_location(zip_code, lat, lon)
        formats = available_formats(
            search_zip,
            radius,
            dates,
            theatre.lower(),
            movie,
            origin,
            included_theatres,
            excluded_theatres,
        )
    return {"formats": formats}


def collect_candidate_showtimes(
    search_zip,
    radius,
    dates,
    theatre_query,
    movie_query,
    requested_format,
    start_time,
    end_time,
    origin,
    included_theatres=(),
    excluded_theatres=(),
):
    """Gather (theatre, showtime) pairs that pass every non-seat filter."""
    candidates = []
    for _, theatre in dated_theatres(
        search_zip, radius, dates, theatre_query, origin, included_theatres, excluded_theatres
    ):
        movies = [
            movie
            for movie in theatre["rawMovies"]
            if movie_matches(movie.get("title", ""), movie_query)
        ]
        for showtime in normalize_showtimes(movies):
            if not format_matches(showtime["format"], requested_format):
                continue
            if showtime["time"] < start_time or showtime["time"] > end_time:
                continue
            candidates.append((theatre, showtime))
    return candidates


def seat_checked_matches(candidates, page_end, check_candidate):
    """Seat-check candidates in parallel until a match beyond page_end is found.

    Candidates must be pre-sorted, so stopping early never skips a better match.
    """
    matches = []
    checked = 0
    if not candidates:
        return [], 0
    worker_count = min(12, max(4, len(candidates)))
    batch_size = worker_count * 2
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for offset in range(0, len(candidates), batch_size):
            batch = candidates[offset : offset + batch_size]
            checked += len(batch)
            matches.extend(result for result in executor.map(check_candidate, batch) if result)
            if len(matches) > page_end:
                break
    return matches, checked


def find_seat_matches(
    radius: Radius,
    movie: RequiredShortText,
    zip_code: ShortText = "",
    theatre: ShortText = "",
    included_theatres=(),
    excluded_theatres=(),
    requested_format: RequiredShortText = "any",
    start_date: date | None = None,
    end_date: date | None = None,
    start_time: TimeText = "00:00",
    end_time: TimeText = "23:59",
    adjacent_seats: AdjacentSeatCount = 1,
    seat_grid: str = "",
    exclude_accessible: bool = True,
    sort: SearchSort = "earliest",
    page: PageNumber = 1,
    page_size: PageSize = DEFAULT_PAGE_SIZE,
    lat: float | None = None,
    lon: float | None = None,
):
    """Run the shared live showtime and seat-map search operation."""
    with api_errors("Could not search real showtimes/seats"):
        if start_time > end_time:
            raise ValueError("Latest time must be on or after earliest time.")
        start_date = start_date or us_today()
        dates = date_range(start_date, end_date or start_date)
        selected_cells = parse_seat_grid(seat_grid)
        page_start = (page - 1) * page_size
        page_end = page_start + page_size

        search_zip, origin, _ = api_search_location(zip_code, lat, lon)
        candidates = collect_candidate_showtimes(
            search_zip,
            radius,
            dates,
            theatre.lower(),
            movie,
            requested_format,
            start_time,
            end_time,
            origin,
            included_theatres,
            excluded_theatres,
        )
        candidates.sort(
            key=lambda candidate: search_sort_key(
                candidate[0]["distanceMiles"],
                candidate[1]["date"],
                candidate[1]["time"],
                candidate[0]["name"],
                sort,
            )
        )

        failed_seat_maps = 0
        failure_lock = threading.Lock()

        def check_candidate(candidate):
            nonlocal failed_seat_maps
            theatre_item, showtime = candidate
            try:
                seat_match = showtime_seat_match(
                    showtime, adjacent_seats, selected_cells, exclude_accessible, seat_map
                )
            except (*UPSTREAM_ERRORS, ValueError):
                with failure_lock:
                    failed_seat_maps += 1
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
                "showtimeHashCode": showtime["showtimeHashCode"],
            }

        matches, checked_seat_maps = seat_checked_matches(candidates, page_end, check_candidate)

    if failed_seat_maps and failed_seat_maps == checked_seat_maps:
        raise HTTPException(
            status_code=502,
            detail="Seat availability could not be checked. Please try again.",
        )
    return {
        "matches": matches[page_start:page_end],
        "zipCode": search_zip,
        "page": page,
        "pageSize": page_size,
        "hasPreviousPage": page > 1,
        "hasNextPage": len(matches) > page_end,
        "checkedShowtimes": len(candidates),
        "checkedSeatMaps": checked_seat_maps,
        "failedSeatMaps": failed_seat_maps,
        "accessibleSeatsExcluded": exclude_accessible,
    }


@app.get("/api/search")
def api_search(
    radius: Radius,
    movie: RequiredShortText,
    zip_code: Annotated[ShortText, Query(alias="zip")] = "",
    theatre: ShortText = "",
    included_theatres: IncludedTheatres = [],
    excluded_theatres: ExcludedTheatres = [],
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
    return find_seat_matches(
        radius=radius,
        movie=movie,
        zip_code=zip_code,
        theatre=theatre,
        included_theatres=included_theatres,
        excluded_theatres=excluded_theatres,
        requested_format=requested_format,
        start_date=startDate,
        end_date=endDate,
        start_time=startTime,
        end_time=endTime,
        adjacent_seats=adjacentSeats,
        seat_grid=seatGrid,
        exclude_accessible=excludeAccessible,
        sort=sort,
        page=page,
        page_size=pageSize,
        lat=lat,
        lon=lon,
    )


# `npm run build` puts bundles, fonts and images in dist/. Vercel promotes a plain StaticFiles
# mount to its CDN at build time, so these requests never invoke the function there.
app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
