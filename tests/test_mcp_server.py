import base64
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import server as entrypoint
from backend.mcp_server import server


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
    }


def sample_seat_map_request():
    return {
        "showtime_hash_code": "showtime-123",
        "option_number": 1,
        "movie": "The Odyssey",
        "theatre": "Test Cinema",
        "show_date": "2026-08-04",
        "show_time": "7:30 PM",
        "movie_format": "IMAX",
        "seat_region": {"row_min": 7, "row_max": 8, "column_min": 7, "column_max": 8},
        "adjacent_seats": 2,
        "exclude_accessible": True,
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

    def test_mcp_endpoint_rejects_an_untrusted_browser_origin(self):
        headers = self.request_headers()
        headers["Origin"] = "https://attacker.example"
        response = self.client.post("/mcp", headers=headers, json={})
        self.assertEqual(response.status_code, 403)

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
        structured = result["structuredContent"]
        self.assertEqual(structured["options"][0]["matchingGroups"], [["H10", "H11"]])
        self.assertEqual(structured["options"][0]["bestGroup"], ["H10", "H11"])
        self.assertEqual(
            structured["options"][0]["ticketUrl"], "https://tickets.fandango.com/order"
        )
        search = find_seat_matches.call_args.kwargs
        self.assertEqual(search["seat_grid"], "10:5,10:6,10:7,11:5,11:6,11:7")
        self.assertEqual(search["requested_format"], "IMAX,IMAX 70mm")

    @patch.object(
        server.application,
        "showtime_seat_match",
        return_value=sample_search_result()["matches"][0]["seatMap"],
    )
    def test_client_receives_an_image_and_structured_seat_map_fallback(self, _showtime_seat_match):
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "show_movie_seat_map",
                "arguments": sample_seat_map_request(),
            },
        }
        response = self.client.post("/mcp", headers=self.request_headers(), json=request)

        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertFalse(result["isError"])
        self.assertEqual([item["type"] for item in result["content"]], ["image", "text"])
        self.assertEqual(result["content"][0]["mimeType"], "image/png")
        self.assertTrue(base64.b64decode(result["content"][0]["data"]).startswith(b"\x89PNG"))
        self.assertEqual(result["structuredContent"]["matchingGroups"], [["H10", "H11"]])
        self.assertEqual(result["structuredContent"]["bestGroup"], ["H10", "H11"])
