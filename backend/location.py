"""Location resolution and exact-radius calculations for theatre searches."""

import math
import re
import threading

import requests


USER_AGENT = "MovieSeatFinder/1.0 (location lookup)"
_LOCAL = threading.local()


def _session():
    session = getattr(_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        _LOCAL.session = session
    return session


def distance_miles(lat1, lon1, lat2, lon2):
    radius = 3958.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geocode_zip(zip_code):
    response = _session().get(
        f"https://api.zippopotam.us/us/{zip_code}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    place = data["places"][0]
    return {
        "label": f'{place["place name"]}, {place["state abbreviation"]} {data["post code"]}',
        "lat": float(place["latitude"]),
        "lon": float(place["longitude"]),
    }


def reverse_geocode_zip(lat, lon):
    """Find a ZIP for Fandango's ZIP-only API without retaining location data."""
    response = _session().get(
        "https://nominatim.openstreetmap.org/reverse",
        params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 10},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=10,
    )
    response.raise_for_status()
    zip_code = str(response.json().get("address", {}).get("postcode", ""))[:5]
    if not re.fullmatch(r"\d{5}", zip_code):
        raise KeyError("No nearby US ZIP code")
    return zip_code


def validate_coordinates(lat, lon):
    if lat is None or lon is None:
        return None
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        raise ValueError("Location coordinates must be valid numbers.")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Location coordinates are out of range.")
    return lat, lon


def resolve_search_location(zip_code, lat=None, lon=None):
    """Use opt-in browser coordinates, otherwise use the ZIP centroid."""
    origin = validate_coordinates(lat, lon)
    if origin:
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
