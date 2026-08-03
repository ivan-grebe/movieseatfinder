"""Remote MCP server exposing Movie Seat Finder as an agent tool."""

import os
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.routing import Route

from backend import application
from .security import McpSecurityMiddleware

SortOrder = Literal["earliest", "latest", "nearest"]
GridCell = Annotated[
    str,
    Field(
        pattern=r"^(?:[1-9]|1[0-5]):(?:[1-9]|1[0-5])$",
        description="One 1-based auditorium cell in row:column form, for example 8:7",
    ),
]
FormatName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=120,
        description="One exact format returned by get_location_and_movie_info",
    ),
]


def internal_seat_grid(seat_cells):
    """Convert the MCP's human-friendly 1-based cells to the site's 0-based grid."""
    converted = []
    seen = set()
    for cell in seat_cells:
        row, column = (int(value) - 1 for value in cell.split(":"))
        internal = f"{row}:{column}"
        if internal not in seen:
            seen.add(internal)
            converted.append(internal)
    return ",".join(converted)


def compact_search_results(result, query):
    """Return only the details a messaging assistant needs to present options."""
    options = []
    for position, match in enumerate(result["matches"], start=1):
        layout = match["seatMap"]["layout"]
        matching_seats = [
            seat["id"]
            for seat in layout["seats"]
            if seat.get("matched") and seat.get("id")
        ]
        options.append({
            "option": position,
            "movie": match["movieTitle"],
            "theatre": match["theatre"]["name"],
            "address": match["theatre"]["address"],
            "distanceMiles": match["theatre"]["distanceMiles"],
            "date": match["date"],
            "time": match["displayTime"],
            "format": match["format"],
            "amenities": match["amenities"],
            "availableSeatCount": match["seatMap"]["availableSeatCount"],
            "matchingSeatCount": len(matching_seats),
            "matchingSeatExamples": matching_seats[:12],
            "ticketUrl": match["ticketUrl"],
        })
    return {
        "query": query,
        "options": options,
        "resultCount": len(options),
        "checkedShowtimes": result["checkedShowtimes"],
        "checkedSeatMaps": result["checkedSeatMaps"],
        "message": (
            "Present these options concisely and include each ticket URL."
            if options
            else "No matching live seat maps were found. Ask whether to widen the time, format, radius, or seat area."
        ),
    }


def _public_error(error):
    detail = error.detail
    if isinstance(detail, dict):
        return detail.get("error", "The seat search could not be completed.")
    return str(detail)


movie_seat_mcp = MCPServer(
    name="movie-seat-finder",
    title="Movie Seat Finder",
    description="Discover canonical movie options, then search live showtimes and adjacent available seats.",
    instructions=(
        "Resolve relative dates such as tomorrow to ISO dates before calling tools. Use the user's "
        "onboarded ZIP code when the message does not include a location. First call "
        "get_location_and_movie_info to obtain the exact currently available movie titles, formats, "
        "and theatre names for the requested location and date range. Use those returned strings "
        "verbatim in find_movie_seats; never guess or silently rewrite a title such as adding or "
        "removing a release year. If multiple returned titles plausibly match the request, ask the "
        "user which one they mean. Never claim tickets are reserved or purchased; return the "
        "supplied Fandango ticket links."
    ),
    website_url="https://movieseatfinder.com",
    version="0.2.0",
)


@movie_seat_mcp.tool(
    title="Get location and movie information",
    description=(
        "Resolve a ZIP code and list the exact live theatre names, movie titles, available dates, "
        "and format strings that may be passed to find_movie_seats. Call this before searching."
    ),
    structured_output=True,
)
def get_location_and_movie_info(
    zip_code: Annotated[str, Field(pattern=r"^\d{5}$", description="Five-digit US ZIP code")],
    start_date: Annotated[date, Field(description="First calendar date in YYYY-MM-DD form")],
    end_date: Annotated[
        date | None,
        Field(description="Last calendar date in YYYY-MM-DD form; omit for one day"),
    ] = None,
    radius_miles: Annotated[float, Field(ge=1, le=100, description="Search radius around the ZIP code")] = 25,
    theatre: Annotated[
        str,
        Field(max_length=120, description="Optional partial theatre-name filter; normally leave empty"),
    ] = "",
) -> dict[str, Any]:
    """Discover canonical live values before an agent performs a seat search."""
    try:
        result = application.location_movie_info(
            radius=radius_miles,
            zip_code=zip_code,
            start_date=start_date,
            end_date=end_date,
            theatre=theatre,
        )
    except HTTPException as error:
        raise ValueError(_public_error(error)) from error
    return {
        **result,
        "message": (
            "Use an exact returned movie title, format, and optional theatre name in find_movie_seats. "
            "Ask the user to disambiguate when more than one title is plausible."
        ),
    }


