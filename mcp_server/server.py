"""Remote MCP server exposing Movie Seat Finder as an agent tool."""

import os
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.routing import Route

from backend import application
from .seat_map_image import render_seat_map_png
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


def compact_search_results(result, query, seat_map_request):
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
            "seatMapRequest": {
                **seat_map_request,
                "option_number": position,
                "expected_theatre": match["theatre"]["name"],
                "expected_date": match["date"],
                "expected_time": match["displayTime"],
                "expected_format": match["format"],
                "expected_ticket_url": match["ticketUrl"],
            },
        })
    return {
        "query": query,
        "options": options,
        "resultCount": len(options),
        "checkedShowtimes": result["checkedShowtimes"],
        "checkedSeatMaps": result["checkedSeatMaps"],
        "message": (
            "Present these options concisely, include each ticket URL, then offer to show a seat map."
            if options
            else "No matching live seat maps were found. Ask whether to widen the time, format, radius, or seat area."
        ),
    }


def _public_error(error):
    detail = error.detail
    if isinstance(detail, dict):
        return detail.get("error", "The seat search could not be completed.")
    return str(detail)


def _run_seat_search(
    movie,
    start_date,
    end_date,
    zip_code,
    movie_formats,
    seat_cells,
    adjacent_seats,
    radius_miles,
    start_time,
    end_time,
    theatre,
    exclude_accessible,
    sort,
    page_size,
):
    requested_format = ",".join(movie_formats) or "any"
    try:
        return application.find_seat_matches(
            radius=radius_miles,
            movie=movie,
            zip_code=zip_code,
            theatre=theatre,
            requested_format=requested_format,
            start_date=start_date,
            end_date=end_date,
            start_time=start_time,
            end_time=end_time,
            adjacent_seats=adjacent_seats,
            seat_grid=internal_seat_grid(seat_cells),
            exclude_accessible=exclude_accessible,
            sort=sort,
            page=1,
            page_size=page_size,
        )
    except HTTPException as error:
        raise ValueError(_public_error(error)) from error


