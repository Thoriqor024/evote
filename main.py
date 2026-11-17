import os
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager

from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend

import database
from database import SessionLocal, User, ElectionState, SiteBranding, UploadedFile, UPLOAD_DIR
from routes.admin import admin_router
from routes.client import client_router
from utilities.utilities import get_db

# 1. Initialize Cache on Startup and seed admin
@asynccontextmanager
async def lifespan(app: FastAPI):
    FastAPICache.init(InMemoryBackend(), prefix="election")
    db = SessionLocal()
    if not db.query(ElectionState).filter(ElectionState.id == 1).first():
        db.add(ElectionState(id=1, status="SETUP"))
    if not db.query(User).first():
        db.add(User(student_id="admin", password="password", role="admin"))
    if not db.query(SiteBranding).filter(SiteBranding.id == 1).first():
        db.add(SiteBranding(
            id=1,
            school_name="Sekolah Kristen Tunas Daud",
            portal_subtitle="E-Voting Portal",
            logo_path="static/logo_Sekolah.png",
            favicon_path="static/favicon.ico",
            browser_title="Student Council Election"
        ))
    db.commit()
    db.close()
    yield

app = FastAPI(lifespan=lifespan)


# Uploaded images are served from the database (persistent), with a filesystem
# fallback for the images bundled in the repo. Registered before the /static mount.
@app.get("/static/uploads/{filename}")
def serve_upload(filename: str, db: Session = Depends(get_db)):
    record = db.query(UploadedFile).filter(UploadedFile.filename == filename).first()
    if record:
        return Response(
            content=record.data,
            media_type=record.content_type or "application/octet-stream",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    for directory in (UPLOAD_DIR, os.path.join("static", "uploads")):
        path = os.path.join(directory, filename)
        if os.path.isfile(path):
            return FileResponse(path)

    raise HTTPException(status_code=404, detail="Image not found")


app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/script", StaticFiles(directory="script"), name="script")

app.include_router(client_router)
app.include_router(admin_router)
