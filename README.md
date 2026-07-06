# Podcast to Short Reel MVP API

This is a local, no-Docker FastAPI backend that automatically converts video podcasts into highly viral vertical (9:16) and landscape (16:9) short clips.

## Features
- **FastAPI Backend**: Lean asynchronous web server.
- **Local Whisper Transcription**: Local audio transcription using the optimized `faster-whisper` library.
- **Two-Stage Virality Scoring**:
  - Stage 1: Fast offline pattern-matching heuristics.
  - Stage 2: Semantic grading using NVIDIA's Llama Nemotron 120B model (via NVIDIA NIM).
- **Background Runner Thread**: Polling queue system in Python to run processing jobs reliably without external message brokers.
- **Dual FFmpeg Render Outputs**: Outputs both vertical (9:16) center-cropped video and landscape (16:9) video with custom-styled burned subtitles.

---

## Folder Structure
```text
app/
  main.py
  config.py
  db.py
  models.py
  schemas.py
  crud.py
  services/
    audio.py
    transcribe.py
    segment.py
    rank.py
    render.py
  api/
    routes_jobs.py
    routes_videos.py
    routes_clips.py
  workers/
    job_runner.py
output/      # Final rendered vertical & landscape MP4 clips
uploads/     # Uploaded source video podcasts
temp/        # Temporary WAV audio and SRT subtitle files
```

---

## Prerequisites
1. **Python 3.10+**
2. **FFmpeg & FFprobe**: Must be installed and available in your system path.
   - Mac: `brew install ffmpeg`
   - Linux: `sudo apt-get install ffmpeg`
3. **PostgreSQL**: A local PostgreSQL database instance named `podcast`.

---

## Installation & Setup

1. **Clone/Navigate to the workspace**:
   ```bash
   cd "/Users/priyanshimodi/Documents/Podcast To shortReel"
   ```

2. **Create and Activate a Virtual Environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Database Configuration**:
   Ensure PostgreSQL is running locally and you have created a database named `podcast`.
   ```sql
   CREATE DATABASE podcast;
   ```

5. **Configure environment variables**:
   Open the `.env` file and customize if needed:
   - Provide your `NVIDIA_API_KEY` (Get one from [build.nvidia.com](https://build.nvidia.com)) for Nemotron 120B scoring.
   - Adjust `DATABASE_URL` if you use a custom Postgres username/password.

---

## How to Run
Start the development server with:
```bash
uvicorn app.main:app --reload
```

---

## API Usage Flow

1. **Upload Video**:
   Make a `POST /videos/upload` with a video file (MP4).
   - This saves the video, registers it in the DB, and adds a `transcribe` job to the queue.
   - The background worker will automatically:
     1. Parse video length.
     2. Extract & normalize audio to WAV.
     3. Perform Whisper transcription and save timestamps.
     4. Enqueue a `rank` job.

2. **Rank & Candidate Generation**:
   The background worker claims the `rank` job, generates candidate clips of 30-75 seconds, uses heuristics + NVIDIA Llama Nemotron 120B to score them, and registers them under `clip_candidates`.
   - Monitor job progress using `GET /jobs/{job_id}`.
   - Get the finished ranked candidates using `GET /videos/{video_id}/clips`.

3. **Approve & Edit Candidates**:
   - Approve clips with `POST /clips/{clip_id}/approve`.
   - Adjust duration window with `POST /clips/{clip_id}/trim`.

4. **Render Approved Clips**:
   - Render the clip using `POST /clips/{clip_id}/render`.
   - The background worker will claim the job, generate an SRT subtitle file, and export **both vertical 9:16 (`_vertical.mp4`) and landscape 16:9 (`_landscape.mp4`) outputs** with burned subtitles into the `output/` folder.
   - Get export file paths using `GET /clips/{clip_id}/exports`.