MCP_INSTRUCTIONS = """
ROLE

Help users find real movie showtimes and adjacent available seats with Movie Seat Finder. Ask for
missing concrete details before using tools. Use reasonable judgment for subjective seat quality,
but never invent a movie, date, location, party size, format, theatre, or accessibility need.

CLARIFICATION FIRST

Do not call get_location_and_movie_info or find_movie_seats until all of these required details are
known from the current conversation or trustworthy onboarded context:

- the movie the user wants
- a show date or inclusive date range
- a five-digit US ZIP code
- the exact number of adjacent seats needed

If any required detail is missing, ask the user. Ask only for missing information and combine
closely related questions when that is natural. For example, if the user says "find me good movie
seats near me," ask which movie, what date or date range, how many seats, and their ZIP code if it
is not already available. Do not start discovery merely to guess which movie they want.

Use reasonable judgment for ordinary subjective seat-quality language. "Good seats" or "best
seats" normally means the middle of the auditorium, centered horizontally and neither extremely
close to the screen nor at the very back. Translate that into a sensible compact group of middle
grid cells without forcing an extra question. If the user gives a more specific seat preference,
follow it. Ask only when preferences conflict or the meaning is genuinely ambiguous.

This judgment exception applies only to subjective seat position. Do not guess a movie, date, ZIP
code, party size, movie format, theatre, accessibility need, or other concrete constraint. If
optional preferences for format, showtime, theatre, radius, or accessibility are missing, ask
whether the user has a preference or wants a broad search. Only use broad defaults after the user
explicitly says they do not care, says "any," or asks to search broadly.

Resolve unambiguous relative dates such as "tomorrow" or "this Friday" to ISO calendar dates. That
normalization is not a preference inference. If a relative date is genuinely ambiguous, ask.

LOCATION AND DATES

- Use the user's onboarded ZIP code when it is available and they have not supplied another one.
- If no ZIP code is available, ask for one; never invent a location from "near me."
- Treat date ranges as inclusive and keep them within the supported 14-day span.
- If the user gives one date, use it as both the start and end date.
- Never invent a date when none was supplied.

DISCOVERY MUST HAPPEN FIRST

After the required details are known, always call get_location_and_movie_info before
find_movie_seats. Pass the ZIP code, first date, optional last date, requested radius, and a theatre
filter only when the user named a theatre.

Discovery returns the resolved place and ZIP code, exact live theatre names, addresses, distances,
canonical movie titles, available dates, formats, and theatres showing each movie. Use returned
movie titles, theatre names, and formats exactly as written. Never silently add, remove, or rewrite
a release year, subtitle, edition, or other title text. "The Odyssey" and "The Odyssey (2026)" are
different canonical titles.

If one discovered title clearly matches the user's requested movie, use that exact title. If
multiple discovered titles plausibly match, ask which one they mean before searching. If none
match, explain what is available or ask whether to change the date range or radius.

FORMATS

Use only exact live format labels returned by discovery, such as Standard, IMAX, IMAX 70mm, IMAX
with Laser, Dolby Cinema, 4DX, or ScreenX. Pass multiple formats when the user accepts multiple
formats. Use an empty movie_formats array only after the user explicitly says any format is fine.
Do not treat plain IMAX and IMAX 70mm as interchangeable unless the user accepts both.

PARTY SIZE

Use adjacent_seats for the exact number of contiguous available seats required. Never assume two
seats when the party size is missing; ask the user. The supported party size is one through ten.

SEAT POSITION

The preference grid has 15 rows and 15 columns. Row 1 is nearest the screen, row 15 is the back,
column 1 is the left edge of the displayed auditorium, and column 15 is the right edge. Rows 6-10
and columns 6-10 are the broad center. Write each cell as row:column, for example 8:7.

Translate an explicit user preference into every grid cell they would reasonably accept:

- "dead center" means a compact area around rows 7-9 and columns 7-9
- "center" means rows 6-10 and columns 6-10
- "center-left" means center rows with columns toward the left of center
- "center-right" means center rows with columns toward the right of center
- "front-center" means front rows with center columns
- "back-center" or "center-back" means back rows with center columns
- "near the back" means a broader collection of rear-row cells
- "left aisle" means cells near the left edge
- "right aisle" means cells near the right edge
- "anywhere" means an empty seat_cells array

Arbitrary and irregular cell selections are allowed. A grid cell is a normalized auditorium region,
not a literal seat number. It is acceptable to translate ordinary "good" or "best" seats into a
compact middle-center preference using the judgment rule above.

SEARCHING

After discovery and any required disambiguation, call find_movie_seats with the exact canonical
title, inclusive dates, ZIP code, exact party size, explicitly accepted formats, explicitly accepted
seat cells, radius, time window, optional exact theatre, accessibility behavior, sort order, and
result limit.

The following broad values may be used only after the user explicitly permits a broad search or
says they have no preference for that dimension:

- 25-mile radius
- entire-day time window
- any format
- any theatre
- anywhere in the auditorium
- earliest-showtime ordering
- up to five options

Exclude wheelchair and companion seats unless the user explicitly requests accessible seating.
Do not infer an accessibility need. Supported sorting choices are earliest, latest, and nearest.

RESULTS

Present the best options as a concise numbered list. Include the movie title, theatre, date, time,
format, distance, useful amenities, matching-seat information, and Fandango ticket link. Do not
expose raw seat-map payloads, internal grid coordinates, or implementation details unless asked.

After presenting one or more options, offer to show the live seat map. A natural prompt is: "Would
you like to see the seat map for any of these?" If the user says yes and there is more than one
option, ask which numbered option they want. Then call show_movie_seat_map with the exact
seatMapRequest object returned for that option. The tool returns an image/png seat map and caption.
Send the image to the user. Do not call it before the user asks or accepts the offer.

Never claim tickets or seats were purchased, reserved, held, or guaranteed. Movie Seat Finder only
checks current availability and returns ticket links; availability can change before checkout.

NO RESULTS

When no matches are found, summarize the important constraints searched and ask whether the user
wants to widen the date range, time window, radius, accepted formats, theatre selection, or seat
area. Never weaken an explicit constraint without permission.
""".strip()


movie_seat_mcp = MCPServer(
    name="movie-seat-finder",
    title="Movie Seat Finder",
    description="Discover canonical movie options, then search live showtimes and adjacent available seats.",
    instructions=MCP_INSTRUCTIONS,
    website_url="https://movieseatfinder.com",
    version="0.4.0",
)


