"""Geo Tracker — Flask web app.

Combines live GPS location sharing, IP geolocation, and phone number lookup
in a single dashboard with a Leaflet/OpenStreetMap map.

Run locally:
    python app.py            (http://127.0.0.1:5000)
    flask --app app run --host 0.0.0.0   (accessible on your LAN)
"""

import json
import os
import queue
import secrets
import threading
import time
from functools import wraps
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, abort, send_from_directory, make_response, redirect, url_for, session as flask_session

import io
import piexif
from PIL import Image

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
# Ensure Flask's secret key is always set
app.secret_key = config.SECRET_KEY

db.init_db()

MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", str(config.BASE_DIR / "uploads")))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
MAX_CAMERA_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_CAMERA_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

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
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' https://*.tile.openstreetmap.org https://unpkg.com data:; "
        "connect-src 'self'; "
        "font-src 'self' https://unpkg.com;"
    )

    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(self), geolocation=(self)'

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
    """Validate a CSRF token.

    Tokens are valid for 1 hour and are reusable within that window so a
    share page can keep uploading camera captures with the token it was
    given at page load.
    """
    if not token:
        return False
    with _csrf_lock:
        if token in _csrf_tokens and time.time() - _csrf_tokens[token] < 3600:
            return True
        # Drop stale tokens lazily
        stale = [t for t, ts in _csrf_tokens.items() if time.time() - ts >= 3600]
        for t in stale:
            del _csrf_tokens[t]
    return False

