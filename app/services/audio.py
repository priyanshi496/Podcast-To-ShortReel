import subprocess
import os
import logging
import time
from app.config import settings

logger = logging.getLogger(__name__)

def get_video_duration(video_path: str) -> float:
    """Get the duration of a video file using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        logger.error(f"Failed to get duration for {video_path}: {e}")
        # Default or fallback
        return 0.0

def get_video_dimensions(video_path: str) -> tuple[int, int]:
    """Get the width and height of a video file using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        parts = result.stdout.strip().split("x")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
        return 1920, 1080  # default fallback
    except Exception as e:
        logger.error(f"Failed to get dimensions for {video_path}: {e}")
        return 1920, 1080  # default fallback


def extract_and_normalize_audio(video_path: str, output_wav_name: str) -> str:
    """
    Extracts audio from video and resamples it to 16kHz, mono, 64kbps MP3.
    This dramatically reduces file size to prevent Deepgram upload timeouts.
    Returns the absolute path to the generated MP3 file.
    """
    output_path = os.path.join(settings.TEMP_DIR, output_wav_name)
    
    # Primary command: Fast extraction without loudnorm
    cmd = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", "64k",
        "-ar", "16000",
        "-ac", "1",
        output_path
    ]
    
    try:
        logger.info(f"Extracting audio (fast mode): {' '.join(cmd)}")
        start_time = time.time()
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        logger.info(f"FFmpeg extraction took {time.time() - start_time:.2f}s")
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg primary extraction failed: {e.stderr.decode('utf-8', errors='ignore')}")
        # If standard downmix fails (e.g., corrupted AAC claiming 44 channels), use robust pan filter
        logger.info("Retrying audio extraction with robust channel mapping...")
        cmd_robust = [
            "ffmpeg",
            "-y",
            "-err_detect", "ignore_err",
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-filter:a", "pan=mono|c0=c0",
            output_path
        ]
        try:
            start_time = time.time()
            subprocess.run(cmd_robust, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            logger.info(f"FFmpeg robust extraction took {time.time() - start_time:.2f}s")
            return output_path
        except subprocess.CalledProcessError as e2:
            logger.error(f"FFmpeg robust fallback failed: {e2.stderr.decode('utf-8', errors='ignore')}")
            raise e2
