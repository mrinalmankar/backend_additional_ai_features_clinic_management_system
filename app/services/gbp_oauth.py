import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from cryptography.fernet import Fernet

from app.core.config import settings

logger = logging.getLogger(__name__)

_dev_ephemeral_key: bytes | None = None

def _get_fernet() -> Fernet:
    if not settings.secret_key:
        global _dev_ephemeral_key
        if _dev_ephemeral_key is None:
            _dev_ephemeral_key = Fernet.generate_key()
            logger.warning(
                "SECRET_KEY is not set -- using a random per-process key for "
                "token encryption. This is only safe for local development: "
                "tokens encrypted now will fail to decrypt after a restart, "
                "and this path is refused entirely when ENVIRONMENT=production."
            )
        return Fernet(_dev_ephemeral_key)

    raw = settings.secret_key.encode()

    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)

def encrypt_token(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()

def decrypt_token(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()

SCOPES = [
    "https://www.googleapis.com/auth/business.manage",
    "openid",
    "email",
    "profile",
]

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

def get_auth_url(account_id: str) -> tuple[str, str]:
    state = f"{account_id}:{secrets.token_urlsafe(16)}"
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{GOOGLE_AUTH_URL}?{query}", state

async def exchange_code(code: str) -> dict[str, Any]:
    if settings.gbp_mock_mode:
        return {
            "access_token": "mock_access_token_" + secrets.token_hex(8),
            "refresh_token": "mock_refresh_token_" + secrets.token_hex(8),
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": " ".join(SCOPES),
        }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        return resp.json()

async def refresh_access_token(refresh_token_enc: str) -> tuple[str, datetime]:
    if settings.gbp_mock_mode:
        new_access = "mock_access_token_" + secrets.token_hex(8)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        return new_access, expires_at

    refresh_token = decrypt_token(refresh_token_enc)

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600))
    return data["access_token"], expires_at

async def fetch_account_info(access_token: str) -> dict[str, Any]:
    if settings.gbp_mock_mode:
        return {
            "accounts": [
                {
                    "name": "accounts/123456789",
                    "accountName": "Apex Dental Clinic",
                    "type": "LOCATION_GROUP",
                    "verificationState": "VERIFIED",
                }
            ]
        }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://mybusinessaccountmanagement.googleapis.com/v1/accounts",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()

async def fetch_locations(access_token: str, account_name: str) -> dict[str, Any]:
    if settings.gbp_mock_mode:
        return {
            "locations": [
                {
                    "name": f"{account_name}/locations/111111111",
                    "title": "Apex Dental Clinic – Main Branch",
                    "storefrontAddress": {"locality": "Pune", "administrativeArea": "MH"},
                },
                {
                    "name": f"{account_name}/locations/222222222",
                    "title": "Apex Dental Clinic – Koregaon Park",
                    "storefrontAddress": {"locality": "Pune", "administrativeArea": "MH"},
                },
            ]
        }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"https://mybusinessbusinessinformation.googleapis.com/v1/{account_name}/locations",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()
