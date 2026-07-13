import time
import threading
import logging
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import cast
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app import crud, schemas, models
from app.config import settings
from app.services import audio, transcribe, segment, rank, render

logger = logging.getLogger(__name__)

def process_job(db: Session, job: models.Job):
    logger.info(f"Processing job {job.id} (type: {job.job_type}) for video {job.video_id}")
    
    # Update job to running
    crud.update_job(db, cast(int, job.id), status="running", progress=0.1)
    
    video = crud.get_video(db, cast(int, job.video_id))
    if not video:
        raise ValueError(f"Video {job.video_id} not found.")

    if cast(str, job.job_type) == "transcribe":
        # 1. Update video status
        crud.update_video_status(db, cast(int, video.id), status="processing")
        
        # 2. Get video duration
        logger.info("Getting video duration...")
        duration = audio.get_video_duration(cast(str, video.storage_path))
        crud.update_video_status(db, cast(int, video.id), status="processing", duration_sec=duration)
        crud.update_job(db, cast(int, job.id), status="running", progress=0.3)
        
        # 3. Extract and normalize audio
        logger.info("Extracting and normalizing audio...")
        audio_filename = f"video_{video.id}_audio.wav"
        audio_path = audio.extract_and_normalize_audio(cast(str, video.storage_path), audio_filename)
        crud.update_job(db, cast(int, job.id), status="running", progress=0.5)
        
        # 4. Transcribe using Whisper local
        logger.info("Transcribing audio...")
        transcripts_data = transcribe.transcribe_audio(audio_path)
        crud.update_job(db, cast(int, job.id), status="running", progress=0.7)
        
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
                video_id=cast(int, video.id),
                speaker=item["speaker"],
                start_time=item["start_time"],
                end_time=item["end_time"],
                text=item["text"],
                confidence=item["confidence"]
            )
            for item in transcripts_data
        ]
        crud.create_transcript_segments(db, segments_create)
        crud.update_job(db, cast(int, job.id), status="running", progress=0.9)
        
        # 6. Complete Job and update video status
        crud.update_video_status(db, cast(int, video.id), status="transcribed")
        crud.update_job(db, cast(int, job.id), status="done", progress=1.0)
        logger.info(f"Transcribe job {job.id} done. Video {video.id} status set to transcribed.")

    elif cast(str, job.job_type) == "rank":
        # 1. Fetch transcript segments
        logger.info("Fetching segments...")
        segments = crud.get_segments_by_video(db, cast(int, video.id))
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
        crud.update_job(db, cast(int, job.id), status="running", progress=0.3)
        
        # 2. Segment transcript into overlapping windows
        logger.info("Segmenting transcript into windows...")
        candidates = segment.segment_transcript(segments_dict)
        crud.update_job(db, cast(int, job.id), status="running", progress=0.5)
        
        # 3. Score and rank candidates
        logger.info(f"Scoring {len(candidates)} candidates using Nemotron batch scorer...")
        
        # Prepare audio path (re-extract if it was cleaned up by transcribe step)
        audio_filename = f"video_{video.id}_audio.wav"
        wav_path = os.path.join(settings.TEMP_DIR, audio_filename)
        audio_re_extracted = False
        if not os.path.exists(wav_path):
            storage_path_str = cast(str, video.storage_path)
            if storage_path_str and os.path.isfile(storage_path_str):
                logger.info("Temporary audio file not found. Re-extracting audio for ranking features...")
                try:
                    wav_path = audio.extract_and_normalize_audio(cast(str, video.storage_path), audio_filename)
                    audio_re_extracted = True
                except Exception as ae:
                    logger.warning(f"Failed to extract audio for ranking features: {ae}")
                    wav_path = None
            else:
                logger.info("No source video file found (uploaded transcript only). Skipping audio signal features.")
                wav_path = None
            
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
                    if wav_path and os.path.exists(wav_path):
                        os.remove(wav_path)
                        logger.info(f"Temporary audio file removed after ranking: {wav_path}")
                except Exception as e:
                    logger.warning(f"Failed to remove temp audio file {wav_path}: {e}")
                    
        crud.update_job(db, cast(int, job.id), status="running", progress=0.8)
        
        # 4. Save ranked candidates
        logger.info("Saving clip candidates to database...")
        for clip_data in ranked_candidates:
            clip_create = schemas.ClipCandidateCreate(
                video_id=cast(int, video.id),
                rank=clip_data.get("rank", 1),
                start_time=clip_data.get("start_time", 0.0),
                end_time=clip_data.get("end_time", 0.0),
                duration_sec=clip_data.get("duration_sec", 0.0),
                category=clip_data.get("category", "unknown"),
                virality_score=clip_data.get("virality_score", 0.0),
                hook_score=clip_data.get("hook_score", 0.0),
                emotion_score=clip_data.get("emotion_score", 0.0),
                curiosity_score=clip_data.get("curiosity_score", 0.0),
                surprise_score=clip_data.get("surprise_score", 0.0),
                reaction_score=clip_data.get("reaction_score", 0.0),
                context_completeness=clip_data.get("context_completeness", 0.0),
                scroll_stop_score=clip_data.get("scroll_stop_score", 0.0),
                part2_score=clip_data.get("part2_score", 0.0),
                rewatch_score=clip_data.get("rewatch_score", 0.0),
                standalone_score=clip_data.get("standalone_score", 0.0),
                cliffhanger_score=clip_data.get("cliffhanger_score", 0.0),
                reason=clip_data.get("reason", ""),
                hook_line=clip_data.get("hook_line", ""),
                transcript_excerpt=clip_data.get("transcript_excerpt", ""),
                why_viewers_keep_watching=clip_data.get("why_viewers_keep_watching", ""),
                needs_manual_review=clip_data.get("needs_manual_review", False),
                suggested_title=clip_data.get("suggested_title", "Untitled"),
                suggested_caption=clip_data.get("suggested_caption", ""),
                best_aspect_ratio=clip_data.get("best_aspect_ratio", "9:16"),
                section_type=clip_data.get("section_type", "unknown"),
                narrative_summary=clip_data.get("narrative_summary", ""),
                cut_rationale=clip_data.get("cut_rationale", ""),
                status="suggested"
            )
            crud.create_clip_candidate(db, clip_create)
            
        crud.update_job(db, cast(int, job.id), status="done", progress=1.0)
        crud.update_video_status(db, cast(int, video.id), status="processed")
        logger.info(f"Rank job {job.id} done. Extracted and saved {len(ranked_candidates)} clips.")


    elif cast(str, job.job_type) == "render":
        if not cast(int, job.clip_candidate_id):
            raise ValueError("Render job missing clip_candidate_id relation.")
            
        clip = crud.get_clip(db, cast(int, job.clip_candidate_id))
        if not clip:
            raise ValueError(f"Clip candidate {job.clip_candidate_id} not found.")
            
        storage_path_str = cast(str, video.storage_path)
        if not storage_path_str or not os.path.exists(storage_path_str):
            crud.update_clip_status(db, cast(int, clip.id), "failed")
            crud.update_job(db, cast(int, job.id), status="failed", error_message="No source video file found. Video rendering requires the original MP4 upload.")
            logger.warning(f"Render job {job.id} failed: No source video file found for video {video.id}.")
            return

        # 1. Update clip status
        crud.update_clip_status(db, cast(int, clip.id), "rendering")
        crud.update_job(db, cast(int, job.id), status="running", progress=0.2)
        
        # 2. Get segments for SRT subtitles
        segments = crud.get_segments_by_video(db, cast(int, video.id))
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
        render.generate_srt_file(segments_dict, cast(float, clip.start_time), cast(float, clip.end_time), srt_path)
        crud.update_job(db, cast(int, job.id), status="running", progress=0.4)
        
        # 4. Render vertical and landscape
        logger.info(f"Rendering formats for clip {clip.id}...")
        output_base = f"clip_{clip.id}"
        render_results = render.render_clip(
            video_path=cast(str, video.storage_path),
            clip_start=cast(float, clip.start_time),
            clip_end=cast(float, clip.end_time),
            srt_path=srt_path,
            output_base_name=output_base
        )
        crud.update_job(db, cast(int, job.id), status="running", progress=0.8)
        
        # 5. Save clip exports
        logger.info("Saving exports paths to database...")
        for fmt, path in render_results.items():
            crud.create_clip_export(db, cast(int, clip.id), path, srt_path, fmt)
        
        # 6. Mark done
        crud.update_clip_status(db, cast(int, clip.id), "rendered")
        crud.update_job(db, cast(int, job.id), status="done", progress=1.0)
        logger.info(f"Render job {job.id} done for clip {clip.id}.")

    elif cast(str, job.job_type) == "compile":
        if not cast(int, job.clip_candidate_id):
            raise ValueError("Compile job missing clip_candidate_id relation.")
            
        clip = crud.get_clip(db, cast(int, job.clip_candidate_id))
        if not clip:
            raise ValueError(f"Placeholder clip candidate {job.clip_candidate_id} not found.")
            
        storage_path_str = cast(str, video.storage_path)
        if not storage_path_str or not os.path.exists(storage_path_str):
            crud.update_clip_status(db, cast(int, clip.id), "failed")
            crud.update_job(db, cast(int, job.id), status="failed", error_message="No source video file found. Video rendering requires the original MP4 upload.")
            logger.warning(f"Compile job {job.id} failed: No source video file found for video {video.id}.")
            return

        # 1. Update status
        crud.update_clip_status(db, cast(int, clip.id), "rendering")
        crud.update_job(db, cast(int, job.id), status="running", progress=0.2)
        
        # 2. Read the request parts from the temporary JSON payload file
        import json
        payload_path = os.path.join(settings.TEMP_DIR, f"compile_request_{job.id}.json")
        if not os.path.exists(payload_path):
            crud.update_clip_status(db, cast(int, clip.id), "failed")
            crud.update_job(db, cast(int, job.id), status="failed", error_message=f"Job payload not found at {payload_path}")
            logger.error(f"Compile job {job.id} failed: payload file {payload_path} does not exist.")
            return
            
        try:
            with open(payload_path, "r", encoding="utf-8") as f:
                parts = json.load(f)
        except Exception as je:
            crud.update_clip_status(db, cast(int, clip.id), "failed")
            crud.update_job(db, cast(int, job.id), status="failed", error_message=f"Failed to parse job payload: {je}")
            logger.error(f"Compile job {job.id} failed: could not parse payload file. Error: {je}")
            return
            
        crud.update_job(db, cast(int, job.id), status="running", progress=0.4)
        
        # 3. Get segments for SRT subtitles
        segments = crud.get_segments_by_video(db, cast(int, video.id))
        segments_dict = [
            {
                "start_time": s.start_time,
                "end_time": s.end_time,
                "text": s.text
            }
            for s in segments
        ]
        
        # 4. Compile the parts
        logger.info(f"Compiling {len(parts)} parts for job {job.id}...")
        output_base = f"compile_{video.id}_{job.id}"
        
        try:
            compiled_video_path = render.compile_parts(
                video_path=cast(str, video.storage_path),
                parts=parts,
                segments_dict=segments_dict,
                output_base_name=output_base
            )
            crud.update_job(db, cast(int, job.id), status="running", progress=0.8)
            
            # 5. Create ClipExport record
            crud.create_clip_export(db, cast(int, clip.id), compiled_video_path, None, "vertical")
            
            # 6. Mark done
            crud.update_clip_status(db, cast(int, clip.id), "rendered")
            crud.update_job(db, cast(int, job.id), status="done", progress=1.0)
            logger.info(f"Compile job {job.id} done successfully. Output: {compiled_video_path}")
            
        finally:
            # Clean up the payload file
            if os.path.exists(payload_path):
                try:
                    os.remove(payload_path)
                    logger.info(f"Removed temp job payload file {payload_path}")
                except Exception as ce:
                    logger.warning(f"Failed to clean up job payload file {payload_path}: {ce}")

