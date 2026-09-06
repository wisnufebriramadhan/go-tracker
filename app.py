"""Geo Tracker — Flask web app.

Combines live GPS location sharing, IP geolocation, and phone number lookup
in a single dashboard with a Leaflet/OpenStreetMap map.

Run locally:
    python app.py            (http://127.0.0.1:5000)
    flask --app app run --host 0.0.0.0   (accessible on your LAN)
"""

import json
import queue
import threading
import time
from functools import wraps
from collections import defaultdict
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template, request, abort
from flask import make_response

import db
import services
from config import load_config
from exceptions import (
    DatabaseError, SessionNotFoundError, SessionExpiredError,
    SessionPausedError, InvalidLocationError, ValidationError,
    APIError, RateLimitError, CSRFError
)
from validators import (
    validate_ip_address, validate_phone_number, validate_coordinates,
    validate_session_name, validate_accuracy, validate_session_id, sanitize_string
)

app = Flask(__name__)
config = load_config()
app.config.from_object(config)

db.init_db()

# ---------------------------------------------------------------------------
# Rate limiting (simple in-memory implementation)
# ---------------------------------------------------------------------------
_rate_limit_data = defaultdict(list)
_rate_limit_lock = threading.Lock()


def check_rate_limit(key, max_requests=100, window_seconds=3600):
    """Check if a request should be rate limited.
    
    Args:
        key: Rate limit key (e.g., IP address)
        max_requests: Maximum requests allowed in window
        window_seconds: Time window in seconds
        
    Returns:
        True if allowed, False if rate limited
    """
    if not config.RATELIMIT_ENABLED:
        return True

    now = time.time()
    with _rate_limit_lock:
        # Clean old entries
        _rate_limit_data[key] = [
            t for t in _rate_limit_data[key] if now - t < window_seconds
        ]

        if len(_rate_limit_data[key]) >= max_requests:
            return False

        _rate_limit_data[key].append(now)
        return True


def rate_limit(f):
    """Decorator for rate limiting endpoints."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        client_ip = request.remote_addr
        if not check_rate_limit(client_ip):
            return jsonify({
                "error": "Rate limit exceeded. Please try again later.",
                "retry_after": 3600
            }), 429
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
@app.after_request
def add_security_headers(response):
    """Add security headers to all responses."""
    # Content Security Policy
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' https://*.tile.openstreetmap.org https://unpkg.com data:; "
        "connect-src 'self'; "
        "font-src 'self' https://unpkg.com;"
    )

    # Other security headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'

    # HSTS for HTTPS (only in production)
    if not config.DEBUG:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    return response


# ---------------------------------------------------------------------------
# CSRF protection (simple token-based)
# ---------------------------------------------------------------------------
_csrf_tokens = {}
_csrf_lock = threading.Lock()


def generate_csrf_token():
    """Generate a CSRF token."""
    import secrets
    token = secrets.token_hex(32)
    with _csrf_lock:
        _csrf_tokens[token] = time.time()
    return token


def validate_csrf_token(token):
    """Validate a CSRF token."""
    if not token:
        return False
    with _csrf_lock:
        if token in _csrf_tokens:
            # Token expires after 1 hour
            if time.time() - _csrf_tokens[token] < 3600:
                del _csrf_tokens[token]
                return True
            del _csrf_tokens[token]
    return False


def csrf_protect(f):
    """Decorator for CSRF protection on state-changing endpoints."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method in ['POST', 'PUT', 'DELETE']:
            # Skip CSRF check in testing mode
            if app.config.get('TESTING'):
                return f(*args, **kwargs)
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
            if not validate_csrf_token(token):
                return jsonify({"error": "Invalid or missing CSRF token"}), 403
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------------------------
# Server-Sent Events (SSE): push session changes to open dashboards instantly
# ---------------------------------------------------------------------------
_sse_clients = []
_sse_lock = threading.Lock()


def _broadcast_sessions():
    """Push the current session list to every connected SSE client."""
    payload = json.dumps(db.get_sessions_with_status())
    with _sse_lock:
        for q in list(_sse_clients):
            try:
                q.put_nowait(("sessions", payload))
            except queue.Full:
                pass  # slow client — skip this update


