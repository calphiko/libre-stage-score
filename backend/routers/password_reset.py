import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend import auth, models, schemas
from backend.utils.password_validator import validate_password


router = APIRouter(prefix="/password_reset", tags=["pw_reset"])


def mark_token_as_used(db: Session, token: str) -> None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db.add(models.UsedPasswordResetToken(token_hash=token_hash, used_at=datetime.now(timezone.utc)))
    db.commit()


@router.get("/verify_reset_token")
def verify_reset_token(auth_data=Depends(auth.verify_password_reset_token), db: Session = Depends(auth.get_db)):
    username, _ = auth_data
    user = db.query(models.User).filter(models.User.user_name == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user_name": username}


@router.post("/new_password")
def set_new_password(
    data: schemas.PasswordResetRequest,
    auth_data=Depends(auth.verify_password_reset_token),
    db: Session = Depends(auth.get_db),
):
    username, token = auth_data
    user = db.query(models.User).filter(models.User.user_name == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    is_valid, error_msg = validate_password(data.new_password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    user.user_pw = auth.hash_pw(data.new_password)
    mark_token_as_used(db, token)
    db.commit()
    return {"message": "Password reset successful"}