def run_job(job_id: int):
    db = SessionLocal()
    try:
        job = crud.get_job(db, job_id)
        if job:
            try:
                process_job(db, job)
            except Exception as ex:
                error_msg = f"Error processing job: {str(ex)}\n{traceback.format_exc()}"
                logger.error(error_msg)
                crud.update_job(db, cast(int, job.id), status="failed", error_message=error_msg)
                # Also update video or clip status to failed if applicable
                if cast(str, job.job_type) in ["transcribe", "rank"]:
                    crud.update_video_status(db, cast(int, job.video_id), status="failed")
                elif cast(str, job.job_type) == "render" and cast(int, job.clip_candidate_id) is not None:
                    crud.update_clip_status(db, cast(int, job.clip_candidate_id), status="failed")
    except Exception as e:
        logger.error(f"Worker exception in thread for job {job_id}: {e}")
    finally:
        db.close()

def job_worker_loop():
    logger.info("Job runner daemon worker loop starting with ThreadPoolExecutor...")
    executor = ThreadPoolExecutor(max_workers=3)
    active_futures = set()

    while True:
        db = SessionLocal()
        try:
            # Clean up finished futures
            done_futures = {f for f in active_futures if f.done()}
            active_futures.difference_update(done_futures)

            # If pool is not full, try to pick up a new queued job
            if len(active_futures) < 3:
                job = crud.get_next_queued_job(db)
                if job:
                    # Immediately mark as running to prevent picking it up again on the next tick
                    crud.update_job(db, cast(int, job.id), status="running")
                    
                    # Submit to thread pool
                    future = executor.submit(run_job, cast(int, job.id))
                    active_futures.add(future)
                else:
                    # No job in queue, sleep
                    time.sleep(settings.WORKER_POLL_INTERVAL)
            else:
                # Pool is full, sleep
                time.sleep(settings.WORKER_POLL_INTERVAL)
        except Exception as e:
            logger.error(f"Worker polling loop exception: {e}")
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
                j.status = "queued"  # type: ignore
                j.progress = 0.0     # type: ignore
            db.commit()
    except Exception as e:
        logger.error(f"Failed to reset orphaned jobs on startup: {e}")
    finally:
        db.close()

    t = threading.Thread(target=job_worker_loop, daemon=True)
    t.start()
    logger.info("Background job worker thread started.")

