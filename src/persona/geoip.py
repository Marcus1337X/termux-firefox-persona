"""Exit IP and GeoIP detection with fail-safe fallback and caching."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

_CACHE: dict | None = None
_CACHE_EXPIRES_AT: float = 0.0


def _parse_provider_response(data: dict) -> dict | None:
    ip = data.get("ip") or data.get("query")
    timezone = data.get("timezone")
    lat = data.get("latitude") if "latitude" in data else data.get("lat")
    lon = data.get("longitude") if "longitude" in data else data.get("lon")
    country_code = data.get("country_code") or data.get("countryCode")
    city = data.get("city")

    if not timezone or not isinstance(timezone, str):
        return None

    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return None

    try:
        lat = float(lat) if lat is not None else 0.0
        lon = float(lon) if lon is not None else 0.0
    except (ValueError, TypeError):
        return None

    return {
        "ip": str(ip) if ip else "",
        "timezone": timezone,
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "country_code": str(country_code) if country_code else "",
        "city": str(city) if city else "",
    }


def _fetch_from_provider(url: str, timeout: float = 1.5) -> dict | None:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            body = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(body)
            return _parse_provider_response(data)
    except Exception as exc:
        logger.debug("GeoIP query to %s failed: %s", url, exc)
        return None


def detect_exit_geoip_sync(timeout: float = 2.5, cache_ttl: float = 300.0) -> dict | None:
    """Synchronously detect exit IP geoip information."""
    global _CACHE, _CACHE_EXPIRES_AT
    now = time.time()
    if _CACHE and now < _CACHE_EXPIRES_AT:
        return dict(_CACHE)

    providers = [
        "https://api.ip.sb/geoip",
        "http://ip-api.com/json/",
        "https://ipwhois.app/json/",
    ]

    single_timeout = min(1.5, timeout)
    for url in providers:
        result = _fetch_from_provider(url, timeout=single_timeout)
        if result and result.get("timezone"):
            _CACHE = result
            _CACHE_EXPIRES_AT = now + cache_ttl
            return dict(result)

    return None


async def detect_exit_geoip(timeout: float = 2.5, cache_ttl: float = 300.0) -> dict | None:
    """Asynchronously detect exit IP geoip information without blocking the event loop."""
    global _CACHE, _CACHE_EXPIRES_AT
    now = time.time()
    if _CACHE and now < _CACHE_EXPIRES_AT:
        return dict(_CACHE)

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, detect_exit_geoip_sync, timeout, cache_ttl),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.debug("detect_exit_geoip timed out after %s seconds", timeout)
        return None
    except Exception as exc:
        logger.debug("detect_exit_geoip error: %s", exc)
        return None


def clear_geoip_cache():
    global _CACHE, _CACHE_EXPIRES_AT
    _CACHE = None
    _CACHE_EXPIRES_AT = 0.0
