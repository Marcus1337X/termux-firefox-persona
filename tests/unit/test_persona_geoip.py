"""Unit tests for persona geoip detection module."""

import asyncio
import time
import unittest
from unittest import mock

from src.persona.geoip import (
    _parse_provider_response,
    clear_geoip_cache,
    detect_exit_geoip,
    detect_exit_geoip_sync,
)


class PersonaGeoIPTests(unittest.TestCase):
    def setUp(self):
        clear_geoip_cache()

    def tearDown(self):
        clear_geoip_cache()

    def test_parse_provider_response_valid(self):
        data = {
            "ip": "72.110.85.175",
            "timezone": "America/Los_Angeles",
            "latitude": 37.2692,
            "longitude": -121.8450,
            "country_code": "US",
            "city": "San Jose",
        }
        parsed = _parse_provider_response(data)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["ip"], "72.110.85.175")
        self.assertEqual(parsed["timezone"], "America/Los_Angeles")
        self.assertEqual(parsed["latitude"], 37.2692)
        self.assertEqual(parsed["longitude"], -121.8450)
        self.assertEqual(parsed["country_code"], "US")
        self.assertEqual(parsed["city"], "San Jose")

    def test_parse_provider_response_alternate_keys(self):
        data = {
            "query": "1.2.3.4",
            "timezone": "Asia/Tokyo",
            "lat": 35.6895,
            "lon": 139.6917,
            "countryCode": "JP",
            "city": "Tokyo",
        }
        parsed = _parse_provider_response(data)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["ip"], "1.2.3.4")
        self.assertEqual(parsed["timezone"], "Asia/Tokyo")
        self.assertEqual(parsed["latitude"], 35.6895)
        self.assertEqual(parsed["longitude"], 139.6917)
        self.assertEqual(parsed["country_code"], "JP")

    def test_parse_provider_response_invalid_timezone(self):
        data = {"ip": "1.1.1.1", "timezone": "Invalid/Not_A_Timezone"}
        self.assertIsNone(_parse_provider_response(data))

    def test_parse_provider_response_missing_timezone(self):
        data = {"ip": "1.1.1.1"}
        self.assertIsNone(_parse_provider_response(data))

    @mock.patch("src.persona.geoip._fetch_from_provider")
    def test_sync_detection_and_cache(self, mock_fetch):
        mock_fetch.return_value = {
            "ip": "1.2.3.4",
            "timezone": "Europe/London",
            "latitude": 51.5074,
            "longitude": -0.1278,
            "country_code": "GB",
            "city": "London",
        }
        first = detect_exit_geoip_sync(timeout=1.0, cache_ttl=10.0)
        self.assertIsNotNone(first)
        self.assertEqual(first["timezone"], "Europe/London")
        self.assertEqual(mock_fetch.call_count, 1)

        # Second call should use cache, not calling provider again
        second = detect_exit_geoip_sync(timeout=1.0)
        self.assertEqual(second["timezone"], "Europe/London")
        self.assertEqual(mock_fetch.call_count, 1)

    @mock.patch("src.persona.geoip._fetch_from_provider")
    def test_provider_fallback(self, mock_fetch):
        # First provider fails, second succeeds
        mock_fetch.side_effect = [
            None,
            {
                "ip": "8.8.8.8",
                "timezone": "America/Chicago",
                "latitude": 41.8781,
                "longitude": -87.6298,
                "country_code": "US",
                "city": "Chicago",
            },
        ]
        res = detect_exit_geoip_sync(timeout=2.0)
        self.assertIsNotNone(res)
        self.assertEqual(res["timezone"], "America/Chicago")
        self.assertEqual(mock_fetch.call_count, 2)

    @mock.patch("src.persona.geoip._fetch_from_provider", return_value=None)
    def test_all_providers_fail_returns_none(self, mock_fetch):
        res = detect_exit_geoip_sync(timeout=1.0)
        self.assertIsNone(res)


class PersonaGeoIPAsyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_geoip_cache()

    def tearDown(self):
        clear_geoip_cache()

    @mock.patch("src.persona.geoip.detect_exit_geoip_sync")
    async def test_async_detection_success(self, mock_sync):
        mock_sync.return_value = {
            "ip": "72.110.85.175",
            "timezone": "America/Los_Angeles",
            "latitude": 37.2692,
            "longitude": -121.8450,
            "country_code": "US",
            "city": "San Jose",
        }
        res = await detect_exit_geoip(timeout=1.0)
        self.assertIsNotNone(res)
        self.assertEqual(res["timezone"], "America/Los_Angeles")

    @mock.patch("src.persona.geoip.detect_exit_geoip_sync", return_value=None)
    async def test_async_detection_failure(self, mock_sync):
        res = await detect_exit_geoip(timeout=1.0)
        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
