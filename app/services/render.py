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
    Generates an SRT file for the clip. If word-level timestamps are available,
    generates word-by-word karaoke-style captions (active word in yellow).
    """
    logger.info(f"Generating SRT subtitle file at: {srt_path} for range {clip_start}s to {clip_end}s")
    srt_lines = []
    index = 1
    
    for seg in segments:
        seg_start = seg["start_time"]
        seg_end = seg["end_time"]
        
        if seg_end <= clip_start or seg_start >= clip_end:
            continue
            
        relative_start = max(0.0, seg_start - clip_start)
        relative_end = min(clip_end - clip_start, seg_end - clip_start)
        
        if relative_end - relative_start < 0.1:
            continue
            
        words = seg.get("words", [])
        clip_words = []
        
        if words:
            # Filter words to only those inside the clip window
            for w in words:
                w_start = w.get("start", w.get("start_time", 0.0))
                w_end = w.get("end", w.get("end_time", 0.0))
                
                if w_end <= clip_start or w_start >= clip_end:
                    continue
                    
                w_rel_start = max(0.0, w_start - clip_start)
                w_rel_end = min(clip_end - clip_start, w_end - clip_start)
                
                if w_rel_end <= w_rel_start:
                    w_rel_end = w_rel_start + 0.1
                    
                clip_words.append({
                    "text": w.get("punctuated_word", w.get("text", "")),
                    "start": w_rel_start,
                    "end": w_rel_end
                })
        else:
            # Fallback for older transcripts that lack word-level data
            # Mathematically estimate the timing of each word by splitting the duration
            raw_text = seg["text"]
            clean_text = re.sub(r"^\[.*?\]\s*", "", raw_text)
            clean_text = re.sub(r"^Speaker\s*\d+:\s*", "", clean_text, flags=re.IGNORECASE)
            
            raw_word_list = clean_text.split()
            if raw_word_list:
                word_duration = (relative_end - relative_start) / len(raw_word_list)
                for k, w_text in enumerate(raw_word_list):
                    clip_words.append({
                        "text": w_text,
                        "start": relative_start + k * word_duration,
                        "end": relative_start + (k + 1) * word_duration
                    })
                    
        if not clip_words:
            continue
            
        # Chunk words into groups of 3 to prevent massive text walls
        chunk_size = 3
        for c_idx in range(0, len(clip_words), chunk_size):
            chunk = clip_words[c_idx : c_idx + chunk_size]
            
            # Generate one cue per word for karaoke effect
            for i, current_word in enumerate(chunk):
                cue_start = current_word["start"]
                
                if i < len(chunk) - 1:
                    cue_end = chunk[i+1]["start"]
                else:
                    if c_idx + chunk_size < len(clip_words):
                        cue_end = clip_words[c_idx + chunk_size]["start"]
                    else:
                        cue_end = current_word["end"]
                        
                if cue_end <= cue_start:
                    cue_end = cue_start + 0.1
                    
                start_ts = format_seconds_to_srt_timestamp(cue_start)
                end_ts = format_seconds_to_srt_timestamp(cue_end)
                
                formatted_words = []
                for j, w in enumerate(chunk):
                    text = w["text"]
                    if j == i:
                        # Use HTML font tags for SRT compatibility
                        formatted_words.append(f'<font color="#ffff00">{text}</font>')
                    else:
                        formatted_words.append(text)
                        
                cue_text = " ".join(formatted_words)
                
                # Clean speaker labels if they somehow got into the words (rare)
                cue_text = re.sub(r"^\[.*?\]\s*", "", cue_text)
                cue_text = re.sub(r"^Speaker\s*\d+:\s*", "", cue_text, flags=re.IGNORECASE)
                
                srt_lines.append(f"{index}")
                srt_lines.append(f"{start_ts} --> {end_ts}")
                srt_lines.append(cue_text)
                srt_lines.append("")
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
            
        # Second: Render Auto-Framed 9:16 Vertical Version
        vertical_output = os.path.join(settings.OUTPUT_DIR, f"{output_base_name}_vertical.mp4")
        from app.services.smart_crop import analyze_video_layout
        import uuid
        
        segments = analyze_video_layout(video_path, clip_start, clip_end)
        fc_nodes = []
        concat_inputs = ""
        cmd_files = []
        
        try:
            for i, seg in enumerate(segments):
                seg_start = clip_start + seg["start_time"]
                seg_end = clip_start + seg["end_time"]
                seg_dur = seg["end_time"] - seg["start_time"]
                
                # Audio trim
                fc_nodes.append(f"[0:a]atrim=start={seg_start}:end={seg_end},asetpts=PTS-STARTPTS[outa{i}];")
                
                if seg["mode"] == "double":
                    target_ratio = 9.0 / 8.0
                    w1 = seg["box_left"][2]
                    w2 = seg["box_right"][2]
                    max_w = max(w1, w2) * 1.15
                    crop_w = int(max_w)
                    if crop_w > width / 2.0:
                        crop_w = int(width / 2.0)
                        
                    crop_h = int(crop_w / target_ratio)
                    if crop_h > height:
                        crop_h = height
                        crop_w = int(crop_h * target_ratio)
                    if crop_w > width:
                        crop_w = width
                        crop_h = int(crop_w / target_ratio)
                        
                    def get_centered_crop(box, c_w, c_h):
                        x, y, w, h = box
                        cx, cy = x + w/2.0, y + h * 0.4
                        cx = max(c_w/2.0, min(width - c_w/2.0, cx))
                        cy = max(c_h/2.0, min(height - c_h/2.0, cy))
                        return int(cx - c_w/2.0), int(cy - c_h/2.0)
                        
                    x1, y1 = get_centered_crop(seg["box_left"], crop_w, crop_h)
                    x2, y2 = get_centered_crop(seg["box_right"], crop_w, crop_h)
                    
                    fc_nodes.append(f"[0:v]trim=start={seg_start}:end={seg_end},setpts=PTS-STARTPTS[v{i}_base];")
                    fc_nodes.append(f"[v{i}_base]split=2[v{i}_top][v{i}_bottom];")
                    fc_nodes.append(f"[v{i}_top]crop={crop_w}:{crop_h}:{x1}:{y1}[top{i}];")
                    fc_nodes.append(f"[v{i}_bottom]crop={crop_w}:{crop_h}:{x2}:{y2}[bottom{i}];")
                    fc_nodes.append(f"[top{i}][bottom{i}]vstack=2,drawbox=x=0:y=(ih-10)/2:w=iw:h=10:color=white:t=fill,scale=1080:1920:flags=lanczos,setsar=1:1[outv{i}];")
                elif seg["mode"] == "blur_pad":
                    fc_nodes.append(f"[0:v]trim=start={seg_start}:end={seg_end},setpts=PTS-STARTPTS[v{i}_base];")
                    fc_nodes.append(f"[v{i}_base]split=2[v{i}_orig][v{i}_blur];")
                    fc_nodes.append(f"[v{i}_blur]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=luma_radius=40:luma_power=2[blurred{i}];")
                    fc_nodes.append(f"[v{i}_orig]scale=1080:1920:force_original_aspect_ratio=decrease[scaled{i}];")
                    fc_nodes.append(f"[blurred{i}][scaled{i}]overlay=(W-w)/2:(H-h)/2,setsar=1:1[outv{i}];")
                    
                else:
                    # Single person: Always use max vertical height to get maximum horizontal padding (607 pixels)
                    crop_h = height
                    crop_w = int(crop_h * (9.0 / 16.0))
                    if crop_w > width:
                        crop_w = width
                        crop_h = int(crop_w * (16.0 / 9.0))
                        
                    # Smooth cinematic tracking
                    fps = 25
                    step = 1.0 / fps
                    t_dense = [j * step for j in range(int(seg_dur * fps) + 1)]
                    
                    keyframes = []
                    for f in seg["frames"]:
                        t = f["time"] - seg["start_time"]
                        if f["people"]:
                            px, py, pw, ph, anchor_x = f["people"][0]
                            # Center the crop perfectly on the stable skeletal anchor (e.g. Nose)
                            biased_cx = anchor_x
                            keyframes.append((t, biased_cx))
                            
                    if not keyframes:
                        keyframes = [(0.0, width / 2.0), (seg_dur, width / 2.0)]
                        
                    def get_target_cx(t):
                        if t <= keyframes[0][0]: return keyframes[0][1]
                        if t >= keyframes[-1][0]: return keyframes[-1][1]
                        for k in range(len(keyframes) - 1):
                            t1, cx1 = keyframes[k]
                            t2, cx2 = keyframes[k+1]
                            if t1 <= t <= t2:
                                if t2 == t1: return cx1
                                return cx1 + ((t - t1) / (t2 - t1)) * (cx2 - cx1)
                        return width / 2.0
                        
                    current_cx = get_target_cx(0.0) # Start exactly on the person
                    desired_cx = current_cx # The camera's intended destination
                    
                    dead_zone = 50.0 # pixels
                    alpha = 0.10 # Smoother tracking for inertia
                    max_speed = 15.0 # Max pixels the camera can move per frame (simulates camera weight)
                    
                    cmd_lines = []
                    for t in t_dense:
                        target_cx = get_target_cx(t)
                        
                        # 1. Dead Zone Logic: Only pull the camera's desired destination if target escapes the dead zone
                        if target_cx > desired_cx + dead_zone:
                            desired_cx = target_cx - dead_zone
                        elif target_cx < desired_cx - dead_zone:
                            desired_cx = target_cx + dead_zone
                            
                        # 2. Camera Inertia & Speed Limit
                        diff = desired_cx - current_cx
                        step = alpha * diff
                        
                        if step > max_speed: step = max_speed
                        elif step < -max_speed: step = -max_speed
                            
                        current_cx += step
                        
                        clamped_cx = max(crop_w/2.0, min(width - crop_w/2.0, current_cx))
                        crop_x = int(clamped_cx - crop_w/2.0)
                        cmd_lines.append(f"{t:.3f} crop@c{i} x {crop_x};")
                        
                    cmd_file = os.path.join(settings.TEMP_DIR, f"cmd_{i}_{uuid.uuid4().hex[:8]}.txt")
                    cmd_files.append(cmd_file)
                    with open(cmd_file, "w") as f:
                        f.write("\n".join(cmd_lines))
                        
                    escaped_cmd = cmd_file.replace("\\", "/").replace(":", "\\:")
                    fc_nodes.append(f"[0:v]trim=start={seg_start}:end={seg_end},setpts=PTS-STARTPTS,sendcmd=f='{escaped_cmd}',crop@c{i}={crop_w}:{crop_h}:0:0,scale=1080:1920:flags=lanczos,setsar=1:1[outv{i}];")
                    
                concat_inputs += f"[outv{i}][outa{i}]"
                
            escaped_srt = ""
            if os.path.exists(srt_path):
                escaped_srt = srt_path.replace("\\", "/").replace(":", "\\:")
                fc_nodes.append(f"{concat_inputs}concat=n={len(segments)}:v=1:a=1[outv_concat][outa];")
                fc_nodes.append(f"[outv_concat]subtitles='{escaped_srt}':force_style='Alignment=2,FontSize=15,PrimaryColour=&HFFFFFF&,Outline=1.5,Shadow=1,MarginV=15'[outv]")
            else:
                fc_nodes.append(f"{concat_inputs}concat=n={len(segments)}:v=1:a=1[outv][outa]")
                
            filter_complex = "".join(fc_nodes)
            
            cmd_ffmpeg = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-filter_complex", filter_complex,
                "-map", "[outv]",
                "-map", "[outa]",
                "-c:v", "libx264",
                "-profile:v", "high",
                "-level:v", "4.2",
                "-preset", "veryfast",
                "-crf", "18",
                "-c:a", "aac",
                "-b:a", "192k",
                vertical_output
            ]
            
            logger.info(f"Executing single-pass smart crop: {' '.join(cmd_ffmpeg)}")
            subprocess.run(cmd_ffmpeg, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            results["vertical"] = vertical_output
            
        finally:
            for cf in cmd_files:
                if os.path.exists(cf):
                    os.remove(cf)
    else:
        # Input is vertical (9:16) -> Render Vertical only (no crop needed, just scale)
        vertical_output = os.path.join(settings.OUTPUT_DIR, f"{output_base_name}_vertical.mp4")
        vf_vertical = f"scale=1080:1920,subtitles='{escaped_srt_path}':force_style='Alignment=2,FontSize=15,PrimaryColour=&HFFFFFF&,Outline=1.5,Shadow=1,MarginV=15'"
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

