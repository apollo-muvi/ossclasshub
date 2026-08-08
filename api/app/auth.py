"""ClassHub OSS — Auth dependency for FastAPI"""
from fastapi import Depends, HTTPException, Request, status

from app.security import verify_token


async def get_admin_user(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        token = auth
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    if payload.startswith("parent:"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin token required")
    return payload


async def get_optional_parent(request: Request | None = None) -> str | None:
    if request is None:
        return None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    elif auth:
        token = auth
    else:
        return None
    return verify_token(token)
