"""Remote MCP server exposing Movie Seat Finder as an agent tool."""

import json
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import Annotations, CallToolResult, TextContent
from pydantic import BaseModel, Field
from starlette.routing import Route

from .. import application
from ..application import (
    MAX_ADJACENT_SEATS,
    MAX_RADIUS_MILES,
    Area,
    RequiredShortText,
    Search,
    ShortText,
    TimeText,
)
from ..fandango import UPSTREAM_ERRORS, seat_map
from ..seat_map_visual import render_seat_map_svg, render_svg_png
from ..seat_matching import GRID_SIZE, parse_seat_grid, seat_map_match
from ..share_links import decode_share_link, encode_share_link
from .browser_page import BrowserPageMiddleware

SHARE_URL_PREFIX = "https://movieseatfinder.com/s/"
# Above this many movies, discovery gives each movie's theatre count instead of its theatre names.
MOVIE_THEATRE_NAME_LIMIT = 5

GridCell = Annotated[
    str,
    Field(
        pattern=r"^(?:[1-9]|1[0-5]):(?:[1-9]|1[0-5])$",
        description="One 1-based auditorium cell in row:column form, for example 8:7",
    ),
]
FormatName = Annotated[
    RequiredShortText, Field(description="One exact format returned by get_location_and_movie_info")
]


class SeatRegion(BaseModel):
    """A rectangular area on the public 1-based auditorium grid."""

    row_min: int = Field(
        ge=1, le=GRID_SIZE, description="First acceptable row; row 1 is nearest the screen"
    )
    row_max: int = Field(
        ge=1, le=GRID_SIZE, description="Last acceptable row; row 15 is at the back"
    )
    column_min: int = Field(
        ge=1, le=GRID_SIZE, description="First acceptable column from the displayed left edge"
    )
    column_max: int = Field(
        ge=1, le=GRID_SIZE, description="Last acceptable column from the displayed left edge"
    )


class SeatMapOutput(BaseModel):
    """Structured fallback returned alongside the seat-map image."""

    movie: str
    theatre: str
    date: str
    time: str
    format: str
    best_group: list[str] = Field(alias="bestGroup")
    matching_groups: list[list[str]] = Field(alias="matchingGroups")
    available_seat_count: int = Field(alias="availableSeatCount")  # noqa
    total_seat_count: int = Field(alias="totalSeatCount")  # noqa


def internal_seat_grid(seat_region, seat_cells):
    """Turn the MCP's 1-based seat preference into the website's 0-based seatGrid value."""
    if seat_region and seat_cells:
        raise ToolError("Use seat_region or seat_cells, not both.")
    if seat_region:
        if seat_region.row_min > seat_region.row_max:
            raise ToolError("row_min must be less than or equal to row_max.")
        if seat_region.column_min > seat_region.column_max:
            raise ToolError("column_min must be less than or equal to column_max.")
        cells = [
            (row, column)
            for row in range(seat_region.row_min, seat_region.row_max + 1)
            for column in range(seat_region.column_min, seat_region.column_max + 1)
        ]
    else:
        cells = [tuple(int(value) for value in cell.split(":")) for cell in seat_cells]
    return ",".join(f"{row - 1}:{column - 1}" for row, column in cells)


def tool_result(data):
    """Return structured content plus the compact JSON text copy that text-only clients read."""
    return CallToolResult(
        content=[TextContent(text=json.dumps(data, ensure_ascii=False, separators=(",", ":")))],
        structuredContent=data,
    )


def _public_error(error):
    detail = error.detail
    if isinstance(detail, dict):
        return detail["error"]
    return detail


def compact_movie_info(result, area, movie_query, format_query):
    """Keep discovery small enough for a model's context: exact values only, no addresses."""
    multi_day = result["startDate"] != result["endDate"]
    name_theatres = len(result["movies"]) <= MOVIE_THEATRE_NAME_LIMIT
    movies = []
    for movie in result["movies"]:
        item = {"title": movie["title"], "formats": movie["formats"]}
        if multi_day:
            item["dates"] = movie["dates"]
        if name_theatres:
            item["theatres"] = movie["theatres"]
        else:
            item["theatreCount"] = len(movie["theatres"])
        movies.append(item)

    info = {
        "place": result["place"],
        "startDate": result["startDate"],
        "endDate": result["endDate"],
        "theatres": [
            {"name": theatre["name"], "distanceMiles": round(theatre["distanceMiles"], 1)}
            for theatre in result["theatres"]
        ],
        "movies": movies,
    }
    if not movies:
        wanted = " ".join(filter(None, [movie_query, format_query, area.theatre_query]))
        subject = f'Nothing matching "{wanted}" is' if wanted else "No movies are"
        info["message"] = (
            f"{subject} showing within {area.radius:g} miles of {result['place']} on these dates."
        )
    return info


