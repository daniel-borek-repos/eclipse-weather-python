"""Tests for the astronomy, the settings store and the HTTP routes.

Run with::

    python -m pytest test_app.py -q

No network access is needed: the weather provider is stubbed out. The
astronomy tests check real published eclipse circumstances, so they are the
ones that would catch a regression in the maths.
"""

import datetime as dt
import json
import os
import re
import tempfile
import unittest

import astro
import providers
import services
import settings as settings_store


UTC = dt.timezone.utc


def utc_at(year, month, day, hour=0, minute=0):
    return dt.datetime(year, month, day, hour, minute, tzinfo=UTC)


# --------------------------------------------------------------------------
# Astronomy
# --------------------------------------------------------------------------


class TestTimeConversions(unittest.TestCase):
    def test_known_julian_days(self):
        self.assertAlmostEqual(
            astro.datetime_to_jd(utc_at(2000, 1, 1, 12)), 2451545.0, places=6
        )
        self.assertAlmostEqual(
            astro.datetime_to_jd(utc_at(1987, 4, 10)), 2446895.5, places=6
        )

    def test_round_trip(self):
        moment = dt.datetime(2026, 8, 17, 12, 34, 56, tzinfo=UTC)
        self.assertEqual(astro.jd_to_datetime(astro.datetime_to_jd(moment)), moment)

    def test_delta_t_is_plausible(self):
        # Delta T has sat near 69 seconds for the whole of the 2020s.
        self.assertAlmostEqual(
            astro.delta_t_seconds(astro.datetime_to_jd(utc_at(2024, 4, 8))),
            69.2,
            delta=1.0,
        )
        # It must stay monotonic and finite far into the future.
        far = astro.delta_t_seconds(astro.datetime_to_jd(utc_at(2060, 1, 1)))
        self.assertTrue(70 < far < 200, far)


class TestPositions(unittest.TestCase):
    """Checked against the worked examples in Meeus."""

    def test_moon_matches_meeus_example_47a(self):
        moon = astro.moon_position(2448724.5)
        # Meeus gives apparent longitude 133.167265, latitude -3.229126,
        # distance 368409.7 km.
        self.assertAlmostEqual(moon["apparent_longitude"], 133.167265, places=3)
        self.assertAlmostEqual(moon["latitude"], -3.229126, places=4)
        self.assertAlmostEqual(moon["distance_km"], 368409.7, delta=1.0)

    def test_sun_matches_meeus_example_25a(self):
        sun = astro.sun_position(2448908.5)
        # Meeus 25.a quotes apparent longitude 199.90895, declination
        # -7d47'06" and R = 0.99766 AU for this low-precision method. (His
        # 25.b VSOP87 solution gives R = 0.9976078; the 5e-5 AU difference
        # changes the Sun's apparent radius by well under a hundredth of a
        # percent, which no eclipse timing here is sensitive to.)
        self.assertAlmostEqual(sun["apparent_longitude"], 199.90895, places=2)
        self.assertAlmostEqual(sun["dec"], -7.78509, places=3)
        self.assertAlmostEqual(sun["distance_km"] / astro.AU_KM, 0.99766, places=4)

    def test_new_moon_instants(self):
        # Published new moons: 2024-04-08 18:21 UT and 2017-08-21 18:30 UT.
        for expected in (utc_at(2024, 4, 8, 18, 21), utc_at(2017, 8, 21, 18, 30)):
            # Lunation number, counted from the new moon of 2000-01-06.
            year = expected.year + (expected.timetuple().tm_yday - 1) / 365.25
            k = round((year - 2000.0) * 12.3685)
            found = astro.tt_to_ut(astro.true_new_moon_tt(astro.mean_new_moon_jd(k)))
            delta = abs((astro.jd_to_datetime(found) - expected).total_seconds())
            self.assertLess(delta, 120, "new moon %s off by %ds" % (expected, delta))