@app.route("/api/events")
def sse_events():
    """EventSource stream: emits a 'sessions' event whenever data changes."""
    def gen():
        q = queue.Queue(maxsize=100)
        with _sse_lock:
            _sse_clients.append(q)
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event_type, data = q.get(timeout=15)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: {event_type}\ndata: {data}\n\n"
        finally:
            with _sse_lock:
                if q in _sse_clients:
                    _sse_clients.remove(q)

    resp = Response(gen(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/share/<token>")
def share(token):
    session = db.get_session_by_token(token)
    if session is None:
        return render_template("share.html", error="Session not found"), 404

    # Check if session is expired
    if session.get("expires_at"):
        try:
            expires = datetime.fromisoformat(session["expires_at"])
            if datetime.now(timezone.utc) > expires:
                return render_template("share.html", error="Session has expired"), 410
        except ValueError:
            pass

    # Check if session is paused
    if session.get("paused"):
        return render_template("share.html", error="Session is paused"), 403

    # Generate CSRF token for the page
    csrf_token = generate_csrf_token()

    return render_template("share.html", session=session, csrf_token=csrf_token)


# ---------------------------------------------------------------------------
# Sessions API
# ---------------------------------------------------------------------------
@app.route("/api/sessions", methods=["GET", "POST"])
@rate_limit
def sessions_api():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        name = sanitize_string(data.get("name"), max_length=100)

        # Validate name
        valid, error = validate_session_name(name)
        if not valid:
            return jsonify({"error": error}), 400

        # Optional TTL in hours
        ttl_hours = data.get("ttl_hours")
        if ttl_hours is not None:
            try:
                ttl_hours = int(ttl_hours)
                if ttl_hours < 0 or ttl_hours > 8760:  # Max 1 year
                    return jsonify({"error": "TTL must be between 0 and 8760 hours"}), 400
            except (TypeError, ValueError):
                return jsonify({"error": "TTL must be a number"}), 400

        session = db.create_session(name=name, ttl_hours=ttl_hours)
        _broadcast_sessions()
        return jsonify(session), 201

    # GET - list sessions
    sessions = db.get_sessions_with_status()
    return jsonify(sessions)


@app.route("/api/sessions/<int:session_id>", methods=["GET", "DELETE"])
@rate_limit
def session_api(session_id):
    # Validate session ID
    valid, error, session_id = validate_session_id(session_id)
    if not valid:
        return jsonify({"error": error}), 400

    session = db.get_session_by_id(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    if request.method == "DELETE":
        db.delete_session(session_id)
        _broadcast_sessions()
        return jsonify({"ok": True})

    # GET - session detail
    trail = db.get_trail(session["token"])
    latest = trail[-1] if trail else None
    return jsonify({
        "session": session,
        "points": len(trail),
        "latest": latest,
        "trail": trail
    })


@app.route("/api/sessions/<token>/location", methods=["POST"])
@rate_limit
def save_location(token):
    """Receive a GPS fix from the share page."""
    data = request.get_json(silent=True) or {}

    lat = data.get("lat")
    lon = data.get("lon")

    if lat is None or lon is None:
        return jsonify({"error": "lat and lon are required"}), 400

    # Validate coordinates
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon must be numbers"}), 400

    valid, error = validate_coordinates(lat, lon)
    if not valid:
        return jsonify({"error": error}), 400

    accuracy = data.get("accuracy")
    if accuracy is not None:
        try:
            accuracy = float(accuracy)
        except (TypeError, ValueError):
            accuracy = None

        valid, error = validate_accuracy(accuracy)
        if not valid:
            return jsonify({"error": error}), 400

    source = sanitize_string(data.get("source", "gps"), max_length=50)

    try:
        fix = db.save_location(token, lat, lon, accuracy=accuracy, source=source)
    except InvalidLocationError as e:
        return jsonify({"error": str(e)}), 400
    except SessionNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except DatabaseError as e:
        return jsonify({"error": "Database error"}), 500

    if fix is None:
        return jsonify({"error": "Session not found"}), 404

    _broadcast_sessions()
    return jsonify({"ok": True, "fix": fix}), 201


# ---------------------------------------------------------------------------
# Session control endpoints (pause/resume)
# ---------------------------------------------------------------------------
@app.route("/api/sessions/<int:session_id>/pause", methods=["POST"])
@rate_limit
@csrf_protect
def pause_session(session_id):
    """Pause a session (stops accepting location updates)."""
    valid, error, session_id = validate_session_id(session_id)
    if not valid:
        return jsonify({"error": error}), 400

    session = db.get_session_by_id(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    db.update_session(session_id, paused=1)
    _broadcast_sessions()
    return jsonify({"ok": True, "paused": True})


@app.route("/api/sessions/<int:session_id>/resume", methods=["POST"])
@rate_limit
@csrf_protect
def resume_session(session_id):
    """Resume a paused session."""
    valid, error, session_id = validate_session_id(session_id)
    if not valid:
        return jsonify({"error": error}), 400

    session = db.get_session_by_id(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    db.update_session(session_id, paused=0)
    _broadcast_sessions()
    return jsonify({"ok": True, "paused": False})


# ---------------------------------------------------------------------------
# Export endpoints
# ---------------------------------------------------------------------------
@app.route("/api/sessions/<int:session_id>/export/<format>")
@rate_limit
def export_session(session_id, format):
    """Export session trail to GPX or KML format."""
    valid, error, session_id = validate_session_id(session_id)
    if not valid:
        return jsonify({"error": error}), 400

    session = db.get_session_by_id(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    if format not in ["gpx", "kml"]:
        return jsonify({"error": "Format must be 'gpx' or 'kml'"}), 400

    trail = db.get_trail(session["token"])
    if not trail:
        return jsonify({"error": "No location data to export"}), 404

    if format == "gpx":
        content = _generate_gpx(session, trail)
        content_type = "application/gpx+xml"
        filename = f"{session['name']}.gpx"
    else:
        content = _generate_kml(session, trail)
        content_type = "application/vnd.google-earth.kml+xml"
        filename = f"{session['name']}.kml"

    response = make_response(content)
    response.headers['Content-Type'] = content_type
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _generate_gpx(session, trail):
    """Generate GPX XML content."""
    import xml.etree.ElementTree as ET

    gpx = ET.Element("gpx")
    gpx.set("version", "1.1")
    gpx.set("creator", "Geo Tracker")
    gpx.set("xmlns", "http://www.topografix.com/GPX/1/1")

    metadata = ET.SubElement(gpx, "metadata")
    name = ET.SubElement(metadata, "name")
    name.text = session["name"]

    trk = ET.SubElement(gpx, "trk")
    trk_name = ET.SubElement(trk, "name")
    trk_name.text = session["name"]

    trkseg = ET.SubElement(trk, "trkseg")
    for point in trail:
        trkpt = ET.SubElement(trkseg, "trkpt")
        trkpt.set("lat", str(point["lat"]))
        trkpt.set("lon", str(point["lon"]))

        ele = ET.SubElement(trkpt, "ele")
        ele.text = "0"

        time_elem = ET.SubElement(trkpt, "time")
        time_elem.text = point["timestamp"]

    return ET.tostring(gpx, encoding="unicode", xml_declaration=True)


def _generate_kml(session, trail):
    """Generate KML XML content."""
    import xml.etree.ElementTree as ET

    kml = ET.Element("kml")
    kml.set("xmlns", "http://www.opengis.net/kml/2.2")

    document = ET.SubElement(kml, "Document")
    doc_name = ET.SubElement(document, "name")
    doc_name.text = session["name"]

    # Style for the track
    style = ET.SubElement(document, "Style")
    style.set("id", "trackStyle")
    line_style = ET.SubElement(style, "LineStyle")
    color = ET.SubElement(line_style, "color")
    color.text = "ff0000ff"  # Red
    width = ET.SubElement(line_style, "width")
    width.text = "3"

    # Create track
    placemark = ET.SubElement(document, "Placemark")
    pm_name = ET.SubElement(placemark, "name")
    pm_name.text = session["name"]
    style_url = ET.SubElement(placemark, "styleUrl")
    style_url.text = "#trackStyle"

    track = ET.SubElement(placemark, "Track")
    track.set("id", "track")

    for point in trail:
        when = ET.SubElement(track, "when")
        when.text = point["timestamp"]

        coord = ET.SubElement(track, "gx:coord")
        coord.text = f"{point['lon']} {point['lat']} 0"

    return ET.tostring(kml, encoding="unicode", xml_declaration=True)


# ---------------------------------------------------------------------------
# Lookup APIs
# ---------------------------------------------------------------------------
@app.route("/api/ip/<path:address>")
@rate_limit
def ip_api(address):
    # Validate IP/domain
    valid, error = validate_ip_address(address)
    if not valid:
        return jsonify({"error": error}), 400

    try:
        result = services.ip_lookup(address)
        return jsonify(result)
    except APIError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        app.logger.error(f"IP lookup error: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/phone/<path:number>")
@rate_limit
def phone_api(number):
    # Validate phone number format
    valid, error = validate_phone_number(number)
    if not valid:
        return jsonify({"error": error}), 400

    try:
        result = services.phone_lookup(number)
        return jsonify(result)
    except APIError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        app.logger.error(f"Phone lookup error: {e}")
        return jsonify({"error": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# Stats API
# ---------------------------------------------------------------------------
@app.route("/api/stats")
@rate_limit
def stats_api():
    """Get summary statistics about sessions."""
    try:
        stats = db.get_session_stats()
        return jsonify(stats)
    except Exception as e:
        app.logger.error(f"Stats error: {e}")
        return jsonify({"error": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# CSRF token endpoint
# ---------------------------------------------------------------------------
@app.route("/api/csrf-token")
def csrf_token():
    """Get a new CSRF token."""
    token = generate_csrf_token()
    return jsonify({"csrf_token": token})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return render_template("share.html", error="Page not found"), 404


@app.errorhandler(429)
def rate_limit_exceeded(e):
    return jsonify({
        "error": "Rate limit exceeded",
        "retry_after": 3600
    }), 429


@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error"}), 500


@app.errorhandler(DatabaseError)
def handle_database_error(e):
    return jsonify({"error": "Database error"}), 500


@app.errorhandler(ValidationError)
def handle_validation_error(e):
    return jsonify({"error": str(e)}), 400


@app.errorhandler(APIError)
def handle_api_error(e):
    return jsonify({"error": str(e)}), 502


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    return jsonify({"error": "CSRF validation failed"}), 403


# ---------------------------------------------------------------------------
# Cleanup task (runs in background)
# ---------------------------------------------------------------------------
def cleanup_expired_sessions():
    """Background task to delete expired sessions."""
    while True:
        try:
            db.delete_expired_sessions()
        except Exception as e:
            app.logger.error(f"Cleanup error: {e}")
        time.sleep(300)  # Run every 5 minutes


# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_expired_sessions, daemon=True)
cleanup_thread.start()


if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
