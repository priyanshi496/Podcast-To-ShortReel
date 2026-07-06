import os
import shutil
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app import crud, schemas, models
from app.db import get_db
from app.config import settings

router = APIRouter(prefix="/videos", tags=["Videos"])

@router.post("/upload", response_model=schemas.VideoResponse)
def upload_video(file: UploadFile = File(...), db: Session = Depends(get_db)):
    # 1. Create a safe filename and path
    ext = os.path.splitext(file.filename)[1]
    # Keep original filename but use UUID for storage file to avoid duplicate name collisions
    unique_filename = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)
    
    # 2. Write file to uploads/
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not save file: {e}")
        
    # 3. Create Video DB model
    video_in = schemas.VideoCreate(
        original_filename=file.filename,
        storage_path=file_path,
        duration_sec=None,
        status="uploaded"
    )
    db_video = crud.create_video(db, video_in)
    
    # 4. Create first job in pipeline (transcribe)
    job_in = schemas.JobCreate(
        video_id=db_video.id,
        job_type="transcribe",
        status="queued"
    )
    crud.create_job(db, job_in)
    
    return db_video

@router.post("/{video_id}/process", response_model=schemas.JobResponse)
def process_video(video_id: int, db: Session = Depends(get_db)):
    video = crud.get_video(db, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Guard: if a transcribe or rank job is already queued/running, return it — don't duplicate
    from app import models as _models
    active_job = (
        db.query(_models.Job)
        .filter(
            _models.Job.video_id == video_id,
            _models.Job.job_type.in_(["transcribe", "rank"]),
            _models.Job.status.in_(["queued", "running"]),
        )
        .order_by(_models.Job.id.desc())
        .first()
    )
    if active_job:
        return active_job


    # Reset video status and enqueue fresh transcribe job
    crud.update_video_status(db, video.id, status="uploaded")

    job_in = schemas.JobCreate(
        video_id=video.id,
        job_type="transcribe",
        status="queued"
    )
    db_job = crud.create_job(db, job_in)
    return db_job

@router.get("", response_model=List[schemas.VideoResponse])
def read_videos(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return crud.get_videos(db, skip=skip, limit=limit)

@router.get("/{video_id}", response_model=schemas.VideoResponse)
def read_video(video_id: int, db: Session = Depends(get_db)):
    video = crud.get_video(db, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return video

@router.get("/{video_id}/clips", response_model=List[schemas.ClipCandidateResponse])
def read_video_clips(video_id: int, db: Session = Depends(get_db)):
    video = crud.get_video(db, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return crud.get_clips_by_video(db, video_id)

@router.get("/{video_id}/transcript", response_model=List[schemas.TranscriptSegmentResponse])
def read_video_transcript(video_id: int, db: Session = Depends(get_db)):
    video = crud.get_video(db, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return crud.get_segments_by_video(db, video_id)

@router.get("/{video_id}/jobs", response_model=List[schemas.JobResponse])
def read_video_jobs(video_id: int, db: Session = Depends(get_db)):
    video = crud.get_video(db, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return video.jobs


@router.post("/reset")
def reset_pipeline_data(db: Session = Depends(get_db)):
    """Resets UI session state: only clears temporary files, leaving the database and permanent media intact."""
    try:
        # Clean only the temporary cache folder (TEMP_DIR) to free up space,
        # but keep UPLOAD_DIR (uploaded videos) and OUTPUT_DIR (rendered short clips).
        folder = settings.TEMP_DIR
        if os.path.exists(folder):
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception:
                    pass
        return {"status": "success", "message": "UI reset successfully. Database and media preserved."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reset UI cache: {e}")


