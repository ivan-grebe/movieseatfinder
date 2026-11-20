"""Compressed showtime links contain public metadata and seat preferences only."""

import base64
import binascii
import re
import zlib
from datetime import date
from typing import Annotated
from urllib.parse import parse_qsl

from pydantic import BaseModel, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class SharedShowtime(BaseModel, extra="forbid"):
    movie: Text
    theatre: Text
    address: str = Field(default="", max_length=300)
    amenities: str = Field(default="", max_length=500)
    show_date: date = Field(alias="date")
    time: Text
    format: Text
    showtime: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,200}$")]
    ticket: str = Field(default="", max_length=2000)
    poster: str = Field(default="", max_length=2000)
    rating: str = Field(default="", max_length=20)
    runtime: str = Field(default="", max_length=40)
    genres: str = Field(default="", max_length=200)
    adjacent_seats: int = Field(default=2, alias="adjacentSeats", ge=1, le=10)
    exclude_accessible: bool = Field(default=True, alias="excludeAccessible")
    seat_grid: str = Field(default="", alias="seatGrid", max_length=39)


def unpack_seat_grid(area):
    if not area:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{39}", area):
        raise ValueError("Invalid seat area")
    bitmap = base64.urlsafe_b64decode(area + "=")
    if len(bitmap) != 29 or bitmap[-1] > 1:
        raise ValueError("Invalid seat area")
    return ",".join(
        f"{index // 15}:{index % 15}"
        for index in range(225)
        if bitmap[index // 8] & (1 << (index % 8))
    )


def decode_share_link(token):
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
        if len(params) != len(pairs):
            raise ValueError
        shared = SharedShowtime.model_validate(params)
        shared.seat_grid = unpack_seat_grid(shared.seat_grid)
        return shared
    except (ValueError, UnicodeError, binascii.Error, zlib.error) as error:
        raise ValueError(
            "This shared showtime link is invalid. Please create a new link."
        ) from error
