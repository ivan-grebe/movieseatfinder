import base64
import unittest
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import seat_map_visual
from backend import server as entrypoint
from backend.mcp_server import security, server


def sample_seat_layout():
    return {
        "width": 100,
        "height": 100,
        "backgroundSvg": "<svg>large payload</svg>",
        "seats": [
            {
                "id": "H10", "matched": True, "status": "A", "type": "standard",
                "x": 40, "y": 50, "width": 8, "height": 8,
            },
            {
                "id": "H11", "matched": True, "status": "A", "type": "standard",
                "x": 50, "y": 50, "width": 8, "height": 8,
            },
            {
                "id": "A1", "matched": False, "status": "S", "type": "standard",
                "x": 10, "y": 10, "width": 8, "height": 8,
            },
        ],
    }


def sample_search_result():
    return {
        "matches": [{
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
        }],
        "checkedShowtimes": 7,
        "checkedSeatMaps": 3,
    }


def sample_seat_map_request(serialized=False):
    return {
        "showtime_hash_code": "showtime-123",
        "option_number": 1,
        "movie": "The Odyssey",
        "theatre": "Test Cinema",
        "show_date": "2026-08-04" if serialized else date(2026, 8, 4),
        "show_time": "7:30 PM",
        "movie_format": "IMAX",
        "seat_region": {"row_min": 7, "row_max": 8, "column_min": 7, "column_max": 8},
        "adjacent_seats": 2,
        "exclude_accessible": True,
    }


class McpToolTests(unittest.TestCase):
    def test_seat_map_renderer_loads_its_bundled_font(self):
        self.assertTrue(seat_map_visual._FONT_PATH.is_file())
        self.assertIn("Inter Variable", seat_map_visual._render_options().list_fonts())

    def test_exact_one_based_cells_map_to_the_internal_zero_based_grid(self):
        self.assertEqual(
            server.internal_seat_grid(("1:1", "6:6", "15:15", "6:6")),
            "0:0,5:5,14:14",
        )
        self.assertEqual(server.internal_seat_grid(()), "")

    def test_rectangular_region_expands_to_the_expected_cells(self):
        region = server.SeatRegion(row_min=6, row_max=7, column_min=1, column_max=2)
        self.assertEqual(
            server.resolved_seat_cells(region, ()),
            ("6:1", "6:2", "7:1", "7:2"),
        )
        with self.assertRaisesRegex(ValueError, "not both"):
            server.resolved_seat_cells(region, ("8:8",))

    @patch.object(server.application, "find_seat_matches", return_value=sample_search_result())
    def test_find_movie_seats_calls_the_shared_search_and_returns_compact_options(self, find_seat_matches):
        result = server.find_movie_seats(
            movie="The Odyssey",
            start_date=date(2026, 8, 4),
            end_date=date(2026, 8, 6),
            zip_code="10023",
            movie_formats=("IMAX", "IMAX 70mm"),
            seat_region=server.SeatRegion(row_min=6, row_max=7, column_min=1, column_max=2),
            adjacent_seats=2,
            radius_miles=25,
        )

        search = find_seat_matches.call_args.kwargs
        self.assertEqual(search["seat_grid"], "5:0,5:1,6:0,6:1")
        self.assertEqual(search["requested_format"], "IMAX,IMAX 70mm")
        self.assertEqual(
            result["query"]["dateRange"],
            {"start": "2026-08-04", "end": "2026-08-06"},
        )
        self.assertEqual(result["query"]["formats"], ["IMAX", "IMAX 70mm"])
        self.assertEqual(
            result["query"]["seatRegion"],
            {"row_min": 6, "row_max": 7, "column_min": 1, "column_max": 2},
        )
        self.assertEqual(result["resultCount"], 1)
        seat_map_request = result["options"][0]["seatMapRequest"]
        self.assertEqual(seat_map_request, {
            "showtime_hash_code": "showtime-123",
            "option_number": 1,
            "movie": "The Odyssey",
            "theatre": "Test Cinema",
            "show_date": "2026-08-04",
            "show_time": "7:30 PM",
            "movie_format": "IMAX",
            "seat_region": {"row_min": 6, "row_max": 7, "column_min": 1, "column_max": 2},
            "seat_cells": [],
            "adjacent_seats": 2,
            "exclude_accessible": True,
        })

    def test_find_movie_seats_rejects_a_backwards_time_window_without_searching(self):
        with patch.object(server.application, "find_seat_matches") as find_seat_matches:
            with self.assertRaisesRegex(ValueError, "start_time"):
                server.find_movie_seats(
                    movie="The Odyssey",
                    start_date=date(2026, 8, 4),
                    zip_code="10023",
                    adjacent_seats=2,
                    movie_formats=("IMAX",),
                    seat_region=server.SeatRegion(row_min=8, row_max=8, column_min=7, column_max=8),
                    radius_miles=10,
                    start_time="20:00",
                    end_time="18:00",
                )
        find_seat_matches.assert_not_called()

    @patch.object(server.application, "showtime_seat_match", return_value=sample_search_result()["matches"][0]["seatMap"])
    def test_show_movie_seat_map_refreshes_the_selected_showtime(self, showtime_seat_match):
        server.show_movie_seat_map(**sample_seat_map_request())

        showtime_seat_match.assert_called_once()
        self.assertEqual(showtime_seat_match.call_args.args[0]["showtimeHashCode"], "showtime-123")


class McpProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(entrypoint.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    def request_headers(self, token=None):
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def test_public_mcp_request_does_not_require_authentication(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        response = self.client.post("/mcp", headers=self.request_headers(), json=request)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["result"].get("isError", False))

    def test_public_mcp_request_ignores_an_authorization_header(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        response = self.client.post(
            "/mcp",
            headers=self.request_headers(token="irrelevant"),
            json=request,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["result"].get("isError", False))

    @patch.object(security.MCP_RATE_LIMITER, "hit", return_value=False)
    def test_mcp_endpoint_applies_the_global_limit(self, rate_limit_hit):
        response = self.client.post("/mcp", headers=self.request_headers(), json={})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")
        self.assertEqual(rate_limit_hit.call_count, 1)

    def test_mcp_endpoint_rejects_an_untrusted_browser_origin(self):
        headers = self.request_headers()
        headers["Origin"] = "https://attacker.example"
        response = self.client.post("/mcp", headers=headers, json={})
        self.assertEqual(response.status_code, 403)

    @patch.object(security.MCP_RATE_LIMITER, "hit")
    def test_browser_navigation_gets_a_friendly_page_instead_of_an_sse_stream(self, rate_limit_hit):
        response = self.client.get(
            "/mcp",
            headers={"Accept": "text/html", "Sec-Fetch-Dest": "document"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertNotIn(": ping", response.text)
        rate_limit_hit.assert_not_called()

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

        discovery_properties = tools["get_location_and_movie_info"]["inputSchema"]["properties"]
        self.assertTrue({"movie_query", "format_query"} <= set(discovery_properties))

        search_properties = tools["find_movie_seats"]["inputSchema"]["properties"]
        self.assertTrue({"seat_region", "seat_cells"} <= set(search_properties))

        output_schema = tools["show_movie_seat_map"]["outputSchema"]
        self.assertTrue({"matchingGroups", "bestGroup"} <= set(output_schema["properties"]))

    @patch.object(server.application, "location_movie_info")
    def test_client_can_discover_exact_titles_before_searching(self, location_movie_info):
        location_movie_info.return_value = {
            "place": "New York, NY",
            "zipCode": "10023",
            "startDate": "2026-08-04",
            "endDate": "2026-08-06",
            "theatres": [{
                "name": "AMC Lincoln Square 13",
                "address": "1998 Broadway, New York, NY 10023",
                "distanceMiles": 0.5,
            }],
            "movies": [{
                "title": "The Odyssey (2026)",
                "dates": ["2026-08-04", "2026-08-05", "2026-08-06"],
                "formats": ["IMAX 70mm"],
                "theatres": ["AMC Lincoln Square 13"],
            }],
        }
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "get_location_and_movie_info",
                "arguments": {
                    "zip_code": "10023",
                    "start_date": "2026-08-04",
                    "end_date": "2026-08-06",
                    "movie_query": "Odyssey",
                    "format_query": "IMAX 70mm",
                },
            },
        }
        response = self.client.post("/mcp", headers=self.request_headers(), json=request)

        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertFalse(result["isError"])
        structured = result["structuredContent"]
        self.assertEqual(structured["movies"][0]["title"], "The Odyssey (2026)")
        self.assertEqual(structured["movies"][0]["formats"], ["IMAX 70mm"])
        location_movie_info.assert_called_once_with(
            radius=25,
            zip_code="10023",
            start_date=date(2026, 8, 4),
            end_date=date(2026, 8, 6),
            theatre="",
            movie_query="Odyssey",
            format_query="IMAX 70mm",
        )

    @patch.object(server.application, "find_seat_matches", return_value=sample_search_result())
    def test_client_can_call_the_tool_and_receive_structured_ticket_options(self, find_seat_matches):
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
                        "row_min": 11, "row_max": 12, "column_min": 6, "column_max": 8,
                    },
                    "adjacent_seats": 2,
                },
            },
        }
        response = self.client.post("/mcp", headers=self.request_headers(), json=request)

        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertFalse(result["isError"])
        structured = result["structuredContent"]
        self.assertEqual(structured["options"][0]["matchingGroups"], [["H10", "H11"]])
        self.assertEqual(structured["options"][0]["bestGroup"], ["H10", "H11"])
        self.assertEqual(structured["options"][0]["ticketUrl"], "https://tickets.fandango.com/order")
        self.assertEqual(structured["query"]["radiusMiles"], 25)
        self.assertEqual(structured["query"]["timeRange"], {"start": "00:00", "end": "23:59"})
        self.assertTrue(structured["query"]["excludeAccessible"])
        self.assertEqual(find_seat_matches.call_args.kwargs["radius"], 25)
        self.assertEqual(find_seat_matches.call_args.kwargs["start_time"], "00:00")

    @patch.object(server.application, "showtime_seat_match", return_value=sample_search_result()["matches"][0]["seatMap"])
    def test_client_receives_an_image_and_structured_seat_map_fallback(self, _showtime_seat_match):
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "show_movie_seat_map",
                "arguments": sample_seat_map_request(serialized=True),
            },
        }
        response = self.client.post("/mcp", headers=self.request_headers(), json=request)

        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertFalse(result["isError"])
        self.assertEqual([item["type"] for item in result["content"]], ["image", "text"])
        self.assertEqual(result["content"][0]["annotations"], {"audience": ["user"], "priority": 1.0})
        self.assertEqual(result["content"][0]["mimeType"], "image/png")
        self.assertTrue(base64.b64decode(result["content"][0]["data"]).startswith(b"\x89PNG"))
        self.assertIn("Display the attached seat-map image", result["content"][1]["text"])
        self.assertIn("Live seat map for option 1: The Odyssey", result["content"][1]["text"])
        self.assertEqual(result["structuredContent"]["matchingGroups"], [["H10", "H11"]])
        self.assertEqual(result["structuredContent"]["bestGroup"], ["H10", "H11"])


if __name__ == "__main__":
    unittest.main()
