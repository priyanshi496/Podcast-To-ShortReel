from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from app import models, schemas

# Video CRUD
def get_video(db: Session, video_id: int) -> Optional[models.Video]:
    return db.query(models.Video).filter(models.Video.id == video_id).first()

def get_videos(db: Session, skip: int = 0, limit: int = 100) -> List[models.Video]:
    return db.query(models.Video).offset(skip).limit(limit).all()

def create_video(db: Session, video: schemas.VideoCreate) -> models.Video:
    db_video = models.Video(
        original_filename=video.original_filename,
        storage_path=video.storage_path,
        duration_sec=video.duration_sec,
        status=video.status
    )
    db.add(db_video)
    db.commit()
    db.refresh(db_video)
    return db_video

def update_video_status(db: Session, video_id: int, status: str, duration_sec: Optional[float] = None) -> Optional[models.Video]:
    db_video = get_video(db, video_id)
    if db_video:
        db_video.status = status
        if duration_sec is not None:
            db_video.duration_sec = duration_sec
        db.commit()
        db.refresh(db_video)
    return db_video

# Job CRUD
def get_job(db: Session, job_id: int) -> Optional[models.Job]:
    return db.query(models.Job).filter(models.Job.id == job_id).first()

def create_job(db: Session, job: schemas.JobCreate) -> models.Job:
    db_job = models.Job(
        video_id=job.video_id,
        clip_candidate_id=job.clip_candidate_id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        error_message=job.error_message
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    return db_job


def update_job(db: Session, job_id: int, status: str, progress: float = 0.0, error_message: Optional[str] = None) -> Optional[models.Job]:
    db_job = get_job(db, job_id)
    if db_job:
        db_job.status = status
        db_job.progress = progress
        db_job.error_message = error_message
        db_job.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(db_job)
    return db_job

def get_next_queued_job(db: Session) -> Optional[models.Job]:
    # Atomically lock and retrieve the first queued job
    # For standard Sqlalchemy, we can do a select with with_for_update
    # or just a simple query for a local single-worker system.
    # A simple query is fine since there is only one worker thread polling.
    return db.query(models.Job).filter(models.Job.status == "queued").order_by(models.Job.created_at.asc()).first()

# Transcript Segment CRUD
def create_transcript_segments(db: Session, segments: List[schemas.TranscriptSegmentCreate]) -> List[models.TranscriptSegment]:
    db_segments = [
        models.TranscriptSegment(
            video_id=seg.video_id,
            speaker=seg.speaker,
            start_time=seg.start_time,
            end_time=seg.end_time,
            text=seg.text,
            confidence=seg.confidence
        )
        for seg in segments
    ]
    db.add_all(db_segments)
    db.commit()
    return db_segments

def get_segments_by_video(db: Session, video_id: int) -> List[models.TranscriptSegment]:
    return db.query(models.TranscriptSegment).filter(models.TranscriptSegment.video_id == video_id).order_by(models.TranscriptSegment.start_time.asc()).all()

# Clip Candidates CRUD
def create_clip_candidate(db: Session, clip: schemas.ClipCandidateCreate) -> models.ClipCandidate:
    db_clip = models.ClipCandidate(
        video_id=clip.video_id,
        rank=clip.rank,
        start_time=clip.start_time,
        end_time=clip.end_time,
        duration_sec=clip.duration_sec,
        category=clip.category,
        virality_score=clip.virality_score,
        hook_score=clip.hook_score,
        emotion_score=clip.emotion_score,
        curiosity_score=clip.curiosity_score,
        surprise_score=clip.surprise_score,
        reaction_score=clip.reaction_score,
        context_completeness=clip.context_completeness,
        reason=clip.reason,
        hook_line=clip.hook_line,
        transcript_excerpt=clip.transcript_excerpt,
        why_viewers_keep_watching=clip.why_viewers_keep_watching,
        needs_manual_review=clip.needs_manual_review,
        suggested_title=clip.suggested_title,
        suggested_caption=clip.suggested_caption,
        best_aspect_ratio=clip.best_aspect_ratio,
        status=clip.status
    )
    db.add(db_clip)
    db.commit()
    db.refresh(db_clip)
    return db_clip


def get_clip(db: Session, clip_id: int) -> Optional[models.ClipCandidate]:
    return db.query(models.ClipCandidate).filter(models.ClipCandidate.id == clip_id).first()

def get_clips_by_video(db: Session, video_id: int) -> List[models.ClipCandidate]:
    return db.query(models.ClipCandidate).filter(models.ClipCandidate.video_id == video_id).order_by(models.ClipCandidate.virality_score.desc()).all()

def update_clip_status(db: Session, clip_id: int, status: str) -> Optional[models.ClipCandidate]:
    db_clip = get_clip(db, clip_id)
    if db_clip:
        db_clip.status = status
        db.commit()
        db.refresh(db_clip)
    return db_clip

def update_clip_times(db: Session, clip_id: int, start_time: float, end_time: float) -> Optional[models.ClipCandidate]:
    db_clip = get_clip(db, clip_id)
    if db_clip:
        db_clip.start_time = start_time
        db_clip.end_time = end_time
        db_clip.duration_sec = end_time - start_time
        db_clip.status = "trimmed"
        db.commit()
        db.refresh(db_clip)
    return db_clip

# Clip Export CRUD
def create_clip_export(db: Session, clip_id: int, file_path: str, subtitle_path: Optional[str], format: str) -> models.ClipExport:
    db_export = models.ClipExport(
        clip_candidate_id=clip_id,
        file_path=file_path,
        subtitle_path=subtitle_path,
        format=format
    )
    db.add(db_export)
    db.commit()
    db.refresh(db_export)
    return db_export

def get_clip_exports(db: Session, clip_id: int) -> List[models.ClipExport]:
    return db.query(models.ClipExport).filter(models.ClipExport.clip_candidate_id == clip_id).all()
