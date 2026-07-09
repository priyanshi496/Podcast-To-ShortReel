import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app import crud, schemas, models
from app.db import get_db

router = APIRouter(prefix="/clips", tags=["Clips"])

@router.post("/{clip_id}/approve", response_model=schemas.ClipCandidateResponse)
def approve_clip(clip_id: int, db: Session = Depends(get_db)):
    clip = crud.get_clip(db, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip candidate not found")
    return crud.update_clip_status(db, clip_id, "approved")

@router.post("/{clip_id}/reject", response_model=schemas.ClipCandidateResponse)
def reject_clip(clip_id: int, db: Session = Depends(get_db)):
    clip = crud.get_clip(db, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip candidate not found")
    return crud.update_clip_status(db, clip_id, "rejected")

@router.post("/{clip_id}/trim", response_model=schemas.ClipCandidateResponse)
def trim_clip(clip_id: int, trim_data: schemas.ClipCandidateTrim, db: Session = Depends(get_db)):
    clip = crud.get_clip(db, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip candidate not found")
    if trim_data.start_time >= trim_data.end_time:
        raise HTTPException(status_code=400, detail="Start time must be less than end time")
    return crud.update_clip_times(db, clip_id, trim_data.start_time, trim_data.end_time)

@router.post("/{clip_id}/render", response_model=schemas.JobResponse)
def render_clip(clip_id: int, db: Session = Depends(get_db)):
    clip = crud.get_clip(db, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip candidate not found")
    
    # Check if the source video file exists on disk
    video = crud.get_video(db, clip.video_id)
    if not video or not video.storage_path or not os.path.exists(video.storage_path):
        raise HTTPException(
            status_code=400,
            detail="Cannot render video formats: No video file was uploaded. Video rendering requires the original MP4 upload."
        )
    
    # Update clip status
    crud.update_clip_status(db, clip_id, "rendering")
    
    # Create the render job in the background jobs queue
    job_in = schemas.JobCreate(
        video_id=clip.video_id,
        clip_candidate_id=clip.id,
        job_type="render",
        status="queued"
    )
    db_job = crud.create_job(db, job_in)
    return db_job

@router.get("/{clip_id}/exports", response_model=List[schemas.ClipExportResponse])
def get_exports(clip_id: int, db: Session = Depends(get_db)):
    clip = crud.get_clip(db, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip candidate not found")
    return crud.get_clip_exports(db, clip_id)
