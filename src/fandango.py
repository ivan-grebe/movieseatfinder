"""Fandango upstream client: cached theatre/seat-map fetches and showtime normalization."""

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from urllib.parse import urlsplit

import requests
from cachetools import TTLCache

from .location import filter_theatres_within_radius, http_session

LOGGER = logging.getLogger("movieseatfinder")
FANDANGO_USER_AGENT = "Mozilla/5.0 MovieSeatFinder/1.0"
FANDANGO_ORIGIN = "https://www.fandango.com"
# Fandango payloads are untyped JSON, so a missing or reshaped field surfaces as one of these.
UPSTREAM_ERRORS = (requests.RequestException, KeyError, AttributeError, TypeError)


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
        self._fetch_locks = {}

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

    def get_or_fetch(self, key, fetch):
        """Return the cached value, letting only one thread fetch a missing key."""
        value = self.get(key)
        if value is not None:
            return value
        with self._lock:
            key_lock = self._fetch_locks.setdefault(key, threading.Lock())
        try:
            with key_lock:
                value = self.get(key)
                if value is None:
                    value = fetch()
                    self.set(key, value)
                return value
        finally:
            with self._lock:
                self._fetch_locks.pop(key, None)


SEAT_MAP_CACHE = TtlCache("seat_map", ttl_seconds=300, max_entries=200)
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


FORMAT_LABEL_ALIASES = {
    "35 mm": "35mm",
    "35 mm film": "35mm",
    "35 mm presentation": "35mm",
    "35mm": "35mm",
    "35mm film": "35mm",
    "35mm presentation": "35mm",
    "70 mm": "70mm",
    "70 mm film": "70mm",
    "70 mm presentation": "70mm",
    "70mm": "70mm",
    "70mm film": "70mm",
    "70mm presentation": "70mm",
    "3d": "3D",
    "4dx": "4DX",
    "d box": "D-BOX",
    "dbox": "D-BOX",
    "dolby": "Dolby Cinema",
    "dolby atmos": "Dolby Atmos",
    "dolby cinema": "Dolby Cinema",
    "dolby cinema amc": "Dolby Cinema",
    "dolby cinema at amc": "Dolby Cinema",
    "imax": "IMAX",
    "imax 70": "IMAX 70mm",
    "imax70": "IMAX 70mm",
    "imax 70 mm": "IMAX 70mm",
    "imax 70mm": "IMAX 70mm",
    "imax 70 mm film": "IMAX 70mm",
    "imax 70mm film": "IMAX 70mm",
    "imax laser": "IMAX with Laser",
    "imax with laser": "IMAX with Laser",
    "prime at amc": "PRIME at AMC",
    "reald 3d": "RealD 3D",
    "rpx": "RPX",
    "screenx": "ScreenX",
    "xl at amc": "XL at AMC",
}


def canonical_format_label(value):
    """Collapse equivalent upstream format labels into one public label."""
    cleaned = clean_title(value)
    return FORMAT_LABEL_ALIASES.get(normalized_text(cleaned), cleaned)


@lru_cache(maxsize=256)
def requested_format_names(requested):
    return frozenset(
        normalized_text(canonical_format_label(value))
        for value in requested.split(",")
        if normalized_text(value)
    )


def format_matches(format_name, requested):
    if requested == "any":
        return True
    return normalized_text(canonical_format_label(format_name)) in requested_format_names(requested)


GENERIC_FORMAT_NAMES = {"format", "premium format", "standard"}
FORMAT_AMENITY_TERMS = (
    "imax",
    "dolby",
    "4dx",
    "screenx",
    "rpx",
    "prime",
    "xl",
    "d box",
    "reald",
    "3d",
    "35mm",
    "70mm",
)
FORMAT_PRIORITY = (
    "dolby cinema",
    "4dx",
    "screenx",
    "rpx",
    "prime at amc",
    "xl at amc",
    "d box",
    "reald 3d",
    "70mm",
    "35mm",
    "3d",
    "dolby atmos",
)


def is_format_amenity(value):
    normalized = normalized_text(canonical_format_label(value))
    return any(re.search(rf"\b{re.escape(term)}\b", normalized) for term in FORMAT_AMENITY_TERMS)


def showtime_format(format_header, group, showtime):
    """Resolve Fandango's overlapping format fields to one public label."""
    showtime_names = [
        name
        for item in showtime.get("filmFormat") or []
        if (name := clean_title(item.get("filterName", "")))
    ]
    amenity_names = [
        part.strip()
        for part in clean_title(group.get("amenityString", "")).split(",")
        if part.strip()
    ]
    amenity_names.extend(group_amenity_names(group))
    evidence = [*showtime_names, format_header, *amenity_names]
    labels_by_name = {
        normalized_text(label): label
        for value in evidence
        if (label := canonical_format_label(value))
    }
    if "imax 70mm" in labels_by_name:
        return "IMAX 70mm"

    has_imax = any(name == "imax" or name.startswith("imax ") for name in labels_by_name)
    if "imax with laser" in labels_by_name:
        return "IMAX with Laser"
    if has_imax:
        return "IMAX"

    for preferred in FORMAT_PRIORITY:
        if preferred in labels_by_name:
            return labels_by_name[preferred]

    for name in showtime_names:
        label = canonical_format_label(name)
        if normalized_text(label) not in GENERIC_FORMAT_NAMES:
            return label

    for name in amenity_names:
        if is_format_amenity(name):
            return canonical_format_label(name)

    return canonical_format_label(format_header)


