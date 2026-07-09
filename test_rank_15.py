import os
import re
import json
import logging
import sys

# Add current directory to path
sys.path.append(os.getcwd())

from app.services.rank import rank_candidates
from app.services.segment import segment_transcript

logging.basicConfig(level=logging.INFO)

def parse_transcript(file_path):
    candidates = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            # Match pattern: [MM:SS.ms - MM:SS.ms] Speaker N: text
            # Or [SS.ms - SS.ms] Speaker N: text
            
            # Let's extract using a relaxed regex
            match = re.match(r'^\[([\d:\.]+) - ([\d:\.]+)\]\s*(Speaker \d+):\s*(.*)$', line)
            if not match:
                # Try format: [10.22s - 15.22s] Speaker 1: text
                match = re.match(r'^\[([\d\.]+s) - ([\d\.]+s)\]\s*(Speaker \d+):\s*(.*)$', line)
                if not match:
                    print(f"Skipping line: {line}")
                    continue
                
            start_str, end_str, speaker, text = match.groups()
            
            def parse_time(ts):
                ts = ts.replace('s', '')
                if ':' in ts:
                    parts = ts.split(':')
                    if len(parts) == 3:
                        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                    elif len(parts) == 2:
                        return float(parts[0]) * 60 + float(parts[1])
                return float(ts)
            
            start_time = parse_time(start_str)
            end_time = parse_time(end_str)
            
            candidates.append({
                "start_time": start_time,
                "end_time": end_time,
                "speaker": speaker,
                "text": text
            })
    return candidates

if __name__ == "__main__":
    transcript_path = "transcript_video_15.txt"
    parsed_lines = parse_transcript(transcript_path)
    print(f"Parsed {len(parsed_lines)} lines from transcript.")
    
    # Segment transcript into windows
    candidates = segment_transcript(parsed_lines)
    print(f"Segmented into {len(candidates)} candidates.")
    
    # Run rank_candidates
    try:
        results = rank_candidates(candidates=candidates, video_id="15", limit=5, transcript_lines=parsed_lines)
        # Only print the requested fields
        output = []
        for r in results:
            output.append({
                "start_time": r.get("start_time"),
                "end_time": r.get("end_time"),
                "virality_score": r.get("virality_score", r.get("score")), # it might be named score
                "category": r.get("category"),
                "text": r.get("transcript_excerpt", "")[:100] + "..." # snippet for context
            })
        print("\n--- RESULTS ---\n")
        print(json.dumps(output, indent=2))
    except Exception as e:
        print(f"Error running rank_candidates: {e}")
