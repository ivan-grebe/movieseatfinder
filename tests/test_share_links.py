import base64
import unittest
import zlib
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from backend.application import app
from backend.share_links import decode_share_link


def token_for(query):
    return base64.urlsafe_b64encode(zlib.compress(query.encode())).decode().rstrip("=")


class ShareLinkTests(unittest.TestCase):
    def test_public_filters_round_trip_and_redirect_locally(self):
        bitmap = bytearray(29)
        bitmap[2] = 6
        area = base64.urlsafe_b64encode(bitmap).decode().rstrip("=")
        query = f"zip=10001&movie=Test+Movie&showtime=abc&seatGrid={area}"
        token = token_for(query)
        with TestClient(app) as client:
            response = client.get(f"/s/{token}", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["location"].startswith("/?"))
        params = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertEqual(params["zip"], ["10001"])
        self.assertEqual(params["movie"], ["Test Movie"])
        self.assertEqual(params["seatGrid"], ["1:2,1:3"])
        self.assertEqual(params["shared"], ["1"])

    def test_coordinates_and_unrecognized_fields_are_rejected(self):
        for extra in ("lat=40.75", "lon=-73.99", "redirect=https://evil.test", "zip=90210"):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                decode_share_link(token_for("zip=10001&movie=Test&" + extra))

    def test_malformed_and_oversized_links_are_rejected(self):
        for token in ("bad!", "abc", "a" * 4097, token_for("x" * 100_000)):
            with self.subTest(token=token[:20]), self.assertRaises(ValueError):
                decode_share_link(token)

    def test_invalid_public_location_is_rejected(self):
        for query in ("movie=Test", "zip=bad&movie=Test", "zip=10001"):
            with self.assertRaises(ValueError):
                decode_share_link(token_for(query))
