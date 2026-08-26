"""Remote MCP server exposing Movie Seat Finder as an agent tool."""

from datetime import date
from typing import Annotated, Any

from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import Annotations, CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field
from starlette.routing import Route

from .. import application
from ..seat_map_visual import render_seat_map_svg, render_svg_png
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


class SeatRegion(BaseModel):
    """A rectangular area on the public 1-based auditorium grid."""

    row_min: int = Field(
        ge=1, le=15, description="First acceptable row; row 1 is nearest the screen"
    )
    row_max: int = Field(ge=1, le=15, description="Last acceptable row; row 15 is at the back")
    column_min: int = Field(
        ge=1, le=15, description="First acceptable column from the displayed left edge"
    )
    column_max: int = Field(
        ge=1, le=15, description="Last acceptable column from the displayed left edge"
    )


class SeatMapOutput(BaseModel):
    """Structured fallback returned alongside the seat-map image."""

    model_config = ConfigDict(populate_by_name=True)  # noqa

    option: int  # noqa
    movie: str
    theatre: str
    date: str
    time: str
    format: str  # noqa
    matching_groups: list[list[str]] = Field(alias="matchingGroups")
    best_group: list[str] = Field(alias="bestGroup")
    available_seat_count: int = Field(alias="availableSeatCount")  # noqa
    total_seat_count: int = Field(alias="totalSeatCount")  # noqa
    seat_map_available: bool = Field(alias="seatMapAvailable")  # noqa


