import base64
import struct
import unittest
from xml.etree import ElementTree
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import server as entrypoint
from backend.mcp_server import seat_map_image, security, server


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
                "layout": {
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
                },
            },
        }],
        "checkedShowtimes": 7,
        "checkedSeatMaps": 3,
    }


def sample_selection_token(now=None):
    return server.create_selection_token({
        "showtimeHashCode": "showtime-123",
        "movie": "The Odyssey",
        "theatre": {
            "name": "Test Cinema",
            "address": "1 Main St",
            "distanceMiles": 2.5,
        },
        "date": "2026-08-04",
        "time": "7:30 PM",
        "format": "IMAX",
        "amenities": "IMAX with Laser",
        "ticketUrl": "https://tickets.fandango.com/order",
        "seatCells": ["7:7", "7:8", "8:7", "8:8"],
        "adjacentSeats": 2,
        "excludeAccessible": True,
        "optionNumber": 1,
        "search": {
            "movie": "The Odyssey",
            "start_date": "2026-08-04",
            "end_date": "2026-08-06",
            "zip_code": "10023",
            "movie_formats": ["IMAX"],
            "seat_cells": ["7:7", "7:8", "8:7", "8:8"],
            "adjacent_seats": 2,
            "radius_miles": 25,
            "start_time": "14:00",
            "end_time": "23:59",
            "theatre": "",
            "exclude_accessible": True,
            "sort": "nearest",
        },
    }, now=now)


