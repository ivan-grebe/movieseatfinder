import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src import application, fandango, seat_matching


class TicketUrlTests(unittest.TestCase):
    def test_ticket_urls_are_allowlisted(self):
        self.assertEqual(
            fandango.safe_fandango_url("https://tickets.fandango.com/order"),
            "https://tickets.fandango.com/order",
        )
        self.assertEqual(fandango.safe_fandango_url("https://example.com"), "")
        self.assertEqual(fandango.safe_fandango_url("javascript:alert(1)"), "")


class MovieAndFormatTests(unittest.TestCase):
    def test_movie_matching_requires_the_selected_title(self):
        self.assertTrue(fandango.movie_matches("Spider-Man: Homecoming", "spider man homecoming"))
        self.assertFalse(fandango.movie_matches("Spider-Man: Homecoming", "spider man"))
        self.assertFalse(fandango.movie_matches("", "spider man"))

    def test_format_matching_distinguishes_imax_variants(self):
        self.assertTrue(fandango.format_matches("IMAX", "imax"))
        self.assertFalse(fandango.format_matches("IMAX", "IMAX with Laser"))
        self.assertFalse(fandango.format_matches("IMAX with Laser", "IMAX"))
        self.assertFalse(fandango.format_matches("IMAX 70mm", "IMAX"))
        self.assertTrue(fandango.format_matches("IMAX 70mm", "imax70"))
        self.assertTrue(fandango.format_matches("IMAX with Laser", "Dolby Cinema,IMAX with Laser"))

    def test_format_matching_collapses_only_equivalent_labels(self):
        self.assertTrue(fandango.format_matches("Dolby Cinema", "dolby"))
        self.assertTrue(fandango.format_matches("70mm", "70MM Film"))
        self.assertFalse(fandango.format_matches("IMAX 70mm", "70mm"))
        self.assertFalse(fandango.format_matches("Dolby Atmos", "Dolby Cinema"))
        self.assertFalse(fandango.format_matches("Standard", "dolby,imax"))


class SeatSelectionTests(unittest.TestCase):
    def setUp(self):
        self.seats = [
            {"id": "A1", "row": 0, "column": 1, "x": 10, "y": 0, "status": "A", "type": "standard"},
            {"id": "A2", "row": 0, "column": 2, "x": 20, "y": 0, "status": "A", "type": "standard"},
            {"id": "A3", "row": 0, "column": 3, "x": 30, "y": 0, "status": "U", "type": "standard"},
            {
                "id": "B1",
                "row": 1,
                "column": 1,
                "x": 10,
                "y": 10,
                "status": "A",
                "type": "wheelchair",
            },
            {
                "id": "B2",
                "row": 1,
                "column": 2,
                "x": 20,
                "y": 10,
                "status": "A",
                "type": "standard",
            },
            {
                "id": "B3",
                "row": 1,
                "column": 3,
                "x": 30,
                "y": 10,
                "status": "A",
                "type": "companion",
            },
        ]

    def test_adjacent_blocks_require_available_contiguous_seats(self):
        blocks = seat_matching.adjacent_blocks(self.seats, 2, [], False)
        self.assertIn(["A1", "A2"], blocks)
        self.assertNotIn(["A2", "A3"], blocks)

    def test_accessible_seats_can_be_excluded(self):
        blocks = seat_matching.adjacent_blocks(self.seats, 1, [], True)
        seat_ids = {seat_id for block in blocks for seat_id in block}
        self.assertNotIn("B1", seat_ids)
        self.assertNotIn("B3", seat_ids)
        self.assertIn("B2", seat_ids)

    def test_only_seats_in_the_selected_region_can_match(self):
        blocks = seat_matching.adjacent_blocks(self.seats, 1, [(14, 7)], False)
        self.assertEqual(blocks, [["B2"]])

    def test_groups_have_the_requested_party_size(self):
        seats = [
            {"id": f"A{column}", "row": 0, "column": column, "x": column * 10, "status": "A"}
            for column in range(1, 5)
        ]
        groups = seat_matching.ranked_adjacent_groups(
            seats,
            [["A1", "A2", "A3", "A4"]],
            2,
            [],
        )
        self.assertCountEqual(groups, [["A1", "A2"], ["A2", "A3"], ["A3", "A4"]])


