from typing import List, Dict, Any

def segment_transcript(segments: List[Dict[str, Any]], target_min_duration: float = 30.0, target_max_duration: float = 75.0, slide_step_seconds: float = 20.0) -> List[Dict[str, Any]]:
    """
    Groups individual transcript segments into overlapping candidate clip windows.
    Uses a sliding window approach to capture hooks that span multiple sentences.
    Ensures windows align with the boundaries of individual segments to avoid cut-off words.
    """
    if not segments:
        return []

    candidates = []
    num_segments = len(segments)
    
    # We iterate through the segments and start a window at each segment (or skip some to avoid too many duplicates)
    for i in range(num_segments):
        start_time = segments[i]["start_time"]
        
        # Don't start a window if it's too close to the end of the video
        if segments[-1]["end_time"] - start_time < target_min_duration:
            # Not enough time left for a valid duration clip
            # We can still add one final clip that goes to the end if it meets the min duration
            if i == 0 or (segments[-1]["end_time"] - start_time >= 15.0):
                # Allow a slightly shorter clip for the very end if it's the only choice, but let's stick to min duration mostly
                pass
            else:
                break
                
        # Find segments that fit into the current window starting at index i
        current_text_parts = []
        current_end_time = start_time
        
        for j in range(i, num_segments):
            seg = segments[j]
            current_duration = seg["end_time"] - start_time
            
            # If adding this segment would exceed our max duration, stop before adding it
            if current_duration > target_max_duration:
                break
                
            current_text_parts.append(seg["text"])
            current_end_time = seg["end_time"]
            
            # If we've reached the minimum duration, check if we're at a good sentence boundary
            current_duration = current_end_time - start_time
            if current_duration >= target_min_duration:
                # Good boundary is punctuation at the end of the current segment's text
                last_char = seg["text"].strip()[-1] if seg["text"].strip() else ""
                is_sentence_end = last_char in [".", "!", "?", '"', "'"]
                
                # If it's a sentence end or we are close to the max duration, save this candidate
                if is_sentence_end or (target_max_duration - current_duration < 10.0) or (j == num_segments - 1):
                    text_content = " ".join(current_text_parts)
                    candidates.append({
                        "start_time": round(start_time, 2),
                        "end_time": round(current_end_time, 2),
                        "duration_sec": round(current_end_time - start_time, 2),
                        "text": text_content
                    })
                    # If we found a good sentence boundary, let's stop this window here
                    if is_sentence_end:
                        break
                        
    # Filter candidates to remove duplicates or highly overlapping segments if necessary.
    # To keep the MVP simple and rich, we return all unique start/end time windows.
    # We can deduplicate candidates that have identical start and end times.
    seen = set()
    unique_candidates = []
    for c in candidates:
        key = (c["start_time"], c["end_time"])
        if key not in seen:
            seen.add(key)
            unique_candidates.append(c)
            
    return unique_candidates
