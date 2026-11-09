"""Authenticator-app codes (RFC 6238 TOTP over RFC 4226 HOTP), recovery codes, and a small secret box
for keeping the TOTP secret at rest (D-142). Standard library only; pure functions.

The secret box is encrypt-then-MAC with HMAC-SHA256 as the PRF: a keystream HMAC(enc_key, nonce ‖ i) is
XORed with the plaintext, and HMAC(mac_key, nonce ‖ ciphertext) authenticates it. Both keys are derived
from the app's SECRET_KEY, so a database copy alone doesn't give the secrets away.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote, urlencode

PERIOD, DIGITS = 30, 6
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # recovery codes: no 0/O, 1/I


def new_secret() -> str:
    """A fresh 160-bit secret, base32 (what authenticator apps take)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret_b32: str) -> bytes:
    padded = secret_b32.upper() + "=" * (-len(secret_b32) % 8)
    return base64.b32decode(padded)


def hotp(key: bytes, counter: int, digits: int = DIGITS, digest=hashlib.sha1) -> str:
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def step_at(unix_time: float) -> int:
    return int(unix_time // PERIOD)


def code_at(secret_b32: str, unix_time: float) -> str:
    return hotp(_key(secret_b32), step_at(unix_time))


def verify(
    secret_b32: str, code: str, unix_time: float, last_step: int | None, window: int = 1
) -> int | None:
    """The time step `code` belongs to (now ± `window` steps), or None. A step at or before `last_step` (a
    code already used) never counts again."""
    code = "".join(code.split())
    if len(code) != DIGITS or not code.isdigit():
        return None
    now, key = step_at(unix_time), _key(secret_b32)
    for step in range(now - window, now + window + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(hotp(key, step), code):
            return step
    return None


def otpauth_uri(secret_b32: str, account: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account}")
    query = urlencode({"secret": secret_b32, "issuer": issuer, "digits": DIGITS, "period": PERIOD})
    return f"otpauth://totp/{label}?{query}"


def recovery_codes(n: int = 10) -> list[str]:
    """`n` single-use codes like 7KQ4-M2XP (40 bits each)."""
    return [
        "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(2)) for _ in range(n)
    ]


def normalise_recovery(code: str) -> str:
    raw = "".join(c for c in code.upper() if c.isalnum())
    return f"{raw[:4]}-{raw[4:]}" if len(raw) == 8 else raw


# ---------------------------------------------------------------- the secret box


def _keys(secret_key: str) -> tuple[bytes, bytes]:
    root = hashlib.sha256(("slim-lims mfa v1:" + secret_key).encode()).digest()
    return hmac.new(root, b"enc", hashlib.sha256).digest(), hmac.new(root, b"mac", hashlib.sha256).digest()


def _stream(key: bytes, nonce: bytes, length: int) -> bytes:
    out, i = b"", 0
    while len(out) < length:
        out += hmac.new(key, nonce + struct.pack(">I", i), hashlib.sha256).digest()
        i += 1
    return out[:length]


def seal(secret_key: str, plaintext: str) -> str:
    enc, mac = _keys(secret_key)
    nonce = secrets.token_bytes(16)
    data = plaintext.encode()
    cipher = bytes(a ^ b for a, b in zip(data, _stream(enc, nonce, len(data)), strict=True))
    tag = hmac.new(mac, nonce + cipher, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + tag + cipher).decode()


def unseal(secret_key: str, token: str) -> str:
    """The plaintext, or ValueError if the box was altered or sealed under another key."""
    enc, mac = _keys(secret_key)
    raw = base64.urlsafe_b64decode(token.encode())
    nonce, tag, cipher = raw[:16], raw[16:48], raw[48:]
    if not hmac.compare_digest(tag, hmac.new(mac, nonce + cipher, hashlib.sha256).digest()):
        raise ValueError("the sealed secret doesn't match this SECRET_KEY")
    return bytes(a ^ b for a, b in zip(cipher, _stream(enc, nonce, len(cipher)), strict=True)).decode()