def resolved_seat_cells(seat_region, seat_cells):
    """Expand a normal rectangular selection or retain an advanced arbitrary shape."""
    if seat_region and seat_cells:
        raise ValueError("Use seat_region or seat_cells, not both.")
    if not seat_region:
        return tuple(seat_cells)
    if not isinstance(seat_region, SeatRegion):
        seat_region = SeatRegion.model_validate(seat_region)
    if seat_region.row_min > seat_region.row_max:
        raise ValueError("row_min must be less than or equal to row_max.")
    if seat_region.column_min > seat_region.column_max:
        raise ValueError("column_min must be less than or equal to column_max.")
    return tuple(
        f"{row}:{column}"
        for row in range(seat_region.row_min, seat_region.row_max + 1)
        for column in range(seat_region.column_min, seat_region.column_max + 1)
    )


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
        all_matching_groups = match["seatMap"]["matchingGroups"]
        matching_groups = all_matching_groups[:12]
        matching_seats = {seat for group in all_matching_groups for seat in group}
        showtime_request = {
            "showtime_hash_code": match["showtimeHashCode"],
            "option_number": position,
            "movie": match["movieTitle"],
            "theatre": match["theatre"]["name"],
            "show_date": match["date"],
            "show_time": match["displayTime"],
            "movie_format": match["format"],
            "seat_region": seat_map_request["seat_region"],
            "seat_cells": seat_map_request["seat_cells"],
            "adjacent_seats": seat_map_request["adjacent_seats"],
            "exclude_accessible": seat_map_request["exclude_accessible"],
        }
        options.append(
            {
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
                "matchingGroups": matching_groups,
                "bestGroup": match["seatMap"]["bestGroup"],
                "ticketUrl": match["ticketUrl"],
                "seatMapRequest": showtime_request,
            }
        )
    return {
        "query": query,
        "options": options,
        "resultCount": len(options),
        "checkedShowtimes": result["checkedShowtimes"],
        "checkedSeatMaps": result["checkedSeatMaps"],
        "message": (
            "Present these compact options and offer the seat map or ticket link for a selected option."
            if options
            else "No matching live seat maps were found. Ask which explicit constraint the user wants to change."
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
Help users find live movie showtimes and adjacent available seats. Carry forward details already
provided and ask only when required information is genuinely missing, ambiguous, or unavailable.
When you need to ask, always use the client's user-question or input tool if one is available. If
the client has no such tool, ask in your response. Collect all and only the currently blocking
details in one concise question instead of asking for them one at a time. Never invent a movie,
date, ZIP code, party size, format, or seat preference.

Do not ask for an optional preference when a default is defined. When presenting results, briefly
state every default applied because the user omitted that preference.

Use get_location_and_movie_info before the first search and whenever the location, date, theatre,
movie, or format changes. ZIP code and date or date range must be known; radius defaults to 25 miles.
Pass movie_query and format_query whenever the user has expressed those preferences so discovery
stays focused. If one returned title or normalized format unambiguously matches the request, proceed
without asking the user to repeat it. Ask only when multiple plausible choices remain or the
requested choice is unavailable. An empty movie_formats array searches every format. Do not treat
IMAX, IMAX with Laser, and IMAX 70mm as interchangeable.

Translate ordinary rectangular seat preferences into seat_region. The grid has 15 rows and 15
columns: row 1 is nearest the screen, row 15 is the back, column 1 is the displayed left edge, and
column 15 is the right edge. When the user's preference is vague, reasonable defaults are: good/best
= rows 8-12 and columns 5-11; dead center = rows 7-9 and columns 7-9; center = rows 6-10 and columns
6-10. Honor more specific wording. Use seat_cells only for an arbitrary non-rectangular shape. Use
seat_region=null and an empty seat_cells array when anywhere is acceptable.

Search 00:00-23:59 when no time is given, all theatres when none is named, every format when none is
requested, anywhere in the auditorium when no seat preference is given, and exclude accessible seats
unless accessible seating is requested. Treat "ASAP" as the earliest future date with matching seats
across the requested or supported date range, not merely the earliest time today. Never weaken an
explicit constraint without permission.

Present up to five concise numbered options with movie, date, time, theatre, format, distance, and
bestGroup when useful. Use the selected option's ticketUrl for checkout or copy its seatMapRequest
into show_movie_seat_map to refresh availability. The map tool returns both an image and structured
seat groups. When the user asks to see the map, include the returned image in the user-visible
response instead of leaving it only in the tool trace. If the client cannot display tool-result
images, say so and present the structured seat groups. Never claim seats are held, reserved,
purchased, or guaranteed.
""".strip()


movie_seat_mcp = MCPServer(
    name="movie-seat-finder",
    title="Movie Seat Finder",
    description="Discover canonical movie options, then search live showtimes and adjacent available seats.",
    instructions=MCP_INSTRUCTIONS,
    website_url="https://movieseatfinder.com",
    version="0.6.0",
)


@movie_seat_mcp.tool(
    title="Get location and movie information",
    description=(
        "List live theatre names, canonical movie titles, dates, and normalized formats for a location. "
        "Pass movie_query and format_query when known to keep the response focused."
    ),
    structured_output=True,
)
def get_location_and_movie_info(
    zip_code: Annotated[str, Field(pattern=r"^\d{5}$", description="Five-digit US ZIP code")],
    start_date: Annotated[date, Field(description="First calendar date in YYYY-MM-DD form")],
    radius_miles: Annotated[
        float,
        Field(ge=1, le=100, description="Search radius in miles; defaults to 25 when unspecified"),
    ] = 25,
    end_date: Annotated[
        date | None,
        Field(description="Last calendar date in YYYY-MM-DD form; omit for one day"),
    ] = None,
    theatre: Annotated[
        str,
        Field(
            max_length=120, description="Optional partial theatre-name filter; normally leave empty"
        ),
    ] = "",
    movie_query: Annotated[
        str,
        Field(
            max_length=120,
            description="Optional partial movie-title hint used to reduce the response",
        ),
    ] = "",
    format_query: Annotated[
        str,
        Field(
            max_length=120,
            description="Optional format hint, such as IMAX 70mm, used to reduce the response",
        ),
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
            movie_query=movie_query,
            format_query=format_query,
        )
    except HTTPException as error:
        raise ValueError(_public_error(error)) from error
    return {
        **result,
        "message": (
            "Use the returned title, normalized format, and optional theatre in find_movie_seats. "
            "Clarify only when multiple plausible choices remain."
        ),
    }


@movie_seat_mcp.tool(
    title="Find available movie seats",
    description=(
        "Find real showtimes with adjacent available seats. Prefer seat_region for a rectangular "
        "preference and seat_cells only for an arbitrary shape."
    ),
    structured_output=True,
)
def find_movie_seats(
    movie: Annotated[
        str,
        Field(
            min_length=1,
            max_length=120,
            description="Exact title returned by get_location_and_movie_info",
        ),
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
    seat_region: Annotated[
        SeatRegion | None,
        Field(
            description="Normal rectangular seat preference; use null only when anywhere is acceptable"
        ),
    ],
    radius_miles: Annotated[
        float,
        Field(ge=1, le=100, description="Search radius in miles; defaults to 25 when unspecified"),
    ] = 25,
    end_date: Annotated[
        date | None,
        Field(description="Last calendar date in YYYY-MM-DD form; omit for one day"),
    ] = None,
    start_time: Annotated[
        str,
        Field(
            pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
            description="Earliest time, HH:MM; defaults to 00:00",
        ),
    ] = "00:00",
    end_time: Annotated[
        str,
        Field(
            pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
            description="Latest time, HH:MM; defaults to 23:59",
        ),
    ] = "23:59",
    theatre: Annotated[
        str,
        Field(max_length=120, description="Optional theatre filter; empty searches all theatres"),
    ] = "",
    exclude_accessible: Annotated[
        bool,
        Field(description="Exclude wheelchair and companion seats unless explicitly requested"),
    ] = True,
    seat_cells: Annotated[
        tuple[GridCell, ...],
        Field(
            max_length=225,
            description="Advanced arbitrary-shape override. Leave empty when using seat_region or accepting anywhere.",
        ),
    ] = (),
) -> dict[str, Any]:
    """Find live showtimes with seats matching a conversational preference."""
    if start_time > end_time:
        raise ValueError("start_time must be earlier than or equal to end_time.")
    selected_cells = resolved_seat_cells(seat_region, seat_cells)
    serialized_region = (
        seat_region.model_dump() if isinstance(seat_region, SeatRegion) else seat_region
    )
    search_end_date = end_date or start_date
    query = {
        "movie": movie,
        "dateRange": {"start": start_date.isoformat(), "end": search_end_date.isoformat()},
        "zipCode": zip_code,
        "formats": list(movie_formats),
        "seatRegion": serialized_region,
        "seatCells": list(seat_cells),
        "adjacentSeats": adjacent_seats,
        "radiusMiles": radius_miles,
        "timeRange": {"start": start_time, "end": end_time},
        "theatre": theatre,
        "excludeAccessible": exclude_accessible,
    }
    seat_map_request = {
        "movie": movie,
        "start_date": start_date.isoformat(),
        "end_date": search_end_date.isoformat(),
        "zip_code": zip_code,
        "movie_formats": list(movie_formats),
        "seat_region": serialized_region,
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
        selected_cells,
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
        "Refresh one numbered result from find_movie_seats and return a user-facing image/png plus "
        "structured matchingGroups and bestGroup fallback data. Copy the selected option's "
        "seatMapRequest values and include the returned image in the response to the user."
    ),
    structured_output=True,
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
    theatre: Annotated[
        str, Field(min_length=1, max_length=200, description="Selected theatre name")
    ],
    show_date: Annotated[date, Field(description="Selected calendar date in YYYY-MM-DD form")],
    show_time: Annotated[
        str, Field(min_length=1, max_length=40, description="Selected display time")
    ],
    movie_format: Annotated[
        str, Field(min_length=1, max_length=120, description="Selected format")
    ],
    adjacent_seats: Annotated[int, Field(ge=1, le=10, description="Required adjacent seat count")],
    exclude_accessible: Annotated[
        bool, Field(description="Whether accessible seats remain excluded")
    ],
    seat_region: Annotated[
        SeatRegion | None,
        Field(description="Seat region copied from the selected option"),
    ],
    seat_cells: Annotated[
        tuple[GridCell, ...],
        Field(max_length=225, description="Advanced seat cells copied from the selected option"),
    ] = (),
) -> SeatMapOutput:
    """Refresh only the selected showtime and return its live layout as MCP image content."""
    selected_cells = resolved_seat_cells(seat_region, seat_cells)
    seat_map = application.showtime_seat_match(
        {"showtimeHashCode": showtime_hash_code},
        adjacent_seats,
        application.parse_seat_grid(internal_seat_grid(selected_cells)),
        exclude_accessible,
        application.seat_map,
    )
    if not seat_map:
        raise ValueError(
            "Those seats are no longer available for that exact showtime. Run find_movie_seats again."
        )

    matching_groups = seat_map["matchingGroups"][:12]
    best_group = seat_map["bestGroup"]
    recommended_summary = "-".join(best_group) or "none currently highlighted"
    caption = (
        f"Display the attached seat-map image directly to the user. "
        f"Live seat map for option {option_number}: {movie} at {theatre} — "
        f"{show_date.isoformat()} at {show_time} ({movie_format}). "
        f"Red = seats matching the request; white = available; gray = unavailable; "
        f"blue = accessible. Best matching group: {recommended_summary}."
    )
    image = render_svg_png(
        render_seat_map_svg(
            seat_map["layout"],
            available_count=seat_map["availableSeatCount"],
            total_count=seat_map["totalSeatCount"],
            accessible_seats_excluded=exclude_accessible,
        )
    )
    output = SeatMapOutput(
        option=option_number,
        movie=movie,
        theatre=theatre,
        date=show_date.isoformat(),
        time=show_time,
        format=movie_format,
        matchingGroups=matching_groups,
        bestGroup=best_group,
        availableSeatCount=seat_map["availableSeatCount"],
        totalSeatCount=seat_map["totalSeatCount"],
        seatMapAvailable=True,
    )
    image_content = (
        Image(data=image, format="png")
        .to_image_content()
        .model_copy(
            update={
                "annotations": Annotations(audience=["user"], priority=1.0),
            }
        )
    )
    # MCPServer validates structuredContent against SeatMapOutput while preserving image content.
    return CallToolResult(
        content=[image_content, TextContent(text=caption)],
        structuredContent=output.model_dump(mode="json", by_alias=True),
    )


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
