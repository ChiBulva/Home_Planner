from __future__ import annotations

from io import BytesIO

import pyotp
import qrcode
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User


def has_users(db: Session) -> bool:
    return db.scalar(select(func.count(User.id))) > 0


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    user = db.get(User, user_id)
    if not user or not user.active:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code.replace(" ", ""), valid_window=1)


def provisioning_uri(username: str, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=settings.totp_issuer)


def qr_data_uri(uri: str) -> str:
    image = qrcode.make(uri)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    import base64

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