class TestSunEvents(unittest.TestCase):
    def test_equinox_day_is_about_twelve_hours(self):
        events = astro.sun_events(dt.date(2026, 3, 20), 0.0, 0.0, "UTC")
        self.assertAlmostEqual(events["day_length_hours"], 12.1, delta=0.15)

    def test_polar_day_and_night(self):
        summer = astro.sun_events(dt.date(2026, 6, 21), 78.2, 15.6, "UTC")
        self.assertTrue(summer["polar_day"])
        self.assertIsNone(summer["sunrise"])

        winter = astro.sun_events(dt.date(2026, 12, 21), 78.2, 15.6, "UTC")
        self.assertTrue(winter["polar_night"])
        self.assertIsNone(winter["sunset"])

    def test_sunrise_precedes_noon_precedes_sunset(self):
        events = astro.sun_events(dt.date(2026, 8, 17), 50.0614, 19.9366, "Europe/Warsaw")
        self.assertLess(events["sunrise"], events["solar_noon"])
        self.assertLess(events["solar_noon"], events["sunset"])
        # Mid-August in Krakow: a little over fourteen hours of daylight.
        self.assertAlmostEqual(events["day_length_hours"], 14.4, delta=0.3)

    def test_southern_hemisphere_seasons_are_inverted(self):
        june = astro.sun_events(dt.date(2026, 6, 21), -33.8688, 151.2093, "Australia/Sydney")
        december = astro.sun_events(dt.date(2026, 12, 21), -33.8688, 151.2093, "Australia/Sydney")
        self.assertLess(june["day_length_hours"], december["day_length_hours"])


class TestEclipses(unittest.TestCase):
    """Against published local circumstances for real eclipses.

    Tolerances reflect the accuracy of the abridged lunar theory: about a
    minute in time and fifteen seconds in the duration of totality.
    """

    def assert_eclipse(self, lat, lon, search_from, expect_date, expect_type,
                       expect_max, expect_central=None):
        eclipse = astro.next_solar_eclipse(lat, lon, start=search_from)
        self.assertIsNotNone(eclipse, "no eclipse found")

        maximum = astro.jd_to_datetime(eclipse["maximum_jd"])
        self.assertEqual(maximum.date(), expect_date)
        self.assertEqual(eclipse["type"], expect_type)

        offset = abs((maximum - expect_max).total_seconds())
        self.assertLess(offset, 90, "maximum off by %ds" % offset)

        if expect_central is not None:
            self.assertIn("central_duration_seconds", eclipse)
            self.assertAlmostEqual(
                eclipse["central_duration_seconds"], expect_central, delta=20
            )

        # Contacts must bracket the maximum.
        self.assertLess(eclipse["first_contact_jd"], eclipse["maximum_jd"])
        self.assertGreater(eclipse["last_contact_jd"], eclipse["maximum_jd"])
        return eclipse

    def test_dallas_2024(self):
        eclipse = self.assert_eclipse(
            32.7767, -96.7970, utc_at(2024, 4, 1),
            dt.date(2024, 4, 8), "total", utc_at(2024, 4, 8, 18, 42, ), 230,
        )
        self.assertGreater(eclipse["magnitude"], 1.0)
        self.assertEqual(eclipse["obscuration"], 1.0)

    def test_nashville_2017(self):
        self.assert_eclipse(
            36.1627, -86.7816, utc_at(2017, 8, 1),
            dt.date(2017, 8, 21), "total", utc_at(2017, 8, 21, 18, 28), 116,
        )

    def test_reykjavik_2026(self):
        self.assert_eclipse(
            64.1466, -21.9426, utc_at(2026, 8, 1),
            dt.date(2026, 8, 12), "total", utc_at(2026, 8, 12, 17, 48), 63,
        )

    def test_sydney_2028(self):
        self.assert_eclipse(
            -33.8688, 151.2093, utc_at(2026, 8, 17),
            dt.date(2028, 7, 22), "total", utc_at(2028, 7, 22, 4, 3), 229,
        )

    def test_partial_from_krakow(self):
        eclipse = self.assert_eclipse(
            50.0614, 19.9366, utc_at(2026, 8, 17),
            dt.date(2027, 8, 2), "partial", utc_at(2027, 8, 2, 9, 22),
        )
        self.assertLess(eclipse["magnitude"], 1.0)
        self.assertNotIn("central_duration_seconds", eclipse)

    def test_eclipse_is_always_above_the_horizon(self):
        """A returned eclipse must be visible, not below the horizon."""
        for lat, lon in ((50.06, 19.94), (-33.87, 151.21), (35.68, 139.69), (-22.91, -43.17)):
            eclipse = astro.next_solar_eclipse(lat, lon, start=utc_at(2026, 8, 17))
            self.assertIsNotNone(eclipse)
            self.assertGreater(eclipse["maximum_sun_altitude"], -0.6)
            self.assertGreater(eclipse["obscuration"], 0.0)

    def test_result_is_in_the_future(self):
        start = utc_at(2026, 8, 17)
        eclipse = astro.next_solar_eclipse(50.0614, 19.9366, start=start)
        self.assertGreater(
            astro.jd_to_datetime(eclipse["maximum_jd"]), start
        )


