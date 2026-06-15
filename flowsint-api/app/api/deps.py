from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import os
from sqlalchemy.orm import Session
from flowsint_core.core.auth import ALGORITHM, AUTH_SECRET
from flowsint_core.core.postgre_db import get_db
from flowsint_core.core.models import Profile

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

DEV_AUTO_LOGIN = os.getenv("DEV_AUTO_LOGIN", "").strip()

def _get_or_create_dev_user(db: Session) -> Profile:
    """Auto-login: return or create the configured dev user."""
    user = db.query(Profile).filter(Profile.email == DEV_AUTO_LOGIN).first()
    if not user:
        from flowsint_core.core.auth import get_password_hash
        user = Profile(
            email=DEV_AUTO_LOGIN,
            hashed_password=get_password_hash("admin"),
            first_name="Dev",
            last_name="Admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

def get_current_user(
    token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> Profile:
    # Dev auto-login: skip auth entirely
    if DEV_AUTO_LOGIN:
        if not token:
            return _get_or_create_dev_user(db)
        # Token present but invalid in dev mode -> still auto-login
        try:
            jwt.decode(token, AUTH_SECRET, algorithms=[ALGORITHM])
        except JWTError:
            return _get_or_create_dev_user(db)

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, AUTH_SECRET, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.query(Profile).filter(Profile.email == email).first()
    if user is None:
        raise credentials_exception
    return user
