"""Remote MCP server exposing Movie Seat Finder as an agent tool."""

from datetime import date
from typing import Annotated, Any

from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.routing import Route

from .. import application
from .seat_map_image import render_seat_map_png
from .security import McpSecurityMiddleware

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
        showtime_request = {
            "showtime_hash_code": match["showtimeHashCode"],
            "option_number": position,
            "movie": match["movieTitle"],
            "theatre": match["theatre"]["name"],
            "show_date": match["date"],
            "show_time": match["displayTime"],
            "movie_format": match["format"],
            "seat_cells": seat_map_request["seat_cells"],
            "adjacent_seats": seat_map_request["adjacent_seats"],
            "exclude_accessible": seat_map_request["exclude_accessible"],
        }
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
            "seatMapRequest": showtime_request,
        })
    return {
        "query": query,
        "options": options,
        "resultCount": len(options),
        "checkedShowtimes": result["checkedShowtimes"],
        "checkedSeatMaps": result["checkedSeatMaps"],
        "message": (
            "Present exactly five or fewer compact one-line options without ticket URLs. Then ask whether "
            "the user wants a seat map or ticket link for any numbered option."
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
            include_showtime_hash=True,
        )
    except HTTPException as error:
        raise ValueError(_public_error(error)) from error


MCP_INSTRUCTIONS = """
ROLE

Help users find live movie showtimes and adjacent available seats. Keep every user-facing message
clear, concise, and conversational. Never bombard the user with a checklist or several questions at
once. Never invent a movie, date, ZIP code, radius, party size, format, or seat preference.

CONVERSATION STATE

Treat the conversation as one continuing search. Extract and retain every detail the user supplies,
including details volunteered before you ask. Never ask again for a value that is already clear. If
the user changes one value, carry every other established value forward. A correction such as
"actually Dolby" changes only format unless the user says otherwise.

Ask one focused question per turn for the next missing value. Short acknowledgements are fine, but
do not send a separate recap or confirmation turn. Before discovery, obtain these values in this
order when missing:

1. movie
2. date or inclusive date range
3. five-digit US ZIP code
4. radius in miles, asked openly as "Within how many miles should I search?"
5. exact number of adjacent seats, from one through ten
6. seat-position preference

For example, "Find good seats for Dune tomorrow" already supplies movie, date, and seat preference.
Retain those values and ask only for the next missing value. Use an onboarded ZIP code when it is
trustworthy and the user has not supplied a different location.

Resolve unambiguous relative dates such as "tomorrow" or "this Friday" to ISO dates. Treat a single
date as both start and end, treat ranges as inclusive, and keep them within the supported 14 days.
Ask when a relative date is genuinely ambiguous.

DEFAULTS THAT DO NOT REQUIRE QUESTIONS

- If time is missing, explicitly tell the user once that the search will cover 2:00 PM through
  midnight, then proceed with 14:00 through 23:59. Do not ask for confirmation and do not create a
  separate message solely for this notice.
- Search all theatres and order matches by nearest first. Do not ask for a theatre unless the user
  wants to restrict or change it.
- Exclude wheelchair and companion seats silently unless the user explicitly requests accessible
  seating. Never infer an accessibility need.
- Return at most five options.

DISCOVERY, THEN FORMAT

After the six pre-discovery values are known, always call get_location_and_movie_info before
find_movie_seats. Pass the requested ZIP code, inclusive dates, radius, and a theatre filter only
when the user named one.

Discovery returns canonical titles and exact live formats. If exactly one title clearly matches,
proceed with that canonical title. If sequels, rereleases, editions, dubbed versions, years, or other
similar titles create a real ambiguity, ask which exact title the user means. Never silently add,
remove, or rewrite title text. If nothing matches, explain that concisely and ask what single search
constraint the user wants to change.

Only after discovery, ask the format question using the exact formats actually available for the
chosen title, even when only one format was found. Ask one concise question, for example: "It is
available in IMAX, Dolby Cinema, and Standard. Which format would you like?" Pass multiple exact
formats if the user accepts several. Pass an empty movie_formats array only when the user explicitly
says any available format is fine. IMAX and IMAX 70mm are never interchangeable without permission.

SEAT POSITION

Always ask when no seat-position preference was supplied. Translate natural language internally;
do not mention the 15-by-15 grid unless the user asks how seat matching works.

The internal grid has 15 rows and 15 columns. Row 1 is nearest the screen, row 15 is the back,
column 1 is the left edge, and column 15 is the right edge. Include every cell the user would
reasonably accept. Use these mappings:

- "good" or "best" = broadly centered and about two-thirds back: rows 8-12, columns 5-11
- "dead center" = rows 7-9, columns 7-9
- "center" = rows 6-10, columns 6-10
- "center-left" or "center-right" = center rows on the requested side
- "front-center" or "back-center" = requested depth with center columns
- "near the back" = a broad rear region
- "left aisle" or "right aisle" = cells near that displayed edge
- "anywhere" = an empty seat_cells array

SEARCH

After format is answered, call find_movie_seats with the retained canonical title, dates, ZIP code,
radius, party size, exact accepted formats, translated seat cells, 14:00-23:59 unless the user gave
a different time, optional exact theatre, exclude_accessible=true unless requested otherwise,
sort="nearest", and no more than five results. Never weaken or omit an explicit constraint.

RESULTS

Return the available options together in one message as a numbered list of no more than five compact
single-line entries. Every line must contain movie title, date, time, theatre, exact format, and
distance. Do not include ticket URLs, raw payloads, grid coordinates, implementation details,
amenity prose, warnings, or a long explanation. Use this shape:

1. The Odyssey — Aug 8, 7:00 PM at AMC Victoria Gardens — IMAX — 2.4 mi

After the list, ask exactly one next-step question: "Would you like the seat map or ticket link for
any option?" If the requested option is ambiguous, ask only for its number.

For a seat map, call show_movie_seat_map with every argument copied exactly from that option's
seatMapRequest. Do not reconstruct or alter the values. Send the returned image and concise caption.
If availability changed, explain that briefly and offer to run the search again. Do not automatically
call the map tool before the user asks.

For a ticket link, return the selected option's ticketUrl without repeating the showtime summary,
followed only by a short warning that availability is not held or guaranteed and can change before
checkout. Never claim that tickets or seats were purchased, reserved, held, or guaranteed.

NO RESULTS AND RELAXATION

If no matches are found, state the important constraints searched in one concise sentence and ask
which one the user wants to change: date, time, radius, format, theatre, or seat area. The user must
choose. Ask before every relaxation and change only the constraint they approve.
""".strip()


movie_seat_mcp = MCPServer(
    name="movie-seat-finder",
    title="Movie Seat Finder",
    description="Discover canonical movie options, then search live showtimes and adjacent available seats.",
    instructions=MCP_INSTRUCTIONS,
    website_url="https://movieseatfinder.com",
    version="0.5.0",
)


@movie_seat_mcp.tool(
    title="Get location and movie information",
    description=(
        "Resolve a ZIP code and list the exact live theatre names, movie titles, available dates, "
        "and format strings that may be passed to find_movie_seats. Call only after movie, date, ZIP "
        "code, radius, party size, and seat preference are known. Ask about format after this call."
    ),
    structured_output=True,
)
def get_location_and_movie_info(
    zip_code: Annotated[str, Field(pattern=r"^\d{5}$", description="Five-digit US ZIP code")],
    start_date: Annotated[date, Field(description="First calendar date in YYYY-MM-DD form")],
    radius_miles: Annotated[
        float,
        Field(ge=1, le=100, description="Exact radius in miles supplied by the user"),
    ],
    end_date: Annotated[
        date | None,
        Field(description="Last calendar date in YYYY-MM-DD form; omit for one day"),
    ] = None,
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
        "good or best seats mean broadly centered and about two-thirds back. Format, radius, party "
        "size, and seat preference must be explicitly established; never invent them."
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
    movie_formats: Annotated[
        tuple[FormatName, ...],
        Field(
            max_length=10,
            description=(
                "Exact formats returned by get_location_and_movie_info. A showtime may match any "
                "selected value; use an empty array when every format is acceptable."
            ),
        ),
    ],
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
                "array only when the user explicitly accepts anywhere in the auditorium."
            ),
        ),
    ],
    radius_miles: Annotated[
        float,
        Field(ge=1, le=100, description="Exact radius in miles supplied by the user"),
    ],
    end_date: Annotated[
        date | None,
        Field(description="Last calendar date in YYYY-MM-DD form; omit for one day"),
    ] = None,
    start_time: Annotated[str, Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$", description="Earliest time, HH:MM")]
    = "14:00",
    end_time: Annotated[str, Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$", description="Latest time, HH:MM")]
    = "23:59",
    theatre: Annotated[str, Field(max_length=120, description="Optional theatre name filter")] = "",
    exclude_accessible: Annotated[
        bool,
        Field(description="Exclude wheelchair and companion seats unless explicitly requested"),
    ] = True,
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
        "sort": "nearest",
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
        "nearest",
        5,
    )
    return compact_search_results(result, query, seat_map_request)


@movie_seat_mcp.tool(
    title="Show a live movie seat map",
    description=(
        "Refresh and render one numbered result from find_movie_seats as an image/png seat map. "
        "Call only after the user asks to see a map. Copy every argument exactly from that option's "
        "seatMapRequest object; never reconstruct, alter, or guess the values."
    ),
    structured_output=False,
)
def show_movie_seat_map(
    showtime_hash_code: Annotated[
        str,
        Field(
            min_length=1,
            max_length=512,
            description="Exact showtime hash copied from the selected option's seatMapRequest",
        ),
    ],
    option_number: Annotated[int, Field(ge=1, le=5, description="Selected option number")],
    movie: Annotated[str, Field(min_length=1, max_length=120, description="Selected movie title")],
    theatre: Annotated[str, Field(min_length=1, max_length=200, description="Selected theatre name")],
    show_date: Annotated[date, Field(description="Selected calendar date in YYYY-MM-DD form")],
    show_time: Annotated[str, Field(min_length=1, max_length=40, description="Selected display time")],
    movie_format: Annotated[str, Field(min_length=1, max_length=120, description="Selected format")],
    seat_cells: Annotated[
        tuple[GridCell, ...],
        Field(max_length=225, description="Acceptable seat cells copied from the selected option"),
    ],
    adjacent_seats: Annotated[int, Field(ge=1, le=10, description="Required adjacent seat count")],
    exclude_accessible: Annotated[bool, Field(description="Whether accessible seats remain excluded")],
) -> list[Any]:
    """Refresh only the selected showtime and return its live layout as MCP image content."""
    seat_map = application.showtime_seat_match(
        {"showtimeHashCode": showtime_hash_code},
        adjacent_seats,
        application.parse_seat_grid(internal_seat_grid(seat_cells)),
        exclude_accessible,
        application.seat_map,
    )
    if not seat_map:
        raise ValueError(
            "Those seats are no longer available for that exact showtime. Run find_movie_seats again."
        )

    layout = seat_map["layout"]
    recommended_seats = [
        seat["id"]
        for seat in layout["seats"]
        if seat.get("matched") and seat.get("id")
    ]
    recommended_summary = ", ".join(recommended_seats[:12]) or "none currently highlighted"
    caption = (
        f"Live seat map for option {option_number}: {movie} at {theatre} — "
        f"{show_date.isoformat()} at {show_time} ({movie_format}). "
        f"Red = seats matching the request; white = available; gray = unavailable; "
        f"blue = accessible. Matching seat examples: {recommended_summary}."
    )
    details = {
        "movie": movie,
        "theatre": theatre,
        "date": show_date.isoformat(),
        "time": show_time,
        "format": movie_format,
    }
    image = render_seat_map_png(
        layout,
        details=details,
        available_count=seat_map["availableSeatCount"],
        total_count=seat_map["totalSeatCount"],
        accessible_seats_excluded=exclude_accessible,
    )
    return [caption, Image(data=image, format="png")]


transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[
        "movieseatfinder.com",
        "www.movieseatfinder.com",
        "movieseatfinder.vercel.app",
        "testserver",
        "localhost:*",
        "127.0.0.1:*",
    ],
    allowed_origins=[
        "http://localhost:*",
        "http://127.0.0.1:*",
    ],
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
