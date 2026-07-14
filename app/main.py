import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.db import Base, engine
from app.config import settings
from app.api import routes_videos, routes_jobs, routes_clips, routes_projects
from app.workers.job_runner import start_job_worker

# Create logs directory if not exists
os.makedirs("logs", exist_ok=True)

# Setup logging configuration (writing to logs/app.log instead of terminal)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/app.log", encoding="utf-8")
    ]
)
# Mute external libraries that log heavily at INFO level
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# Automatically create SQL tables on startup for the local PostgreSQL MVP
logger.info("Initializing database tables...")
try:
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized successfully.")
except Exception as e:
    logger.error(f"Error initializing database tables: {e}")

# Run additive column migrations for existing tables
def _run_migrations():
    """Safely add new columns to existing tables without dropping data."""
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    with engine.begin() as conn:
        # Add project_id to videos
        if "videos" in tables:
            existing = {c["name"] for c in inspector.get_columns("videos")}
            if "project_id" not in existing:
                conn.execute(text(
                    "ALTER TABLE videos ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL"
                ))
                logger.info("Migration: added project_id to videos")

        # Add source + title to clip_candidates
        if "clip_candidates" in tables:
            existing = {c["name"] for c in inspector.get_columns("clip_candidates")}
            if "source" not in existing:
                conn.execute(text("ALTER TABLE clip_candidates ADD COLUMN source VARCHAR DEFAULT 'ai'"))
                logger.info("Migration: added source to clip_candidates")
            if "title" not in existing:
                conn.execute(text("ALTER TABLE clip_candidates ADD COLUMN title VARCHAR"))
                logger.info("Migration: added title to clip_candidates")

try:
    _run_migrations()
except Exception as e:
    logger.error(f"Migration error (non-fatal): {e}")

# Create the FastAPI app
app = FastAPI(
    title="Podcast to Short Reel Converter API",
    description="MVP API to extract, transcribe, rank, and render viral short clips from video podcasts",
    version="1.0.0"
)

# Configure CORS for easy frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Start background polling worker on startup
@app.on_event("startup")
def startup_event():
    logger.info("Application starting up...")
    start_job_worker()

# Mount API routers
app.include_router(routes_projects.router)
app.include_router(routes_videos.router)
app.include_router(routes_jobs.router)
app.include_router(routes_clips.router)

from app.api import routes_test
app.include_router(routes_test.router)

# Serve output and uploads directories as static files
# This allows downloading / viewing the raw and cropped videos directly.
if os.path.exists(settings.OUTPUT_DIR):
    app.mount("/static/output", StaticFiles(directory=settings.OUTPUT_DIR), name="output")
if os.path.exists(settings.UPLOAD_DIR):
    app.mount("/static/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# Serve the new modular frontend assets (JS modules, CSS, etc.)
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(_static_dir):
    app.mount("/assets", StaticFiles(directory=_static_dir), name="frontend_assets")

from fastapi.responses import HTMLResponse

@app.get("/test", response_class=HTMLResponse)
def read_test():
    static_file_path = os.path.join(os.path.dirname(__file__), "static", "test.html")
    if os.path.exists(static_file_path):
        with open(static_file_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Test HTML file not found</h1>", status_code=404)

@app.get("/legacy", response_class=HTMLResponse)
def read_legacy():
    """Serves the old UI during the incremental migration."""
    static_file_path = os.path.join(os.path.dirname(__file__), "static", "index.legacy.html")
    if os.path.exists(static_file_path):
        with open(static_file_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Legacy UI not found</h1>", status_code=404)

@app.get("/", response_class=HTMLResponse)
def read_root():
    static_file_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(static_file_path):
        with open(static_file_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Frontend HTML file not found</h1>", status_code=404)
