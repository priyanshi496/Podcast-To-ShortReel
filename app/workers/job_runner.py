import time
import threading
import logging
import os
import traceback
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app import crud, schemas, models
from app.config import settings
from app.services import audio, transcribe, segment, rank, render

logger = logging.getLogger(__name__)

def process_job(db: Session, job: models.Job):
    logger.info(f"Processing job {job.id} (type: {job.job_type}) for video {job.video_id}")
    
    # Update job to running
    crud.update_job(db, job.id, status="running", progress=0.1)
    
    video = crud.get_video(db, job.video_id)
    if not video:
        raise ValueError(f"Video {job.video_id} not found.")

    if job.job_type == "transcribe":
        # 1. Update video status
        crud.update_video_status(db, video.id, status="processing")
        
        # 2. Get video duration
        logger.info("Getting video duration...")
        duration = audio.get_video_duration(video.storage_path)
        crud.update_video_status(db, video.id, status="processing", duration_sec=duration)
        crud.update_job(db, job.id, status="running", progress=0.3)
        
        # 3. Extract and normalize audio
        logger.info("Extracting and normalizing audio...")
        audio_filename = f"video_{video.id}_audio.wav"
        audio_path = audio.extract_and_normalize_audio(video.storage_path, audio_filename)
        crud.update_job(db, job.id, status="running", progress=0.5)
        
        # 4. Transcribe using Whisper local
        logger.info("Transcribing audio...")
        transcripts_data = transcribe.transcribe_audio(audio_path)
        crud.update_job(db, job.id, status="running", progress=0.7)
        
        # Clean up audio WAV file to save space
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
                logger.info(f"Temporary audio file removed: {audio_path}")
        except Exception as e:
            logger.warning(f"Failed to remove temp audio file {audio_path}: {e}")
            
        # 5. Save segments
        logger.info("Saving transcript segments to database...")
        segments_create = [
            schemas.TranscriptSegmentCreate(
                video_id=video.id,
                speaker=item["speaker"],
                start_time=item["start_time"],
                end_time=item["end_time"],
                text=item["text"],
                confidence=item["confidence"]
            )
            for item in transcripts_data
        ]
        crud.create_transcript_segments(db, segments_create)
        crud.update_job(db, job.id, status="running", progress=0.9)
        
        # 6. Complete Job and automatically enqueue Ranking Job
        crud.update_job(db, job.id, status="done", progress=1.0)
        
        rank_job = schemas.JobCreate(
            video_id=video.id,
            job_type="rank",
            status="queued"
        )
        crud.create_job(db, rank_job)
        logger.info(f"Transcribe job {job.id} done. Enqueued rank job.")

    elif job.job_type == "rank":
        # 1. Fetch transcript segments
        logger.info("Fetching segments...")
        segments = crud.get_segments_by_video(db, video.id)
        if not segments:
            raise ValueError("No transcript segments found for ranking.")
        
        segments_dict = [
            {
                "start_time": s.start_time,
                "end_time": s.end_time,
                "text": s.text,
                "speaker": s.speaker
            }
            for s in segments
        ]
        crud.update_job(db, job.id, status="running", progress=0.3)
        
        # 2. Segment transcript into overlapping windows
        logger.info("Segmenting transcript into windows...")
        candidates = segment.segment_transcript(segments_dict)
        crud.update_job(db, job.id, status="running", progress=0.5)
        
        # 3. Score and rank candidates
        logger.info(f"Scoring {len(candidates)} candidates using Nemotron batch scorer...")
        
        # Prepare audio path (re-extract if it was cleaned up by transcribe step)
        audio_filename = f"video_{video.id}_audio.wav"
        wav_path = os.path.join(settings.TEMP_DIR, audio_filename)
        audio_re_extracted = False
        if not os.path.exists(wav_path):
            logger.info("Temporary audio file not found. Re-extracting audio for ranking features...")
            wav_path = audio.extract_and_normalize_audio(video.storage_path, audio_filename)
            audio_re_extracted = True
            
        try:
            ranked_candidates = rank.rank_candidates(
                candidates,
                video_id=str(video.id),
                limit=5,
                audio_path=wav_path,
                transcript_lines=segments_dict
            )
        finally:
            if audio_re_extracted:
                try:
                    if os.path.exists(wav_path):
                        os.remove(wav_path)
                        logger.info(f"Temporary audio file removed after ranking: {wav_path}")
                except Exception as e:
                    logger.warning(f"Failed to remove temp audio file {wav_path}: {e}")
                    
        crud.update_job(db, job.id, status="running", progress=0.8)
        
        # 4. Save ranked candidates
        logger.info("Saving clip candidates to database...")
        for clip_data in ranked_candidates:
            clip_create = schemas.ClipCandidateCreate(
                video_id=video.id,
                rank=clip_data["rank"],
                start_time=clip_data["start_time"],
                end_time=clip_data["end_time"],
                duration_sec=clip_data["duration_sec"],
                category=clip_data["category"],
                virality_score=clip_data["virality_score"],
                hook_score=clip_data["hook_score"],
                emotion_score=clip_data["emotion_score"],
                curiosity_score=clip_data["curiosity_score"],
                surprise_score=clip_data["surprise_score"],
                reaction_score=clip_data["reaction_score"],
                context_completeness=clip_data["context_completeness"],
                reason=clip_data["reason"],
                hook_line=clip_data["hook_line"],
                transcript_excerpt=clip_data["transcript_excerpt"],
                why_viewers_keep_watching=clip_data["why_viewers_keep_watching"],
                needs_manual_review=clip_data["needs_manual_review"],
                suggested_title=clip_data["suggested_title"],
                suggested_caption=clip_data["suggested_caption"],
                best_aspect_ratio=clip_data["best_aspect_ratio"],
                status="suggested"
            )
            crud.create_clip_candidate(db, clip_create)
            
        crud.update_job(db, job.id, status="done", progress=1.0)
        crud.update_video_status(db, video.id, status="processed")
        logger.info(f"Rank job {job.id} done. Extracted and saved {len(ranked_candidates)} clips.")


    elif job.job_type == "render":
        if not job.clip_candidate_id:
            raise ValueError("Render job missing clip_candidate_id relation.")
            
        clip = crud.get_clip(db, job.clip_candidate_id)
        if not clip:
            raise ValueError(f"Clip candidate {job.clip_candidate_id} not found.")
            
        # 1. Update clip status
        crud.update_clip_status(db, clip.id, "rendering")
        crud.update_job(db, job.id, status="running", progress=0.2)
        
        # 2. Get segments for SRT subtitles
        segments = crud.get_segments_by_video(db, video.id)
        segments_dict = [
            {
                "start_time": s.start_time,
                "end_time": s.end_time,
                "text": s.text
            }
            for s in segments
        ]
        
        # 3. Create temp SRT file
        srt_name = f"clip_{clip.id}_subtitles.srt"
        srt_path = os.path.join(settings.TEMP_DIR, srt_name)
        render.generate_srt_file(segments_dict, clip.start_time, clip.end_time, srt_path)
        crud.update_job(db, job.id, status="running", progress=0.4)
        
        # 4. Render vertical and landscape
        logger.info(f"Rendering formats for clip {clip.id}...")
        output_base = f"clip_{clip.id}"
        render_results = render.render_clip(
            video_path=video.storage_path,
            clip_start=clip.start_time,
            clip_end=clip.end_time,
            srt_path=srt_path,
            output_base_name=output_base
        )
        crud.update_job(db, job.id, status="running", progress=0.8)
        
        # 5. Save clip exports
        logger.info("Saving exports paths to database...")
        for fmt, path in render_results.items():
            crud.create_clip_export(db, clip.id, path, srt_path, fmt)
        
        # 6. Mark done
        crud.update_clip_status(db, clip.id, "rendered")
        crud.update_job(db, job.id, status="done", progress=1.0)
        logger.info(f"Render job {job.id} done for clip {clip.id}.")

