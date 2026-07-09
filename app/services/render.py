import subprocess
import os
import logging
import re
from typing import List, Dict, Any
from app.config import settings

logger = logging.getLogger(__name__)

def format_seconds_to_srt_timestamp(seconds: float) -> str:
    """Converts a float number of seconds to SRT timestamp format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    milliseconds = int(round((seconds - int(seconds)) * 1000))
    # Cap values to avoid overflow
    if milliseconds >= 1000:
        milliseconds = 999
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"

def generate_srt_file(segments: List[Dict[str, Any]], clip_start: float, clip_end: float, srt_path: str):
    """
    Filters transcript segments that overlap with [clip_start, clip_end],
    shifts their timestamps relative to the start of the clip (0.0),
    and writes them to an SRT file.
    """
    logger.info(f"Generating SRT subtitle file at: {srt_path} for range {clip_start}s to {clip_end}s")
    srt_lines = []
    index = 1
    
    for seg in segments:
        seg_start = seg["start_time"]
        seg_end = seg["end_time"]
        
        # Check if segment overlaps with the clip window
        if seg_end <= clip_start or seg_start >= clip_end:
            continue
            
        # Clamp times to the clip window
        relative_start = max(0.0, seg_start - clip_start)
        relative_end = min(clip_end - clip_start, seg_end - clip_start)
        
        # Skip extremely short segments
        if relative_end - relative_start < 0.1:
            continue
            
        start_ts = format_seconds_to_srt_timestamp(relative_start)
        end_ts = format_seconds_to_srt_timestamp(relative_end)
        
        # Clean the segment text to remove metadata like "[45.14 - 50.14] Speaker 1: "
        raw_text = seg["text"]
        clean_text = re.sub(r"^\[.*?\]\s*", "", raw_text)
        clean_text = re.sub(r"^Speaker\s*\d+:\s*", "", clean_text, flags=re.IGNORECASE)
        
        srt_lines.append(f"{index}")
        srt_lines.append(f"{start_ts} --> {end_ts}")
        srt_lines.append(clean_text)
        srt_lines.append("")  # empty line separator
        index += 1
        
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))
        
    logger.info(f"SRT file written with {index - 1} subtitles.")

def render_clip(video_path: str, clip_start: float, clip_end: float, srt_path: str, output_base_name: str) -> Dict[str, str]:
    """
    Renders two versions of the video clip:
    1. 9:16 vertical center crop with burned subtitles.
    2. 16:9 landscape with burned subtitles.
    
    Returns a dict with paths to both files: {'vertical': path, 'landscape': path}
    """
    duration = clip_end - clip_start
    
    from app.services.audio import get_video_dimensions
    width, height = get_video_dimensions(video_path)
    logger.info(f"Input video dimensions: {width}x{height}")
    
    # We must escape the SRT path for the FFmpeg subtitles filter
    escaped_srt_path = srt_path.replace("\\", "/").replace(":", "\\:")
    
    results = {}
    
    if width >= height:
        # Input is landscape (16:9) or square -> Render Landscape only
        landscape_output = os.path.join(settings.OUTPUT_DIR, f"{output_base_name}_landscape.mp4")
        vf_landscape = f"scale=1920:1080,subtitles='{escaped_srt_path}':force_style='Alignment=2,FontSize=14,PrimaryColour=&H00FFFF&'"
        # --- Shared accurate-seek + encode settings ---
        # NOTE: -ss placed AFTER -i for frame-accurate cuts (input seeking with -ss
        # before -i uses keyframe-only seeking, which can drift the clip start by
        # up to a few seconds depending on the source's GOP size — noticeable on
        # short 15-60s clips).
        cmd_landscape = [
            "ffmpeg", "-y",
            "-ss", str(clip_start),
            "-i", video_path,
            "-t", str(duration),
            "-vf", vf_landscape,
            "-c:v", "libx264",
            "-profile:v", "high",
            "-level:v", "4.2",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            landscape_output
        ]
        try:
            logger.info(f"Rendering 16:9 Landscape: {' '.join(cmd_landscape)}")
            subprocess.run(cmd_landscape, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            logger.info(f"Landscape render complete: {landscape_output}")
            results["landscape"] = landscape_output
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg landscape render failed: {e.stderr.decode('utf-8', errors='ignore')}")
            raise e
    else:
        # Input is vertical (9:16) -> Render Vertical only (no crop needed, just scale)
        vertical_output = os.path.join(settings.OUTPUT_DIR, f"{output_base_name}_vertical.mp4")
        vf_vertical = f"scale=1080:1920,subtitles='{escaped_srt_path}':force_style='Alignment=2,FontSize=16,PrimaryColour=&H00FFFF&'"
        cmd_vertical = [
            "ffmpeg", "-y",
            "-ss", str(clip_start),
            "-i", video_path,
            "-t", str(duration),
            "-vf", vf_vertical,
            "-c:v", "libx264",
            "-profile:v", "high",
            "-level:v", "4.2",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            vertical_output
        ]
        try:
            logger.info(f"Rendering 9:16 Vertical: {' '.join(cmd_vertical)}")
            subprocess.run(cmd_vertical, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            logger.info(f"Vertical render complete: {vertical_output}")
            results["vertical"] = vertical_output
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg vertical render failed: {e.stderr.decode('utf-8', errors='ignore')}")
            raise e
            
    return results

