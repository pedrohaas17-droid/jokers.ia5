"""Authentication primitives: salted password hashes and opaque server-side sessions."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from database import utc_now

PBKDF2_ROUNDS = 310_000
SESSION_DAYS = 14
RESET_MINUTES = 30


def new_id() -> str:
    return secrets.token_urlsafe(18)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ROUNDS,
        salt.hex(),
        derived.hex(),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        ).hex()
        return hmac.compare_digest(actual, digest_hex)
    except (ValueError, TypeError):
        return False


def create_session(conn, user_id: str) -> str:
    raw_token = secrets.token_urlsafe(36)
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
        (
            token_digest(raw_token),
            user_id,
            (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds"),
            utc_now(),
        ),
    )
    return raw_token


def is_guest_email(email: str) -> bool:
    return str(email or "").lower().endswith("@guest.invalid")


def user_for_session(conn, raw_token: str):
    if not raw_token:
        return None
    row = conn.execute(
        """SELECT u.id,u.email,u.full_name,u.created_at,s.expires_at
           FROM sessions s JOIN users u ON u.id=s.user_id
           WHERE s.token_hash=?""",
        (token_digest(raw_token),),
    ).fetchone()
    if not row:
        return None
    try:
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_digest(raw_token),))
            return None
    except ValueError:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_digest(raw_token),))
        return None
    user = {k: row[k] for k in ("id", "email", "full_name", "created_at")}
    user["is_guest"] = is_guest_email(user["email"])
    return user


def create_reset(conn, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO password_resets(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
        (
            token_digest(token),
            user_id,
            (now + timedelta(minutes=RESET_MINUTES)).isoformat(timespec="seconds"),
            utc_now(),
        ),
    )
    return token


def consume_reset(conn, token: str, new_password_hash: str) -> bool:
    digest = token_digest(token)
    row = conn.execute(
        "SELECT user_id,expires_at,used_at FROM password_resets WHERE token_hash=?", (digest,)
    ).fetchone()
    if not row or row["used_at"]:
        return False
    try:
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            return False
    except ValueError:
        return False
    now = utc_now()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (new_password_hash, row["user_id"]))
    conn.execute("UPDATE password_resets SET used_at=? WHERE token_hash=?", (now, digest))
    # Resetting the password revokes all existing sessions for safety.
    conn.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
    return True