def seat_map_url(match, search):
    """A share link for one result: show_movie_seat_map reads it, and it opens the live map.

    It carries only what the map needs, which keeps it short enough to copy between tool calls.
    """
    token = encode_share_link(
        {
            "adjacentSeats": search.adjacent_seats,
            "date": match["date"],
            "excludeAccessible": int(search.exclude_accessible),
            "format": match["format"],
            "movie": match["movieTitle"],
            "seatGrid": search.seat_grid,
            "showtime": match["showtimeHashCode"],
            "theatre": match["theatre"]["name"],
            "time": match["displayTime"],
        }
    )
    return f"{SHARE_URL_PREFIX}{token}"


def search_message(result, search):
    """Say what limits these results, if anything does."""
    if result["failedSeatMaps"]:
        return (
            f"Incomplete: {result['failedSeatMaps']} seat maps couldn't be checked, so a retry "
            "may find more."
        )
    if result["capped"]:
        return (
            f"Only the first {result['checkedSeatMaps']} of {result['checkedShowtimes']} "
            "candidate showtimes were checked; narrower dates, times, radius or theatre would "
            "check the rest."
        )
    if result["hasNextPage"]:
        return "More matching showtimes exist; narrower dates, times or theatre would show them."
    if result["matches"]:
        return ""
    if not result["checkedShowtimes"]:
        return (
            f"No {search.movie} showtimes match these dates, times, formats and theatres within "
            f"{search.area.radius:g} miles; the title, formats or dates may not match what "
            "get_location_and_movie_info lists."
        )
    return (
        f"None of the {result['checkedSeatMaps']} showtimes checked has {search.adjacent_seats} "
        "adjacent seats in the requested area."
    )


def compact_search_results(result, search):
    """Return only the details an assistant needs to present and act on options."""
    options = []
    for position, match in enumerate(result["matches"], start=1):
        seat_match = match["seatMap"]
        options.append(
            {
                "option": position,
                "movie": match["movieTitle"],
                "theatre": match["theatre"]["name"],
                "address": match["theatre"]["address"],
                "distanceMiles": round(match["theatre"]["distanceMiles"], 1),
                "date": match["date"],
                "time": match["displayTime"],
                "format": match["format"],
                "amenities": match["amenities"],
                "availableSeatCount": seat_match["availableSeatCount"],
                "matchingSeatCount": len(
                    {seat for group in seat_match["matchingGroups"] for seat in group}
                ),
                "bestGroup": seat_match["bestGroup"],
                "ticketUrl": match["ticketUrl"],
                "seatMapUrl": seat_map_url(match, search),
            }
        )
    results = {
        "options": options,
        "checkedShowtimes": result["checkedShowtimes"],
        "checkedSeatMaps": result["checkedSeatMaps"],
        "failedSeatMaps": result["failedSeatMaps"],
    }
    message = search_message(result, search)
    if message:
        results["message"] = message
    return results


MCP_INSTRUCTIONS = """
Help users find live movie showtimes with adjacent available seats. Carry forward details the user
already gave. Ask only when required information is missing or ambiguous, and collect every
blocking detail in one question, using the client's question tool if it has one. Never invent a
movie, date, ZIP code, party size, format or seat preference.

Required: ZIP code, date or date range, movie and party size. Dates must be today or later, and one
search covers at most 15 days. Everything else has a default, so don't ask for it: 25-mile radius,
any time, every format, all theatres, anywhere in the auditorium, accessible seats excluded,
nearest theatres first. Mention an assumption when it shapes the results, such as how you read a
time or seat area.

Call get_location_and_movie_info before the first search, and again when the location changes or
you need titles or formats for dates you haven't looked up. Pass movie_query and format_query when
the user named them, and use the returned titles and formats exactly. If one title clearly matches,
use it without asking.
If the user described a kind of movie instead of a title, offer a few fitting titles from the list.
IMAX, IMAX with Laser and IMAX 70mm are different formats: when the user just says "IMAX", pass
every IMAX format returned; when they name one, pass only that one.

Times: morning = 00:00-11:59, afternoon = 12:00-16:59, evening = 17:00-23:59, tonight = today
17:00-23:59. For "ASAP", "soonest" or "earliest", search from today through the next 14 days with
sort "earliest"; otherwise use sort "nearest".

seat_region is a 15 by 15 grid scaled to the auditorium: row 1 is the front, row 15 the back,
column 1 the left edge facing the screen, column 15 the right edge. It is relative position, not the
theatre's row letters or seat numbers. Defaults for vague wording: good or best = rows 8-12,
columns 5-11; center or middle = rows 6-10, columns 6-10; dead center = rows 7-9, columns 7-9; back
half = rows 8-15; front half = rows 1-7; back row = rows 14-15. Honor more specific wording. Use
seat_cells only for a shape that isn't a rectangle, and seat_region null when anywhere is fine.

If the user needs wheelchair or companion seating, set exclude_accessible to false and tell them the
results don't guarantee those spaces; they choose them on the seat map or at checkout.

A result's message says what limits it, such as seat maps that couldn't be checked or more matches
than shown; pass that on when it matters to the user. bestGroup uses the theatre's own seat labels.
For checkout, share the option's ticketUrl. To show seats, call show_movie_seat_map with the
option's seatMapUrl and include the returned image in your reply; if images can't be shown, share
the seatMapUrl link, which opens the live map in a browser. Never claim seats are held, reserved,
purchased or guaranteed.
""".strip()


