"""Location resolution and exact-radius calculations for theatre searches."""

import re
from functools import lru_cache

import requests
from geopy.distance import geodesic

USER_AGENT = "MovieSeatFinder/1.0 (location lookup)"
# One process-wide session so upstream connections are reused.
HTTP_SESSION = requests.Session()
# Room for every seat-map worker plus the per-search theatre fetches.
HTTP_SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=48))


class ZipNotFoundError(ValueError):
    """The supplied five-digit ZIP does not identify a US location."""


@lru_cache(maxsize=4096)
def distance_miles(lat1, lon1, lat2, lon2):
    return geodesic((lat1, lon1), (lat2, lon2)).miles


@lru_cache(maxsize=512)
def geocode_zip(zip_code):
    """Resolve stable ZIP metadata once per process instead of once per MCP step."""
    response = HTTP_SESSION.get(
        f"https://api.zippopotam.us/us/{zip_code}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=20,
    )
    if response.status_code == 404:
        raise ZipNotFoundError("We couldn't find that ZIP code. Check it and try again.")
    response.raise_for_status()
    data = response.json()
    place = data["places"][0]
    return {
        "label": f"{place['place name']}, {place['state abbreviation']} {data['post code']}",
        "lat": float(place["latitude"]),
        "lon": float(place["longitude"]),
    }


def reverse_geocode_zip(lat, lon):
    """Find a ZIP for Fandango's ZIP-only API.

    Nominatim allows 1 request/second, so lookups are cached per ~100 m. Zoom 16
    is used because city-level results often omit the postcode.
    """
    return _reverse_geocode_zip_cached(round(lat, 3), round(lon, 3))


@lru_cache(maxsize=512)
def _reverse_geocode_zip_cached(lat, lon):
    response = HTTP_SESSION.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 16},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=10,
    )
    response.raise_for_status()
    zip_code = response.json()["address"]["postcode"][:5]
    if not re.fullmatch(r"\d{5}", zip_code):
        raise KeyError("No nearby US ZIP code")
    return zip_code


def resolve_search_location(zip_code, lat, lon):
    """Use opt-in browser coordinates, otherwise use the ZIP centroid."""
    if lat is not None:
        origin = (lat, lon)
        return reverse_geocode_zip(*origin), origin, "your current location"
    if not re.fullmatch(r"\d{5}", zip_code):
        raise ValueError("Enter a valid 5 digit US ZIP code or use your location.")
    zip_location = geocode_zip(zip_code)
    return zip_code, (zip_location["lat"], zip_location["lon"]), zip_location["label"]


def filter_theatres_within_radius(theatres, origin_lat, origin_lon, radius):
    """Return only theatres whose coordinates are inside the requested circle."""
    filtered = []
    for theatre in theatres:
        exact_distance = distance_miles(
            origin_lat, origin_lon, theatre["latitude"], theatre["longitude"]
        )
        if exact_distance <= radius:
            filtered.append({**theatre, "distanceMiles": exact_distance})
    return sorted(filtered, key=lambda item: item["distanceMiles"])
