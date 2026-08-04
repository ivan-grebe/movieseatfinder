"""Location resolution and exact-radius calculations for theatre searches."""

import re
import threading
from functools import lru_cache

import requests
from geopy.distance import geodesic

USER_AGENT = "MovieSeatFinder/1.0 (location lookup)"
_LOCAL = threading.local()


class ZipNotFoundError(ValueError):
    """The supplied five-digit ZIP does not identify a US location."""


def http_session():
    """One requests.Session per thread, shared by every upstream call."""
    session = getattr(_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        _LOCAL.session = session
    return session


def distance_miles(lat1, lon1, lat2, lon2):
    return geodesic((lat1, lon1), (lat2, lon2)).miles


@lru_cache(maxsize=512)
def geocode_zip(zip_code):
    """Resolve stable ZIP metadata once per process instead of once per MCP step."""
    response = http_session().get(
        f"https://api.zippopotam.us/us/{zip_code}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=20,
    )
    if response.status_code == 404:
        raise ZipNotFoundError("We couldn't find that ZIP code. Check it and try again.")
    response.raise_for_status()
    data = response.json()
    places = data.get("places") or []
    if not places:
        raise ZipNotFoundError("We couldn't find that ZIP code. Check it and try again.")
    place = places[0]
    return {
        "label": f"{place['place name']}, {place['state abbreviation']} {data['post code']}",
        "lat": float(place["latitude"]),
        "lon": float(place["longitude"]),
    }


def reverse_geocode_zip(lat, lon):
    """Find a ZIP for Fandango's ZIP-only API without retaining location data."""
    response = http_session().get(
        "https://nominatim.openstreetmap.org/reverse",
        # City-level results (zoom 10) omit postcodes in many places. Street/
        # neighbourhood detail reliably includes the ZIP needed by Fandango.
        params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 16},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=10,
    )
    response.raise_for_status()
    zip_code = str(response.json().get("address", {}).get("postcode", ""))[:5]
    if not re.fullmatch(r"\d{5}", zip_code):
        raise KeyError("No nearby US ZIP code")
    return zip_code


def resolve_search_location(zip_code, lat, lon):
    """Use opt-in browser coordinates, otherwise use the ZIP centroid."""
    if (lat is None) != (lon is None):
        raise ValueError("Location must include both latitude and longitude.")
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
        lat = theatre.get("latitude")
        lon = theatre.get("longitude")
        if lat is None or lon is None:
            continue
        exact_distance = distance_miles(origin_lat, origin_lon, lat, lon)
        if exact_distance <= radius:
            filtered.append({**theatre, "distanceMiles": exact_distance})
    return sorted(filtered, key=lambda item: item["distanceMiles"])
