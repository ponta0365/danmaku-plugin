# pkce.py
import secrets
import hashlib
import base64

def generate_code_verifier(length: int = 64) -> str:
    """
    Generates a high-entropy random string (43 to 128 chars) as code_verifier.
    """
    if length < 43 or length > 128:
        length = 64
    # Allowed characters: [A-Z], [a-z], [0-9], "-", ".", "_", "~"
    allowed_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    return "".join(secrets.choice(allowed_chars) for _ in range(length))

def generate_code_challenge(verifier: str) -> str:
    """
    Generates a code_challenge using SHA256 of the verifier, encoded in base64url without padding.
    """
    sha256_bytes = hashlib.sha256(verifier.encode('ascii')).digest()
    challenge_encoded = base64.urlsafe_b64encode(sha256_bytes).decode('ascii')
    # Strip padding '=' characters for base64url standard compliance
    return challenge_encoded.rstrip('=')

def generate_state(length: int = 16) -> str:
    """
    Generates a secure random state string for CSRF protection.
    """
    return secrets.token_urlsafe(length)
