import cv2
import logging
import statistics
import subprocess
import re
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def analyze_video_layout(video_path: str, clip_start: float, clip_end: float) -> List[Dict[str, Any]]:
    """
    Analyzes a clip at 4 FPS to detect bounding boxes, and groups them perfectly 
    into visual scenes by running an FFmpeg scene detection pass.
    """
    logger.info(f"Analyzing chunk layouts for {video_path} from {clip_start:.2f}s to {clip_end:.2f}s")
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error(f"Failed to open video for tracking: {video_path}")
        return []
        
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    duration = clip_end - clip_start
    scene_boundaries = [0.0]
    
    # 1. FFmpeg Scene Detection
    logger.info("Running precise FFmpeg scene detection...")
    cmd_scenes = [
        "ffmpeg", "-y", "-ss", str(clip_start), "-t", str(duration),
        "-i", video_path, "-vf", "select='gt(scene,0.2)',showinfo",
        "-f", "null", "-"
    ]
    
    try:
        result = subprocess.run(cmd_scenes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in result.stderr.splitlines():
            match = re.search(r'pts_time:([0-9\.]+)', line)
            if match:
                cut_time = float(match.group(1))
                if cut_time not in scene_boundaries:
                    scene_boundaries.append(cut_time)
    except Exception as e:
        logger.error(f"Scene detection failed: {e}")
        
    if not scene_boundaries or abs(scene_boundaries[-1] - duration) > 0.1:
        scene_boundaries.append(duration)
        
    logger.info(f"Detected exact scene boundaries: {scene_boundaries}")
    
    # 2. Run YOLO Tracking (at 4 fps)
    try:
        from ultralytics import YOLO
        model = YOLO("yolov8n-pose.pt") 
    except Exception as e:
        logger.error(f"Failed to load YOLO model: {e}")
        cap.release()
        return []
        
    interval_sec = 0.25
    current_time = clip_start
    
    raw_frames_data = []
    
    while current_time <= clip_end:
        cap.set(cv2.CAP_PROP_POS_MSEC, current_time * 1000)
        ret, frame = cap.read()
        if not ret:
            break
            
        relative_time = current_time - clip_start
        # Base conf 0.50, we use keypoints to filter out false positives robustly
        results = model.predict(source=frame, classes=[0], conf=0.50, verbose=False)
        boxes = results[0].boxes
        keypoints = results[0].keypoints if hasattr(results[0], 'keypoints') else None
        
        people = []
        if boxes is not None:
            for i, box in enumerate(boxes):  # type: ignore
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                w = x2 - x1
                h = y2 - y1
                conf = float(box.conf[0])
                
                if w > width * 0.12 and h > height * 0.25:
                    if keypoints is not None and keypoints.conf is not None:
                        kp_xy = keypoints.xy[i]
                        kp_conf = keypoints.conf[i]
                        
                        # 0: Nose, 1: L Eye, 2: R Eye, 3: L Ear, 4: R Ear
                        nose_c = float(kp_conf[0])
                        l_eye_c = float(kp_conf[1])
                        r_eye_c = float(kp_conf[2])
                        l_ear_c = float(kp_conf[3])
                        r_ear_c = float(kp_conf[4])
                        
                        l_shoulder_c = float(kp_conf[5])
                        r_shoulder_c = float(kp_conf[6])
                        
                        upper_body_confs = [c for c in [nose_c, l_eye_c, r_eye_c, l_ear_c, r_ear_c, l_shoulder_c, r_shoulder_c] if c > 0.30]
                        
                        # Rule 1 (Relaxed): Must have at least 2 reliable upper-body keypoints (face or shoulders)
                        if len(upper_body_confs) >= 2:
                            # Determine stable anchor_x for cinematic tracking
                            # Centering on the nose makes profile shots look unbalanced (body pushed to one side).
                            # We use the bounding box center to ensure the body is perfectly centered.
                            # The Dead Zone tracker in render.py will absorb any jitter from hand waving.
                            anchor_x = x1 + w / 2.0
                            
                            people.append((x1, y1, w, h, conf, anchor_x))
                
        people.sort(key=lambda p: p[0])
        
        mode = "single"
        final_people = []
        
        if len(people) >= 2:
            p1 = people[0]
            p2 = people[1]
            center1 = p1[0] + p1[2] / 2.0
            center2 = p2[0] + p2[2] / 2.0
            distance = abs(center2 - center1)
            
            w_ratio = min(p1[2], p2[2]) / max(p1[2], p2[2])
            
            # True 2-shot: Centers are apart horizontally, subjects are comparable in size, AND high bounding-box confidence
            # (Statues/paintings can pass the keypoint check, but their bounding-box confidence is usually lower than real humans)
            if distance > width * 0.20 and w_ratio > 0.35 and p1[4] > 0.65 and p2[4] > 0.65:
                mode = "double"
                final_people = [p1, p2]
            else:
                # Failed 2-shot (e.g. statue in background, or messy over-the-shoulder)
                # Immediately fall back to single person tracking mode and lock onto the Main Subject.
                # Mathematically identify the Main Subject: Size (Area) * Detection Confidence.
                mode = "single"
                main_subject = max(people, key=lambda p: (p[2] * p[3]) * p[4])
                final_people = [main_subject]
        elif len(people) == 1:
            final_people = [people[0]]
        else:
            mode = "blur_pad"
            
        raw_frames_data.append({
            "time": relative_time,
            "mode": mode,
            "people": [(p[0], p[1], p[2], p[3], p[5]) for p in final_people]  # extract x, y, w, h, anchor_x
        })
        
        current_time += interval_sec
        
    cap.release()
    
    if not raw_frames_data:
        return [{"start_time": 0.0, "end_time": duration, "mode": "single", "box": (width/4, height/4, width/2, height/2), "frames": []}]
        
    # 3. Align Frames strictly into Scene Boundaries
    final_layout = []
    
    for i in range(len(scene_boundaries) - 1):
        scene_start = scene_boundaries[i]
        scene_end = scene_boundaries[i+1]
        
        if scene_end - scene_start < 0.1:
            continue # Skip micro-scenes
            
        scene_frames = [f for f in raw_frames_data if scene_start <= f["time"] < scene_end]
        
        if not scene_frames:
            # Inherit from previous scene if possible
            if final_layout:
                mode = final_layout[-1]["mode"]
                box = final_layout[-1].get("box", (width/4, height/4, width/2, height/2))
                box_left = final_layout[-1].get("box_left")
                box_right = final_layout[-1].get("box_right")
            else:
                mode = "single"
                box = (width/4, height/4, width/2, height/2)
                box_left = None
                box_right = None
        else:
            box = None
            box_left = None
            box_right = None
            
            # Vote on mode
            sc = sum(1 for f in scene_frames if f["mode"] == "single")
            dc = sum(1 for f in scene_frames if f["mode"] == "double")
            bc = sum(1 for f in scene_frames if f["mode"] == "blur_pad")
            
            mode_counts = {"single": sc, "double": dc, "blur_pad": bc}
            mode = max(mode_counts, key=lambda k: mode_counts[k])
            
            if mode == "double":
                left_xs, left_ys, left_ws, left_hs = [], [], [], []
                right_xs, right_ys, right_ws, right_hs = [], [], [], []
                for f in scene_frames:
                    if len(f["people"]) >= 2:
                        left_xs.append(f["people"][0][0])
                        left_ys.append(f["people"][0][1])
                        left_ws.append(f["people"][0][2])
                        left_hs.append(f["people"][0][3])
                        
                        right_xs.append(f["people"][1][0])
                        right_ys.append(f["people"][1][1])
                        right_ws.append(f["people"][1][2])
                        right_hs.append(f["people"][1][3])
                        
                if not left_xs:
                    mode = "single"
                else:
                    box_left = (statistics.median(left_xs), statistics.median(left_ys), statistics.median(left_ws), statistics.median(left_hs))
                    box_right = (statistics.median(right_xs), statistics.median(right_ys), statistics.median(right_ws), statistics.median(right_hs))
                    
            if mode == "single":
                xs, ys, ws, hs = [], [], [], []
                for f in scene_frames:
                    if f["people"]:
                        xs.append(f["people"][0][0])
                        ys.append(f["people"][0][1])
                        ws.append(f["people"][0][2])
                        hs.append(f["people"][0][3])
                        
                if not xs:
                    box = (width/4, height/4, width/2, height/2)
                else:
                    box = (statistics.median(xs), statistics.median(ys), statistics.median(ws), statistics.median(hs))
                    
        if mode == "double":
            final_layout.append({
                "start_time": scene_start,
                "end_time": scene_end,
                "mode": mode,
                "box_left": box_left,
                "box_right": box_right
            })
        elif mode == "single":
            final_layout.append({
                "start_time": scene_start,
                "end_time": scene_end,
                "mode": mode,
                "box": box,
                "frames": scene_frames if scene_frames else [{"time": scene_start, "people": []}]
            })
        elif mode == "blur_pad":
            final_layout.append({
                "start_time": scene_start,
                "end_time": scene_end,
                "mode": mode
            })
            
    return final_layout