movie_seat_mcp = MCPServer(
    name="movie-seat-finder",
    title="Movie Seat Finder",
    description="Discover canonical movie options, then search live showtimes and adjacent available seats.",
    instructions=MCP_INSTRUCTIONS,
    website_url="https://movieseatfinder.com",
    version="0.7.0",
)

ZipCode = Annotated[str, Field(pattern=r"^\d{5}$", description="Five-digit US ZIP code")]
StartDate = Annotated[date, Field(description="First date, YYYY-MM-DD; today or later")]
EndDate = Annotated[
    date | None,
    Field(description="Last date, YYYY-MM-DD, at most 14 days after start_date; omit for one day"),
]
RadiusMiles = Annotated[
    float, Field(ge=1, le=MAX_RADIUS_MILES, description="Search radius in miles; defaults to 25")
]
StartTime = Annotated[TimeText, Field(description="Earliest start time, HH:MM; defaults to 00:00")]
EndTime = Annotated[TimeText, Field(description="Latest start time, HH:MM; defaults to 23:59")]


@movie_seat_mcp.tool(
    title="Get location and movie information",
    description=(
        "List the movies, normalized formats and theatres showing near a ZIP code on the given "
        "dates. Pass movie_query and format_query when the user named them."
    ),
    structured_output=True,
)
def get_location_and_movie_info(
    zip_code: ZipCode,
    start_date: StartDate,
    radius_miles: RadiusMiles = 25,
    end_date: EndDate = None,
    theatre: Annotated[
        ShortText, Field(description="Optional partial theatre name; normally leave empty")
    ] = "",
    movie_query: Annotated[
        ShortText, Field(description="Optional partial movie title the user named")
    ] = "",
    format_query: Annotated[
        ShortText, Field(description="Optional format the user named, such as IMAX")
    ] = "",
) -> dict[str, Any]:
    """Discover canonical live values before an agent performs a seat search."""
    area = Area(
        radius=radius_miles,
        zip_code=zip_code,
        start_date=start_date,
        end_date=end_date,
        theatre_query=theatre,
    )
    try:
        result = application.location_movie_info(
            area, movie_query=movie_query, format_query=format_query
        )
    except HTTPException as error:
        raise ToolError(_public_error(error)) from error
    return tool_result(compact_movie_info(result, area, movie_query, format_query))