@movie_seat_mcp.tool(
    title="Get location and movie information",
    description=(
        "Resolve a ZIP code and list the exact live theatre names, movie titles, available dates, "
        "and format strings that may be passed to find_movie_seats. Call this before searching, but "
        "only after the user has supplied a movie, date or date range, party size, and ZIP code."
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
    description=(
        "Find real showtimes with adjacent available seats after discovery and clarification. Ordinary "
        "good or best seats may be interpreted as a compact middle-center area, but concrete search "
        "details must never be invented."
    ),
    structured_output=True,
)
def find_movie_seats(
    movie: Annotated[
        str,
        Field(min_length=1, max_length=120, description="Exact title returned by get_location_and_movie_info"),
    ],
    start_date: Annotated[date, Field(description="First calendar date in YYYY-MM-DD form")],
    zip_code: Annotated[str, Field(pattern=r"^\d{5}$", description="Five-digit US ZIP code")],
    adjacent_seats: Annotated[
        int,
        Field(ge=1, le=10, description="Exact user-provided number of adjacent seats needed"),
    ],
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
    seat_map_request = {
        "movie": movie,
        "start_date": start_date.isoformat(),
        "end_date": search_end_date.isoformat(),
        "zip_code": zip_code,
        "movie_formats": list(movie_formats),
        "seat_cells": list(seat_cells),
        "adjacent_seats": adjacent_seats,
        "radius_miles": radius_miles,
        "start_time": start_time,
        "end_time": end_time,
        "theatre": theatre,
        "exclude_accessible": exclude_accessible,
        "sort": sort,
    }
    result = _run_seat_search(
        movie,
        start_date,
        search_end_date,
        zip_code,
        movie_formats,
        seat_cells,
        adjacent_seats,
        radius_miles,
        start_time,
        end_time,
        theatre,
        exclude_accessible,
        sort,
        max_results,
    )
    return compact_search_results(result, query, seat_map_request)


@movie_seat_mcp.tool(
    title="Show a live movie seat map",
    description=(
        "Render one numbered result from find_movie_seats as an image/png seat map. Call only after "
        "the user asks to see a map. Copy every argument exactly from that option's seatMapRequest "
        "object; do not reconstruct, alter, or guess the search."
    ),
    structured_output=False,
)
def show_movie_seat_map(
    movie: Annotated[
        str,
        Field(min_length=1, max_length=120, description="Exact title from the selected seatMapRequest"),
    ],
    start_date: Annotated[date, Field(description="First date from the selected seatMapRequest")],
    zip_code: Annotated[str, Field(pattern=r"^\d{5}$", description="ZIP code from the selected seatMapRequest")],
    adjacent_seats: Annotated[
        int,
        Field(ge=1, le=10, description="Party size from the selected seatMapRequest"),
    ],
    option_number: Annotated[
        int,
        Field(ge=1, le=10, description="Number of the result whose live seat map the user requested"),
    ],
    expected_theatre: Annotated[
        str,
        Field(min_length=1, max_length=120, description="Exact theatre in the selected seatMapRequest"),
    ],
    expected_date: Annotated[
        date,
        Field(description="Exact show date in the selected seatMapRequest"),
    ],
    expected_time: Annotated[
        str,
        Field(min_length=1, max_length=24, description="Exact display time in the selected seatMapRequest"),
    ],
    expected_format: Annotated[
        str,
        Field(min_length=1, max_length=120, description="Exact format in the selected seatMapRequest"),
    ],
    expected_ticket_url: Annotated[
        str,
        Field(min_length=1, max_length=2048, description="Exact ticket URL in the selected seatMapRequest"),
    ],
    end_date: Annotated[
        date | None,
        Field(description="Last date from the selected seatMapRequest"),
    ] = None,
    movie_formats: Annotated[
        tuple[FormatName, ...],
        Field(max_length=10, description="Exact formats from the selected seatMapRequest"),
    ] = (),
    seat_cells: Annotated[
        tuple[GridCell, ...],
        Field(max_length=225, description="Exact acceptable grid cells from the selected seatMapRequest"),
    ] = (),
    radius_miles: Annotated[
        float,
        Field(ge=1, le=100, description="Radius from the selected seatMapRequest"),
    ] = 25,
    start_time: Annotated[
        str,
        Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$", description="Start time from the selected seatMapRequest"),
    ] = "00:00",
    end_time: Annotated[
        str,
        Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$", description="End time from the selected seatMapRequest"),
    ] = "23:59",
    theatre: Annotated[
        str,
        Field(max_length=120, description="Theatre filter from the selected seatMapRequest"),
    ] = "",
    exclude_accessible: Annotated[
        bool,
        Field(description="Accessibility behavior from the selected seatMapRequest"),
    ] = True,
    sort: Annotated[SortOrder, Field(description="Sort order from the selected seatMapRequest")] = "earliest",
) -> list[Any]:
    """Re-run a prior search and return the selected live layout as MCP image content."""
    if start_time > end_time:
        raise ValueError("start_time must be earlier than or equal to end_time.")
    search_end_date = end_date or start_date
    result = _run_seat_search(
        movie,
        start_date,
        search_end_date,
        zip_code,
        movie_formats,
        seat_cells,
        adjacent_seats,
        radius_miles,
        start_time,
        end_time,
        theatre,
        exclude_accessible,
        sort,
        10,
    )
    match = next((
        candidate
        for candidate in result["matches"]
        if candidate["theatre"]["name"] == expected_theatre
        and candidate["date"] == expected_date.isoformat()
        and candidate["displayTime"] == expected_time
        and candidate["format"] == expected_format
        and candidate["ticketUrl"] == expected_ticket_url
    ), None)
    if match is None:
        raise ValueError(
            "That exact showtime is no longer available. Run find_movie_seats again to refresh the results."
        )

    layout = match["seatMap"]["layout"]
    recommended_seats = [
        seat["id"]
        for seat in layout["seats"]
        if seat.get("matched") and seat.get("id")
    ]
    recommended_summary = ", ".join(recommended_seats[:12]) or "none currently highlighted"
    caption = (
        f"Live seat map for option {option_number}: {match['movieTitle']} at {match['theatre']['name']} — "
        f"{match['date']} at {match['displayTime']} ({match['format']}). "
        f"Red = seats matching the request; white = available; gray = unavailable; "
        f"blue = accessible. Matching seat examples: {recommended_summary}. "
        f"Availability can change before checkout. Tickets: {match['ticketUrl']}"
    )
    return [caption, Image(data=render_seat_map_png(layout), format="png")]


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
