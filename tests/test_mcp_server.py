import base64
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from src import server as entrypoint
from src.application import Area, Search
from src.mcp_server import server
from src.share_links import decode_share_link


def sample_seat_layout():
    return {
        "width": 100,
        "height": 100,
        "backgroundSvg": "<svg>large payload</svg>",
        "seats": [
            {
                "id": "H10",
                "matched": True,
                "status": "A",
                "type": "standard",
                "x": 40,
                "y": 50,
                "width": 8,
                "height": 8,
            },
            {
                "id": "H11",
                "matched": True,
                "status": "A",
                "type": "standard",
                "x": 50,
                "y": 50,
                "width": 8,
                "height": 8,
            },
            {
                "id": "A1",
                "matched": False,
                "status": "S",
                "type": "standard",
                "x": 10,
                "y": 10,
                "width": 8,
                "height": 8,
            },
        ],
    }


def sample_search_result():
    return {
        "matches": [
            {
                "theatre": {
                    "name": "Test Cinema",
                    "address": "1 Main St",
                    "distanceMiles": 2.5,
                },
                "movieTitle": "The Odyssey",
                "date": "2026-08-04",
                "displayTime": "7:30 PM",
                "format": "IMAX",
                "amenities": "IMAX with Laser",
                "ticketUrl": "https://tickets.fandango.com/order",
                "showtimeHashCode": "showtime-123",
                "seatMap": {
                    "availableSeatCount": 42,
                    "totalSeatCount": 100,
                    "matchingGroups": [["H10", "H11"]],
                    "bestGroup": ["H10", "H11"],
                    "layout": sample_seat_layout(),
                },
            }
        ],
        "checkedShowtimes": 7,
        "checkedSeatMaps": 3,
        "failedSeatMaps": 0,
        "capped": False,
        "hasNextPage": False,
    }


def sample_search():
    return Search(
        area=Area(radius=25, zip_code="10023"),
        movie="The Odyssey",
        adjacent_seats=2,
        seat_grid="6:6,6:7",
    )


def sample_seat_map_url():
    return server.seat_map_url(sample_search_result()["matches"][0], sample_search())


def sample_movie_info(movie_count):
    return {
        "place": "New York, NY 10023",
        "startDate": "2026-08-04",
        "endDate": "2026-08-04",
        "theatres": [{"name": "Test Cinema", "address": "1 Main St", "distanceMiles": 2.54}],
        "movies": [
            {
                "title": f"Movie {number}",
                "dates": ["2026-08-04"],
                "formats": ["Standard"],
                "theatres": ["Test Cinema"],
            }
            for number in range(movie_count)
        ],
    }


class McpProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(entrypoint.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    def request_headers(self):
        return {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }

    def test_client_can_discover_the_discovery_and_search_tools(self):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }
        response = self.client.post("/mcp", headers=self.request_headers(), json=request)

        self.assertEqual(response.status_code, 200)
        tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
        self.assertEqual(
            set(tools),
            {"get_location_and_movie_info", "find_movie_seats", "show_movie_seat_map"},
        )

    @patch.object(server.application, "find_seat_matches", return_value=sample_search_result())
    def test_client_can_call_the_tool_and_receive_structured_ticket_options(
        self, find_seat_matches
    ):
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "find_movie_seats",
                "arguments": {
                    "movie": "The Odyssey",
                    "start_date": "2026-08-04",
                    "end_date": "2026-08-06",
                    "zip_code": "10023",
                    "movie_formats": ["IMAX", "IMAX 70mm"],
                    "seat_region": {
                        "row_min": 11,
                        "row_max": 12,
                        "column_min": 6,
                        "column_max": 8,
                    },
                    "adjacent_seats": 2,
                },
            },
        }
        response = self.client.post("/mcp", headers=self.request_headers(), json=request)

        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertFalse(result["isError"])
        option = result["structuredContent"]["options"][0]
        self.assertEqual(option["bestGroup"], ["H10", "H11"])
        self.assertEqual(option["matchingSeatCount"], 2)
        self.assertEqual(option["ticketUrl"], "https://tickets.fandango.com/order")
        shared = decode_share_link(option["seatMapUrl"].rsplit("/", 1)[1])
        self.assertEqual(shared.showtime, "showtime-123")
        self.assertEqual(shared.seat_grid, "10:5,10:6,10:7,11:5,11:6,11:7")
        search = find_seat_matches.call_args.args[0]
        self.assertEqual(search.seat_grid, "10:5,10:6,10:7,11:5,11:6,11:7")
        self.assertEqual(search.requested_format, "IMAX,IMAX 70mm")

    @patch.object(
        server.application,
        "find_seat_matches",
        side_effect=HTTPException(
            status_code=400, detail="Latest time must be on or after earliest time."
        ),
    )
    def test_expected_search_errors_reach_the_model_without_logging_a_crash(
        self, _find_seat_matches
    ):
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "find_movie_seats",
                "arguments": {
                    "movie": "The Odyssey",
                    "start_date": "2026-08-04",
                    "zip_code": "10023",
                    "movie_formats": [],
                    "seat_region": None,
                    "adjacent_seats": 2,
                },
            },
        }
        with self.assertNoLogs(level="ERROR"):
            response = self.client.post("/mcp", headers=self.request_headers(), json=request)

        result = response.json()["result"]
        self.assertTrue(result["isError"])
        self.assertIn(
            "Latest time must be on or after earliest time.", result["content"][0]["text"]
        )

    @patch.object(server, "seat_map")
    @patch.object(
        server,
        "seat_map_match",
        return_value=sample_search_result()["matches"][0]["seatMap"],
    )
    def test_client_receives_an_image_and_structured_seat_map_fallback(self, _match, _seat_map):
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "show_movie_seat_map",
                "arguments": {"seat_map_url": sample_seat_map_url()},
            },
        }
        response = self.client.post("/mcp", headers=self.request_headers(), json=request)

        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertFalse(result["isError"])
        self.assertEqual([item["type"] for item in result["content"]], ["image", "text"])
        self.assertEqual(result["content"][0]["mimeType"], "image/png")
        self.assertTrue(base64.b64decode(result["content"][0]["data"]).startswith(b"\x89PNG"))
        self.assertIn("Best matching group: H10-H11", result["content"][1]["text"])
        self.assertEqual(result["structuredContent"]["matchingGroups"], [["H10", "H11"]])
        _match.assert_called_once_with(_seat_map.uncached.return_value, 2, [(6, 6), (6, 7)], True)

    def test_an_invalid_seat_map_url_asks_for_the_exact_link(self):
        request = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "show_movie_seat_map",
                "arguments": {"seat_map_url": "https://movieseatfinder.com/s/not-a-link"},
            },
        }
        response = self.client.post("/mcp", headers=self.request_headers(), json=request)

        result = response.json()["result"]
        self.assertTrue(result["isError"])
        self.assertIn("That seatMapUrl is invalid", result["content"][0]["text"])


class CompactSearchResultsTests(unittest.TestCase):
    def test_a_complete_search_has_no_message(self):
        compact = server.compact_search_results(sample_search_result(), sample_search())

        self.assertNotIn("message", compact)

    def test_capped_search_says_how_much_was_checked(self):
        result = sample_search_result()
        result["capped"] = True
        result["checkedSeatMaps"] = 150
        result["checkedShowtimes"] = 400

        compact = server.compact_search_results(result, sample_search())

        self.assertIn(
            "Only the first 150 of 400 candidate showtimes were checked", compact["message"]
        )

    def test_a_full_page_says_more_showtimes_exist(self):
        result = {**sample_search_result(), "hasNextPage": True}

        compact = server.compact_search_results(result, sample_search())

        self.assertIn("More matching showtimes exist", compact["message"])

    def test_a_search_with_no_showtimes_points_back_to_discovery(self):
        result = {**sample_search_result(), "matches": [], "checkedShowtimes": 0}

        compact = server.compact_search_results(result, sample_search())

        self.assertEqual(compact["options"], [])
        self.assertIn("No The Odyssey showtimes match", compact["message"])
        self.assertIn("get_location_and_movie_info", compact["message"])


class CompactMovieInfoTests(unittest.TestCase):
    def test_a_short_list_names_each_movies_theatres(self):
        compact = server.compact_movie_info(
            sample_movie_info(2), Area(radius=25, zip_code="10023"), "", ""
        )

        self.assertEqual(compact["theatres"], [{"name": "Test Cinema", "distanceMiles": 2.5}])
        self.assertEqual(
            compact["movies"][0],
            {"title": "Movie 0", "formats": ["Standard"], "theatres": ["Test Cinema"]},
        )

    def test_a_long_list_counts_theatres_instead(self):
        compact = server.compact_movie_info(
            sample_movie_info(6), Area(radius=25, zip_code="10023"), "", ""
        )

        self.assertEqual(compact["movies"][0]["theatreCount"], 1)
        self.assertNotIn("theatres", compact["movies"][0])

    def test_no_match_says_what_was_not_found(self):
        compact = server.compact_movie_info(
            sample_movie_info(0), Area(radius=25, zip_code="10023"), "wicked", ""
        )

        self.assertIn('Nothing matching "wicked" is showing within 25 miles', compact["message"])
