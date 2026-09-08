import uuid
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

_bearer_scheme = HTTPBearer(auto_error=False)

_VALID_ROLES = ("doctor", "staff", "admin")

_DEV_INSECURE_FALLBACK_SECRET = "dev-only-insecure-key-do-not-use-in-production"

@dataclass(frozen=True)
class AuthContext:

    doctor_id: uuid.UUID
    role: str

    @property
    def is_privileged(self) -> bool:
        return self.role in ("staff", "admin")

def _jwt_secret() -> str:
    return settings.jwt_secret_key or _DEV_INSECURE_FALLBACK_SECRET

async def get_current_doctor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> AuthContext:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(
            credentials.credentials,
            _jwt_secret(),
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing 'sub' claim")
    try:
        doctor_id = uuid.UUID(str(sub))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 'sub' claim is not a valid UUID")

    role = payload.get("role", "doctor")
    if role not in _VALID_ROLES:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Unrecognized role claim: {role!r}")

    return AuthContext(doctor_id=doctor_id, role=role)

CurrentDoctor = Annotated[AuthContext, Depends(get_current_doctor)]

def require_owner_or_privileged(auth: AuthContext, owner_doctor_id: uuid.UUID) -> None:
    if auth.is_privileged:
        return
    if auth.doctor_id != owner_doctor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this record",
        )
