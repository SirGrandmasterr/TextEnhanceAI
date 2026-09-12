"""Bearer-token authentication with constant-time comparison."""

import hmac
import secrets

KEY_PREFIX = "teai_"


def generate_key(nbytes=32):
    """Return a new random key suitable for RELAY_AGENT_KEYS / RELAY_CLIENT_KEYS."""
    return KEY_PREFIX + secrets.token_urlsafe(nbytes)


def bearer_token(request):
    """Extract the bearer token from an aiohttp request (or ``None``)."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def lookup(token, keys):
    """Return the key's name when ``token`` matches one of ``keys`` (key -> name)."""
    if not token:
        return None
    match = None
    for key, name in keys.items():
        # Compare every key so timing does not reveal which prefix matched.
        if hmac.compare_digest(key.encode("utf-8"), token.encode("utf-8")):
            match = name
    return match