@movie_seat_mcp.tool(
    title="Find available movie seats",
    description="Find real showtimes with adjacent available seats in a preferred auditorium area.",
    structured_output=True,
)
def find_movie_seats(
    movie: Annotated[
        str,
        Field(min_length=1, max_length=120, description="Exact title returned by get_location_and_movie_info"),
    ],
    start_date: Annotated[date, Field(description="First calendar date in YYYY-MM-DD form")],
    zip_code: Annotated[str, Field(pattern=r"^\d{5}$", description="Five-digit US ZIP code")],
    end_date: Annotated[
        date | None,
        Field(description="Last calendar date in YYYY-MM-DD form; omit for one day"),
    ] = None,
    movie_formats: Annotated[
        tuple[FormatName, ...],
        Field(
            max_length=10,
            description=(
                "Exact formats returned by get_location_and_movie_info. A showtime may match any "
                "selected value; use an empty array when every format is acceptable."
            ),
        ),
    ] = (),
    seat_cells: Annotated[
        tuple[GridCell, ...],
        Field(
            max_length=225,
            description=(
                "Exact acceptable areas on a 15x15 auditorium grid, written as row:column. "
                "Both coordinates are 1 through 15. Row 1 is nearest the screen and row 15 is "
                "the back. Column 1 is the left edge of the displayed auditorium and column 15 "
                "is the right edge. The geometric center is rows 6-10 and columns 6-10. Select "
                "every cell acceptable to the user; arbitrary shapes are allowed. Use an empty "
                "array when the user has no seat-position preference."
            ),
        ),
    ] = (),
    adjacent_seats: Annotated[int, Field(ge=1, le=10, description="Number of adjacent seats needed")] = 2,
    radius_miles: Annotated[float, Field(ge=1, le=100, description="Search radius around the ZIP code")] = 25,
    start_time: Annotated[str, Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$", description="Earliest time, HH:MM")]
    = "00:00",
    end_time: Annotated[str, Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$", description="Latest time, HH:MM")]
    = "23:59",
    theatre: Annotated[str, Field(max_length=120, description="Optional theatre name filter")] = "",
    exclude_accessible: Annotated[
        bool,
        Field(description="Exclude wheelchair and companion seats unless explicitly requested"),
    ] = True,
    sort: Annotated[SortOrder, Field(description="How to order matching showtimes")] = "earliest",
    max_results: Annotated[int, Field(ge=1, le=10, description="Maximum options to return")] = 5,
) -> dict[str, Any]:
    """Find live showtimes with seats matching a conversational preference."""
    if start_time > end_time:
        raise ValueError("start_time must be earlier than or equal to end_time.")
    search_end_date = end_date or start_date
    requested_format = ",".join(movie_formats) or "any"
    query = {
        "movie": movie,
        "dateRange": {"start": start_date.isoformat(), "end": search_end_date.isoformat()},
        "zipCode": zip_code,
        "formats": list(movie_formats),
        "seatCells": list(seat_cells),
        "adjacentSeats": adjacent_seats,
        "radiusMiles": radius_miles,
        "timeRange": {"start": start_time, "end": end_time},
        "theatre": theatre,
    }
    try:
        result = application.find_seat_matches(
            radius=radius_miles,
            movie=movie,
            zip_code=zip_code,
            theatre=theatre,
            requested_format=requested_format,
            start_date=start_date,
            end_date=search_end_date,
            start_time=start_time,
            end_time=end_time,
            adjacent_seats=adjacent_seats,
            seat_grid=internal_seat_grid(seat_cells),
            exclude_accessible=exclude_accessible,
            sort=sort,
            page=1,
            page_size=max_results,
        )
    except HTTPException as error:
        raise ValueError(_public_error(error)) from error
    return compact_search_results(result, query)


def _csv_environment(name, defaults):
    configured = [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]
    return configured or defaults


transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=_csv_environment(
        "MCP_ALLOWED_HOSTS",
        [
            "movieseatfinder.com",
            "www.movieseatfinder.com",
            "movieseatfinder.vercel.app",
            "testserver",
            "localhost:*",
            "127.0.0.1:*",
        ],
    ),
    allowed_origins=_csv_environment(
        "MCP_ALLOWED_ORIGINS",
        [
            "https://poke.com",
            "https://www.poke.com",
            "http://localhost:*",
            "http://127.0.0.1:*",
        ],
    ),
)

mcp_protocol_app = movie_seat_mcp.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    max_request_body_size=64 * 1024,
    transport_security=transport_security,
)
mcp_route = Route(
    "/mcp",
    endpoint=McpSecurityMiddleware(mcp_protocol_app.routes[0].endpoint),
)
