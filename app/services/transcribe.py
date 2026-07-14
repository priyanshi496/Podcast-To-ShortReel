import logging
import mimetypes
from typing import List, Dict, Any, Optional
import requests
from app.config import settings

logger = logging.getLogger(__name__)

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


def _guess_content_type(audio_path: str) -> str:
    """Best-effort content-type detection; defaults to audio/wav since that's
    what this pipeline's audio-extraction step (extract_audio) produces."""
    guessed, _ = mimetypes.guess_type(audio_path)
    return guessed or "audio/wav"


def _fetch_deepgram_transcript(audio_path: str) -> Dict[str, Any]:
    """
    Sends the audio file to Deepgram for transcription with diarization enabled.
    Raises RuntimeError with a clear message on missing API key or request failure —
    this is now the ONLY transcription path, so a loud failure here is preferable
    to silently returning empty/wrong results.
    """
    if not settings.DEEPGRAM_API_KEY or settings.DEEPGRAM_API_KEY in ["your_deepgram_api_key_here", ""]:
        raise RuntimeError(
            "DEEPGRAM_API_KEY is not set. Add it to your .env — get one at "
            "https://console.deepgram.com (free tier available)."
        )

    params = {
        "model": settings.DEEPGRAM_MODEL,
        "language": "multi",     # nova-3 multilingual — REPLACES detect_language.
                                  # detect_language never reliably picks up Hindi on nova-3;
                                  # Hindi is only available through multilingual/code-switch mode.
        "smart_format": "true",
        "diarize": "true",       # real per-word speaker IDs — this is the whole point of the switch
        "punctuate": "true",
        "filler_words": "true",
        "sentiment": "true",     # Deepgram Audio Intelligence — feeds rank.py's virality scoring
        "topics": "true",        # Deepgram Audio Intelligence — informational, used for filtering/titles
    }
    headers = {
        "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
        "Content-Type": _guess_content_type(audio_path),
    }

    logger.info(f"Sending audio to Deepgram (model='{settings.DEEPGRAM_MODEL}', language=multi, diarize=true): {audio_path}")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with open(audio_path, "rb") as f:
                audio_data = f.read()
                
            response = requests.post(
                DEEPGRAM_URL,
                params=params,
                headers=headers,
                data=audio_data,
                timeout=3600,  # Single timeout for connect/read/write - 1hr max for huge podcasts
                )

            if response.status_code != 200:
                raise RuntimeError(f"Deepgram request failed ({response.status_code}): {response.text[:500]}")

            return response.json()
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise RuntimeError(f"Deepgram request failed after {max_retries} attempts: {e}")
            logger.warning(f"Deepgram request failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in 10s...")
            import time
            time.sleep(10)

    raise RuntimeError("Deepgram transcription failed: unexpected exit from retry loop.")


