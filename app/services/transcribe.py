import logging
import math
from typing import List, Dict, Any
from app.config import settings

logger = logging.getLogger(__name__)
_model = None

def get_whisper_model():
    """Lazily load the Whisper model once and keep it in memory."""
    global _model
    if _model is None:
        logger.info(f"Loading Whisper model '{settings.WHISPER_MODEL}' on device '{settings.WHISPER_DEVICE}'...")
        from faster_whisper import WhisperModel

        # Force int8 on CPU for maximum speed. 
        # If ctranslate2 can't do int8, it will fall back to float32 gracefully.
        _model = WhisperModel(
            settings.WHISPER_MODEL,
            device=settings.WHISPER_DEVICE,
            compute_type="int8",          # fastest on CPU
            cpu_threads=4,                # use multiple cores
            num_workers=1,
        )
        logger.info("Whisper model loaded successfully.")
    return _model


def transcribe_audio(audio_path: str) -> List[Dict[str, Any]]:
    """
    Transcribes the audio file and returns a list of segment dictionaries.
    Each segment contains: start_time, end_time, text, speaker, confidence
    """
    model = get_whisper_model()

    logger.info(f"Starting transcription of audio: {audio_path}")

    # Key performance settings for long-form audio on CPU:
    #   beam_size=1          → greedy decoding, ~3-5x faster than beam_size=5
    #   word_timestamps=False → no word-level alignment pass (saves ~30% time)
    #   condition_on_previous_text=False → avoids compounding errors & slowdown
    #   vad_filter=True      → skip silent segments entirely
    segments_gen, info = model.transcribe(
        audio_path,
        beam_size=1,
        word_timestamps=False,
        condition_on_previous_text=False,
        vad_filter=True,            # Voice Activity Detection — skip silence
        vad_parameters=dict(
            min_silence_duration_ms=500
        ),
        language=None,              # auto-detect language
        task="translate",           # automatically translate foreign language (like Hindi) to English
    )

    logger.info(f"Audio duration: {info.duration:.1f}s, Language: {info.language} ({info.language_probability:.2%})")

    results = []
    for segment in segments_gen:
        # Convert avg_logprob to a pseudo confidence score [0, 1]
        confidence = round(math.exp(segment.avg_logprob), 3) if segment.avg_logprob else 1.0
        confidence = min(max(confidence, 0.0), 1.0)

        results.append({
            "start_time": round(segment.start, 2),
            "end_time":   round(segment.end, 2),
            "text":       segment.text.strip(),
            "speaker":    "Speaker 1",
            "confidence": confidence,
        })

    logger.info(f"Transcription complete. Transcribed {len(results)} segments.")
    return results