def search_with_candidates(candidates, matching):
    """Patch the search setup so `matching(candidate)` decides which candidates match."""

    def check(candidate):
        return {"candidate": candidate} if matching(candidate) else None

    check.failed = 0
    check.exclude_accessible = True
    return patch.object(application, "prepare_search", return_value=("10001", candidates, check))


class CandidateSearchTests(unittest.TestCase):
    def test_exactly_full_page_keeps_checking_for_a_next_match(self):
        with search_with_candidates(list(range(100)), lambda value: value < 5 or value == 99):
            result = application.find_seat_matches(radius=5, movie="Test", page_size=5)

        self.assertEqual(result["matches"], [{"candidate": value} for value in range(5)])
        self.assertTrue(result["hasNextPage"])
        self.assertEqual(result["checkedSeatMaps"], 100)

    def stream(self, candidate_count):
        client = TestClient(application.app)
        with search_with_candidates(list(range(candidate_count)), lambda value: True):
            response = client.get("/api/search/stream", params={"radius": 5, "movie": "Test"})
        return [json.loads(line) for line in response.text.splitlines()]

    def test_stream_sends_matches_in_order_then_reports_full_coverage(self):
        events = self.stream(30)
        self.assertEqual(events[0]["checkedShowtimes"], 30)
        matches = [event["match"]["candidate"] for event in events if event["type"] == "match"]
        self.assertEqual(matches, list(range(30)))
        self.assertEqual(
            events[-1],
            {"type": "done", "complete": True, "checkedSeatMaps": 30, "failedSeatMaps": 0},
        )

    def test_stream_stops_at_the_seat_map_cap(self):
        events = self.stream(application.STREAM_SEAT_MAP_LIMIT + 50)
        matches = [event for event in events if event["type"] == "match"]
        self.assertEqual(len(matches), application.STREAM_SEAT_MAP_LIMIT)
        self.assertEqual(events[-1]["checkedSeatMaps"], application.STREAM_SEAT_MAP_LIMIT)
        self.assertFalse(events[-1]["complete"])


def showtime_group(format_name, code, time):
    return {
        "amenities": [{"name": format_name}],
        "showtimes": [
            {
                "type": "available",
                "ticketingDate": f"2026-07-20+{time}",
                "showtimeHashCode": code,
                "filmFormat": [{"filterName": "IMAX"}],
            }
        ],
    }


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(application.app)
        self.params = {
            "zip": "10001",
            "radius": 25,
            "movie": "Test Movie",
            "startDate": "2026-07-20",
            "endDate": "2026-07-20",
            "adjacentSeats": 2,
        }
        theatre = {
            "name": "Cinema",
            "address": "Main St",
            "distanceMiles": 1,
            "rawMovies": [
                {
                    "title": "Test Movie",
                    "variants": [
                        {
                            "filmFormatHeader": "Premium Format",
                            "amenityGroups": [
                                showtime_group("IMAX", "plain", "19:00"),
                                showtime_group("IMAX with Laser", "laser", "20:00"),
                            ],
                        }
                    ],
                }
            ],
        }
        seats = [
            {"id": "A1", "row": 0, "column": 1, "x": 0, "status": "A", "type": "standard"},
            {"id": "A2", "row": 0, "column": 2, "x": 10, "status": "A", "type": "standard"},
            {"id": "A3", "row": 0, "column": 3, "x": 20, "status": "A", "type": "wheelchair"},
        ]
        patches = [
            patch.object(
                application,
                "resolve_search_location",
                return_value=("10001", (40, -75), "Testville"),
            ),
            patch.object(
                application, "fandango_theatres_by_date", return_value={"2026-07-20": [theatre]}
            ),
            patch.object(application, "seat_map", return_value={"seats": seats}),
        ]
        for mock in patches:
            mock.start()
            self.addCleanup(mock.stop)

    def test_search_returns_the_requested_adjacent_seats(self):
        response = self.client.get("/api/search", params={**self.params, "format": "IMAX"})
        self.assertEqual(response.status_code, 200)
        matches = response.json()["matches"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["seatMap"]["bestGroup"], ["A1", "A2"])
        self.assertEqual(matches[0]["showtimeHashCode"], "plain")

    def test_format_picker_and_search_distinguish_plain_and_laser_imax(self):
        formats = self.client.get("/api/formats", params=self.params)
        self.assertEqual(formats.json()["formats"], ["IMAX", "IMAX with Laser"])
        for format_name, code in [("IMAX", "plain"), ("IMAX with Laser", "laser")]:
            with self.subTest(format=format_name):
                response = self.client.get(
                    "/api/search", params={**self.params, "format": format_name}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [
                        (match["format"], match["showtimeHashCode"])
                        for match in response.json()["matches"]
                    ],
                    [(format_name, code)],
                )

    def test_theatre_include_and_exclude_lists_filter_every_endpoint(self):
        for name, value, kept in [
            ("excludeTheatre", ["Other Place", "cinema"], False),
            ("excludeTheatre", ["Cinema Two"], True),
            ("includeTheatre", ["CINEMA"], True),
            ("includeTheatre", ["Cinema Two"], False),
        ]:
            with self.subTest(name=name, value=value):
                params = {**self.params, "format": "IMAX", name: value}
                movies = self.client.get("/api/movies", params=params).json()["movies"]
                formats = self.client.get("/api/formats", params=params).json()["formats"]
                search = self.client.get("/api/search", params=params).json()
                self.assertEqual(bool(movies), kept)
                self.assertEqual(bool(formats), kept)
                self.assertEqual(len(search["matches"]), 1 if kept else 0)