def _extract_words(deepgram_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pulls the flat per-word list (with speaker IDs and timestamps) out of
    Deepgram's response shape."""
    try:
        alternative = deepgram_json["results"]["channels"][0]["alternatives"][0]
    except (KeyError, IndexError):
        raise RuntimeError("Deepgram response did not include an alternative transcript.")

    raw_words = alternative.get("words", [])
    words = []
    for w in raw_words:
        text = (w.get("punctuated_word") or w.get("word") or "").strip()
        if not text:
            continue
        words.append({
            "text": text,
            "start": float(w.get("start", 0.0)),
            "end": float(w.get("end", 0.0)),
            "speaker": w.get("speaker"),  # raw int speaker ID from Deepgram, or None
            "confidence": float(w.get("confidence", 1.0)),
        })
    return words


def _extract_sentiment_segments(deepgram_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Pulls Deepgram's own sentence-level sentiment segments. NOTE: these are on
    Deepgram's own sentence boundaries, NOT our diarization-based segments — the
    two timelines don't line up 1:1, so this list gets time-overlapped onto our
    segments in _attach_sentiment_and_topics rather than zipped directly.
    """
    try:
        segs = deepgram_json["results"]["sentiments"]["segments"]
    except (KeyError, TypeError):
        return []
    return [{
        "start": float(s.get("start", 0.0)),
        "end": float(s.get("end", 0.0)),
        "sentiment": s.get("sentiment", "neutral"),
        "sentiment_score": float(s.get("sentiment_score", 0.0)),
    } for s in segs]


def _extract_topic_segments(deepgram_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pulls Deepgram's topic-detection segments (text spans tagged with topics)."""
    try:
        segs = deepgram_json["results"]["topics"]["segments"]
    except (KeyError, TypeError):
        return []
    return [{
        "start": float(s.get("start", 0.0)),
        "end": float(s.get("end", 0.0)),
        "topics": [t.get("topic") for t in s.get("topics", []) if t.get("topic")],
    } for s in segs]


def _attach_sentiment_and_topics(
    segments: List[Dict[str, Any]],
    sentiment_segs: List[Dict[str, Any]],
    topic_segs: List[Dict[str, Any]],
) -> None:
    """
    Mutates our diarization-based segments in place, averaging in whichever
    Deepgram sentiment/topic spans time-overlap each segment. This is what lets
    rank.py's enrich_clip() pull sentiment_score/topics straight off each
    candidate segment without needing to know about Deepgram's own timeline.
    """
    for seg in segments:
        s_start, s_end = seg["start_time"], seg["end_time"]

        overlap_sent = [d for d in sentiment_segs if d["start"] < s_end and d["end"] > s_start]
        if overlap_sent:
            seg["sentiment_score"] = round(sum(d["sentiment_score"] for d in overlap_sent) / len(overlap_sent), 3)
            seg["sentiment"] = max(overlap_sent, key=lambda d: abs(d["sentiment_score"]))["sentiment"]
        else:
            seg["sentiment_score"] = 0.0
            seg["sentiment"] = "neutral"

        overlap_topics = [t for d in topic_segs if d["start"] < s_end and d["end"] > s_start for t in d["topics"]]
        seg["topics"] = sorted(set(overlap_topics))


def _build_speaker_label_map(words: List[Dict[str, Any]]) -> Dict[Optional[int], str]:
    """
    Maps Deepgram's raw numeric speaker IDs (0, 1, 2, ...) to friendly,
    stable labels ("Speaker 1", "Speaker 2", ...) in order of first
    appearance in the audio.
    """
    label_map: Dict[Optional[int], str] = {}
    next_index = 1
    for w in words:
        spk = w.get("speaker")
        if spk not in label_map:
            label_map[spk] = f"Speaker {next_index}"
            next_index += 1
    return label_map


def _build_segments(
    words: List[Dict[str, Any]],
    speaker_label_map: Dict[Optional[int], str],
) -> List[Dict[str, Any]]:
    """
    Groups words into segments using the 3-way break rule from JayWebtech/autoshorts:
    a new segment starts whenever ANY of these are true —
      - there's a >0.9s pause since the last word
      - the speaker changed
      - the previous segment already ended on sentence-final punctuation ('.', '!', '?')

    This produces cleaner, speaker-turn-aware segments than pause-only grouping —
    a segment reliably ends at a real speaker turn even without a long silence gap,
    which is exactly the boundary rank.py's interview_discussion validation needs.
    """
    segments: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for w in words:
        speaker_label = speaker_label_map.get(w.get("speaker"), "Speaker 1")

        should_break = False
        if current is not None:
            pause = w["start"] - current["end_time"]
            speaker_changed = current["speaker"] != speaker_label
            sentence_end = current["text"].rstrip().endswith((".", "!", "?"))
            should_break = pause > 0.9 or speaker_changed or sentence_end

        if should_break and current is not None:
            segments.append(current)
            current = None

        if current is None:
            current = {
                "start_time": w["start"],
                "end_time": w["end"],
                "text": w["text"],
                "speaker": speaker_label,
                "words": [w],
                "_confidences": [w["confidence"]],
            }
        else:
            current["end_time"] = w["end"]
            current["text"] += " " + w["text"]
            current["words"].append(w)
            current["_confidences"].append(w["confidence"])

    if current is not None:
        segments.append(current)

    # Finalize: average confidence per segment, round timestamps, drop helper field
    results = []
    for seg in segments:
        confs = seg.pop("_confidences")
        avg_conf = round(sum(confs) / len(confs), 3) if confs else 1.0
        results.append({
            "start_time": round(seg["start_time"], 2),
            "end_time": round(seg["end_time"], 2),
            "text": seg["text"].strip(),
            "speaker": seg["speaker"],
            "words": seg.get("words", []),
            "confidence": min(max(avg_conf, 0.0), 1.0),
        })
    return results


def transcribe_audio(audio_path: str) -> List[Dict[str, Any]]:
    """
    Transcribes the audio file via Deepgram (with real speaker diarization) and
    returns a list of segment dictionaries: start_time, end_time, text, speaker,
    confidence, sentiment, sentiment_score, topics.

    Replaces the previous local faster-whisper pipeline entirely. Speaker labels
    now come from Deepgram's diarize=true parameter (real per-word speaker IDs),
    mapped to friendly "Speaker 1", "Speaker 2", ... labels in order of first
    appearance — instead of the previous hardcoded "Speaker 1" for every segment.

    language=multi (nova-3 multilingual) replaces the old detect_language=true —
    detect_language never reliably surfaced Hindi on nova-3; Hindi is only
    available through multilingual/code-switch mode. NOTE: there is a known
    Deepgram issue where nova-3 multi occasionally misclassifies Hindi speech
    as Spanish mid-conversation — worth spot-checking output on Hindi-English
    content until Deepgram ships language-restriction prompting for multi mode.

    sentiment/topics come from Deepgram's Audio Intelligence features and are
    time-aligned onto our diarization-based segments (see
    _attach_sentiment_and_topics) so rank.py's enrich_clip() can use them
    directly as virality-scoring signals without any extra alignment work.

    NOTE: This is now the only transcription path. If DEEPGRAM_API_KEY is missing
    or the request fails, this raises loudly rather than silently degrading —
    a broken transcription should stop the pipeline, not produce a bad transcript
    that looks fine downstream.
    """
    logger.info(f"Starting Deepgram transcription of audio: {audio_path}")

    deepgram_json = _fetch_deepgram_transcript(audio_path)

    metadata = deepgram_json.get("metadata", {})
    duration = metadata.get("duration", 0.0)
    try:
        language = deepgram_json["results"]["channels"][0].get("detected_language") \
            or metadata.get("language", "multi")
    except (KeyError, IndexError):
        language = "multi"

    words = _extract_words(deepgram_json)
    if not words:
        logger.warning("Deepgram returned zero words for this audio file.")
        return []

    speaker_label_map = _build_speaker_label_map(words)
    distinct_speakers = {spk for spk in speaker_label_map.keys() if spk is not None}

    if len(distinct_speakers) >= 2:
        logger.info(f"Diarization detected {len(distinct_speakers)} distinct speaker(s).")
    else:
        logger.warning(
            "Diarization detected only one speaker (or none) for this audio. "
            "This may genuinely be a single-speaker recording, or diarization may "
            "have failed to distinguish voices — worth spot-checking against the source "
            "if this was expected to be a multi-speaker interview."
        )

    results = _build_segments(words, speaker_label_map)

    sentiment_segs = _extract_sentiment_segments(deepgram_json)
    topic_segs = _extract_topic_segments(deepgram_json)
    _attach_sentiment_and_topics(results, sentiment_segs, topic_segs)

    logger.info(
        f"Transcription complete. Audio duration: {duration:.1f}s, language: {language}. "
        f"Built {len(results)} segments from {len(words)} words."
    )
    return results