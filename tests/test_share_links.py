import base64
import unittest
import zlib
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from backend import application
from backend.share_links import decode_share_link


def token_for(query):
    return base64.urlsafe_b64encode(zlib.compress(query.encode())).decode().rstrip("=")


class ShareLinkTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(application.app)
        application.RATE_LIMITER.storage.reset()
        self.details = {
            "movie": "Test Movie",
            "theatre": "Cinema",
            "address": "Main St",
            "date": "2026-10-25",
            "time": "7 PM",
            "format": "IMAX",
            "showtime": "showtime-1",
        }
        self.seats = [
            {"id": "A1", "row": 0, "column": 1, "x": 0, "status": "A"},
            {"id": "A2", "row": 0, "column": 2, "x": 10, "status": "A"},
        ]

    def test_shared_view_refreshes_one_seat_map_without_a_location_search(self):
        token = token_for(urlencode(self.details))
        with (
            patch.object(application, "seat_map", return_value={"seats": self.seats}) as seat_map,
            patch.object(application, "api_search_location") as location,
            patch.object(application, "collect_candidate_showtimes") as search,
        ):
            page = self.client.get(f"/s/{token}", follow_redirects=False)
            response = self.client.get(f"/api/shared-showtime/{token}")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("location", page.headers)
        self.assertIn('src="/app.bundle.js"', page.text)
        self.assertEqual(response.status_code, 200)
        match = response.json()["matches"][0]
        self.assertEqual(match["seatMap"]["bestGroup"], ["A1", "A2"])
        self.assertEqual(match["theatre"], {"name": "Cinema", "address": "Main St"})
        self.assertNotIn("zipCode", response.json())
        seat_map.assert_called_once_with("showtime-1")
        location.assert_not_called()
        search.assert_not_called()

    def test_sender_location_and_search_fields_are_rejected(self):
        for field in ("zip", "lat", "lon", "radius", "distanceMiles"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                decode_share_link(token_for(urlencode({**self.details, field: "12345"})))

    def test_seat_preferences_decode_without_a_search_origin(self):
        bitmap = bytearray(29)
        bitmap[2] = 6
        area = base64.urlsafe_b64encode(bitmap).decode().rstrip("=")
        shared = decode_share_link(
            token_for(
                urlencode(
                    {
                        **self.details,
                        "seatGrid": area,
                        "adjacentSeats": 3,
                        "excludeAccessible": "0",
                    }
                )
            )
        )
        self.assertEqual(shared.seat_grid, "1:2,1:3")
        self.assertEqual(shared.adjacent_seats, 3)
        self.assertFalse(shared.exclude_accessible)

    def test_malformed_and_oversized_links_are_rejected(self):
        for token in ("bad!", "abc", "a" * 4097, token_for("x" * 100_000), token_for("movie=Test")):
            with self.subTest(token=token[:20]), self.assertRaises(ValueError):
                decode_share_link(token)

    def test_no_matching_seats_still_returns_the_live_map(self):
        for seat in self.seats:
            seat["status"] = "U"
        with patch.object(application, "seat_map", return_value={"seats": self.seats}):
            response = self.client.get(f"/api/shared-showtime/{token_for(urlencode(self.details))}")
        self.assertEqual(response.status_code, 200)
        preview = response.json()["matches"][0]["seatMap"]
        self.assertEqual(preview["bestGroup"], [])
        self.assertEqual(len(preview["layout"]["seats"]), 2)

    def test_unavailable_seat_map_is_an_error_instead_of_a_match(self):
        with patch.object(application, "seat_map", side_effect=application.requests.Timeout()):
            response = self.client.get(f"/api/shared-showtime/{token_for(urlencode(self.details))}")
        self.assertEqual(response.status_code, 502)

    def test_shared_metadata_cannot_link_to_untrusted_sites(self):
        details = {
            **self.details,
            "ticket": "https://evil.test/pay",
            "poster": "https://evil.test/track",
        }
        with patch.object(application, "seat_map", return_value={"seats": self.seats}):
            response = self.client.get(f"/api/shared-showtime/{token_for(urlencode(details))}")
        match = response.json()["matches"][0]
        self.assertEqual(match["ticketUrl"], "")
        self.assertEqual(match["poster"], "")