def job_worker_loop():
    logger.info("Job runner daemon worker loop starting...")
    while True:
        db = SessionLocal()
        try:
            job = crud.get_next_queued_job(db)
            if job:
                try:
                    process_job(db, job)
                except Exception as ex:
                    error_msg = f"Error processing job: {str(ex)}\n{traceback.format_exc()}"
                    logger.error(error_msg)
                    crud.update_job(db, job.id, status="failed", error_message=error_msg)
                    # Also update video or clip status to failed if applicable
                    if job.job_type in ["transcribe", "rank"]:
                        crud.update_video_status(db, job.video_id, status="failed")
                    elif job.job_type == "render" and job.clip_candidate_id:
                        crud.update_clip_status(db, job.clip_candidate_id, status="failed")
            else:
                # No job in queue, sleep
                time.sleep(settings.WORKER_POLL_INTERVAL)
        except Exception as e:
            logger.error(f"Worker exception: {e}")
            time.sleep(settings.WORKER_POLL_INTERVAL)
        finally:
            db.close()

def start_job_worker():
    """Starts the background worker thread and resets orphaned jobs."""
    # Reset any orphaned 'running' jobs back to 'queued' on startup
    db = SessionLocal()
    try:
        orphaned_jobs = db.query(models.Job).filter(models.Job.status == "running").all()
        if orphaned_jobs:
            logger.info(f"Found {len(orphaned_jobs)} orphaned 'running' jobs. Resetting to 'queued'...")
            for j in orphaned_jobs:
                j.status = "queued"
                j.progress = 0.0
            db.commit()
    except Exception as e:
        logger.error(f"Failed to reset orphaned jobs on startup: {e}")
    finally:
        db.close()

    t = threading.Thread(target=job_worker_loop, daemon=True)
    t.start()
    logger.info("Background job worker thread started.")