def csrf_protect(f):
    """Decorator for CSRF protection on state-changing endpoints."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method in ['POST', 'PUT', 'DELETE']:
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
                pass

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
# Login / Logout
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            return render_template("login.html", error="Username and password are required")

        # Check against stored credentials
        stored_user = app.config.get("ADMIN_USER", "admin")
        stored_pass = app.config.get("ADMIN_PASS", "admin123")

        if username == stored_user and password == stored_pass:
            flask_session["logged_in"] = True
            flask_session["user"] = username
            return redirect(url_for("index"))
        else:
            return render_template("login.html", error="Invalid username or password")

    return render_template("login.html")

@app.route("/logout")
def logout():
    flask_session.clear()
    return redirect(url_for("login"))

@app.before_request
def require_login():
    """Protect the dashboard and its APIs. Share pages stay public.

    Everything needs a login except:
      - static assets, the login page itself
      - /share/<token> pages and the token-based share APIs
        (location updates and camera media), which are the whole point
        of sharing a link with another device
    """
    if app.config.get("TESTING"):
        return
    if request.path.startswith("/static") or request.path == "/login" or request.path == "/favicon.ico":
        return
    if request.path.startswith("/share/"):
        return
    # Token-based share APIs stay open: location updates + camera media
    if request.path.startswith("/api/sessions/"):
        rest = request.path[len("/api/sessions/"):]
        if "/location" in rest or "/media" in rest:
            return
    if not flask_session.get("logged_in"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required"}), 401
        return redirect(url_for("login"))

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

def _decimal_to_dms_rational(value):
    """
    Convert decimal GPS coordinate into EXIF DMS rational format.

    Example:
        -6.208763

    becomes approximately:
        6° 12' 31.5468"
    """
    value = abs(float(value))

    degrees = int(value)

    minutes_float = (value - degrees) * 60
    minutes = int(minutes_float)

    seconds = (minutes_float - minutes) * 60

    return (
        (degrees, 1),
        (minutes, 1),
        (int(round(seconds * 1_000_000)), 1_000_000),
    )


def _add_gps_exif_to_jpeg(data, lat, lon):
    """
    Embed latitude/longitude into JPEG EXIF metadata.

    Returns modified JPEG bytes.
    """

    image = Image.open(io.BytesIO(data))

    # Make sure output is JPEG-compatible
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    try:
        existing_exif = image.info.get("exif")

        if existing_exif:
            exif_dict = piexif.load(existing_exif)
        else:
            exif_dict = {
                "0th": {},
                "Exif": {},
                "GPS": {},
                "1st": {},
                "thumbnail": None,
            }
    except Exception:
        exif_dict = {
            "0th": {},
            "Exif": {},
            "GPS": {},
            "1st": {},
            "thumbnail": None,
        }

    gps_ifd = exif_dict.setdefault("GPS", {})

    gps_ifd[piexif.GPSIFD.GPSLatitudeRef] = (
        b"N" if lat >= 0 else b"S"
    )

    gps_ifd[piexif.GPSIFD.GPSLatitude] = (
        _decimal_to_dms_rational(lat)
    )

    gps_ifd[piexif.GPSIFD.GPSLongitudeRef] = (
        b"E" if lon >= 0 else b"W"
    )

    gps_ifd[piexif.GPSIFD.GPSLongitude] = (
        _decimal_to_dms_rational(lon)
    )

    exif_bytes = piexif.dump(exif_dict)

    output = io.BytesIO()

    image.save(
        output,
        format="JPEG",
        quality=95,
        exif=exif_bytes,
    )

    return output.getvalue()

@app.route("/api/sessions/<token>/media", methods=["POST"])
# @rate_limit
@csrf_protect
def upload_camera_capture(token):
    """
    Save a user-initiated camera photo for a share session.

    If valid GPS coordinates are included with a JPEG photo,
    latitude/longitude are also embedded into the JPEG EXIF.
    """

    # ========================================================
    # SESSION VALIDATION
    # ========================================================

    session = db.get_session_by_token(token)

    if session is None:
        return jsonify({
            "error": "Session not found"
        }), 404

    if session.get("paused"):
        return jsonify({
            "error": "Session is paused"
        }), 400


    # ========================================================
    # PHOTO VALIDATION
    # ========================================================

    photo = request.files.get("photo")

    if photo is None or not photo.filename:
        return jsonify({
            "error": "A camera photo is required"
        }), 400

    content_type = photo.mimetype

    extension = ALLOWED_CAMERA_TYPES.get(
        content_type
    )

    if extension is None:
        return jsonify({
            "error":
                "Only JPEG, PNG, and WebP photos are accepted"
        }), 400


    # ========================================================
    # READ PHOTO
    # ========================================================

    data = photo.read(
        MAX_CAMERA_UPLOAD_BYTES + 1
    )

    if (
        not data
        or len(data) > MAX_CAMERA_UPLOAD_BYTES
    ):
        return jsonify({
            "error":
                "Photo must be between 1 byte and 5 MB"
        }), 400


    # ========================================================
    # GPS DATA
    # ========================================================

    lat = None
    lon = None
    accuracy = None

    try:
        raw_lat = request.form.get("lat")
        raw_lon = request.form.get("lon")
        raw_accuracy = request.form.get("accuracy")

        if (
            raw_lat is not None
            and raw_lon is not None
        ):
            lat = float(raw_lat)
            lon = float(raw_lon)

            valid, error = validate_coordinates(
                lat,
                lon
            )

            if not valid:
                lat = None
                lon = None

        if raw_accuracy:
            accuracy = float(
                raw_accuracy
            )

    except (
        TypeError,
        ValueError
    ):
        lat = None
        lon = None
        accuracy = None


    # ========================================================
    # EMBED GPS INTO JPEG EXIF
    # ========================================================

    gps_embedded = False

    if (
        lat is not None
        and lon is not None
        and content_type
        in (
            "image/jpeg",
            "image/jpg",
        )
    ):
        try:
            data = _add_gps_exif_to_jpeg(
                data,
                lat,
                lon,
            )

            gps_embedded = True

        except Exception as exc:
            # Do not reject the upload if EXIF writing fails.
            # Coordinates are still stored in the database.
            app.logger.warning(
                "Could not add GPS EXIF: %s",
                exc,
            )


    # ========================================================
    # SAVE FILE
    # ========================================================

    filename = (
        f"{secrets.token_urlsafe(20)}"
        f"{extension}"
    )

    filepath = (
        MEDIA_DIR / filename
    )

    filepath.write_bytes(
        data
    )


    # ========================================================
    # DATABASE
    # ========================================================

    media = db.add_media(
        session["id"],
        filename,
        content_type,
        lat=lat,
        lon=lon,
    )


    # ========================================================
    # BROADCAST
    # ========================================================

    _broadcast_sessions()


    # ========================================================
    # RESPONSE
    # ========================================================

    return jsonify({
        "ok": True,
        "media": media,
        "gps": {
            "lat": lat,
            "lon": lon,
            "accuracy": accuracy,
            "embedded_in_exif": gps_embedded,
        },
    }), 201

@app.route("/api/sessions/<token>/media", methods=["GET"])
@rate_limit
def session_media(token):
    session = db.get_session_by_token(token)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(db.list_media(session["id"]))

@app.route("/api/sessions/<token>/media/<int:media_id>")
@rate_limit
def get_media_file(token, media_id):
    session = db.get_session_by_token(token)
    media = db.get_media(media_id)
    if media is None or session is None or media["session_id"] != session["id"]:
        abort(404)
    return send_from_directory(MEDIA_DIR, media["filename"], mimetype=media["content_type"])

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