class LocationTests(unittest.TestCase):
    @patch("src.location.reverse_geocode_zip", return_value="10001")
    @patch.object(fandango, "fandango_json")
    def test_radius_uses_coordinates_instead_of_upstream_distance(self, upstream, _zip):
        upstream.return_value = {
            "theaters": [
                {"name": "Nearby", "distance": 0, "geo": {"latitude": 40.03, "longitude": -75}},
                {"name": "Too far", "distance": 0, "geo": {"latitude": 40.10, "longitude": -75}},
            ]
        }
        fandango._fetch_fandango_theatres.cache_clear()
        response = TestClient(application.app).get(
            "/api/theatres",
            params={"lat": 40, "lon": -75, "radius": 5},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["theatres"], [{"name": "Nearby"}])


class RegionNoticeTests(unittest.TestCase):
    def notice_hidden(self, headers=None, cookies=None):
        client = TestClient(application.app, cookies=cookies)
        page = client.get("/", headers=headers or {})
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="regionNotice"', page.text)
        return 'aria-labelledby="regionNoticeTitle" data-show>' not in page.text

    def test_notice_shows_only_for_visitors_outside_the_us(self):
        self.assertFalse(self.notice_hidden({"x-vercel-ip-country": "IN"}))
        self.assertTrue(self.notice_hidden({"x-vercel-ip-country": "US"}))
        self.assertTrue(self.notice_hidden({"x-vercel-ip-country": "PR"}))
        self.assertTrue(self.notice_hidden())

    def test_dismissed_notice_stays_hidden(self):
        dismissed = {application.REGION_NOTICE_COOKIE: "1"}
        self.assertTrue(self.notice_hidden({"x-vercel-ip-country": "IN"}, dismissed))


@unittest.skipUnless(
    os.environ.get("LIVE_UPSTREAM_TESTS"), "set LIVE_UPSTREAM_TESTS=1 to call Fandango"
)
class LiveFandangoIntegrationTests(unittest.TestCase):
    """Contract check for the upstream endpoint used by production searches."""

    def test_theatre_endpoint_returns_live_theatre_payloads(self):
        payload = fandango.fandango_json(
            "/napi/theaterswithshowtimes",
            {"zipCode": "10001", "radius": 5, "limit": 5},
            timeout=30,
        )

        self.assertIsInstance(payload.get("theaters"), list)
        self.assertGreater(len(payload["theaters"]), 0)
        theatre = payload["theaters"][0]
        self.assertTrue(theatre.get("name"))
        self.assertIn("geo", theatre)
        self.assertIn("latitude", theatre["geo"])
        self.assertIn("longitude", theatre["geo"])