def amenity_groups(movie):
    """Yield (format header, amenity group) for every showtime group of a movie."""
    for variant in movie.get("variants") or []:
        format_name = clean_title(variant.get("filmFormatHeader", "Standard")) or "Standard"
        for group in variant.get("amenityGroups") or []:
            yield format_name, group


def group_amenity_names(group):
    names = (clean_title(item.get("name", "")) for item in group.get("amenities") or [])
    return [name for name in names if name]


def group_formats(format_name, group):
    """The resolved format labels exposed by a single amenity group."""
    group_showtimes = group.get("showtimes") or [{}]
    return {showtime_format(format_name, group, showtime) for showtime in group_showtimes}


def fandango_theatres(zip_code, radius, origin, show_date=None):
    """Theatres within radius of origin, nearest first.

    Fandango only searches by ZIP, so this over-fetches around the ZIP and
    filters to the exact circle locally. The raw payload is cached without the
    origin so nearby searches share it.
    """
    fetch_radius = min(max(radius * 2, 25), 100)
    theatres = THEATRES_CACHE.get_or_fetch(
        (zip_code, fetch_radius, show_date or ""),
        lambda: _fetch_fandango_theatres(zip_code, fetch_radius, show_date),
    )
    return filter_theatres_within_radius(theatres, origin[0], origin[1], radius)


def fandango_theatres_by_date(zip_code, radius, dates, origin):
    """Fetch (and cache) theatre+showtime payloads for many dates in parallel."""
    results = {}
    with ThreadPoolExecutor(max_workers=min(8, len(dates))) as executor:
        future_map = {
            executor.submit(fandango_theatres, zip_code, radius, origin, show_date): show_date
            for show_date in dates
        }
        for future in as_completed(future_map):
            show_date = future_map[future]
            try:
                results[show_date] = future.result()
            except (*UPSTREAM_ERRORS, ValueError) as error:
                raise requests.RequestException(
                    "Could not load showtimes for every selected date. Please try again."
                ) from error
    return results


def _fetch_fandango_theatres(zip_code, radius, show_date=None):
    radius_value = int(radius) if float(radius).is_integer() else radius
    params = {"zipCode": zip_code, "radius": radius_value, "limit": 100}
    if show_date:
        params["date"] = show_date
    data = fandango_json("/napi/theaterswithshowtimes", params)
    theatres = []
    for theatre in data.get("theaters", []):
        geo = theatre.get("geo") or {}
        address_parts = [theatre.get(key, "") for key in ("address1", "city", "state", "zip")]
        theatres.append(
            {
                "name": theatre.get("name", ""),
                "address": theatre.get("fullAddress") or ", ".join(filter(None, address_parts)),
                "distanceMiles": float(theatre.get("distance") or 0),
                "latitude": _optional_float(geo.get("latitude")),
                "longitude": _optional_float(geo.get("longitude")),
                "rawMovies": theatre.get("movies") or [],
            }
        )
    return sorted(theatres, key=lambda item: item["distanceMiles"])


def _optional_float(value):
    return None if value is None else float(value)


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
    except TypeError, ValueError:
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
    except ValueError:
        return value
    suffix = "AM" if hour < 12 else "PM"
    hour = hour % 12 or 12
    return f"{hour}:{minute:02d} {suffix}"


def normalize_showtimes(movies):
    normalized = []
    for movie in movies:
        movie_title = clean_title(movie.get("title", ""))
        meta = movie_meta(movie)
        for format_name, group in amenity_groups(movie):
            amenity_text = clean_title(group.get("amenityString", "")) or ", ".join(
                group_amenity_names(group)
            )
            for showtime in group.get("showtimes") or []:
                if showtime.get("type") != "available" or showtime.get("expired"):
                    continue
                ticketing_date = showtime.get("ticketingDate") or ""
                if "+" not in ticketing_date:
                    continue
                show_date, show_time = ticketing_date.split("+", 1)
                normalized.append(
                    {
                        "movieTitle": movie_title,
                        "date": show_date,
                        "time": show_time,
                        "displayTime": display_showtime_time(show_time),
                        "format": showtime_format(format_name, group, showtime),
                        "amenities": amenity_text,
                        "showtimeHashCode": showtime.get("showtimeHashCode"),
                        "ticketUrl": showtime.get("ticketingJumpPageURL"),
                        **meta,
                    }
                )
    return normalized


def seat_map(showtime_hash):
    if not showtime_hash:
        return None
    return SEAT_MAP_CACHE.get_or_fetch(
        showtime_hash,
        lambda: fandango_json(
            f"/napi/seatMap/{showtime_hash}",
            referer="https://www.fandango.com/",
        ),
    )
