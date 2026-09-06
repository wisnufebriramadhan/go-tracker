"""Input validation for Geo Tracker application.

Provides validation functions for API inputs with clear error messages.
"""

import re
from typing import Optional, Tuple
from exceptions import ValidationError


def validate_ip_address(ip: str) -> Tuple[bool, Optional[str]]:
    """Validate an IP address (IPv4 or IPv6).
    
    Args:
        ip: IP address string to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not ip or not ip.strip():
        return False, "IP address is required"

    ip = ip.strip()

    # IPv4 pattern
    ipv4_pattern = r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$'
    ipv4_match = re.match(ipv4_pattern, ip)
    if ipv4_match:
        octets = [int(octet) for octet in ipv4_match.groups()]
        if all(0 <= octet <= 255 for octet in octets):
            return True, None
        return False, "Invalid IPv4 address"

    # IPv6 pattern (simplified)
    ipv6_pattern = r'^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$'
    ipv6_pattern_short = r'^([0-9a-fA-F]{1,4}:){1,7}:$'
    ipv6_pattern_double = r'^([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}$'

    if (re.match(ipv6_pattern, ip) or 
        re.match(ipv6_pattern_short, ip) or 
        re.match(ipv6_pattern_double, ip)):
        return True, None

    # Allow domain names for DNS lookup
    domain_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$'
    if re.match(domain_pattern, ip):
        return True, None

    return False, "Invalid IP address or domain name"


def validate_phone_number(number: str) -> Tuple[bool, Optional[str]]:
    """Validate a phone number format.
    
    Args:
        number: Phone number string to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not number or not number.strip():
        return False, "Phone number is required"

    number = number.strip()

    # Remove spaces, dashes, parentheses
    cleaned = re.sub(r'[\s\-\(\)]', '', number)

    # Must start with + for international format
    if not cleaned.startswith('+'):
        # Allow local format (e.g., 081234567890)
        if re.match(r'^0[1-9]\d{6,14}$', cleaned):
            return True, None
        return False, "Phone number must start with + for international format or 0 for local format"

    # International format: + followed by country code and number
    if re.match(r'^\+[1-9]\d{6,14}$', cleaned):
        return True, None

    return False, "Invalid phone number format"


def validate_coordinates(lat: float, lon: float) -> Tuple[bool, Optional[str]]:
    """Validate GPS coordinates.
    
    Args:
        lat: Latitude value
        lon: Longitude value
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return False, "Coordinates must be numbers"

    if not (-90 <= lat <= 90):
        return False, "Latitude must be between -90 and 90"

    if not (-180 <= lon <= 180):
        return False, "Longitude must be between -180 and 180"

    return True, None


def validate_session_name(name: str) -> Tuple[bool, Optional[str]]:
    """Validate a session name.
    
    Args:
        name: Session name string
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if name is None:
        return True, None  # Name is optional

    name = name.strip()

    if len(name) > 100:
        return False, "Session name must be 100 characters or less"

    # Check for potentially harmful characters
    if re.search(r'[<>"\';]', name):
        return False, "Session name contains invalid characters"

    return True, None


def validate_accuracy(accuracy: Optional[float]) -> Tuple[bool, Optional[str]]:
    """Validate accuracy value.
    
    Args:
        accuracy: Accuracy value in meters
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if accuracy is None:
        return True, None

    try:
        accuracy = float(accuracy)
    except (TypeError, ValueError):
        return False, "Accuracy must be a number"

    if accuracy < 0:
        return False, "Accuracy cannot be negative"

    if accuracy > 100000:  # 100km seems reasonable max
        return False, "Accuracy value seems unreasonably high"

    return True, None


def validate_session_id(session_id: any) -> Tuple[bool, Optional[str], Optional[int]]:
    """Validate and convert session ID.
    
    Args:
        session_id: Session ID (can be string or int)
        
    Returns:
        Tuple of (is_valid, error_message, converted_id)
    """
    if session_id is None:
        return False, "Session ID is required", None

    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return False, "Session ID must be a number", None

    if session_id <= 0:
        return False, "Session ID must be a positive number", None

    return True, None, session_id


def sanitize_string(value: str, max_length: int = 1000) -> str:
    """Sanitize a string input by removing potentially harmful content.
    
    Args:
        value: String to sanitize
        max_length: Maximum allowed length
        
    Returns:
        Sanitized string
    """
    if value is None:
        return ""

    # Strip whitespace
    value = value.strip()

    # Truncate to max length
    value = value[:max_length]

    # Remove null bytes
    value = value.replace('\x00', '')

    return value
