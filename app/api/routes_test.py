from fastapi import APIRouter, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
import os
import uuid
import subprocess
from app.config import settings
from app.services.smart_crop import analyze_video_layout

router = APIRouter(prefix="/test-crop", tags=["Test Crop"])

@router.post("/")
async def upload_test_video(file: UploadFile = File(...)):
    # Save the file
    ext = os.path.splitext(file.filename)[1]
    filename = f"test_{uuid.uuid4().hex}{ext}"
    input_path = os.path.join(settings.UPLOAD_DIR, filename)
    with open(input_path, "wb") as f:
        f.write(await file.read())
        
    output_filename = f"test_{uuid.uuid4().hex}_vertical.mp4"
    output_path = os.path.join(settings.OUTPUT_DIR, output_filename)
    
    # Get duration
    import cv2
    cap = cv2.VideoCapture(input_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    duration = frames / fps if fps > 0 else 10
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    
    # Run smart crop
    segments = analyze_video_layout(input_path, 0, duration)
    fc_nodes = []
    concat_inputs = ""
    cmd_files = []
    
    try:
        for i, seg in enumerate(segments):
            seg_start = seg["start_time"]
            seg_end = seg["end_time"]
            seg_dur = seg_end - seg_start
            
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
                fc_nodes.append(f"[top{i}][bottom{i}]vstack=2,drawbox=x=0:y=(ih-10)/2:w=iw:h=10:color=white:t=fill,scale=1080:1920,setsar=1:1[outv{i}];")
            else:
                # Single person: Always use max vertical height to get maximum horizontal padding (607 pixels)
                crop_h = height
                crop_w = int(crop_h * (9.0 / 16.0))
                if crop_w > width:
                    crop_w = width
                    crop_h = int(crop_w * (16.0 / 9.0))
                    
                fps = 25
                step = 1.0 / fps
                t_dense = [j * step for j in range(int(seg_dur * fps) + 1)]
                keyframes = []
                for f in seg["frames"]:
                    t = f["time"] - seg["start_time"]
                    if f["people"]:
                        px, py, pw, ph = f["people"][0]
                        # Podcast inward-facing bias
                        if (px + pw/2.0) > (width / 2.0):
                            biased_cx = px + pw * 0.35
                        else:
                            biased_cx = px + pw * 0.65
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
                alpha = 0.15 # Faster tracking
                
                cmd_lines = []
                for t in t_dense:
                    target_cx = get_target_cx(t)
                    current_cx = alpha * target_cx + (1 - alpha) * current_cx
                    clamped_cx = max(crop_w/2.0, min(width - crop_w/2.0, current_cx))
                    crop_x = int(clamped_cx - crop_w/2.0)
                    cmd_lines.append(f"{t:.3f} crop@c{i} x {crop_x};")
                    
                cmd_file = os.path.join(settings.TEMP_DIR, f"cmd_{i}_{uuid.uuid4().hex[:8]}.txt")
                cmd_files.append(cmd_file)
                with open(cmd_file, "w") as f:
                    f.write("\n".join(cmd_lines))
                escaped_cmd = cmd_file.replace("\\", "/").replace(":", "\\:")
                fc_nodes.append(f"[0:v]trim=start={seg_start}:end={seg_end},setpts=PTS-STARTPTS,sendcmd=f='{escaped_cmd}',crop@c{i}={crop_w}:{crop_h}:0:0,scale=1080:1920,setsar=1:1[outv{i}];")
                
            concat_inputs += f"[outv{i}][outa{i}]"
            
        fc_nodes.append(f"{concat_inputs}concat=n={len(segments)}:v=1:a=1[outv][outa]")
        filter_complex = "".join(fc_nodes)
        
        cmd_ffmpeg = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "[outa]",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            output_path
        ]
        
        subprocess.run(cmd_ffmpeg, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        
    except subprocess.CalledProcessError as e:
        return JSONResponse(status_code=500, content={"error": e.stderr.decode('utf-8', errors='ignore')})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        for cf in cmd_files:
            if os.path.exists(cf):
                os.remove(cf)
            
    return {"url": f"/static/output/{output_filename}"}
