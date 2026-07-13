import os
import shutil
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app import crud, schemas, models
from app.db import get_db
from app.config import settings

router = APIRouter(prefix="/videos", tags=["Videos"])

@router.post("/upload", response_model=schemas.VideoResponse)
def upload_video(file: UploadFile = File(...), db: Session = Depends(get_db)):
    # 1. Create a safe filename and path
    filename = file.filename or "unknown"
    ext = os.path.splitext(filename)[1]
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
        original_filename=filename,
        storage_path=file_path,
        duration_sec=None,
        status="uploaded"
    )
    db_video = crud.create_video(db, video_in)
    
    # 4. Create first job in pipeline (transcribe)
    job_in = schemas.JobCreate(
        video_id=int(db_video.id),  # type: ignore
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
    crud.update_video_status(db, video_id, status="uploaded")

    job_in = schemas.JobCreate(
        video_id=video_id,
        job_type="transcribe",
        status="queued"
    )
    db_job = crud.create_job(db, job_in)
    return db_job

@router.post("/{video_id}/rank", response_model=schemas.JobResponse)
def rank_video(video_id: int, db: Session = Depends(get_db)):
    video = crud.get_video(db, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Guard: if a rank job is already queued/running, return it
    from app import models as _models
    active_job = (
        db.query(_models.Job)
        .filter(
            _models.Job.video_id == video_id,
            _models.Job.job_type == "rank",
            _models.Job.status.in_(["queued", "running"]),
        )
        .order_by(_models.Job.id.desc())
        .first()
    )
    if active_job:
        return active_job

    job_in = schemas.JobCreate(
        video_id=video_id,
        job_type="rank",
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


@router.post("/upload-transcript", response_model=schemas.VideoResponse)
def upload_video_with_transcript(
    transcript_file: UploadFile = File(...),
    video_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    # 1. Parse the transcript content
    try:
        content = transcript_file.file.read().decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read transcript file: {e}")

    import json
    import re

    parsed_segments = []
    
    # Try parsing as JSON first
    try:
        data = json.loads(content)
        if isinstance(data, list):
            for idx, item in enumerate(data):
                if isinstance(item, dict) and "text" in item:
                    start_t = float(item.get("start_time", idx * 2.0))
                    end_t = float(item.get("end_time", start_t + 2.0))
                    speaker = str(item.get("speaker") or "Speaker")
                    confidence = float(item.get("confidence") or 1.0)
                    parsed_segments.append({
                        "start_time": start_t,
                        "end_time": end_t,
                        "speaker": speaker,
                        "text": str(item["text"]),
                        "confidence": confidence
                    })
    except Exception:
        pass

    # Fallback to text parsing if JSON didn't yield segments
    if not parsed_segments:
        # Regex format: [00:00.16 - 00:03.77] Speaker 1: text or similar
        time_pattern = re.compile(r"^\[\s*([^\]\-]+)\s*-\s*([^\]]+)\s*\]")
        
        def parse_time_str(time_str: str) -> float:
            time_str = time_str.strip().replace("s", "").replace(",", ".")
            parts = time_str.split(":")
            if len(parts) == 3:
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
            elif len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
            return float(time_str)
            
        lines = content.splitlines()
        has_timestamps = any(time_pattern.match(line.strip()) for line in lines)
        
        if has_timestamps:
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                match = time_pattern.match(line)
                if match:
                    try:
                        start_t = parse_time_str(match.group(1))
                        end_t = parse_time_str(match.group(2))
                    except Exception:
                        continue
                    rest = line[match.end():].strip()
                    speaker_text_pattern = re.compile(r"^([^:]+):\s*(.*)$")
                    spk_match = speaker_text_pattern.match(rest)
                    if spk_match:
                        speaker = spk_match.group(1).strip()
                        text = spk_match.group(2).strip()
                    else:
                        speaker = "Speaker 1"
                        text = rest
                    parsed_segments.append({
                        "start_time": start_t,
                        "end_time": end_t,
                        "speaker": speaker,
                        "text": text,
                        "confidence": 1.0
                    })
        else:
            # Plan text Speaker: text or line-by-line format
            speaker_text_pattern = re.compile(r"^([^:]+):\s*(.*)$")
            curr_time = 0.0
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                match = speaker_text_pattern.match(line)
                if match:
                    speaker = match.group(1).strip()
                    text = match.group(2).strip()
                else:
                    speaker = "Speaker 1"
                    text = line
                
                words = text.split()
                duration = max(2.0, len(words) * 0.4)
                end_t = curr_time + duration
                
                parsed_segments.append({
                    "start_time": round(curr_time, 2),
                    "end_time": round(end_t, 2),
                    "speaker": speaker,
                    "text": text,
                    "confidence": 1.0
                })
                curr_time = end_t

    if not parsed_segments:
        raise HTTPException(
            status_code=400,
            detail="Could not extract any transcript segments. Please check file format."
        )

    # 2. Save video file if uploaded, otherwise create a placeholder record
    video_path = ""
    original_filename = "Transcript-only Podcast"
    if video_file:
        ext = os.path.splitext(video_file.filename or "")[1]
        unique_filename = f"{uuid.uuid4()}{ext}"
        video_path = os.path.join(settings.UPLOAD_DIR, unique_filename)
        try:
            with open(video_path, "wb") as buffer:
                shutil.copyfileobj(video_file.file, buffer)
            original_filename = video_file.filename or "unknown_video"
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Could not save video file: {e}")
    else:
        original_filename = f"Transcript: {transcript_file.filename or 'unknown_transcript'}"

    # Determine duration based on transcript end time
    duration_sec = parsed_segments[-1]["end_time"] if parsed_segments else 0.0

    # 3. Create Video DB record
    video_in = schemas.VideoCreate(
        original_filename=original_filename,
        storage_path=video_path,
        duration_sec=duration_sec,
        status="transcribed"  # Directly set as transcribed so we bypass Whispering
    )
    db_video = crud.create_video(db, video_in)

    # 4. Save segments to DB
    db_segments = [
        schemas.TranscriptSegmentCreate(
            video_id=int(db_video.id),  # type: ignore
            speaker=item["speaker"],
            start_time=item["start_time"],
            end_time=item["end_time"],
            text=item["text"],
            confidence=item["confidence"]
        )
        for item in parsed_segments
    ]
    crud.create_transcript_segments(db, db_segments)

    # 5. Create ranking job directly in queue (bypassing transcribe stage)
    job_in = schemas.JobCreate(
        video_id=int(db_video.id),  # type: ignore
        job_type="rank",
        status="queued"
    )
    crud.create_job(db, job_in)

    return db_video

from app.services import render
from fastapi.responses import FileResponse

@router.post("/{video_id}/compile", response_model=schemas.JobResponse)
def compile_video_parts(
    video_id: int,
    request: schemas.CompileRequest,
    db: Session = Depends(get_db)
):
    import json
    from app.config import settings
    video = crud.get_video(db, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
        
    if not video.storage_path or not os.path.exists(str(video.storage_path)):
        raise HTTPException(status_code=400, detail="Video file is missing or not fully processed.")
        
    if not request.parts:
        raise HTTPException(status_code=400, detail="At least one video part must be specified.")
        
    # 1. Create a placeholder ClipCandidate to link the compiled output exports
    clip_in = schemas.ClipCandidateCreate(
        video_id=video_id,
        start_time=request.parts[0].start_time,
        end_time=request.parts[-1].end_time,
        duration_sec=sum(p.end_time - p.start_time for p in request.parts),
        suggested_title="Custom Compilation",
        status="rendering"
    )
    db_clip = crud.create_clip_candidate(db, clip_in)
    
    # 2. Create the compile job in the database
    job_in = schemas.JobCreate(
        video_id=video_id,
        clip_candidate_id=db_clip.id,  # type: ignore
        job_type="compile",
        status="queued"
    )
    db_job = crud.create_job(db, job_in)
    
    # 3. Write requested parts to a temp JSON file for the worker thread
    parts_list = [{"start_time": p.start_time, "end_time": p.end_time} for p in request.parts]
    payload_path = os.path.join(settings.TEMP_DIR, f"compile_request_{db_job.id}.json")
    try:
        with open(payload_path, "w", encoding="utf-8") as f:
            json.dump(parts_list, f, indent=2)
    except Exception as e:
        crud.update_clip_status(db, int(db_clip.id), "failed") # type: ignore
        crud.update_job(db, int(db_job.id), status="failed", error_message=f"Failed to save job payload: {e}") # type: ignore
        raise HTTPException(status_code=500, detail=f"Failed to queue compilation job: {e}")
        
    return db_job