@movie_seat_mcp.tool(
    title="Find available movie seats",
    description=(
        "Find live showtimes of one movie with enough adjacent available seats, up to five "
        "options. Each option has a ticketUrl for checkout and a seatMapUrl for show_movie_seat_map."
    ),
    structured_output=True,
)
def find_movie_seats(
    movie: Annotated[
        RequiredShortText, Field(description="Exact title returned by get_location_and_movie_info")
    ],
    start_date: StartDate,
    zip_code: ZipCode,
    adjacent_seats: Annotated[
        int, Field(ge=1, le=MAX_ADJACENT_SEATS, description="Number of seats needed together")
    ],
    movie_formats: Annotated[
        tuple[FormatName, ...],
        Field(
            max_length=10,
            description=(
                "Exact formats returned by get_location_and_movie_info; a showtime may match any "
                "of them. Use an empty array when every format is fine."
            ),
        ),
    ],
    seat_region: Annotated[
        SeatRegion | None,
        Field(description="Rectangular seat preference; null when anywhere is fine"),
    ],
    radius_miles: RadiusMiles = 25,
    end_date: EndDate = None,
    start_time: StartTime = "00:00",
    end_time: EndTime = "23:59",
    theatre: Annotated[
        ShortText, Field(description="Optional partial theatre name; empty searches all")
    ] = "",
    sort: Annotated[
        Literal["nearest", "earliest"],
        Field(description='"nearest" theatres first (default), or "earliest" showtimes first'),
    ] = "nearest",
    exclude_accessible: Annotated[
        bool,
        Field(description="Exclude wheelchair and companion seats unless the user needs them"),
    ] = True,
    seat_cells: Annotated[
        tuple[GridCell, ...],
        Field(
            max_length=225,
            description="Seat cells for a shape that isn't a rectangle; otherwise leave empty",
        ),
    ] = (),
) -> dict[str, Any]:
    """Find live showtimes with seats matching a conversational preference."""
    search = Search(
        area=Area(
            radius=radius_miles,
            zip_code=zip_code,
            start_date=start_date,
            end_date=end_date,
            theatre_query=theatre,
        ),
        movie=movie,
        requested_format=",".join(movie_formats) or "any",
        start_time=start_time,
        end_time=end_time,
        adjacent_seats=adjacent_seats,
        seat_grid=internal_seat_grid(seat_region, seat_cells),
        exclude_accessible=exclude_accessible,
        sort=sort,
    )
    try:
        result = application.find_seat_matches(search, page_size=5)
    except HTTPException as error:
        raise ToolError(_public_error(error)) from error
    return tool_result(compact_search_results(result, search))


@movie_seat_mcp.tool(
    title="Show a live movie seat map",
    description=(
        "Refresh one find_movie_seats option and return its live seat map as an image, with the "
        "matching seat groups. Pass the option's seatMapUrl."
    ),
    structured_output=True,
)
def show_movie_seat_map(
    seat_map_url: Annotated[
        str,
        Field(
            min_length=1,
            max_length=4200,
            description="seatMapUrl copied exactly from the chosen find_movie_seats option",
        ),
    ],
) -> SeatMapOutput:
    """Refresh only the selected showtime and return its live layout as MCP image content."""
    try:
        shared = decode_share_link(seat_map_url.rstrip("/").rsplit("/", 1)[-1])
    except ValueError as error:
        raise ToolError(
            "That seatMapUrl is invalid. Copy it exactly from a find_movie_seats option."
        ) from error
    try:
        raw_map = seat_map(shared.showtime)
    except UPSTREAM_ERRORS as error:
        raise ToolError("Fandango's seat map couldn't be loaded. Try again.") from error
    seat_match = seat_map_match(
        raw_map,
        shared.adjacent_seats,
        parse_seat_grid(shared.seat_grid),
        shared.exclude_accessible,
    )
    if not seat_match:
        raise ToolError(
            "Those seats are no longer available for that showtime. Run find_movie_seats again."
        )

    best_group = seat_match["bestGroup"]
    if shared.exclude_accessible:
        legend = "gray = unavailable or accessible (excluded from this search)"
    else:
        legend = "gray = unavailable, blue = accessible"
    caption = (
        f"Live seat map for {shared.movie} at {shared.theatre}, {shared.show_date.isoformat()} at "
        f"{shared.time} ({shared.format}). Rows carry the theatre's row letters. Red = matching "
        f"seats, white = available, {legend}. Best matching group: {'-'.join(best_group)}."
    )
    image = render_svg_png(
        render_seat_map_svg(
            seat_match["layout"],
            available_count=seat_match["availableSeatCount"],
            total_count=seat_match["totalSeatCount"],
            accessible_seats_excluded=shared.exclude_accessible,
        )
    )
    output = SeatMapOutput(
        movie=shared.movie,
        theatre=shared.theatre,
        date=shared.show_date.isoformat(),
        time=shared.time,
        format=shared.format,
        bestGroup=best_group,
        matchingGroups=seat_match["matchingGroups"][:12],
        availableSeatCount=seat_match["availableSeatCount"],
        totalSeatCount=seat_match["totalSeatCount"],
    )
    image_content = (
        Image(data=image, format="png")
        .to_image_content()
        .model_copy(update={"annotations": Annotations(audience=["user"], priority=1.0)})
    )
    return CallToolResult(
        content=[image_content, TextContent(text=caption)],
        structuredContent=output.model_dump(mode="json", by_alias=True),
    )


mcp_protocol_app = movie_seat_mcp.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    max_request_body_size=64 * 1024,
    # The SDK otherwise enables localhost-only DNS-rebinding protection, which rejects every
    # request to the public site. That protection is for local servers; this one is public
    # and unauthenticated.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
mcp_route = Route(
    "/mcp",
    endpoint=BrowserPageMiddleware(mcp_protocol_app.routes[0].endpoint),
)
