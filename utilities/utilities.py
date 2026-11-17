import os
import secrets
import uuid

from fastapi import Request, Depends, UploadFile
from sqlalchemy.orm import Session

import database
from database import SessionLocal, User, Vote, UploadedFile

UPLOADS_URL_PREFIX = "/static/uploads/"


def save_upload(upload: UploadFile, db: Session) -> str:
    """Store an uploaded file in the database and return its public URL.

    Bytes live in the DB (not on the container filesystem) so images survive
    restarts/redeploys on hosts like Render. Caller commits the transaction.
    """
    ext = os.path.splitext(upload.filename or "")[1].lower()
    filename = f"{uuid.uuid4().hex}{ext}"
    db.add(UploadedFile(
        filename=filename,
        content_type=upload.content_type,
        data=upload.file.read(),
    ))
    return f"{UPLOADS_URL_PREFIX}{filename}"


def delete_upload(stored_path: str, db: Session) -> None:
    """Remove a previously uploaded file, ignoring paths we do not manage."""
    if not stored_path:
        return

    normalized = "/" + stored_path.lstrip("/")
    if not normalized.startswith(UPLOADS_URL_PREFIX):
        return  # e.g. the bundled default logo, leave it alone

    filename = os.path.basename(normalized)
    db.query(UploadedFile).filter(UploadedFile.filename == filename).delete(synchronize_session=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)):
    student_id = request.cookies.get("student_id")
    if not student_id:
        return None
    return db.query(User).filter(User.student_id == student_id).first()


def get_branding(db: Session):
    return db.query(database.SiteBranding).filter(database.SiteBranding.id == 1).first()


def generate_receipt_id() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "-".join(
        "".join(secrets.choice(alphabet) for _ in range(2))
        for _ in range(3)
    )


def get_unique_receipt_id(db: Session) -> str:
    while True:
        receipt = generate_receipt_id()
        if not db.query(Vote).filter(Vote.receipt_id == receipt).first():
            return receipt
