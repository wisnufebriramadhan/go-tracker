"""Lookup services: IP geolocation (ipwho.is) and phone number info (phonenumbers).

Both are free and require no API key, matching the approach used in GhostTrack.
Enhanced with proper error handling and type hints.
"""

import requests
import phonenumbers
from phonenumbers import carrier, geocoder, timezone
from typing import Dict, Any, Optional
from exceptions import IPLookupError, PhoneLookupError, APIError

# Approximate country centroids for map markers (lat, lon).
# Only a handful of common countries — unknown ones fall back to Google Maps link only.
COUNTRY_CENTROIDS = {
    "ID": (-2.5489, 118.0149),
    "US": (39.8283, -98.5795),
    "GB": (54.7024, -3.2766),
    "SG": (1.3521, 103.8198),
    "MY": (4.2105, 101.9758),
    "PH": (12.8797, 121.7740),
    "TH": (15.8700, 100.9925),
    "VN": (14.0583, 108.2772),
    "JP": (36.2048, 138.2529),
    "KR": (35.9078, 127.7669),
    "CN": (35.8617, 104.1954),
    "IN": (20.5937, 78.9629),
    "AU": (-25.2744, 133.7751),
    "DE": (51.1657, 10.4515),
    "FR": (46.2276, 2.2137),
    "NL": (52.1326, 5.2913),
    "TR": (38.9637, 35.2433),
    "AE": (23.4241, 53.8478),
    "SA": (23.8859, 45.0792),
    "BR": (-14.2350, -51.9253),
    "MX": (23.6345, -102.5528),
}

# Request timeout in seconds
REQUEST_TIMEOUT = 10


def ip_lookup(ip: str) -> Dict[str, Any]:
    """Look up an IP address via ipwho.is.

    Args:
        ip: IP address or domain name to lookup

    Returns:
        Dict of normalized fields, or {'error': msg} on failure.

    Raises:
        IPLookupError: If the lookup fails due to network or API errors.
    """
    try:
        resp = requests.get(
            f"http://ipwho.is/{ip}",
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "GeoTracker/1.0"}
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.Timeout:
        raise IPLookupError("Request to ipwho.is timed out")
    except requests.ConnectionError:
        raise IPLookupError("Could not connect to ipwho.is")
    except requests.RequestException as e:
        raise IPLookupError(f"Network error: {e}")
    except ValueError:
        raise IPLookupError("ipwho.is returned an invalid response")

    if not data.get("success", True):
        message = data.get("message", "Lookup failed")
        raise IPLookupError(message)

    conn = data.get("connection") or {}
    tz = data.get("timezone") or {}

    return {
        "ip": data.get("ip", ip),
        "type": data.get("type"),
        "country": data.get("country"),
        "country_code": data.get("country_code"),
        "city": data.get("city"),
        "region": data.get("region"),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "postal": data.get("postal"),
        "calling_code": data.get("calling_code"),
        "capital": data.get("capital"),
        "flag": (data.get("flag") or {}).get("emoji"),
        "isp": conn.get("isp"),
        "org": conn.get("org"),
        "asn": conn.get("asn"),
        "asn_org": conn.get("asn") and data.get("connection", {}).get("org"),
        "timezone": tz.get("id"),
        "utc_offset": tz.get("offset"),
        "current_time": tz.get("current_time"),
        "maps_url": f"https://www.google.com/maps/@{data.get('latitude')},{data.get('longitude')},12z",
    }


def phone_lookup(number: str, default_region: str = "ID") -> Dict[str, Any]:
    """Parse a phone number with phonenumbers (default region ID, like GhostTrack).

    Args:
        number: Phone number string (e.g., "+6281234567890" or "081234567890")
        default_region: Default region code for local numbers (default: "ID")

    Returns:
        Dict of normalized fields, or {'error': msg} on failure.

    Raises:
        PhoneLookupError: If the lookup fails.
    """
    try:
        parsed = phonenumbers.parse(number, default_region)
    except phonenumbers.NumberParseException as e:
        raise PhoneLookupError(f"Could not parse number: {e}")

    if not phonenumbers.is_possible_number(parsed):
        raise PhoneLookupError("Number is not a possible phone number")

    region_code = phonenumbers.region_code_for_number(parsed) or ""
    country_name = phonenumbers.region_code_for_number(parsed)
    location = geocoder.description_for_number(parsed, "id")
    provider = carrier.name_for_number(parsed, "en")
    tz_list = timezone.time_zones_for_number(parsed)
    number_type = phonenumbers.number_type(parsed)

    type_names = {
        phonenumbers.PhoneNumberType.MOBILE: "Mobile",
        phonenumbers.PhoneNumberType.FIXED_LINE: "Fixed line",
        phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "Fixed line or mobile",
        phonenumbers.PhoneNumberType.TOLL_FREE: "Toll free",
        phonenumbers.PhoneNumberType.PREMIUM_RATE: "Premium rate",
        phonenumbers.PhoneNumberType.SHARED_COST: "Shared cost",
        phonenumbers.PhoneNumberType.VOIP: "VoIP",
        phonenumbers.PhoneNumberType.PAGER: "Pager",
        phonenumbers.PhoneNumberType.UAN: "UAN",
        phonenumbers.PhoneNumberType.UNKNOWN: "Unknown",
    }

    # Country display name (best effort)
    country = geocoder.description_for_number(parsed, "en")

    centroid = COUNTRY_CENTROIDS.get(region_code)

    return {
        "original": number,
        "valid": phonenumbers.is_valid_number(parsed),
        "possible": phonenumbers.is_possible_number(parsed),
        "type": type_names.get(number_type, "Unknown"),
        "is_mobile": number_type == phonenumbers.PhoneNumberType.MOBILE,
        "international": phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
        ),
        "e164": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
        "national_number": parsed.national_number,
        "country_code": parsed.country_code,
        "region_code": region_code,
        "country": country or region_code,
        "location": location,
        "operator": provider,
        "timezones": list(tz_list),
        "latitude": centroid[0] if centroid else None,
        "longitude": centroid[1] if centroid else None,
        "maps_url": f"https://www.google.com/maps/search/{country or region_code}",
    }
