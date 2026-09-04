import os
import time
from typing import Dict

import requests
from fastapi import Depends, HTTPException, Request

import jwt

import db

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
JWKS_URL = os.getenv("SUPABASE_JWKS_URL") or f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
JWT_AUD = os.getenv("SUPABASE_JWT_AUD")


class JWKSCache:
    def __init__(self):
        self._jwks = None
        self._fetched_at = 0

    def get_jwks(self):
        if self._jwks and time.time() - self._fetched_at < 300:
            return self._jwks
        try:
            r = requests.get(JWKS_URL, timeout=5)
            r.raise_for_status()
            self._jwks = r.json()
            self._fetched_at = time.time()
            return self._jwks
        except Exception:
            return None


jwks_cache = JWKSCache()


def verify_jwt(token: str) -> Dict:
    # Lightweight verifier: decode without verification to get kid, then verify using jwks
    try:
        unverified = jwt.get_unverified_header(token)
        jwks = jwks_cache.get_jwks()
        if not jwks:
            raise HTTPException(status_code=401, detail="Unable to fetch JWKS")

        # Find matching key
        keys = jwks.get("keys", [])
        key = None
        for k in keys:
            if k.get("kid") == unverified.get("kid"):
                key = k
                break
        if not key:
            raise HTTPException(status_code=401, detail="No matching JWK found")

        # Build public key for PyJWT
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)

        options = {"verify_aud": bool(JWT_AUD)}
        payload = jwt.decode(token, public_key, algorithms=[key.get("alg", "RS256")], audience=JWT_AUD if JWT_AUD else None, options=options)
        return payload
    except HTTPException:
        raise
    except Exception as e:
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

    # Resolve profile via DB
    profile = db.get_profile_by_id(sub)
    if not profile:
        raise HTTPException(status_code=401, detail="No profile found for authenticated user")

    return {
        "user_id": sub,
        "client_id": profile.get("client_id"),
        "role": profile.get("role") or "member",
    }


__all__ = ["get_current_user", "verify_jwt"]
