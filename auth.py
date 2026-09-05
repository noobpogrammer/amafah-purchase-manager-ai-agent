import os
from typing import Dict

from fastapi import HTTPException, Request
import jwt

import db


def verify_jwt(token: str) -> Dict:
    """Verify a Supabase-issued session JWT (HS256 HMAC, not RSA/JWKS)."""
    secret = os.getenv("SUPABASE_JWT_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="SUPABASE_JWT_SECRET is not configured")

    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


async def get_current_user(request: Request):
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = auth_header.split(None, 1)[1].strip()

    payload = verify_jwt(token)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Token missing sub claim")

    profile = db.get_profile_by_id(sub)
    if not profile:
        raise HTTPException(status_code=401, detail="No profile found for authenticated user")

    return {
        "user_id": sub,
        "client_id": profile.get("client_id"),
        "role": profile.get("role") or "member",
    }


__all__ = ["get_current_user", "verify_jwt"]
