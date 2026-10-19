"""Compact public search links. Coordinates are never valid token contents."""

import base64
import binascii
import re
import zlib
from urllib.parse import parse_qsl, urlencode

PUBLIC_SEARCH_FIELDS = {
    "zip",
    "radius",
    "movie",
    "theatre",
    "format",
    "startDate",
    "endDate",
    "startTime",
    "endTime",
    "adjacentSeats",
    "excludeAccessible",
    "seatGrid",
    "sort",
    "page",
    "shared",
    "showtime",
}


def decode_share_link(token):
    """Bound decompression and accept only public, relative search parameters."""
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,4096}", token):
            raise ValueError
        compressed = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, 8193)
        if len(raw) > 8192 or not decoder.eof or decoder.unused_data:
            raise ValueError
        pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=16)
        params = dict(pairs)
        if len(params) != len(pairs) or not params.keys() <= PUBLIC_SEARCH_FIELDS:
            raise ValueError
        if not re.fullmatch(r"\d{5}", params.get("zip", "")) or not params.get("movie"):
            raise ValueError
        if params.get("seatGrid"):
            area = params["seatGrid"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{39}", area):
                raise ValueError
            bitmap = base64.urlsafe_b64decode(area + "=")
            if len(bitmap) != 29 or bitmap[-1] > 1:
                raise ValueError
            params["seatGrid"] = ",".join(
                f"{index // 15}:{index % 15}"
                for index in range(225)
                if bitmap[index // 8] & (1 << (index % 8))
            )
        params["shared"] = "1"
        return "/?" + urlencode(params)
    except (ValueError, UnicodeError, binascii.Error, zlib.error) as error:
        raise ValueError("This shared link is invalid. Please create a new link.") from error
