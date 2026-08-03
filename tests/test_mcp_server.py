import os
import unittest
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as entrypoint
from mcp_server import security, server


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
            "seatMap": {
                "availableSeatCount": 42,
                "totalSeatCount": 100,
                "layout": {
                    "width": 100,
                    "height": 100,
                    "backgroundSvg": "<svg>large payload</svg>",
                    "seats": [
                        {"id": "H10", "matched": True},
                        {"id": "H11", "matched": True},
                        {"id": "A1", "matched": False},
                    ],
                },
            },
        }],
        "checkedShowtimes": 7,
        "checkedSeatMaps": 3,
    }


class McpToolTests(unittest.TestCase):
    def test_exact_one_based_cells_map_to_the_internal_zero_based_grid(self):
        self.assertEqual(
            server.internal_seat_grid(("1:1", "6:6", "15:15", "6:6")),
            "0:0,5:5,14:14",
        )
        self.assertEqual(server.internal_seat_grid(()), "")

    @patch.object(server.application, "find_seat_matches", return_value=sample_search_result())
    def test_find_movie_seats_calls_the_shared_search_and_returns_compact_options(self, find_seat_matches):
        result = server.find_movie_seats(
            movie="The Odyssey",
            start_date=date(2026, 8, 4),
            end_date=date(2026, 8, 6),
            zip_code="10023",
            movie_formats=("IMAX", "IMAX 70mm"),
            seat_cells=("6:1", "6:2", "7:1", "7:2"),
            adjacent_seats=2,
        )

        search = find_seat_matches.call_args.kwargs
        self.assertEqual(search["movie"], "The Odyssey")
        self.assertEqual(search["requested_format"], "IMAX,IMAX 70mm")
        selected_grid = set(search["seat_grid"].split(","))
        self.assertEqual(len(selected_grid), 4)
        self.assertIn("5:0", selected_grid)
        self.assertNotIn("5:5", selected_grid)
        self.assertEqual(search["start_date"], date(2026, 8, 4))
        self.assertEqual(search["end_date"], date(2026, 8, 6))
        self.assertEqual(
            result["query"]["dateRange"],
            {"start": "2026-08-04", "end": "2026-08-06"},
        )
        self.assertEqual(result["query"]["formats"], ["IMAX", "IMAX 70mm"])
        self.assertEqual(result["resultCount"], 1)
        self.assertEqual(result["options"][0]["matchingSeatExamples"], ["H10", "H11"])
        self.assertEqual(result["options"][0]["ticketUrl"], "https://tickets.fandango.com/order")
        self.assertNotIn("layout", result["options"][0])

    def test_find_movie_seats_rejects_a_backwards_time_window_without_searching(self):
        with patch.object(server.application, "find_seat_matches") as find_seat_matches:
            with self.assertRaisesRegex(ValueError, "start_time"):
                server.find_movie_seats(
                    movie="The Odyssey",
                    start_date=date(2026, 8, 4),
                    zip_code="10023",
                    start_time="20:00",
                    end_time="18:00",
                )
        find_seat_matches.assert_not_called()


class McpProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(entrypoint.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    def request_headers(self, token=None, user_id="00000000-0000-0000-0000-000000000001"):
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if user_id is not None:
            headers["X-Poke-User-Id"] = user_id
        return headers

    def test_public_poke_request_does_not_require_a_server_key(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        with patch.dict(os.environ, {"MCP_API_KEY": ""}):
            response = self.client.post("/mcp", headers=self.request_headers(), json=request)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["result"].get("isError", False))

    @patch.object(security.MCP_RATE_LIMITER, "hit", return_value=True)
    def test_connection_probe_can_discover_tools_without_a_poke_user_identifier(self, rate_limit_hit):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        response = self.client.post(
            "/mcp",
            headers=self.request_headers(user_id=None),
            json=request,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(rate_limit_hit.call_count, 2)

    def test_mcp_endpoint_rejects_a_malformed_poke_user_identifier(self):
        response = self.client.post(
            "/mcp",
            headers=self.request_headers(user_id="not a safe identifier"),
            json={},
        )
        self.assertEqual(response.status_code, 403)

    def test_mcp_endpoint_rejects_an_invalid_bearer_key(self):
        with patch.dict(os.environ, {"MCP_API_KEY": "test-secret"}):
            response = self.client.post("/mcp", headers=self.request_headers(token="wrong"), json={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")

    @patch.object(security.MCP_RATE_LIMITER, "hit", side_effect=[True, True, False])
    def test_mcp_endpoint_applies_global_ip_and_user_limits(self, rate_limit_hit):
        response = self.client.post("/mcp", headers=self.request_headers(), json={})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")
        self.assertEqual(rate_limit_hit.call_count, 3)

    def test_mcp_endpoint_rejects_an_untrusted_browser_origin(self):
        headers = self.request_headers()
        headers["Origin"] = "https://attacker.example"
        response = self.client.post("/mcp", headers=headers, json={})
        self.assertEqual(response.status_code, 403)

    def test_composition_root_preserves_the_website(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Find the perfect movie seats", response.text)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_poke_can_discover_the_discovery_and_search_tools(self):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }
        response = self.client.post("/mcp", headers=self.request_headers(), json=request)

        self.assertEqual(response.status_code, 200)
        tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
        self.assertEqual(set(tools), {"get_location_and_movie_info", "find_movie_seats"})

        discovery_schema = tools["get_location_and_movie_info"]["inputSchema"]
        self.assertEqual(discovery_schema["required"], ["zip_code", "start_date"])
        self.assertIn("exact live theatre names", tools["get_location_and_movie_info"]["description"])

        schema = tools["find_movie_seats"]["inputSchema"]
        self.assertEqual(schema["required"], ["movie", "start_date", "zip_code"])
        seat_cells = schema["properties"]["seat_cells"]
        self.assertEqual(seat_cells["default"], [])
        self.assertEqual(seat_cells["maxItems"], 225)
        self.assertIn("Row 1 is nearest the screen", seat_cells["description"])
        self.assertEqual(schema["properties"]["adjacent_seats"]["default"], 2)
        self.assertEqual(schema["properties"]["movie_formats"]["default"], [])
        self.assertEqual(schema["properties"]["movie_formats"]["maxItems"], 10)

    @patch.object(server.application, "location_movie_info")
    def test_poke_can_discover_exact_titles_before_searching(self, location_movie_info):
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
        )

    @patch.object(server.application, "find_seat_matches", return_value=sample_search_result())
    def test_poke_can_call_the_tool_and_receive_structured_ticket_options(self, _find_seat_matches):
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
                    "seat_cells": ["11:6", "11:7", "11:8", "12:6", "12:7", "12:8"],
                    "adjacent_seats": 2,
                },
            },
        }
        response = self.client.post("/mcp", headers=self.request_headers(), json=request)

        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertFalse(result["isError"])
        structured = result["structuredContent"]
        self.assertEqual(structured["options"][0]["matchingSeatExamples"], ["H10", "H11"])
        self.assertEqual(structured["options"][0]["ticketUrl"], "https://tickets.fandango.com/order")


if __name__ == "__main__":
    unittest.main()
