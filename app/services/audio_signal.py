"""
audio_signal.py

Audio signal layer for the podcast-to-reel pipeline.

Provides real acoustic signals (laughter, applause, energy spikes, pitch
variance) that text-only LLM scoring cannot see. This is what turns
"the LLM guessed this was funny/emotional from words alone" into
"we measured an actual reaction in the audio."

All heavy dependencies (funasr, torch, librosa) are optional imports.
If they are not installed, every function degrades gracefully to a
neutral score (5.0) and logs a warning ONCE, so the ranking pipeline
never breaks because this module is missing its dependencies.
"""

import logging
from functools import lru_cache
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

_DEPENDENCY_WARNING_SHOWN = False
_SENSE_VOICE_MODEL = None


def _warn_missing_deps(exc: Exception):
    global _DEPENDENCY_WARNING_SHOWN
    if not _DEPENDENCY_WARNING_SHOWN:
        logger.warning(
            f"Audio signal layer unavailable ({exc}). "
            f"Install with: pip install funasr librosa torch modelscope --break-system-packages. "
            f"Falling back to neutral audio scores (5.0) for all clips."
        )
        _DEPENDENCY_WARNING_SHOWN = True


def _get_sense_voice_model():
    """Lazy-loads SenseVoiceSmall once per process. Returns None if unavailable."""
    global _SENSE_VOICE_MODEL
    if _SENSE_VOICE_MODEL is not None:
        return _SENSE_VOICE_MODEL
    try:
        from funasr import AutoModel
        # Use iic/SenseVoiceSmall for ModelScope compatibility
        _SENSE_VOICE_MODEL = AutoModel(model="iic/SenseVoiceSmall", trust_remote_code=True, disable_update=True)
        logger.info("SenseVoiceSmall audio event detection model loaded.")
        return _SENSE_VOICE_MODEL
    except Exception as e:
        _warn_missing_deps(e)
        return None


def detect_audio_events(audio_path: str, segment_start: float, segment_end: float) -> Dict[str, Any]:
    """
    Returns audio-derived signals for a candidate clip window:
      - energy_spike_ratio: peak-to-mean RMS energy ratio (excitement/intensity proxy)
      - pitch_variance: variance of voiced pitch (excitement/emotional proxy)
      - has_laughter: bool, detected via SenseVoice sound event tags
      - has_applause: bool, detected via SenseVoice sound event tags
      - audio_score: single 0-10 combined score for use in ranking

    Returns neutral defaults (audio_score=5.0) if audio_path is None or
    dependencies are unavailable, so callers never need special-case logic.
    """
    neutral = {
        "energy_spike_ratio": 1.0,
        "pitch_variance": 0.0,
        "has_laughter": False,
        "has_applause": False,
        "audio_score": 5.0,
    }

    if not audio_path:
        return neutral

    y = None
    sr = 16000
    try:
        import librosa
        import numpy as np

        duration = segment_end - segment_start
        if duration <= 0:
            return neutral

        y, sr = librosa.load(audio_path, sr=16000, offset=max(0.0, segment_start), duration=duration)
        if y is None or len(y) == 0:
            return neutral

        rms = librosa.feature.rms(y=y)[0]
        mean_rms = float(np.mean(rms)) if len(rms) else 0.0
        energy_spike_ratio = float(np.max(rms) / (mean_rms + 1e-6)) if len(rms) else 1.0

        pitches, magnitudes = librosa.piptrack(y=y, sr=sr)
        voiced_mask = magnitudes > np.median(magnitudes)
        pitch_vals = pitches[voiced_mask]
        pitch_variance = float(np.var(pitch_vals)) if pitch_vals.size > 0 else 0.0

    except Exception as e:
        _warn_missing_deps(e)
        return neutral

    has_laughter = False
    has_applause = False
    try:
        model = _get_sense_voice_model()
        if model is not None:
            # Pass the loaded segment 'y' to run event detection specifically on this window.
            # This makes it fast and specific to the clip segment instead of the whole file.
            input_data = y if y is not None else audio_path
            result = model.generate(input=input_data, cache={}, language="auto")
            events_text = ""
            if result and isinstance(result, list) and len(result) > 0:
                events_text = str(result[0].get("text", "")).lower()
            has_laughter = "laughter" in events_text or "<laughter>" in events_text
            has_applause = "applause" in events_text
    except Exception as e:
        _warn_missing_deps(e)

    # Combine into a single 0-10 audio_score
    # Energy spike ratio: typical conversational speech ~1.5-3x, excited moments ~4-8x
    energy_component = min(energy_spike_ratio / 6.0, 1.0) * 10.0
    pitch_component = min(pitch_variance / 5000.0, 1.0) * 10.0
    event_bonus = (3.0 if has_laughter else 0.0) + (2.0 if has_applause else 0.0)

    audio_score = min(10.0, (0.5 * energy_component) + (0.3 * pitch_component) + event_bonus)

    return {
        "energy_spike_ratio": round(energy_spike_ratio, 2),
        "pitch_variance": round(pitch_variance, 2),
        "has_laughter": has_laughter,
        "has_applause": has_applause,
        "audio_score": round(audio_score, 2),
    }