class TestCoverageGeometry(unittest.TestCase):
    def test_no_overlap(self):
        magnitude, obscuration, kind = astro._coverage(1.0, 0.26, 0.26)
        self.assertEqual((magnitude, obscuration, kind), (0.0, 0.0, None))

    def test_total(self):
        _, obscuration, kind = astro._coverage(0.0, 0.26, 0.28)
        self.assertEqual(kind, "total")
        self.assertEqual(obscuration, 1.0)

    def test_annular(self):
        _, obscuration, kind = astro._coverage(0.0, 0.28, 0.26)
        self.assertEqual(kind, "annular")
        self.assertAlmostEqual(obscuration, (0.26 / 0.28) ** 2, places=6)

    def test_partial_half_covered_is_between(self):
        _, obscuration, kind = astro._coverage(0.26, 0.26, 0.26)
        self.assertEqual(kind, "partial")
        self.assertTrue(0.3 < obscuration < 0.5, obscuration)


# --------------------------------------------------------------------------
# Settings store
# --------------------------------------------------------------------------


class SettingsTestCase(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        handle.close()
        os.unlink(handle.name)
        self.path = handle.name
        os.environ["ECLIPSE_WEATHER_SETTINGS"] = self.path

    def tearDown(self):
        os.environ.pop("ECLIPSE_WEATHER_SETTINGS", None)
        if os.path.exists(self.path):
            os.unlink(self.path)


class TestSettings(SettingsTestCase):
    def test_missing_file_gives_unconfigured_defaults(self):
        data = settings_store.load()
        self.assertFalse(data["setup_complete"])
        self.assertEqual(data["views"], [])

    def test_corrupt_file_does_not_raise(self):
        with open(self.path, "w") as handle:
            handle.write("{not json at all")
        data = settings_store.load()
        self.assertFalse(data["setup_complete"])

    def test_create_view_sets_default_and_completes_setup(self):
        data = settings_store.load()
        view = settings_store.create_view(data, "Home")
        settings_store.save(data)

        reloaded = settings_store.load()
        self.assertTrue(reloaded["setup_complete"])
        self.assertEqual(reloaded["default_view_id"], view["id"])

    def test_add_location_rejects_bad_coordinates(self):
        data = settings_store.load()
        view = settings_store.create_view(data, "Home")
        with self.assertRaises(settings_store.SettingsError):
            settings_store.add_location(data, view["id"], {"name": "Nowhere"})
        with self.assertRaises(settings_store.SettingsError):
            settings_store.add_location(
                data, view["id"], {"name": "Off world", "latitude": 120, "longitude": 0}
            )

    def test_duplicate_location_is_rejected(self):
        data = settings_store.load()
        view = settings_store.create_view(data, "Home")
        place = {"name": "Krakow", "latitude": 50.0614, "longitude": 19.9366}
        settings_store.add_location(data, view["id"], place)
        with self.assertRaises(settings_store.SettingsError):
            settings_store.add_location(data, view["id"], dict(place))

    def test_cannot_delete_the_only_view(self):
        data = settings_store.load()
        view = settings_store.create_view(data, "Home")
        with self.assertRaises(settings_store.SettingsError):
            settings_store.delete_view(data, view["id"])

    def test_deleting_default_view_promotes_another(self):
        data = settings_store.load()
        first = settings_store.create_view(data, "Home")
        second = settings_store.create_view(data, "Trip")
        self.assertEqual(data["default_view_id"], first["id"])

        settings_store.delete_view(data, first["id"])
        self.assertEqual(data["default_view_id"], second["id"])

    def test_copied_locations_get_fresh_ids(self):
        data = settings_store.load()
        first = settings_store.create_view(data, "Home")
        original = settings_store.add_location(
            data, first["id"], {"name": "Krakow", "latitude": 50.06, "longitude": 19.94}
        )
        second = settings_store.create_view(
            data, "Copy", [dict(item) for item in first["locations"]]
        )
        self.assertNotEqual(second["locations"][0]["id"], original["id"])

    def test_save_is_atomic_and_leaves_no_temp_files(self):
        data = settings_store.load()
        settings_store.create_view(data, "Home")
        settings_store.save(data)

        directory = os.path.dirname(self.path)
        leftovers = [n for n in os.listdir(directory) if n.startswith(".settings-")]
        self.assertEqual(leftovers, [])

        with open(self.path) as handle:
            self.assertEqual(json.load(handle)["views"][0]["name"], "Home")

    def test_preferences_are_validated(self):
        data = settings_store.load()
        with self.assertRaises(settings_store.SettingsError):
            settings_store.update_preferences(data, {"temperature_unit": "kelvin"})
        settings_store.update_preferences(data, {"temperature_unit": "fahrenheit"})
        self.assertEqual(data["preferences"]["temperature_unit"], "fahrenheit")

    def test_unknown_units_in_file_fall_back_to_defaults(self):
        with open(self.path, "w") as handle:
            json.dump(
                {"version": 1, "preferences": {"temperature_unit": "nonsense"},
                 "views": []},
                handle,
            )
        data = settings_store.load()
        self.assertEqual(data["preferences"]["temperature_unit"], "celsius")


# --------------------------------------------------------------------------
# Presentation helpers
# --------------------------------------------------------------------------


class TestFormatting(unittest.TestCase):
    def test_compass(self):
        self.assertEqual(services.compass_direction(0), "N")
        self.assertEqual(services.compass_direction(90), "E")
        self.assertEqual(services.compass_direction(180), "S")
        self.assertEqual(services.compass_direction(350), "N")
        self.assertIsNone(services.compass_direction(None))

    def test_durations(self):
        self.assertEqual(services.format_duration(230), "3m 50s")
        self.assertEqual(services.format_duration(7380), "2h 03m")
        self.assertEqual(services.format_duration(45), "45s")
        self.assertIsNone(services.format_duration(None))

    def test_clock_formats(self):
        moment = dt.datetime(2027, 8, 2, 14, 5, 9)
        self.assertEqual(services.format_clock(moment, "24h"), "14:05")
        self.assertEqual(services.format_clock(moment, "24h", True), "14:05:09")
        self.assertEqual(services.format_clock(moment, "12h"), "2:05 PM")

    def test_location_label_skips_duplicates(self):
        self.assertEqual(
            services.location_label(
                {"name": "Singapore", "country": "Singapore"}
            ),
            "Singapore",
        )
        self.assertEqual(
            services.location_label(
                {"name": "Krakow", "admin1": "Lesser Poland", "country": "Poland"}
            ),
            "Krakow, Lesser Poland, Poland",
        )


# --------------------------------------------------------------------------
# HTTP routes
# --------------------------------------------------------------------------

FAKE_FORECAST = {
    "timezone": "Europe/Warsaw",
    "elevation": 219.0,
    "current": {
        "time": "2026-08-17T12:00",
        "temperature_2m": 24.3,
        "relative_humidity_2m": 51,
        "apparent_temperature": 24.9,
        "is_day": 1,
        "precipitation": 0.0,
        "weather_code": 1,
        "cloud_cover": 12,
        "pressure_msl": 1017.2,
        "wind_speed_10m": 9.4,
        "wind_direction_10m": 210,
        "wind_gusts_10m": 18.0,
    },
    "daily": {
        "time": ["2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20"],
        "weather_code": [1, 3, 61, 2],
        "temperature_2m_max": [26.1, 24.0, 21.5, 23.9],
        "temperature_2m_min": [14.2, 13.8, 14.9, 13.1],
        "sunrise": ["2026-08-17T05:32", "2026-08-18T05:34", "2026-08-19T05:35", "2026-08-20T05:37"],
        "sunset": ["2026-08-17T19:55", "2026-08-18T19:53", "2026-08-19T19:51", "2026-08-20T19:49"],
        "daylight_duration": [51780.0, 51500.0, 51200.0, 50900.0],
        "uv_index_max": [5.8, 4.1, 3.2, 5.0],
        "precipitation_sum": [0.0, 1.2, 6.4, 0.1],
        "precipitation_probability_max": [3, 35, 80, 10],
    },
}


class TestRoutes(SettingsTestCase):
    def setUp(self):
        super(TestRoutes, self).setUp()
        import app as app_module

        self.app_module = app_module
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

        # Stub the network so tests are hermetic.
        self._real_fetch = providers.fetch_weather
        self._real_search = providers.search_places
        providers.fetch_weather = lambda *a, **k: FAKE_FORECAST
        providers.search_places = lambda query, **k: [
            {
                "name": "Kraków",
                "country": "Poland",
                "admin1": "Lesser Poland",
                "latitude": 50.06143,
                "longitude": 19.93658,
                "timezone": "Europe/Warsaw",
            }
        ]

    def tearDown(self):
        providers.fetch_weather = self._real_fetch
        providers.search_places = self._real_search
        providers.clear_caches()
        services._eclipse_cache.clear()
        super(TestRoutes, self).tearDown()

    def seed(self):
        data = settings_store.load()
        view = settings_store.create_view(data, "Home")
        settings_store.add_location(
            data,
            view["id"],
            {
                "name": "Kraków",
                "country": "Poland",
                "latitude": 50.06143,
                "longitude": 19.93658,
                "timezone": "Europe/Warsaw",
            },
        )
        settings_store.save(data)
        return view["id"]

    def csrf_headers(self):
        """A real CSRF token, lifted from a rendered page.

        The tests send the genuine article rather than switching the protection
        off, so the wiring itself stays under test. The client keeps cookies
        between requests, so the session started here carries into the caller's
        request.
        """
        page = self.client.get("/setup")
        match = re.search(rb'name="csrf-token" content="([^"]+)"', page.data)
        self.assertIsNotNone(match, "no CSRF token in the rendered page")
        return {"X-CSRFToken": match.group(1).decode()}

    def test_first_visit_redirects_to_setup(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/setup", response.headers["Location"])

    def test_setup_creates_default_view(self):
        response = self.client.post(
            "/api/setup",
            json={
                "location": {
                    "name": "Kraków",
                    "latitude": 50.06143,
                    "longitude": 19.93658,
                    "timezone": "Europe/Warsaw",
                },
                "view_name": "Home",
            },
            headers=self.csrf_headers(),
        )
        self.assertEqual(response.status_code, 200)
        view_id = response.get_json()["view_id"]

        data = settings_store.load()
        self.assertTrue(data["setup_complete"])
        self.assertEqual(data["default_view_id"], view_id)

    def test_setup_rejects_missing_location(self):
        response = self.client.post(
            "/api/setup", json={}, headers=self.csrf_headers()
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["ok"])

    def test_index_redirects_to_default_view(self):
        view_id = self.seed()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(view_id, response.headers["Location"])

    def test_dashboard_renders(self):
        view_id = self.seed()
        response = self.client.get("/view/%s" % view_id)
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Home", body)
        self.assertIn("Kraków", body)

    def test_unknown_view_is_404(self):
        self.seed()
        self.assertEqual(self.client.get("/view/nope").status_code, 404)

    def test_weather_endpoint_returns_weather_and_eclipse(self):
        view_id = self.seed()
        response = self.client.get("/api/views/%s/weather" % view_id)
        self.assertEqual(response.status_code, 200)

        location = response.get_json()["locations"][0]
        self.assertEqual(location["errors"], {})
        self.assertEqual(location["weather"]["temperature"], 24)
        self.assertEqual(location["weather"]["condition"]["label"], "Mainly clear")
        self.assertEqual(location["weather"]["wind_compass"], "SSW")
        self.assertEqual(location["today"]["sunrise"], "05:32")
        self.assertEqual(len(location["today"]["forecast"]), 3)

        eclipse = location["eclipse"]
        self.assertIn(eclipse["type"], ("partial", "annular", "total"))
        self.assertTrue(eclipse["times"]["maximum"])
        self.assertTrue(eclipse["sun_events"]["sunrise"])
        self.assertGreater(eclipse["days_away"], 0)

    def test_weather_endpoint_reports_provider_failure_per_location(self):
        view_id = self.seed()

        def boom(*args, **kwargs):
            raise providers.ProviderError("Upstream is down.")

        providers.fetch_weather = boom
        response = self.client.get("/api/views/%s/weather" % view_id)
        self.assertEqual(response.status_code, 200)

        location = response.get_json()["locations"][0]
        self.assertEqual(location["errors"]["weather"], "Upstream is down.")
        # The eclipse is computed locally, so it must still be present.
        self.assertIsNotNone(location["eclipse"])

    def test_add_and_remove_location(self):
        view_id = self.seed()
        response = self.client.post(
            "/api/views/%s/locations" % view_id,
            json={
                "location": {
                    "name": "Reykjavík",
                    "latitude": 64.1466,
                    "longitude": -21.9426,
                    "timezone": "Atlantic/Reykjavik",
                }
            },
            headers=self.csrf_headers(),
        )
        self.assertEqual(response.status_code, 200)
        location_id = response.get_json()["location"]["id"]

        data = settings_store.load()
        self.assertEqual(len(settings_store.get_view(data, view_id)["locations"]), 2)

        response = self.client.delete(
            "/api/views/%s/locations/%s" % (view_id, location_id),
            headers=self.csrf_headers(),
        )
        self.assertEqual(response.status_code, 200)
        data = settings_store.load()
        self.assertEqual(len(settings_store.get_view(data, view_id)["locations"]), 1)

    def test_create_view_copying_locations(self):
        view_id = self.seed()
        response = self.client.post(
            "/api/views",
            json={"name": "Trip", "copy_locations_from": view_id, "make_default": True},
            headers=self.csrf_headers(),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        new_id = payload["view"]["id"]

        data = settings_store.load()
        self.assertEqual(data["default_view_id"], new_id)
        self.assertEqual(len(settings_store.get_view(data, new_id)["locations"]), 1)

    def test_rename_view(self):
        view_id = self.seed()
        response = self.client.patch(
            "/api/views/%s" % view_id,
            json={"name": "Kraków"},
            headers=self.csrf_headers(),
        )
        self.assertEqual(response.status_code, 200)
        data = settings_store.load()
        self.assertEqual(settings_store.get_view(data, view_id)["name"], "Kraków")

    def test_rename_to_blank_is_rejected(self):
        view_id = self.seed()
        response = self.client.patch(
            "/api/views/%s" % view_id,
            json={"name": "   "},
            headers=self.csrf_headers(),
        )
        self.assertEqual(response.status_code, 400)

    def test_delete_last_view_is_rejected(self):
        view_id = self.seed()
        response = self.client.delete(
            "/api/views/%s" % view_id, headers=self.csrf_headers()
        )
        self.assertEqual(response.status_code, 400)

    def test_delete_view_redirects_to_survivor(self):
        first = self.seed()
        second = self.client.post(
            "/api/views", json={"name": "Trip"}, headers=self.csrf_headers()
        ).get_json()["view"]["id"]

        response = self.client.delete(
            "/api/views/%s" % first, headers=self.csrf_headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(second, response.get_json()["redirect"])

    def test_preferences_round_trip(self):
        self.seed()
        response = self.client.patch(
            "/api/preferences",
            json={"temperature_unit": "fahrenheit", "time_format": "12h"},
            headers=self.csrf_headers(),
        )
        self.assertEqual(response.status_code, 200)
        data = settings_store.load()
        self.assertEqual(data["preferences"]["temperature_unit"], "fahrenheit")
        self.assertEqual(data["preferences"]["time_format"], "12h")

    def test_geocode_endpoint(self):
        response = self.client.get("/api/geocode?q=krakow")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["results"][0]["name"], "Kraków")

    def test_reverse_geocode_validates_input(self):
        self.assertEqual(self.client.get("/api/reverse-geocode").status_code, 400)
        self.assertEqual(
            self.client.get("/api/reverse-geocode?lat=999&lon=0").status_code, 400
        )

    def test_twelve_hour_preference_reaches_the_payload(self):
        view_id = self.seed()
        self.client.patch(
            "/api/preferences",
            json={"time_format": "12h"},
            headers=self.csrf_headers(),
        )
        response = self.client.get("/api/views/%s/weather" % view_id)
        maximum = response.get_json()["locations"][0]["eclipse"]["times"]["maximum"]
        self.assertRegex(maximum, r"(AM|PM)$")

    def test_mutation_without_csrf_token_is_rejected(self):
        self.seed()
        response = self.client.post("/api/views", json={"name": "Trip"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["ok"])

    def test_reads_do_not_need_a_csrf_token(self):
        view_id = self.seed()
        self.assertEqual(
            self.client.get("/api/views/%s/weather" % view_id).status_code, 200
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
