import os
from typing import Dict, Optional

from fastapi import HTTPException, Request
import jwt
from jwt import PyJWKClient

import db

_jwks_client: Optional[PyJWKClient] = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        jwks_url = os.getenv("SUPABASE_JWKS_URL") or f"{supabase_url}/auth/v1/.well-known/jwks.json"
        if not supabase_url and not os.getenv("SUPABASE_JWKS_URL"):
            raise HTTPException(status_code=500, detail="SUPABASE_URL is not configured")
        _jwks_client = PyJWKClient(jwks_url)
    return _jwks_client


def verify_jwt(token: str) -> Dict:
    """Verify a Supabase session JWT.

    This project issues ES256 access tokens (see Auth JWKS). Legacy HS256
    tokens are still accepted via SUPABASE_JWT_SECRET.
    """
    try:
        header = jwt.get_unverified_header(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    alg = header.get("alg")

    try:
        if alg == "HS256":
            secret = os.getenv("SUPABASE_JWT_SECRET")
            if not secret:
                raise HTTPException(status_code=500, detail="SUPABASE_JWT_SECRET is not configured")
            return jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience="authenticated",
            )

        if alg in ("ES256", "RS256"):
            signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=[alg],
                audience="authenticated",
            )

        raise HTTPException(
            status_code=401,
            detail=f"Invalid token: The specified alg value is not allowed ({alg})",
        )
    except HTTPException:
        raise
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
