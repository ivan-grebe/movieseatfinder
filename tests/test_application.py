import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import application, seat_matching


class TicketUrlTests(unittest.TestCase):
    def test_ticket_urls_are_allowlisted(self):
        self.assertEqual(
            application.safe_fandango_url("https://tickets.fandango.com/order"),
            "https://tickets.fandango.com/order",
        )
        self.assertEqual(application.safe_fandango_url("https://example.com"), "")
        self.assertEqual(application.safe_fandango_url("javascript:alert(1)"), "")


class MovieAndFormatTests(unittest.TestCase):
    def test_movie_matching_requires_the_selected_title(self):
        self.assertTrue(
            application.movie_matches("Spider-Man: Homecoming", "spider man homecoming")
        )
        self.assertFalse(application.movie_matches("Spider-Man: Homecoming", "spider man"))
        self.assertFalse(application.movie_matches("", "spider man"))

    def test_format_matching_distinguishes_imax_variants(self):
        self.assertTrue(application.format_matches("IMAX", "imax"))
        self.assertFalse(application.format_matches("IMAX", "IMAX with Laser"))
        self.assertFalse(application.format_matches("IMAX with Laser", "IMAX"))
        self.assertFalse(application.format_matches("IMAX 70mm", "IMAX"))
        self.assertTrue(application.format_matches("IMAX 70mm", "imax70"))
        self.assertTrue(
            application.format_matches("IMAX with Laser", "Dolby Cinema,IMAX with Laser")
        )

    def test_format_matching_collapses_only_equivalent_labels(self):
        self.assertTrue(application.format_matches("Dolby Cinema", "dolby"))
        self.assertTrue(application.format_matches("70mm", "70MM Film"))
        self.assertFalse(application.format_matches("IMAX 70mm", "70mm"))
        self.assertFalse(application.format_matches("Dolby Atmos", "Dolby Cinema"))
        self.assertFalse(application.format_matches("Standard", "dolby,imax"))


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


class CandidateSearchTests(unittest.TestCase):
    def test_exactly_full_page_keeps_checking_for_a_next_match(self):
        def check(candidate):
            return {"candidate": candidate} if candidate < 5 or candidate == 99 else None

        matches, checked = application.seat_checked_matches(list(range(100)), 5, check)

        self.assertEqual(matches, [{"candidate": value} for value in [0, 1, 2, 3, 4, 99]])
        self.assertEqual(checked, 100)


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
        application.RATE_LIMITER.storage.reset()
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


class LocationTests(unittest.TestCase):
    @patch("backend.location.reverse_geocode_zip", return_value="10001")
    @patch.object(application, "fandango_json")
    def test_radius_uses_coordinates_instead_of_upstream_distance(self, upstream, _zip):
        upstream.return_value = {
            "theaters": [
                {"name": "Nearby", "distance": 0, "geo": {"latitude": 40.03, "longitude": -75}},
                {"name": "Too far", "distance": 0, "geo": {"latitude": 40.10, "longitude": -75}},
            ]
        }
        application.RATE_LIMITER.storage.reset()
        with patch.object(application, "THEATRES_CACHE", application.TtlCache("theatres", 300, 10)):
            response = TestClient(application.app).get(
                "/api/theatres",
                params={"lat": 40, "lon": -75, "radius": 5},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["theatres"], [{"name": "Nearby"}])
