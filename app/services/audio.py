import subprocess
import os
import logging
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
    Extracts audio from video and normalizes it to 16kHz, mono, 16-bit PCM WAV.
    Returns the absolute path to the generated WAV file.
    """
    output_path = os.path.join(settings.TEMP_DIR, output_wav_name)
    
    # ffmpeg command:
    # -y to overwrite existing output
    # -i video_path input
    # -vn disable video
    # -acodec pcm_s16le WAV output format
    # -ar 16000 resample to 16kHz
    # -ac 1 single channel (mono)
    # -filter:a loudnorm (loudness normalization filter)
    cmd = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        "-filter:a", "loudnorm",
        output_path
    ]
    
    try:
        logger.info(f"Extracting and normalizing audio: {' '.join(cmd)}")
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg error: {e.stderr.decode('utf-8', errors='ignore')}")
        # If loudnorm fails (e.g. very short audio or silent audio), retry without loudnorm
        logger.info("Retrying audio extraction without loudnorm...")
        cmd_fallback = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            output_path
        ]
        subprocess.run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return output_path