class McpToolTests(unittest.TestCase):
    def test_renderer_covers_every_website_seat_state_and_centers_each_legend(self):
        matched_accessible = seat_map_image._seat_svg({
            "status": "A", "type": "wheelchair", "matched": True,
            "x": 10, "y": 10, "width": 8, "height": 8,
        }, 0.5, False)
        excluded_accessible = seat_map_image._seat_svg({
            "status": "A", "type": "companion", "matched": False,
            "x": 10, "y": 10, "width": 8, "height": 8,
        }, 0.5, True)

        self.assertIn('fill="url(#matched-accessible)"', matched_accessible)
        self.assertIn('stroke="url(#matched-accessible-border)"', matched_accessible)
        self.assertIn('fill="#3a4250"', excluded_accessible)
        for excluded in (False, True):
            legend = ElementTree.fromstring(seat_map_image._legend_svg(excluded))
            start = float(legend.attrib["data-start"])
            width = float(legend.attrib["data-width"])
            self.assertAlmostEqual(start + width / 2, 900, places=4)
        full_legend = seat_map_image._legend_svg(False)
        self.assertIn("Accessible match", full_legend)
        self.assertIn("url(#matched-accessible-border)", full_legend)
        self.assertIn("Unavailable / excluded", seat_map_image._legend_svg(True))

    def test_dark_seat_map_renderer_is_high_resolution_and_uses_auditorium_svg(self):
        layout = sample_search_result()["matches"][0]["seatMap"]["layout"]
        layout["backgroundSvg"] = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<path d="M10 12 Q50 2 90 12" fill="none" stroke="#94a3b8"/></svg>'
        )
        with_background = server.render_seat_map_png(layout)
        without_background = server.render_seat_map_png({**layout, "backgroundSvg": ""})

        self.assertEqual(with_background[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", with_background[16:24]), (1800, 1200))
        self.assertNotEqual(with_background, without_background)

    def test_exact_one_based_cells_map_to_the_internal_zero_based_grid(self):
        self.assertEqual(
            server.internal_seat_grid(("1:1", "6:6", "15:15", "6:6")),
            "0:0,5:5,14:14",
        )
        self.assertEqual(server.internal_seat_grid(()), "")

    def test_selection_tokens_reject_changes_and_expose_expiry_only_when_requested(self):
        token = sample_selection_token(now=100)
        payload, signature = token.split(".", 1)
        altered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        with self.assertRaisesRegex(ValueError, "invalid"):
            server.read_selection_token(f"{payload}.{altered_signature}", now=101)
        with self.assertRaisesRegex(ValueError, "expired"):
            server.read_selection_token(token, now=401)
        expired = server.read_selection_token(token, now=401, allow_expired=True)
        self.assertTrue(expired["tokenExpired"])

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
            radius_miles=25,
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
        self.assertEqual(search["start_time"], "14:00")
        self.assertEqual(search["sort"], "nearest")
        self.assertEqual(search["page_size"], 5)
        self.assertTrue(search["include_showtime_hash"])
        self.assertEqual(
            result["query"]["dateRange"],
            {"start": "2026-08-04", "end": "2026-08-06"},
        )
        self.assertEqual(result["query"]["formats"], ["IMAX", "IMAX 70mm"])
        self.assertEqual(result["resultCount"], 1)
        self.assertEqual(result["options"][0]["matchingSeatExamples"], ["H10", "H11"])
        self.assertEqual(result["options"][0]["ticketUrl"], "https://tickets.fandango.com/order")
        seat_map_request = result["options"][0]["seatMapRequest"]
        self.assertEqual(list(seat_map_request), ["selection_token"])
        selection = server.read_selection_token(seat_map_request["selection_token"])
        self.assertEqual(selection["optionNumber"], 1)
        self.assertEqual(selection["showtimeHashCode"], "showtime-123")
        self.assertEqual(selection["theatre"]["name"], "Test Cinema")
        self.assertNotIn("layout", result["options"][0])

    def test_find_movie_seats_rejects_a_backwards_time_window_without_searching(self):
        with patch.object(server.application, "find_seat_matches") as find_seat_matches:
            with self.assertRaisesRegex(ValueError, "start_time"):
                server.find_movie_seats(
                    movie="The Odyssey",
                    start_date=date(2026, 8, 4),
                    zip_code="10023",
                    adjacent_seats=2,
                    movie_formats=("IMAX",),
                    seat_cells=("8:7", "8:8"),
                    radius_miles=10,
                    start_time="20:00",
                    end_time="18:00",
                )
        find_seat_matches.assert_not_called()

    @patch.object(server, "_run_seat_search")
    @patch.object(server.application, "showtime_seat_match", return_value=sample_search_result()["matches"][0]["seatMap"])
    def test_show_movie_seat_map_returns_a_live_png_without_repeating_search(
        self, showtime_seat_match, run_seat_search,
    ):
        result = server.show_movie_seat_map(selection_token=sample_selection_token())

        self.assertIn("Live seat map for option 1: The Odyssey", result[0])
        self.assertIn("Red = seats matching the request", result[0])
        self.assertTrue(result[1].data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(result[1]._mime_type, "image/png")
        self.assertEqual(showtime_seat_match.call_count, 1)
        self.assertEqual(showtime_seat_match.call_args.args[0]["showtimeHashCode"], "showtime-123")
        run_seat_search.assert_not_called()

    @patch.object(server.application, "showtime_seat_match")
    @patch.object(server, "_run_seat_search", return_value=sample_search_result())
    def test_expired_seat_map_token_repeats_the_exact_search(self, run_seat_search, showtime_seat_match):
        with patch("backend.mcp_server.selection_token.time.time", return_value=401):
            result = server.show_movie_seat_map(selection_token=sample_selection_token(now=100))

        self.assertTrue(result[1].data.startswith(b"\x89PNG"))
        showtime_seat_match.assert_not_called()
        search_args = run_seat_search.call_args.args
        self.assertEqual(search_args[0], "The Odyssey")
        self.assertEqual(search_args[1:3], (date(2026, 8, 4), date(2026, 8, 6)))
        self.assertEqual(search_args[7:10], (25, "14:00", "23:59"))
        self.assertEqual(search_args[-2:], ("nearest", 5))


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

    def test_public_poke_request_does_not_require_authentication(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
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
        self.assertEqual(rate_limit_hit.call_count, 1)

    def test_mcp_endpoint_rejects_a_malformed_poke_user_identifier(self):
        response = self.client.post(
            "/mcp",
            headers=self.request_headers(user_id="not a safe identifier"),
            json={},
        )
        self.assertEqual(response.status_code, 403)

    def test_public_mcp_request_ignores_an_authorization_header(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        response = self.client.post(
            "/mcp",
            headers=self.request_headers(token="irrelevant"),
            json=request,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["result"].get("isError", False))

    @patch.object(security.MCP_RATE_LIMITER, "hit", side_effect=[True, False])
    def test_mcp_endpoint_applies_global_and_user_limits(self, rate_limit_hit):
        response = self.client.post("/mcp", headers=self.request_headers(), json={})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")
        self.assertEqual(rate_limit_hit.call_count, 2)

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
        self.assertIn("Movie Seat Finder MCP", response.text)
        self.assertIn("Open Movie Seat Finder", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertNotIn(": ping", response.text)
        rate_limit_hit.assert_not_called()

    def test_mcp_sse_get_is_not_classified_as_browser_navigation(self):
        self.assertFalse(
            security._is_browser_navigation(
                {"method": "GET"},
                {"accept": "text/event-stream"},
            )
        )

    def test_composition_root_preserves_the_website(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Find the perfect movie seats", response.text)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_poke_receives_the_complete_clarification_first_instructions(self):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "recipe-test", "version": "1.0"},
            },
        }
        response = self.client.post("/mcp", headers=self.request_headers(), json=request)

        self.assertEqual(response.status_code, 200)
        instructions = response.json()["result"]["instructions"]
        normalized_instructions = " ".join(instructions.split())
        self.assertIn("CONVERSATION STATE", normalized_instructions)
        self.assertIn("Find good seats for Dune tomorrow", normalized_instructions)
        self.assertIn("Ask one focused question per turn", normalized_instructions)
        self.assertIn("rows 8-12, columns 5-11", normalized_instructions)
        self.assertIn("2:00 PM through midnight", normalized_instructions)
        self.assertIn("DISCOVERY, THEN FORMAT", normalized_instructions)
        self.assertIn("show_movie_seat_map", normalized_instructions)
        self.assertIn("Ask before every relaxation", normalized_instructions)

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
        self.assertEqual(
            set(tools),
            {"get_location_and_movie_info", "find_movie_seats", "show_movie_seat_map"},
        )

        discovery_schema = tools["get_location_and_movie_info"]["inputSchema"]
        self.assertEqual(discovery_schema["required"], ["zip_code", "start_date", "radius_miles"])
        self.assertIn("exact live theatre names", tools["get_location_and_movie_info"]["description"])

        schema = tools["find_movie_seats"]["inputSchema"]
        self.assertEqual(
            schema["required"],
            [
                "movie", "start_date", "zip_code", "adjacent_seats",
                "movie_formats", "seat_cells", "radius_miles",
            ],
        )
        seat_cells = schema["properties"]["seat_cells"]
        self.assertNotIn("default", seat_cells)
        self.assertEqual(seat_cells["maxItems"], 225)
        self.assertIn("Row 1 is nearest the screen", seat_cells["description"])
        self.assertNotIn("default", schema["properties"]["adjacent_seats"])
        self.assertNotIn("default", schema["properties"]["movie_formats"])
        self.assertEqual(schema["properties"]["movie_formats"]["maxItems"], 10)
        self.assertEqual(schema["properties"]["start_time"]["default"], "14:00")
        self.assertNotIn("sort", schema["properties"])
        self.assertNotIn("max_results", schema["properties"])

        map_schema = tools["show_movie_seat_map"]["inputSchema"]
        self.assertEqual(map_schema["required"], ["selection_token"])
        self.assertIn("seatMapRequest", tools["show_movie_seat_map"]["description"])

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
                    "radius_miles": 25,
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
                    "radius_miles": 25,
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

    @patch.object(server.application, "showtime_seat_match", return_value=sample_search_result()["matches"][0]["seatMap"])
    def test_poke_can_receive_a_seat_map_as_image_content(self, _showtime_seat_match):
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "show_movie_seat_map",
                "arguments": {
                    "selection_token": sample_selection_token(),
                },
            },
        }
        response = self.client.post("/mcp", headers=self.request_headers(), json=request)

        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertFalse(result["isError"])
        self.assertEqual([item["type"] for item in result["content"]], ["text", "image"])
        self.assertIn("Live seat map for option 1: The Odyssey", result["content"][0]["text"])
        self.assertEqual(result["content"][1]["mimeType"], "image/png")
        self.assertTrue(base64.b64decode(result["content"][1]["data"]).startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
