import os
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Boolean, ForeignKey, UniqueConstraint, event, inspect, text, LargeBinary, DateTime
from sqlalchemy.orm import sessionmaker, relationship, declarative_base

# Load local .env (gitignored) before reading DATABASE_URL / DATA_DIR.
load_dotenv()

# DATA_DIR lets you point the app's writable data (SQLite DB + uploaded files) at a
# persistent volume. On Render/Docker the container filesystem is ephemeral, so set
# DATA_DIR to a mounted disk (e.g. /var/data) or provide DATABASE_URL below.
DATA_DIR = (os.getenv("DATA_DIR") or "").strip()
if DATA_DIR:
    os.makedirs(DATA_DIR, exist_ok=True)

# Uploaded candidate photos/logos live here. Served by main.py at /static/uploads/.
UPLOAD_DIR = (os.getenv("UPLOAD_DIR") or "").strip() or (
    os.path.join(DATA_DIR, "uploads") if DATA_DIR else "static/uploads"
)


def _database_url() -> str:
    """Prefer DATABASE_URL (e.g. a managed Postgres) over local SQLite."""
    url = (os.getenv("DATABASE_URL") or "").strip()
    if url:
        # Render/Heroku expose postgres://, SQLAlchemy expects postgresql://
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        # SQLAlchemy 2.1 maps postgresql:// to psycopg v3 by default; this project
        # ships psycopg2-binary, so pin the driver explicitly.
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
        return url

    if DATA_DIR:
        db_path = os.path.join(DATA_DIR, "voting.db")
    else:
        db_path = "./voting.db"
    return f"sqlite:///{db_path}"


SQLALCHEMY_DATABASE_URL = _database_url()
IS_SQLITE = SQLALCHEMY_DATABASE_URL.startswith("sqlite")

connect_args = {
    "check_same_thread": False
} if IS_SQLITE else {}

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args)

if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Inside database.py
class ElectionState(Base):
    __tablename__ = "election_state"
    id = Column(Integer, primary_key=True, index=True)
    status = Column(String, default="SETUP") # 'SETUP', 'OPEN', 'CLOSED'

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(String, unique=True, index=True)
    password = Column(String) # Storing plaintext only for this local mock-up!
    role = Column(String, default="student") # 'student' or 'admin'
    
    votes = relationship("Vote", back_populates="voter")

class Candidate(Base):
    __tablename__ = "candidates"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    position = Column(String)
    manifesto = Column(String)
    photo_path = Column(String, nullable=True) # <-- NEW COLUMN
    
    votes = relationship("Vote", back_populates="candidate")

class Vote(Base):
    __tablename__ = "votes"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    receipt_id = Column(String, unique=True, index=True, nullable=True)
    
    # Critical: Ensure a user can only have one vote per database
    __table_args__ = (UniqueConstraint('user_id', name='_user_vote_uc'),)
    
    voter = relationship("User", back_populates="votes")
    candidate = relationship("Candidate", back_populates="votes")

class SiteBranding(Base):
    __tablename__ = "site_branding"
    id = Column(Integer, primary_key=True, index=True)
    school_name = Column(String, default="Sekolah Kristen Tunas Daud")
    portal_subtitle = Column(String, default="E-Voting Portal")
    logo_path = Column(String, default="static/logo_Sekolah.png")
    favicon_path = Column(String, default="static/favicon.ico")
    browser_title = Column(String, default="Student Council Election")

class UploadedFile(Base):
    """Uploaded images (candidate photos, logo, favicon) stored in the database.

    Render/container filesystems are ephemeral, so files saved to disk disappear on
    every restart or redeploy. Keeping the bytes in the (persistent) database means
    the images survive together with the rows that reference them.
    """
    __tablename__ = "uploaded_files"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, unique=True, index=True, nullable=False)
    content_type = Column(String, nullable=True)
    data = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# Create tables
Base.metadata.create_all(bind=engine)

# Add receipt_id column to existing schema if missing
inspector = inspect(engine)
columns = [column_info['name'] for column_info in inspector.get_columns('votes')]
if 'receipt_id' not in columns:
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE votes ADD COLUMN receipt_id VARCHAR'))

# Add new branding columns to site_branding if missing (migrations for older DBs)
try:
    if 'site_branding' in inspector.get_table_names():
        sb_columns = [c['name'] for c in inspector.get_columns('site_branding')]
        with engine.begin() as conn:
            if 'favicon_path' not in sb_columns:
                conn.execute(text('ALTER TABLE site_branding ADD COLUMN favicon_path VARCHAR'))
            if 'browser_title' not in sb_columns:
                conn.execute(text('ALTER TABLE site_branding ADD COLUMN browser_title VARCHAR'))
except Exception:
    # If the table doesn't exist yet or migration fails, ignore and let create_all handle it
    pass
