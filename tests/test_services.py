"""Tests for geo-tracker services and session API."""

from unittest.mock import patch
import io
import pytest
import phonenumbers

import db
import services
import app as app_module
from validators import (
    validate_ip_address, validate_phone_number, validate_coordinates,
    validate_session_name, validate_accuracy, validate_session_id, sanitize_string
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client backed by a throwaway SQLite file."""
    test_db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    # Also set the module-level variable directly
    db.DB_PATH = test_db_path
    # Initialize database with the test path
    db.init_db(drop_existing=True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# -------------------------------------------------------------------------- 
# validators module tests
# -------------------------------------------------------------------------- 
class TestValidators:
    def test_validate_ip_address_valid_ipv4(self):
        valid, error = validate_ip_address("8.8.8.8")
        assert valid is True
        assert error is None

    def test_validate_ip_address_valid_ipv6(self):
        valid, error = validate_ip_address("2001:0db8:85a3:0000:0000:8a2e:0370:7334")
        assert valid is True
        assert error is None

    def test_validate_ip_address_valid_domain(self):
        valid, error = validate_ip_address("example.com")
        assert valid is True
        assert error is None

    def test_validate_ip_address_invalid(self):
        valid, error = validate_ip_address("192.168.1.256")
        assert valid is False
        assert error is not None

    def test_validate_ip_address_empty(self):
        valid, error = validate_ip_address("")
        assert valid is False

    def test_validate_phone_number_international(self):
        valid, error = validate_phone_number("+6281234567890")
        assert valid is True
        assert error is None

    def test_validate_phone_number_local(self):
        valid, error = validate_phone_number("081234567890")
        assert valid is True
        assert error is None

    def test_validate_phone_number_invalid_format(self):
        valid, error = validate_phone_number("123")
        assert valid is False
        assert error is not None

    def test_validate_phone_number_empty(self):
        valid, error = validate_phone_number("")
        assert valid is False

    def test_validate_coordinates_valid(self):
        valid, error = validate_coordinates(-6.2, 106.8)
        assert valid is True
        assert error is None

    def test_validate_coordinates_invalid_lat(self):
        valid, error = validate_coordinates(100, 106.8)
        assert valid is False
        assert "Latitude" in error

    def test_validate_coordinates_invalid_lon(self):
        valid, error = validate_coordinates(-6.2, 200)
        assert valid is False
        assert "Longitude" in error

    def test_validate_coordinates_non_numeric(self):
        valid, error = validate_coordinates("abc", 106.8)
        assert valid is False

    def test_validate_session_name_valid(self):
        valid, error = validate_session_name("Test Session")
        assert valid is True
        assert error is None

    def test_validate_session_name_too_long(self):
        valid, error = validate_session_name("x" * 101)
        assert valid is False
        assert "100 characters" in error

    def test_validate_session_name_special_chars(self):
        valid, error = validate_session_name("Test<script>")
        assert valid is False

    def test_validate_session_name_none(self):
        valid, error = validate_session_name(None)
        assert valid is True  # Name is optional

    def test_validate_accuracy_valid(self):
        valid, error = validate_accuracy(12.5)
        assert valid is True
        assert error is None

    def test_validate_accuracy_none(self):
        valid, error = validate_accuracy(None)
        assert valid is True

    def test_validate_accuracy_negative(self):
        valid, error = validate_accuracy(-5)
        assert valid is False
        assert "negative" in error

    def test_validate_session_id_valid(self):
        valid, error, sid = validate_session_id(123)
        assert valid is True
        assert sid == 123

    def test_validate_session_id_string(self):
        valid, error, sid = validate_session_id("456")
        assert valid is True
        assert sid == 456

    def test_validate_session_id_invalid(self):
        valid, error, sid = validate_session_id("abc")
        assert valid is False

    def test_sanitize_string(self):
        result = sanitize_string("  test  ")
        assert result == "test"

    def test_sanitize_string_max_length(self):
        result = sanitize_string("x" * 2000, max_length=100)
        assert len(result) == 100

    def test_sanitize_string_null_bytes(self):
        result = sanitize_string("test\x00value")
        assert "\x00" not in result


# -------------------------------------------------------------------------- 
# services.phone_lookup
# -------------------------------------------------------------------------- 
def test_phone_valid_id_mobile():
    result = services.phone_lookup("+6281234567890", default_region="ID")
    assert "error" not in result
    assert result["valid"] is True
    assert result["possible"] is True
    assert result["region_code"] == "ID"
    assert result["country_code"] == 62
    assert result["e164"] == "+6281234567890"
    assert result["is_mobile"] is True
    assert result["type"] == "Mobile"
    assert isinstance(result["timezones"], list)


def test_phone_without_plus_uses_default_region():
    result = services.phone_lookup("081234567890", default_region="ID")
    assert "error" not in result
    assert result["country_code"] == 62
    assert result["valid"] is True


def test_phone_invalid_country_code():
    with pytest.raises(Exception):
        services.phone_lookup("+99999999999", default_region="ID")


def test_phone_us_number():
    result = services.phone_lookup("+14155552671", default_region="ID")
    assert "error" not in result
    assert result["region_code"] == "US"
    assert result["country_code"] == 1


def test_phone_lookup_matches_phonenumbers_directly():
    # Sanity: our wrapper agrees with the underlying library
    raw = "+6281234567890"
    parsed = phonenumbers.parse(raw, "ID")
    result = services.phone_lookup(raw)
    assert result["national_number"] == parsed.national_number
    assert result["valid"] == phonenumbers.is_valid_number(parsed)


# -------------------------------------------------------------------------- 
# services.ip_lookup (mocked network)
# -------------------------------------------------------------------------- 
FAKE_IPWHOIS = {
    "ip": "8.8.8.8",
    "type": "IPv4",
    "country": "United States",
    "country_code": "US",
    "city": "Mountain View",
    "region": "California",
    "latitude": 37.405992,
    "longitude": -122.078515,
    "postal": "94043",
    "calling_code": "1",
    "capital": "Washington D.C.",
    "flag": {"emoji": "🇺🇸"},
    "connection": {"asn": 15169, "org": "Google LLC", "isp": "Google LLC"},
    "timezone": {"id": "America/Los_Angeles", "offset": -25200, "current_time": "12:00:00"},
}


def test_ip_lookup_mock(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return FAKE_IPWHOIS

    monkeypatch.setattr(services.requests, "get", lambda *a, **k: FakeResp())
    result = services.ip_lookup("8.8.8.8")
    assert "error" not in result
    assert result["country"] == "United States"
    assert result["isp"] == "Google LLC"
    assert result["latitude"] == 37.405992
    assert result["maps_url"].startswith("https://www.google.com/maps/")


def test_ip_lookup_network_error(monkeypatch):
    from exceptions import IPLookupError
    
    def boom(*a, **k):
        raise services.requests.RequestException("boom")

    monkeypatch.setattr(services.requests, "get", boom)
    with pytest.raises(IPLookupError):
        services.ip_lookup("1.1.1.1")


def test_ip_lookup_api_error(monkeypatch):
    from exceptions import IPLookupError
    
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"success": False, "message": "Invalid IP address"}

    monkeypatch.setattr(services.requests, "get", lambda *a, **k: FakeResp())
    with pytest.raises(IPLookupError) as exc_info:
        services.ip_lookup("not-an-ip")
    assert "Invalid IP address" in str(exc_info.value)


# -------------------------------------------------------------------------- 
# Session API flow
# -------------------------------------------------------------------------- 
def test_session_lifecycle(client):
    # Create
    rv = client.post("/api/sessions", json={"name": "Test Phone"})
    assert rv.status_code == 201
    session = rv.get_json()
    token = session["token"]
    assert token
    assert session["name"] == "Test Phone"

    # Post a location
    rv = client.post(
        f"/api/sessions/{token}/location",
        json={"lat": -6.2, "lon": 106.8, "accuracy": 12.5},
    )
    assert rv.status_code == 201
    fix = rv.get_json()["fix"]
    assert fix["lat"] == -6.2
    assert fix["lon"] == 106.8
    assert fix["accuracy"] == 12.5
    assert fix["source"] == "gps"

    # Sessions list shows it
    rv = client.get("/api/sessions")
    sessions = rv.get_json()
    assert len(sessions) == 1
    assert sessions[0]["points"] == 1
    assert sessions[0]["latest"]["lat"] == -6.2

    # Detail endpoint returns full trail
    rv = client.get(f"/api/sessions/{session['id']}")
    data = rv.get_json()
    assert data["points"] == 1
    assert len(data["trail"]) == 1

    # Unknown token rejected
    rv = client.post("/api/sessions/unknown/location", json={"lat": 0, "lon": 0})
    assert rv.status_code == 404

    # Bad payload rejected
    rv = client.post(f"/api/sessions/{token}/location", json={"lat": "abc"})
    assert rv.status_code == 400

    # Delete
    rv = client.delete(f"/api/sessions/{session['id']}")
    assert rv.status_code == 200
    assert client.get("/api/sessions").get_json() == []
    # Share page for deleted session returns 404
    rv = client.get(f"/share/{token}")
    assert rv.status_code == 404


def test_session_with_ttl(client):
    """Test session creation with TTL."""
    rv = client.post("/api/sessions", json={"name": "TTL Session", "ttl_hours": 24})
    assert rv.status_code == 201
    session = rv.get_json()
    assert session["expires_at"] is not None


def test_session_pause_resume(client):
    """Test session pause and resume."""
    # Create session
    rv = client.post("/api/sessions", json={"name": "Pause Test"})
    assert rv.status_code == 201
    session = rv.get_json()
    token = session["token"]
    session_id = session["id"]

    # Add a location first
    rv = client.post(
        f"/api/sessions/{token}/location",
        json={"lat": -6.2, "lon": 106.8, "accuracy": 10},
    )
    assert rv.status_code == 201

    # Get CSRF token
    rv = client.get("/api/csrf-token")
    csrf_token = rv.get_json()["csrf_token"]

    # Pause session
    rv = client.post(
        f"/api/sessions/{session_id}/pause",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert rv.status_code == 200

    # Verify session is paused
    rv = client.get("/api/sessions")
    session_data = rv.get_json()[0]
    assert session_data["paused"] is True

    # Try to add location while paused (should fail)
    rv = client.post(
        f"/api/sessions/{token}/location",
        json={"lat": -6.3, "lon": 106.9, "accuracy": 15},
    )
    assert rv.status_code == 400

    # Resume session
    rv = client.post(
        f"/api/sessions/{session_id}/resume",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert rv.status_code == 200

    # Now can add location again
    rv = client.post(
        f"/api/sessions/{token}/location",
        json={"lat": -6.3, "lon": 106.9, "accuracy": 15},
    )
    assert rv.status_code == 201


def test_session_export_gpx(client):
    """Test GPX export."""
    # Create session and add locations
    rv = client.post("/api/sessions", json={"name": "Export Test"})
    session = rv.get_json()
    token = session["token"]

    # Add some locations
    client.post(f"/api/sessions/{token}/location", json={"lat": -6.2, "lon": 106.8})
    client.post(f"/api/sessions/{token}/location", json={"lat": -6.3, "lon": 106.9})

    # Export GPX
    rv = client.get(f"/api/sessions/{session['id']}/export/gpx")
    assert rv.status_code == 200
    assert rv.headers['Content-Type'] == 'application/gpx+xml'
    assert b'Export Test' in rv.data
    assert b'-6.2' in rv.data


def test_session_export_kml(client):
    """Test KML export."""
    rv = client.post("/api/sessions", json={"name": "KML Test"})
    session = rv.get_json()
    token = session["token"]

    client.post(f"/api/sessions/{token}/location", json={"lat": -6.2, "lon": 106.8})

    rv = client.get(f"/api/sessions/{session['id']}/export/kml")
    assert rv.status_code == 200
    assert 'kml' in rv.headers['Content-Type']


def test_session_export_invalid_format(client):
    """Test export with invalid format."""
    rv = client.post("/api/sessions", json={"name": "Test"})
    session = rv.get_json()

    rv = client.get(f"/api/sessions/{session['id']}/export/csv")
    assert rv.status_code == 400


def test_stats_endpoint(client):
    """Test statistics endpoint."""
    # Create some sessions
    client.post("/api/sessions", json={"name": "Stats Test 1"})
    client.post("/api/sessions", json={"name": "Stats Test 2"})

    rv = client.get("/api/stats")
    assert rv.status_code == 200
    stats = rv.get_json()
    assert stats["total_sessions"] == 2


def test_csrf_token_endpoint(client):
    """Test CSRF token generation."""
    rv = client.get("/api/csrf-token")
    assert rv.status_code == 200
    data = rv.get_json()
    assert "csrf_token" in data
    assert len(data["csrf_token"]) > 0


def test_share_page_renders(client):
    session = client.post("/api/sessions", json={"name": "Phone"}).get_json()
    rv = client.get(f"/share/{session['token']}")
    assert rv.status_code == 200
    assert b"Share your location" in rv.data
    assert session["token"].encode() in rv.data


def test_consented_camera_capture(client, tmp_path, monkeypatch):
    """A share token can upload a user-initiated JPEG camera capture."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    monkeypatch.setattr(app_module, "MEDIA_DIR", media_dir)
    session = client.post("/api/sessions", json={"name": "Camera test"}).get_json()

    rv = client.post(
        f"/api/sessions/{session['token']}/media",
        data={"photo": (io.BytesIO(b"jpeg-test-data"), "camera.jpg", "image/jpeg")},
        content_type="multipart/form-data",
    )
    assert rv.status_code == 201
    media = rv.get_json()["media"]
    assert (media_dir / media["filename"]).read_bytes() == b"jpeg-test-data"

    listing = client.get(f"/api/sessions/{session['token']}/media")
    assert listing.status_code == 200
    assert listing.get_json()[0]["id"] == media["id"]
    assert client.get(f"/api/sessions/{session['token']}/media/{media['id']}").status_code == 200


# -------------------------------------------------------------------------- 
# Lookup API routes
# -------------------------------------------------------------------------- 
def test_ip_api_route(client, monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return FAKE_IPWHOIS

    monkeypatch.setattr(services.requests, "get", lambda *a, **k: FakeResp())
    rv = client.get("/api/ip/8.8.8.8")
    assert rv.status_code == 200
    assert rv.get_json()["country"] == "United States"


def test_phone_api_route(client):
    rv = client.get("/api/phone/+6281234567890")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["valid"] is True
    assert data["region_code"] == "ID"


def test_dashboard_renders(client):
    rv = client.get("/")
    assert rv.status_code == 200
    assert b"Geo Tracker" in rv.data


def test_sse_endpoint_streams_sessions_events(client):
    """SSE endpoint serves text/event-stream and pushes session events."""
    # Create a session so a broadcast will fire when a fix arrives
    session = client.post("/api/sessions", json={"name": "SSE Test"}).get_json()

    with client.get("/api/events", buffered=False) as resp:
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"
        gen = resp.response

        # First chunk is the connected comment
        first = next(gen)
        assert b"connected" in first

        # Posting a location should broadcast a 'sessions' event
        client.post(
            f"/api/sessions/{session['token']}/location",
            json={"lat": -6.2, "lon": 106.8, "accuracy": 5},
        )
        event = next(gen)
        assert b"event: sessions" in event
        assert b"SSE Test" in event
        assert b"106.8" in event

        gen.close()
