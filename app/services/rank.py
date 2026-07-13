import logging
import json
import re
import os
from typing import List, Dict, Any, Optional, Tuple, cast
from app.config import settings
from app.services.audio_signal import detect_audio_events

logger = logging.getLogger(__name__)

_RATE_LIMITED_PROVIDERS = set()

from app.services.prompts import (
    CATEGORY_CONFIG,
    DEFAULT_CATEGORY,
    LEAN_SCHEMA_BLOCK,
    LEAN_SCHEMA_BLOCK_LOCAL,
    build_moment_finder_prompt,
    build_moment_editor_prompt,
    build_system_prompt
)

# ============================================================
# GENERIC (CATEGORY-AGNOSTIC) PIPELINE TUNABLES
# ============================================================

# Stage 1 used to hard-cap at 6 candidates regardless of video length or category.
# That meant a 44-minute video with 591 windows only ever let 6 of them compete for
# the LLM's attention. Raised so richer/longer content gets a fair shot.
STAGE1_CANDIDATE_POOL_SIZE = 18

# How far (in seconds) we're willing to extend a clip's start backward or end forward
# to reach a clean sentence boundary. Applies to every category equally.
MAX_BOUNDARY_LOOKBACK_SEC = 20.0
MAX_BOUNDARY_LOOKAHEAD_SEC = 90.0  # must be >= max category duration so repair can always reach min_duration floor
MAX_CROSSABLE_GAP_SEC = 3.0  # gaps larger than this between consecutive segments are treated as a hard wall
                             # (real edit cuts / hard topic breaks in the source video) — never walked across
                             # during boundary repair, regardless of remaining lookahead/lookback budget.

# Phrases that are strong, category-agnostic signals a moment matters — the speaker
# themself is telling you it's important. Small heuristic bonus, not a hard rule.
SELF_FLAGGED_IMPORTANCE_PHRASES = [
    "key line", "most important", "remember this", "this is the answer",
    "listen carefully", "the important thing is", "this is the secret",
    "write this down", "the truth is",
]

# --- LOW-SUBSTANCE / PURE-FILLER DETECTION (category-agnostic) ---
# Catches clips that are grammatically clean (pass sentence-boundary repair)
# but carry almost no independent information — e.g. a run of compliments and
# acknowledgments like "Yeah." / "100%" / "It's true." This is a real,
# separate failure mode from mid-sentence cuts: nothing is broken, there's
# just nothing here a stranger would stop scrolling for.
FILLER_WORDS = {
    "yeah", "yes", "no", "okay", "ok", "true", "correct", "right", "100", "100%",
    "sure", "exactly", "totally", "hmm", "wow", "really", "great", "nice",
    "good", "fine", "alright", "thanks", "thank", "cool", "definitely",
    "absolutely", "haha", "lol",
}
# Common connector/filler words stripped before judging a sentence's remaining
# content — these carry no independent meaning on their own.
IGNORABLE_CONNECTOR_WORDS = {
    "it's", "its", "that's", "thats", "this", "is", "so", "well", "um", "uh",
    "like", "i", "mean", "you", "know",
}
LOW_SUBSTANCE_REVIEW_THRESHOLD = 0.4    # above this: flag for review + score penalty
LOW_SUBSTANCE_HARD_BLOCK_THRESHOLD = 0.75  # above this: block from auto-render entirely

# ============================================================
# SAFETY GATE, BOUNDS CHECKERS, AND TITLE CLEANERS
# ============================================================

# Keywords representing potential moderation issues.
# In production, replace check_content_safety with OpenAI's moderation endpoint.
CONTENT_SAFETY_FLAGS = [
    # Self-harm
    "suicide", "kill myself", "end my life", "self-harm", "cutting myself",
    # Unverified medical / dangerous
    "cure cancer with", "vaccines cause autism", "bleach cures", "covid is a hoax",
    # Legal risk / violence / crime
    "how to make a bomb", "illegal drugs", "steal credit cards", "hack into bank"
]

def check_content_safety(text: str) -> Tuple[bool, Optional[str]]:
    """
    Checks if a segment text triggers any content safety keyword flags.
    Returns (is_safe, flag_reason).
    """
    if not text:
        return True, None
    text_lower = text.lower()
    for flag in CONTENT_SAFETY_FLAGS:
        if flag in text_lower:
            return False, f"Flagged keyword: '{flag}'"
    return True, None

def clean_title_from_hook(hook: str) -> str:
    """
    Cleans a hook line to be a premium, properly formatted clip title.
    Trims to a word boundary near 50 characters, capitalizes properly,
    and removes trailing/leading punctuation fragments (dangling commas, hyphens, etc.).
    """
    if not hook:
        return "Untitled Clip"
        
    title = hook.strip()
    # Strip common leading/trailing quotes or punctuation
    title = title.strip('\'"-,.?!;:…')
    
    # If title is too long, truncate gracefully at a word boundary
    if len(title) > 50:
        truncated = title[:50]
        last_space = truncated.rfind(' ')
        if last_space > 20:
            title = truncated[:last_space]
        else:
            title = truncated
            
    # Capitalize the first letter of each word (Title Case)
    title = " ".join([word.capitalize() for word in title.split()])
    return title

# Hindi/Hinglish trailing words that signal an unfinished thought even when
# Deepgram's smart_format has punctuated the segment as if it were a full stop.
# Extremely common in spoken Hindi as continuation markers, conditionals, or tags —
# a clip should never end on one of these regardless of what punctuation follows.
# Shared by check_sentence_boundary() and _ends_clean() so both agree on what
# "clean" means, instead of the two silently drifting apart.
_INCOMPLETE_TRAILING_WORDS = {
    # English
    "and", "but", "or", "so", "because", "although", "while", "if", "then",
    "the", "a", "an", "of", "to", "with",
    # Hindi (Devanagari)
    "और", "लेकिन", "पर", "मगर", "क्योंकि", "जो", "कि", "अगर", "इसलिए",
    "तो", "भी", "ना", "जब", "जबकि", "फिर",
    # Romanized Hindi (Deepgram sometimes transliterates code-switched speech)
    "aur", "lekin", "kyunki", "jo", "ki", "agar", "isliye", "toh", "bhi",
    "na", "jab", "phir",
}


def check_sentence_boundary(text: str) -> bool:
    """
    Checks if the transcript text ends on a clean sentence/clause boundary.
    Flags clips that end on conjunctions or lack terminal punctuation —
    covers both English and Hindi/Hinglish connector/tag words (see
    _INCOMPLETE_TRAILING_WORDS), since Deepgram's smart_format punctuates
    Hindi code-switched speech with a "." even mid-thought fairly often.
    """
    if not text:
        return True

    text = text.strip()

    # Check for terminal punctuation or ellipsis
    terminal_punctuations = ('.', '?', '!', '"', "'", '…', '...')
    if not text.endswith(terminal_punctuations):
        last_words = text.lower().split()
        if last_words:
            last_word = last_words[-1].strip(',.?!;:"\'')
            if last_word in _INCOMPLETE_TRAILING_WORDS:
                return False
        return False

    # Punctuation alone isn't trustworthy for Hindi/Hinglish — check the last
    # real word even when the segment technically ends with a period.
    stripped = text.rstrip('.?!"\'…')
    words = stripped.split()
    if words:
        last_word = words[-1].lower().strip(',;:"\'')
        if last_word in _INCOMPLETE_TRAILING_WORDS:
            return False

    return True

def compute_low_substance_ratio(text: str) -> float:
    """
    Category-agnostic check for clips that are grammatically clean but
    informationally empty — runs of backchannel/affirmation sentences like
    "Yeah.", "100%", "It's true." with no real claim, story, or hook.

    Splits text into sentences, strips common connector/filler words from
    each, and counts a sentence as "filler" if nothing but filler words
    remain. Returns the fraction of sentences (by count, not length — a
    single long substantive sentence shouldn't be diluted by several short
    filler ones) classified as filler. 0.0 = no filler detected.
    """
    if not text or not text.strip():
        return 0.0

    raw_sentences = re.split(r'[.?!…]+', text)
    sentences = [s.strip() for s in raw_sentences if s.strip()]

    if len(sentences) < 3:
        # Too few sentences to judge meaningfully — don't penalize short,
        # punchy clips just for having few sentences.
        return 0.0

    filler_count = 0
    for sentence in sentences:
        # Normalize and strip punctuation
        normalized = re.sub(r"[^\w\s%']", "", sentence.lower()).strip()
        
        # Check direct phrase matches
        if normalized in [
            "correct me if i'm wrong", 
            "correct me if i am wrong",
            "if i'm not wrong",
            "you know what i mean",
            "tell me if i'm wrong"
        ]:
            filler_count += 1
            continue

        words = re.findall(r"[a-z0-9%']+", sentence.lower())
        content_words = [w for w in words if w not in IGNORABLE_CONNECTOR_WORDS]
        if not content_words:
            filler_count += 1
            continue
        if all(w in FILLER_WORDS for w in content_words):
            filler_count += 1

    return filler_count / len(sentences)

def deduplicate_timeline(clips: List[Dict[str, Any]], max_overlap_ratio: float = 0.20) -> List[Dict[str, Any]]:
    """
    Deduplicates a list of clips based on timeline overlap.
    Keeps the higher-ranked clip and filters out lower-ranked clips
    that overlap by more than max_overlap_ratio.
    """
    deduped = []
    for clip in clips:
        overlap_found = False
        s1, e1 = clip["start_time"], clip["end_time"]
        dur1 = e1 - s1
        for existing in deduped:
            s2, e2 = existing["start_time"], existing["end_time"]
            dur2 = e2 - s2
            intersection = max(0.0, min(e1, e2) - max(s1, s2))
            if intersection > 0.0:
                overlap_ratio = intersection / min(dur1, dur2)
                if overlap_ratio > max_overlap_ratio:
                    overlap_found = True
                    break
        if not overlap_found:
            deduped.append(clip)
    return deduped




# ============================================================
# OPTIONAL DEPENDENCIES (each degrades gracefully if missing)
# ============================================================

try:
    from pydantic import BaseModel, Field
    from typing import Literal

    class SentenceAnalysisModel(BaseModel):
        text: str
        role: Literal[
            "HOOK", "SETUP", "QUESTION", "ANSWER", "CLAIM", "COUNTERCLAIM", "CHALLENGE", "TEASE",
            "REVEAL", "EVIDENCE", "EXAMPLE", "REACTION", "CONCLUSION", "CALLBACK", "PAIN",
            "STRUGGLE", "SHIFT", "LESSON", "INCITING_INCIDENT", "ESCALATION", "HIGHEST_TENSION",
            "RESOLUTION", "CONTEXT", "PAUSE", "JUSTIFICATION"
        ]
        importance: float = Field(ge=0.0, le=10.0)
        starts_arc: bool
        ends_arc: bool
        creates_curiosity: bool
        resolves_curiosity: bool
        creates_tension: bool
        resolves_tension: bool

    class ClipCandidateModel(BaseModel):
        start_time: float
        end_time: float
        clip_strategy: Literal["cliffhanger", "payoff"]
        hook_line: str
        reason: str = Field(max_length=200)
        curiosity_score: int = Field(ge=0, le=10)
        hook_score: int = Field(ge=0, le=10)
        sentence_analysis: List[SentenceAnalysisModel] = Field(default_factory=list)

    class ClipResponseModel(BaseModel):
        video_id: str
        clips: List[ClipCandidateModel]

    class DiscoveredMoment(BaseModel):
        start_segment_id: int
        end_segment_id: int
        opportunity_type: str
        key_idea: str
        mandatory_anchor_line: str
        mandatory_context_lines: List[str] = Field(default_factory=list)
        why_viral: str

    class MomentFinderResponse(BaseModel):
        video_id: str
        moments: List[DiscoveredMoment]

    class EditedClip(BaseModel):
        start_segment_id: int
        end_segment_id: int
        clip_strategy: str
        editorial_style: str
        hook_line: str
        reason: str
        curiosity_score: int = Field(ge=0, le=10)
        hook_score: int = Field(ge=0, le=10)
        surprise_score: int = Field(ge=0, le=10)
        emotion_score: int = Field(ge=0, le=10)
        reaction_score: int = Field(ge=0, le=10)
        standalone_score: int = Field(ge=0, le=10)

    class EditedClipsResponse(BaseModel):
        video_id: str
        clips: List[EditedClip]

    _PYDANTIC_AVAILABLE = True
except Exception:
    _PYDANTIC_AVAILABLE = False

try:
    import instructor
    _INSTRUCTOR_AVAILABLE = True
except Exception:
    _INSTRUCTOR_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer, util as st_util
    _SBERT_AVAILABLE = True
except Exception:
    SentenceTransformer = None
    st_util = None
    _SBERT_AVAILABLE = False

_embedding_model = None
_anchor_embeddings = None
_anchor_keys = None


def _load_embedding_classifier():
    """Lazy-loads the sentence-transformer model + category anchor embeddings once."""
    global _embedding_model, _anchor_embeddings, _anchor_keys
    if _embedding_model is not None:
        return
    if SentenceTransformer is None:
        raise ImportError("SentenceTransformer is not available")
    try:
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
    except Exception:
        # Fall back to downloading if it's the first run and not cached yet
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    _anchor_keys = list(CATEGORY_CONFIG.keys())
    anchor_texts = [CATEGORY_CONFIG[k]["description"] for k in _anchor_keys]
    _anchor_embeddings = _embedding_model.encode(anchor_texts, convert_to_tensor=True)


def embedding_classify_category(full_transcript_text: str) -> Tuple[Optional[str], float]:
    """
    Zero-shot classification via sentence embedding similarity against each
    category's description. No API cost, no training data needed, deterministic.
    Returns (None, 0.0) if sentence-transformers isn't installed.
    """
    if not _SBERT_AVAILABLE:
        return None, 0.0
    try:
        _load_embedding_classifier()
        if _embedding_model is None or _anchor_embeddings is None or _anchor_keys is None or st_util is None:
            return None, 0.0
        text_embedding = _embedding_model.encode(full_transcript_text[:3000], convert_to_tensor=True)
        scores = st_util.cos_sim(text_embedding, _anchor_embeddings)[0]
        best_idx = int(scores.argmax())
        return _anchor_keys[best_idx], float(scores[best_idx])
    except Exception as e:
        logger.warning(f"Embedding classifier failed: {e}")
        return None, 0.0


# ============================================================
# STAGE 1: Heuristic pre-filter (kept as a lightweight signal,
# no longer the primary classifier — see classify_content_category)
# ============================================================

def heuristic_pre_filter_score(text: str) -> float:
    """
    Stage 1: Fast keyword/heuristic scoring of a transcript candidate.
    Used only as a supporting signal in the virality formula, NOT for
    category classification anymore.
    """
    text_lower = text.lower()
    score = 0.0

    curiosity_keywords = ["why", "how", "secret", "mystery", "question", "discovered", "realized", "hidden", "truth", "knows"]
    for kw in curiosity_keywords:
        if kw in text_lower:
            score += 1.0

    surprise_keywords = ["suddenly", "shocking", "unexpected", "surprise", "crazy", "unbelievable", "reveal", "wow", "turns out"]
    for kw in surprise_keywords:
        if kw in text_lower:
            score += 1.2

    emotion_keywords = ["felt", "hurt", "angry", "love", "hate", "cry", "laugh", "happy", "scared", "fear", "grief", "pain"]
    for kw in emotion_keywords:
        if kw in text_lower:
            score += 1.0

    conflict_keywords = ["wrong", "lie", "cheat", "fight", "dangerous", "warning", "stop", "never", "actually", "but", "disagree"]
    for kw in conflict_keywords:
        if kw in text_lower:
            score += 1.1

    words = text_lower.split()
    word_count = len(words)
    if 80 <= word_count <= 180:
        score += 2.0
    elif word_count < 40:
        score -= 3.0

    # Category-agnostic signal: the speaker explicitly flags something as important.
    # This is one of the strongest real-world virality signals and costs nothing to check.
    for phrase in SELF_FLAGGED_IMPORTANCE_PHRASES:
        if phrase in text_lower:
            score += 2.5

    return max(0.0, min(score, 10.0))


def heuristic_classify_category(full_transcript_text: str) -> str:
    """
    LAST-RESORT fallback only — used if BOTH the embedding classifier
    and the NIM LLM classifier are unavailable. Kept for total pipeline
    resilience, not as a primary path anymore.
    """
    text = full_transcript_text.lower()

    votes = {k: 0 for k in CATEGORY_CONFIG.keys()}

    narrative_kw = ["story", "one day", "then i", "i remember", "years ago", "after that", "so i went", "i was sitting", "he came", "she said"]
    comedy_kw = ["joke", "funny", "laugh", "hilarious", "kidding", "lol", "haha"]
    interview_kw = ["so tell me", "what do you think", "let me ask", "question is", "in your opinion", "would you say"]
    motivational_kw = ["believe", "accept", "myself", "worth", "enough", "grow", "heal", "trust yourself", "you deserve", "journey"]
    controversial_kw = ["disagree", "wrong", "unpopular opinion", "controversial", "everyone thinks", "actually no", "hot take", "i hate", "overrated"]

    for kw in narrative_kw:
        votes["story_narrative"] += text.count(kw)
    for kw in comedy_kw:
        votes["comedy_punchline"] += text.count(kw)
    for kw in interview_kw:
        votes["interview_discussion"] += text.count(kw)
    for kw in motivational_kw:
        votes["motivational_emotional"] += text.count(kw)
    for kw in controversial_kw:
        votes["controversial_hot_take"] += text.count(kw)

    best_category = max(votes, key=votes.__getitem__)
    if votes[best_category] == 0:
        return DEFAULT_CATEGORY

    logger.info(f"Last-resort heuristic classifier votes: {votes} -> chose '{best_category}'")
    return best_category


# ============================================================
# LLM PROVIDER FALLBACK CHAIN (category-agnostic)
# ============================================================
# NVIDIA NIM and Groq both expose an OpenAI-compatible chat.completions API,
# so the exact same prompts, Instructor/Pydantic guaranteed-schema path, and
# legacy manual-parse path work unchanged for either — only base_url, api_key,
# and model name differ. This means the fallback is purely an infra concern:
# it applies identically no matter which CATEGORY_CONFIG entry is active.

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# A solid general-purpose default if GROQ_MODEL isn't set in settings/.env.
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


def _get_llm_providers() -> List[Dict[str, Any]]:
    """
    Returns an ordered list of usable LLM provider configs.

    Priority order (highest → lowest):
      1. OpenRouter   — cloud primary (fastest, high context, highly reliable)
      2. NVIDIA NIM   — cloud fallback 1 (secondary access)
      3. Ollama       — local last resort (free but slow and prone to hallucination on long prompts)

    Any provider missing its key / flag is silently skipped, so the
    chain degrades gracefully without any category-specific logic.
    """
    providers: List[Dict[str, Any]] = []

    # ── 1. OpenRouter (cloud primary) ─────────────────────────────────────────
    openrouter_key = getattr(settings, "OPENROUTER_API_KEY", None)
    if openrouter_key and openrouter_key not in ["your_openrouter_api_key_here", ""]:
        providers.append({
            "name": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": openrouter_key,
            "model": getattr(settings, "OPENROUTER_MODEL", "google/gemma-4-31b-it:free"),
            "extra_body": {},
        })

    # ── 2. NVIDIA NIM (cloud fallback 1) ────────────────────────────────────────
    nvidia_key = getattr(settings, "NVIDIA_API_KEY", None)
    if nvidia_key and nvidia_key not in ["your_nvidia_api_key_here", ""]:
        nvidia_model = getattr(settings, "NVIDIA_MODEL", "nvidia/nemotron-3-super-120b-a12b")
        # NVIDIA's NIM Nemotron endpoint accepts the reasoning-suppression flag;
        # other endpoints (like Meta Llama) will return errors if passed.
        nvidia_extra_body = {"chat_template_kwargs": {"enable_thinking": False}} if "nemotron" in nvidia_model.lower() else {}
        providers.append({
            "name": "nvidia_nim",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key": nvidia_key,
            "model": nvidia_model,
            "extra_body": nvidia_extra_body,
        })

    # ── 4. Ollama (local last resort) ─────────────────────────────────────────
    # Only used if all cloud providers fail. Slow and prone to hallucination on
    # large JSON schema prompts, but free and offline-capable.
    ollama_enabled = getattr(settings, "OLLAMA_ENABLED", False)
    if ollama_enabled:
        ollama_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434/v1")
        ollama_model = getattr(settings, "OLLAMA_MODEL", "qwen3:8b")
        providers.append({
            "name": "ollama",
            "base_url": ollama_url,
            # Ollama ignores the API key but the OpenAI client requires a non-empty value.
            "api_key": "ollama",
            "model": ollama_model,
            # num_ctx=16384 ensures the large scoring prompts (12k+ tokens) fit.
            # Ollama passes unknown keys in extra_body as model options.
            "extra_body": {"options": {"num_ctx": 16384}},
        })
        logger.info(f"Ollama provider enabled: model='{ollama_model}' at '{ollama_url}'")

    return providers



# ============================================================
# AUTO CATEGORY CLASSIFICATION — primary path is embeddings,
# LLM is only used to break ties / low-confidence cases
# ============================================================

EMBEDDING_CONFIDENCE_THRESHOLD = 0.35


def _classify_via_provider(
    provider: Dict[str, str],
    classify_prompt: str,
    truncated_text: str,
    valid_categories: List[str],
) -> Optional[str]:
    """Attempts classification via a single provider. Returns category or None on failure."""
    from openai import OpenAI
    import httpx

    client = OpenAI(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
        http_client=httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0)),
    )

    completion = client.chat.completions.create(
        model=provider["model"],
        messages=[
            {"role": "system", "content": classify_prompt},
            {"role": "user", "content": truncated_text},
        ],
        temperature=0.2,
        top_p=0.9,
        max_tokens=200,
        extra_body=provider.get("extra_body") or {},
        stream=False,
    )

    if hasattr(completion, "usage") and completion.usage:
        prompt_tokens = getattr(completion.usage, "prompt_tokens", 0)
        completion_tokens = getattr(completion.usage, "completion_tokens", 0)
        total_tokens = getattr(completion.usage, "total_tokens", 0)
        logger.info(
            f"[{provider['name']}] Classification Token Usage: Prompt={prompt_tokens}, "
            f"Completion={completion_tokens}, Total={total_tokens} (model='{provider['model']}')"
        )

    content = completion.choices[0].message.content
    if not content:
        logger.warning(f"[{provider.get('name', 'Unknown')}] returned empty or None content.")
        return None

    raw = content.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0]
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0]

    parsed = json.loads(raw.strip())
    category = parsed.get("category", "").strip()

    if category not in valid_categories:
        logger.warning(f"[{provider['name']}] returned unknown category '{category}'.")
        return None

    logger.info(f"[{provider['name']}] classified transcript as '{category}' (confidence={parsed.get('confidence')})")
    return category


def classify_content_category(full_transcript_text: str) -> str:
    """
    Classification order:
    1. Embedding zero-shot classifier (fast, free, deterministic) — PRIMARY.
    2. If confidence is low or embeddings unavailable, verify/override with an
       LLM call, trying each configured provider in order (NVIDIA NIM, then
       Groq) until one succeeds.
    3. If every LLM provider is unavailable/fails, fall back to keyword voting.
    """
    valid_categories = list(CATEGORY_CONFIG.keys())

    embed_category, confidence = embedding_classify_category(full_transcript_text)

    if embed_category is not None and confidence >= EMBEDDING_CONFIDENCE_THRESHOLD:
        logger.info(f"Embedding classifier chose '{embed_category}' (confidence={confidence:.3f})")
        return embed_category

    if embed_category is not None:
        logger.info(
            f"Embedding classifier low confidence ({confidence:.3f}) for '{embed_category}'. "
            f"Verifying with LLM classifier..."
        )

    # Exclude Ollama (local) from simple classification to save 20+ seconds of CPU/GPU latency
    providers = [p for p in _get_llm_providers() if p["name"] != "ollama"]
    # Classification is low-stakes and high-frequency, same profile as Stage 1 —
    # use the same cheap discovery model on OpenRouter rather than the stronger default.
    discovery_model = getattr(settings, "OPENROUTER_MODEL_DISCOVERY", None)
    if discovery_model:
        for p in providers:
            if p["name"] == "openrouter":
                p["model"] = discovery_model
    if not providers:
        logger.warning("No LLM provider API keys configured. Using embedding result if available, else heuristic fallback.")
        return embed_category if embed_category else heuristic_classify_category(full_transcript_text)

    classify_prompt = (
        "You are a content classifier for short-form video clipping.\n"
        "Read the transcript and classify its OVERALL dominant style into exactly one of these categories:\n\n"
        f"{json.dumps({k: v['description'] for k, v in CATEGORY_CONFIG.items()}, indent=2)}\n\n"
        "Rules:\n"
        "- Pick the single category that best represents the majority of the transcript's content.\n"
        "- If it's a solo narrator telling personal life stories/anecdotes with a beginning-middle-end "
        "arc, choose 'story_narrative' even if there are some funny or emotional moments within it.\n"
        "- Only choose 'comedy_punchline' if the transcript is dominantly joke-structured stand-up.\n"
        "- Only choose 'interview_discussion' if there are multiple speakers engaging in Q&A or debate.\n"
        "- Return ONLY valid JSON, no markdown, no explanation:\n"
        '{"category": "<one_of_the_keys_above>", "confidence": 0.0}'
    )
    truncated_text = full_transcript_text[:6000]

    for provider in providers:
        try:
            logger.info(f"Attempting classification via provider '{provider['name']}'...")
            category = _classify_via_provider(provider, classify_prompt, truncated_text, valid_categories)
            if category:
                return category
            logger.warning(f"[{provider['name']}] classification returned no usable category, trying next provider...")
        except Exception as e:
            logger.error(f"[{provider['name']}] classification failed ({e}). Trying next provider...")

    logger.error("All LLM providers failed for classification. Falling back to embedding/heuristic.")
    return embed_category if embed_category else heuristic_classify_category(full_transcript_text)



# ============================================================
# SPEAKER-TURN / EXCHANGE-COMPLETENESS VALIDATION
# (fixes: "LLM picked a clip that's just a question, no answer")
# ============================================================

def _detect_qa_turns(lines: List[Dict[str, Any]]) -> bool:
    """
    Fallback exchange detector when diarization gives only one speaker label.
    Looks for a Q/A pattern: at least one question line (ends '?') followed by
    at least one non-question line, OR a clear turn gap (>0.8s silence between lines).
    Returns True if the clip looks like a real two-person exchange.
    """
    has_question = False
    has_answer_after_question = False
    last_end = None
    has_turn_gap = False

    for line in lines:
        text = (line.get("text") or "").strip()
        t_start = line.get("start_time", 0)
        t_end = line.get("end_time", 0)

        # Check for a silence gap > 0.8s between lines (speaker turn indicator)
        if last_end is not None and (t_start - last_end) > 0.8:
            has_turn_gap = True

        if text.endswith("?"):
            has_question = True
        elif has_question:
            has_answer_after_question = True

        last_end = t_end

    return (has_question and has_answer_after_question) or has_turn_gap


def validate_exchange_completeness(
    start: float,
    end: float,
    transcript_lines: Optional[List[Dict[str, Any]]],
) -> Tuple[bool, str]:
    """
    For interview_discussion clips: checks whether the clip window contains
    either a real exchange (2+ speakers, doesn't end mid-question) OR a
    self-contained standalone insight (1 speaker telling a coherent fact/thought).

    Returns (is_valid, reason_if_invalid). If transcript_lines is not
    provided, validation is skipped and always returns (True, "").
    """
    if not transcript_lines:
        return True, ""

    EDGE_TOLERANCE = 0.15  # seconds — accept segments ending just past clip boundary
    lines_in_window = [
        l for l in transcript_lines
        if l.get("start_time", 0) >= (start - EDGE_TOLERANCE)
        and l.get("end_time", 0) <= (end + EDGE_TOLERANCE)
    ]

    if len(lines_in_window) < 1:
        return False, "Clip window contains 0 transcript lines."

    first_line_text = (lines_in_window[0].get("text") or "").strip()
    
    # Define referents/pronouns that signify a dangling sentence (missing context)
    dangling_context_words = {"it", "its", "it's", "they", "their", "them", "he", "him", "his", "she", "her", "this", "these", "that", "those", "but", "so", "because", "which"}
    
    words = first_line_text.split()
    first_word = words[0].lower().strip(".,!?;:\"'()") if words else ""

    speakers = {l.get("speaker") for l in lines_in_window if l.get("speaker")}

    if len(speakers) < 2:
        # --- DIARIZATION FALLBACK / STANDALONE INSIGHT ---
        # If all lines are labelled the same speaker, check if it's a Q/A turn (single-speaker label fallback)
        # OR if it's a clean standalone insight that doesn't start with context-dependent words.
        if not _detect_qa_turns(lines_in_window):
            if first_word in dangling_context_words:
                return False, f"Single-speaker clip starts with a context-dependent word '{first_word}' and lacks setup context."

    last_line_text = (lines_in_window[-1].get("text") or "").strip()
    # Only reject if the VERY last line is a dangling question AND there is no
    # answer line after it within the window (i.e. it is truly the final segment)
    if last_line_text.endswith("?"):
        # Check if the segment before it is a different speaker providing an answer
        if len(lines_in_window) >= 2:
            second_last = (lines_in_window[-2].get("text") or "").strip()
            last_speaker = lines_in_window[-1].get("speaker", "")
            second_last_speaker = lines_in_window[-2].get("speaker", "")
            # If the second-to-last segment is an answer from a different speaker, it's fine
            if second_last_speaker != last_speaker and not second_last.endswith("?"):
                return True, ""
        return False, "Clip ends mid-question with no answer captured inside the window."

    return True, ""


# ============================================================
# SENTENCE-BOUNDARY REPAIR (category-agnostic)
# Fixes: "clip starts/ends mid-sentence" — this was the root cause behind both
# clip_14 and clip_15 shipping broken. Works purely off segment text/timing,
# so it applies identically whether the category is comedy, interview, story, etc.
# ============================================================

def _ends_clean(text: str) -> bool:
    """
    True if text ends on a finished thought: real terminal punctuation AND
    the last word isn't a dangling connector/conditional/tag word.

    Punctuation alone used to be the whole check here, but Deepgram's
    smart_format punctuates Hindi/Hinglish speech with a full stop even
    mid-thought fairly often (e.g. "...decision making mein kharab hogi na."
    reads as "clean" by punctuation alone but "na" is a trailing tag, not a
    finished sentence) — so this now delegates to check_sentence_boundary(),
    which also screens the trailing word against _INCOMPLETE_TRAILING_WORDS.
    """
    return check_sentence_boundary(text)


# ============================================================
# ANCHOR / CONTEXT LINE ENFORCEMENT (code-level backstop)
# The editor prompt tells the LLM never to cut the mandatory anchor line or its
# mandatory context lines, but that's an instruction, not a guarantee — this is
# the root cause behind clips like "They told me 97%." shipping with no mention
# of Netflix. This function is the equivalent of repair_sentence_boundaries but
# for CONTENT rather than grammar: it verifies each mandatory line actually
# landed inside the clip's final segment range, and physically widens the range
# to include any that didn't — bounded by the same duration budget repair uses.
# ============================================================

def _normalize_for_line_match(text: str) -> str:
    return re.sub(r"[^\w\s]", "", (text or "").lower()).strip()


def _find_segment_for_line(
    line: str,
    formatted_segments: List[Dict[str, Any]],
    near_start: int = 0,
    near_end: int = 0,
) -> Optional[int]:
    """
    Finds the formatted_segments index whose text best contains `line`, by
    normalized token overlap against the anchor/context line Stage 1 quoted.

    Proximity-aware: a transcript can mention the same word/phrase (e.g.
    "Netflix") more than once. Among all segments clearing the overlap bar,
    this prefers whichever is CLOSEST to [near_start, near_end] — the clip's
    current range — rather than whichever has the single highest overlap
    score. Without this, a context line could latch onto an unrelated distant
    repeat of the same word and force a huge, wrong widen instead of the
    nearby occurrence Stage 1 actually meant.

    Returns None if nothing clears the overlap bar — meaning the LLM likely
    paraphrased rather than quoted verbatim, in which case there's nothing
    reliable to enforce against.
    """
    target_tokens = set(_normalize_for_line_match(line).split())
    if not target_tokens:
        return None

    candidates = []  # (distance_from_range, -overlap, idx) — sort ascending
    for idx, seg in enumerate(formatted_segments):
        seg_tokens = set(_normalize_for_line_match(seg["text"]).split())
        if not seg_tokens:
            continue
        overlap = len(target_tokens & seg_tokens) / len(target_tokens)
        if overlap < 0.6:
            continue
        if idx < near_start:
            distance = near_start - idx
        elif idx > near_end:
            distance = idx - near_end
        else:
            distance = 0
        candidates.append((distance, -overlap, idx))

    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


_BACKCHANNEL_FILLERS = {
    "hmm", "mhmm", "mhm", "haan", "han", "haa", "ok", "okay", "acha", "achha",
    "ji", "yes", "right", "yeah", "theek", "hai", "sahi", "correct", "wow",
    "really", "sach", "wah",
}


def _is_backchannel_filler(text: str) -> bool:
    """
    True if a segment is just a short acknowledgment/interjection — 'Mhmm.',
    'हां.', 'ठीक है?', 'Right?' — rather than substantive speech. These get
    skipped (not treated as turn boundaries) when walking back for a setup
    question, since they're conversational noise, not a real topic shift.
    """
    tokens = _normalize_for_line_match(text).split()
    if not tokens or len(tokens) > 3:
        return False
    return all(t in _BACKCHANNEL_FILLERS for t in tokens)


def find_nearest_setup_question(
    start_seg: int,
    formatted_segments: List[Dict[str, Any]],
    max_lookback: int = 10,
) -> Optional[int]:
    """
    Walks backward from start_seg looking for the nearest preceding question
    from the OTHER speaker — but only within the CONTINUOUS run of the current
    speaker's own turn. Short backchannel interjections from the other speaker
    ("Mhmm.", "हां।", "Right?") are tolerated and skipped, since they're not
    real turn boundaries. The moment a SUBSTANTIVE non-question line from the
    other speaker is hit, the search stops — that's a genuine topic/turn
    boundary, and any question further back than that belongs to a different
    exchange, not the one that set up the current answer.

    Without this check, a fast back-and-forth interview could re-anchor to an
    unrelated earlier question just because it happened to be the nearest
    question mark within the lookback window, even though a real topic
    boundary sat between it and the current answer.

    Returns None if no qualifying question is found before either the lookback
    limit or a topic boundary is hit, or if start_seg already opens on a
    question itself.
    """
    if start_seg <= 0 or start_seg >= len(formatted_segments):
        return None

    current_speaker = formatted_segments[start_seg].get("speaker")
    if formatted_segments[start_seg]["text"].strip().endswith("?"):
        return None  # clip already opens on a question — no re-anchor needed

    floor = max(0, start_seg - max_lookback)
    for idx in range(start_seg - 1, floor - 1, -1):
        seg = formatted_segments[idx]
        text = seg["text"].strip()

        if seg.get("speaker") == current_speaker:
            continue  # still inside the answerer's own continuous turn

        if text.endswith("?"):
            return idx  # the question that opened this turn

        if _is_backchannel_filler(text):
            continue  # short ack — not a real turn boundary, keep walking back

        break  # substantive non-question line from the other speaker — real topic boundary, stop

    return None


def enforce_mandatory_lines(
    start_seg: int,
    end_seg: int,
    mandatory_anchor_line: str,
    mandatory_context_lines: List[str],
    formatted_segments: List[Dict[str, Any]],
    max_duration_sec: float,
) -> Dict[str, Any]:
    """
    Widens [start_seg, end_seg] to include any mandatory anchor/context line
    the editor's chosen range dropped, bounded by max_duration_sec * 1.5 (same
    budget as repair_sentence_boundaries). Lines that can't be recovered within
    budget are returned in 'dropped_lines' so the caller can flag the clip for
    manual review instead of silently shipping it broken.
    """
    dropped_lines: List[str] = []
    all_lines = [mandatory_anchor_line] + list(mandatory_context_lines or [])

    for line in all_lines:
        if not line or not line.strip():
            continue
        seg_idx = _find_segment_for_line(line, formatted_segments, near_start=start_seg, near_end=end_seg)
        if seg_idx is None:
            continue  # not found verbatim anywhere — likely paraphrased by Stage 1, nothing to enforce
        if start_seg <= seg_idx <= end_seg:
            continue  # already inside the clip

        new_start = min(start_seg, seg_idx)
        new_end = max(end_seg, seg_idx)
        candidate_duration = formatted_segments[new_end]["end_time"] - formatted_segments[new_start]["start_time"]
        if candidate_duration <= max_duration_sec * 1.5:
            start_seg, end_seg = new_start, new_end
        else:
            dropped_lines.append(line)

    return {"start_seg": start_seg, "end_seg": end_seg, "dropped_lines": dropped_lines}


def repair_sentence_boundaries(
    start: float,
    end: float,
    segs: List[Any],
    min_duration_sec: float,
    max_duration_sec: float,
) -> Tuple[float, float, bool]:
    """
    Walks the transcript's own segment boundaries to make sure a clip starts at the
    beginning of a sentence and ends at the end of one, instead of trusting whatever
    raw timestamp the LLM guessed or the single nearest segment edge.

    - Walks the start backward while the current start segment begins mid-sentence
      (i.e. doesn't start with a capital letter / isn't the first segment).
    - Walks the end forward while the current end segment doesn't end cleanly.
    - If the clip is too short after finding a clean end, continues walking forward
      until min_duration_sec is met, always landing on a clean sentence boundary.
    - Both walks are capped by MAX_BOUNDARY_LOOKBACK_SEC / MAX_BOUNDARY_LOOKAHEAD_SEC.
    - Hard-caps at max_duration_sec so no clip ever overshoots.

    Returns (repaired_start, repaired_end, is_clean). is_clean=False means we could
    not fully repair it within the lookback/lookahead/duration budget — the caller
    should treat this clip as unsalvageable rather than shipping it anyway.
    """
    if not segs:
        return start, end, True  # nothing to validate against; trust caller's bounds

    ordered = sorted(segs, key=lambda s: float(s.start_time))

    def _seg_starts_clean(idx: int) -> bool:
        """True if the segment at idx begins at the start of a new sentence.
        A segment starts cleanly if:
          - it is the very first segment (idx == 0), OR
          - the previous segment ended with terminal punctuation, OR
          - its text begins with an uppercase letter (Whisper capitalises new sentences)
        """
        text = (ordered[idx].text or "").strip()
        if idx == 0:
            return True
        if _ends_clean(ordered[idx - 1].text):
            return True
        # Whisper capitalises the first word of a new sentence
        if text and text[0].isupper():
            return True
        return False

    def _closest_idx(t: float, key: str) -> int:
        best_i, best_diff = 0, float("inf")
        for i, s in enumerate(ordered):
            diff = abs(float(getattr(s, key)) - t)
            if diff < best_diff:
                best_diff = diff
                best_i = i
        return best_i

    start_idx = _closest_idx(start, "start_time")
    end_idx = _closest_idx(end, "end_time")
    if end_idx < start_idx:
        end_idx = start_idx

    # --- PASS 1: Walk start BACKWARD until we land on a clean sentence start ---
    lookback_used = 0.0
    while start_idx > 0 and not _seg_starts_clean(start_idx):
        gap = float(ordered[start_idx].start_time) - float(ordered[start_idx - 1].start_time)
        if gap > MAX_CROSSABLE_GAP_SEC:
            # Hard wall: never walk back across a real edit cut, regardless of budget.
            break
        if lookback_used + gap > MAX_BOUNDARY_LOOKBACK_SEC:
            break
        start_idx -= 1
        lookback_used += gap

    repaired_start = float(ordered[start_idx].start_time)

    # --- PASS 2: Walk end FORWARD until the segment ends cleanly ---
    # NOTE: this now charges the walk's budget against the GAP to the next
    # segment, not just the current segment's own internal duration — the old
    # version only measured seg_dur (current segment's length) and never
    # accounted for the silence/gap BEFORE the next segment. That meant it
    # could cross a 30-60 second edit cut in the source video for "free"
    # (zero budget cost) since only segment-internal duration was charged.
    # A large gap almost always signals a real edit cut or hard topic break
    # in the source footage — treat it as a wall, never something to walk
    # across, regardless of remaining lookahead budget.
    lookahead_used = 0.0
    while end_idx < len(ordered) - 1:
        cur_seg = ordered[end_idx]
        if _ends_clean(cur_seg.text):
            break

        gap_to_next = float(ordered[end_idx + 1].start_time) - float(cur_seg.end_time)
        if gap_to_next > MAX_CROSSABLE_GAP_SEC:
            # Hard wall: never cross a real edit cut, no matter the budget left.
            break

        seg_dur = float(cur_seg.end_time) - float(cur_seg.start_time)
        step_cost = seg_dur + max(0.0, gap_to_next)
        if lookahead_used + step_cost > MAX_BOUNDARY_LOOKAHEAD_SEC:
            break
        # Also don't let the end walk push us past the max allowed duration
        if float(ordered[end_idx + 1].end_time) - repaired_start > max_duration_sec * 1.5:
            break
        end_idx += 1
        lookahead_used += step_cost

    repaired_end = float(ordered[end_idx].end_time)
    duration = repaired_end - repaired_start

    # --- PASS 3: If clip is too short, keep walking forward to meet min_duration ---
    # Stops at the next clean sentence boundary AFTER min_duration is reached.
    # Hard-caps at max_duration_sec to prevent overshooting.
    # Also gap-aware now: this pass previously had NO gap check at all — it would
    # advance across any size of edit cut in the source video just to hit the
    # minimum duration floor, which is worse than Pass 2's bug since it wasn't
    # even budget-limited, just duration-capped.
    while duration < min_duration_sec and end_idx < len(ordered) - 1:
        gap_to_next = float(ordered[end_idx + 1].start_time) - float(ordered[end_idx].end_time)
        if gap_to_next > MAX_CROSSABLE_GAP_SEC:
            # Hard wall: don't extend across a real edit cut just to hit the duration floor.
            break
        end_idx += 1
        candidate_end = float(ordered[end_idx].end_time)
        candidate_dur = candidate_end - repaired_start
        # Hard cap: never exceed the category's maximum duration
        if candidate_dur > max_duration_sec:
            break
        repaired_end = candidate_end
        duration = candidate_dur
        # Once we've met the minimum, stop at the very next clean sentence boundary
        if duration >= min_duration_sec and _ends_clean(ordered[end_idx].text):
            break

    repaired_end = float(ordered[end_idx].end_time)
    duration = repaired_end - repaired_start

    starts_clean = _seg_starts_clean(start_idx)
    ends_clean = _ends_clean(ordered[end_idx].text)
    # Give some slack either side of the category's configured bounds since repair
    # can legitimately need to extend a bit further than the original guess.
    within_bounds = (min_duration_sec * 0.5) <= duration <= (max_duration_sec * 1.5)

    is_clean = starts_clean and ends_clean and within_bounds
    return repaired_start, repaired_end, is_clean


def find_segment_for_sentence(sentence_text: str, segs: List[Any]) -> Optional[Tuple[float, float]]:
    """
    Finds the start and end times of the transcript segments that best match
    the given sentence text.
    """
    s_clean = re.sub(r"[^\w\s]", "", sentence_text.lower()).strip()
    if not s_clean:
        return None
    
    # Try exact or substring matches first
    for s in segs:
        s_text_clean = re.sub(r"[^\w\s]", "", s.text.lower()).strip()
        if s_clean in s_text_clean or s_text_clean in s_clean:
            return s.start_time, s.end_time
            
    # Fallback to word overlap Jaccard-like matching
    words = set(s_clean.split())
    best_seg = None
    max_overlap = 0
    for s in segs:
        s_text_clean = re.sub(r"[^\w\s]", "", s.text.lower()).strip()
        s_words = set(s_text_clean.split())
        overlap = len(words.intersection(s_words))
        if overlap > max_overlap:
            max_overlap = overlap
            best_seg = s
            
    if best_seg and max_overlap >= 2:
        return best_seg.start_time, best_seg.end_time
        
    return None


def apply_narrative_grammar_trimming(
    start: float,
    end: float,
    strategy: str,
    sentence_analysis: Optional[List[Dict[str, Any]]],
    segs: List[Any]
) -> Tuple[float, float]:
    """
    Refines the clip boundaries (start_time and end_time) based on the
    sentence-by-sentence narrative role analysis.
    """
    if not sentence_analysis or not segs:
        return start, end

    analyzed_with_times = []
    for sent in sentence_analysis:
        text = sent.get("text", "")
        times = find_segment_for_sentence(text, segs)
        if times:
            analyzed_with_times.append((sent, times[0], times[1]))

    if not analyzed_with_times:
        return start, end

    new_start = start
    new_end = end

    # 1. Cliffhanger strategy trimming (cut before the reveal/resolution)
    if strategy == "cliffhanger":
        trim_idx = None
        for idx, (sent, s_start, s_end) in enumerate(analyzed_with_times):
            role = str(sent.get("role", "")).upper()
            resolves = sent.get("resolves_curiosity", False) or sent.get("resolves_tension", False)
            
            # Identify where resolution or reveal starts, and cut before it.
            # But only cut if there's at least one sentence before it to prevent zero-length clips.
            if (resolves or role in ["REVEAL", "EVIDENCE", "EXAMPLE", "REACTION", "CONCLUSION", "CALLBACK", "RESOLUTION"]) and idx > 0:
                trim_idx = idx
                break
        
        if trim_idx is not None:
            _, prev_start, prev_end = analyzed_with_times[trim_idx - 1]
            new_end = prev_end
            logger.info(f"Narrative Grammar: Cliffhanger trimmed end from {end} to {new_end} (cut before '{analyzed_with_times[trim_idx][0]['text']}')")

    # 2. Payoff strategy trimming (cut trailing fluff)
    elif strategy == "payoff":
        trim_idx = None
        n = len(analyzed_with_times)

        # NOTE: EVIDENCE and EXAMPLE were previously included in the trim-worthy
        # role list below, alongside genuine throat-clearing roles like SETUP/
        # CONTEXT/JUSTIFICATION. That was wrong for payoff clips specifically —
        # checked against real output, this was the exact mechanism cutting off
        # the strongest part of several clips: a "give me an example" follow-up
        # answered with concrete specifics (credit/lending/insurance), a lesson
        # delivered via a concrete illustration ("gamble away life earnings...
        # should be investing in diversified stuff"), and a named real-world
        # example (Elon Musk/EVs) — all tagged EXAMPLE or EVIDENCE by the LLM's
        # own sentence analysis, and all trimmed off as "trailing fluff" even
        # though a concrete example is usually THE payoff for a viewer, not
        # disposable setup before it. Only genuine preamble/throat-clearing
        # roles get trimmed now — SETUP, CONTEXT, JUSTIFICATION. EXAMPLE and
        # EVIDENCE are left alone; they're kept in the clip on purpose.
        TRIM_WORTHY_TRAILING_ROLES = ["SETUP", "CONTEXT", "JUSTIFICATION"]

        # Scan backward from the end to remove trailing explanation/fluff
        for idx in range(n - 1, -1, -1):
            sent, s_start, s_end = analyzed_with_times[idx]
            role = str(sent.get("role", "")).upper()
            resolves = sent.get("resolves_curiosity", False) or sent.get("resolves_tension", False)
            
            # If the trailing sentence is genuine preamble/setup and doesn't resolve anything:
            if role in TRIM_WORTHY_TRAILING_ROLES and not resolves:
                # Ensure we have a resolving or payoff sentence earlier in the clip
                has_earlier_payoff = any(
                    (s.get("resolves_curiosity", False) or s.get("resolves_tension", False) or str(s.get("role", "")).upper() in ["REACTION", "CONCLUSION", "REVEAL", "ANSWER", "PUNCHLINE", "LESSON", "RESOLUTION"])
                    for s, _, _ in analyzed_with_times[:idx]
                )
                if has_earlier_payoff:
                    trim_idx = idx
            else:
                break
                
        if trim_idx is not None:
            _, prev_start, prev_end = analyzed_with_times[trim_idx - 1]
            new_end = prev_end
            logger.info(f"Narrative Grammar: Payoff trimmed trailing fluff from {end} to {new_end} (removed '{analyzed_with_times[trim_idx][0]['text']}')")

    return new_start, new_end


# ============================================================
# ENRICHMENT + SCORING (Python fills in what the LLM no longer outputs)
# ============================================================

def enrich_clip(
    raw_clip: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    category: str,
    video_id: str = "unknown",
    audio_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Fills in derived fields Python can compute, aligns boundaries to Whisper segments,
    and attaches real audio-signal scores if an audio_path is provided."""
    start = float(raw_clip.get("start_time", 0.0))
    end = float(raw_clip.get("end_time", 0.0))
    hook = raw_clip.get("hook_line", "")
    cfg = (
        CATEGORY_CONFIG.get(category)
        or CATEGORY_CONFIG[DEFAULT_CATEGORY]
    )
    raw_strategy = raw_clip.get("clip_strategy")
    if raw_strategy is not None:
        strategy = str(raw_strategy)
    else:
        strategy = str(cfg.get("clip_strategy", "standard"))

    boundaries_clean = True

    # Precise Snapping Logic using original database transcript segments
    if video_id != "unknown":
        from app.db import SessionLocal
        from app import models
        db = SessionLocal()
        try:
            segs = db.query(models.TranscriptSegment).filter(
                models.TranscriptSegment.video_id == int(video_id)
            ).order_by(models.TranscriptSegment.start_time).all()

            if segs:
                # 1. Start alignment: find the closest segment start time
                closest_start_diff = float("inf")
                snapped_start = start
                for s in segs:
                    diff = abs(cast(float, s.start_time) - start)
                    if diff < closest_start_diff:
                        closest_start_diff = diff
                        snapped_start = cast(float, s.start_time)

                # 2. End alignment (suspense cut / cliffhanger):
                # Search for the exact segment that contains the cliffhanger/hook text
                hook_clean = hook.lower().strip()
                hook_seg = None

                if hook_clean:
                    for s in segs:
                        s_text_clean = s.text.lower().strip()
                        if hook_clean in s_text_clean or s_text_clean in hook_clean:
                            if abs(cast(float, s.end_time) - end) < 20.0:
                                hook_seg = s
                                break

                    if not hook_seg:
                        hook_words = set(hook_clean.split())
                        max_overlap = 0
                        for s in segs:
                            s_words = set(s.text.lower().split())
                            overlap = len(hook_words.intersection(s_words))
                            if overlap > max_overlap and overlap >= 2:
                                if abs(cast(float, s.end_time) - end) < 20.0:
                                    max_overlap = overlap
                                    hook_seg = s

                if hook_seg:
                    snapped_end = cast(float, hook_seg.end_time)
                    logger.info(f"Precise cliffhanger snap: '{hook}' -> Segment [{cast(float, hook_seg.start_time)}s - {cast(float, hook_seg.end_time)}s]")
                else:
                    closest_end_diff = float("inf")
                    snapped_end = end
                    for s in segs:
                        diff = abs(cast(float, s.end_time) - end)
                        if diff < closest_end_diff:
                            closest_end_diff = diff
                            snapped_end = cast(float, s.end_time)

                start = snapped_start
                end = snapped_end

                # Apply the Narrative Grammar Boundary Trimming!
                start, end = apply_narrative_grammar_trimming(
                    start, end, strategy,
                    raw_clip.get("sentence_analysis"),
                    segs
                )

                # --- SENTENCE-BOUNDARY REPAIR (applies to every category) ---
                # The snapping above only finds the nearest segment edge, which is not
                # necessarily a complete sentence — this is what produced clips that
                # opened or closed mid-clause. This repair pass walks outward from the
                # snapped boundaries until they land on real sentence starts/ends.
                cfg_bounds = (
                    CATEGORY_CONFIG.get(category)
                    or CATEGORY_CONFIG[DEFAULT_CATEGORY]
                )
                repaired_start, repaired_end, boundaries_clean = repair_sentence_boundaries(
                    start, end, segs,
                    min_duration_sec=float(cfg_bounds.get("min_duration_sec", 15.0)),
                    max_duration_sec=float(cfg_bounds.get("max_duration_sec", 60.0)),
                )
                if (repaired_start, repaired_end) != (start, end):
                    logger.info(
                        f"Boundary repair adjusted clip [{start}-{end}] -> "
                        f"[{repaired_start}-{repaired_end}] (clean={boundaries_clean})"
                    )
                start, end = repaired_start, repaired_end
        except Exception as e:
            logger.error(f"Error snapping clip to segment boundaries: {e}")
            boundaries_clean = True  # don't penalize a clip for a snapping exception
        finally:
            db.close()
    else:
        boundaries_clean = True  # no DB segments available to validate against

    duration = round(end - start, 2) if end > start else 30.0

    full_excerpt = " ".join(
        c["text"] for c in candidates
        if c["start_time"] < end and c["end_time"] > start
    )
    excerpt = full_excerpt[:220]

    # --- AUDIO SIGNAL LAYER ---
    audio_signals = detect_audio_events(audio_path, start, end) if audio_path else {
        "energy_spike_ratio": 1.0, "pitch_variance": 0.0,
        "has_laughter": False, "has_applause": False, "audio_score": 5.0,
    }

    # sentence boundary check (kept as a secondary, excerpt-level sanity check —
    # boundaries_clean above is the authoritative, segment-level check)
    sentence_clean = check_sentence_boundary(excerpt)
    
    # check category duration bounds
    cfg = (
        CATEGORY_CONFIG.get(category)
        or CATEGORY_CONFIG[DEFAULT_CATEGORY]
    )
    min_dur = float(cfg.get("min_duration_sec", 15.0))
    max_dur = float(cfg.get("max_duration_sec", 60.0))
    
    needs_review = raw_clip.get("needs_manual_review", False)
    flagged_reason = raw_clip.get("reason", "")
    auto_render_blocked = False
    
    if duration < min_dur or duration > max_dur:
        needs_review = True
        flagged_reason = (flagged_reason + f" [FLAGGED: Out of category duration bounds {min_dur}-{max_dur}s]").strip()
        
    if not sentence_clean:
        needs_review = True
        flagged_reason = (flagged_reason + " [FLAGGED: Clip ends mid-phrase/incomplete sentence]").strip()

    if not boundaries_clean:
        # This is the fix for the bug that shipped clip_14/clip_15 broken: previously
        # a mid-sentence cut only set needs_manual_review=True but nothing stopped it
        # from being saved and rendered anyway. Now it's also hard-blocked from
        # auto-render, regardless of category.
        needs_review = True
        auto_render_blocked = True
        flagged_reason = (
            flagged_reason + " [BLOCKED: Could not repair clip to clean sentence "
            "boundaries within budget — needs manual editing before render]"
        ).strip()

    # --- LOW-SUBSTANCE CHECK ---
    low_substance_ratio = compute_low_substance_ratio(full_excerpt)
    if low_substance_ratio > LOW_SUBSTANCE_REVIEW_THRESHOLD:
        needs_review = True
        flagged_reason = (
            flagged_reason + f" [FLAGGED: Low-substance clip — {low_substance_ratio:.0%} "
            "of sentences are backchannel/filler, not real content]"
        ).strip()
        if low_substance_ratio > LOW_SUBSTANCE_HARD_BLOCK_THRESHOLD:
            auto_render_blocked = True
            flagged_reason = (flagged_reason + " [BLOCKED: Clip is almost entirely filler]").strip()

    # --- SENTIMENT / TOPIC SIGNAL (from Deepgram Audio Intelligence) ---
    # candidates carry per-segment sentiment_score/topics attached in transcribe.py.
    # We average whichever candidate segments fall inside this clip's final [start, end).
    sentiment_overlap = [c for c in candidates if c["start_time"] < end and c["end_time"] > start]
    if sentiment_overlap:
        avg_sentiment = sum(c.get("sentiment_score", 0.0) for c in sentiment_overlap) / len(sentiment_overlap)
        # Virality tracks emotional INTENSITY, not polarity — a very negative moment
        # (rant, betrayal, grief) pops just as hard on short-form as a very positive one.
        sentiment_intensity = min(abs(avg_sentiment) * 10.0, 10.0)
        all_topics = sorted({t for c in sentiment_overlap for t in c.get("topics", [])})
    else:
        avg_sentiment, sentiment_intensity, all_topics = 0.0, 5.0, []

    return {
        "start_time": round(start, 2),
        "end_time": round(end, 2),
        "duration_sec": duration,
        "category": category,
        "clip_strategy": strategy,
        "curiosity_score": float(raw_clip.get("curiosity_score", 5.0)),
        "hook_score": float(raw_clip.get("hook_score", 5.0)),
        "emotion_score": float(raw_clip.get("emotion_score", 5.0)),
        "surprise_score": float(raw_clip.get("surprise_score", 5.0)),
        "reaction_score": float(raw_clip.get("reaction_score", 5.0)),
        "context_completeness": float(raw_clip.get("context_completeness", 5.0)),
        "audio_score": audio_signals["audio_score"],
        "has_laughter": audio_signals["has_laughter"],
        "has_applause": audio_signals["has_applause"],
        "energy_spike_ratio": audio_signals["energy_spike_ratio"],
        "sentiment_score": round(avg_sentiment, 3),
        "sentiment_intensity_score": round(sentiment_intensity, 2),
        "topics": all_topics,
        "why_viewers_keep_watching": raw_clip.get("why_viewers_keep_watching", "Interesting hook point in the transcript."),
        "reason": flagged_reason,
        "hook_line": hook,
        "transcript_excerpt": excerpt,
        "suggested_title": clean_title_from_hook(hook),
        "suggested_caption": excerpt[:100],
        "needs_manual_review": needs_review,
        "auto_render_blocked": auto_render_blocked,
        "best_aspect_ratio": "9:16",
        "low_substance_ratio": low_substance_ratio,
    }


def compute_virality_score(clip: Dict[str, Any], category: str) -> float:
    """
    Applies the category-specific weighted formula across three signal sources:
      1. LLM-judged signals (curiosity_score, hook_score)
      2. Real audio signal (audio_score — laughter/applause/energy/pitch)
      3. Text heuristic signal (heuristic_score — Stage 1 keyword scan, supporting only)
    """
    cfg = (
        CATEGORY_CONFIG.get(category)
        or CATEGORY_CONFIG[DEFAULT_CATEGORY]
    )
    w = cfg["weights"]

    curiosity = float(clip.get("curiosity_score", 5.0))
    hook = float(clip.get("hook_score", 5.0))
    reaction = float(clip.get("reaction_score", 5.0))
    surprise = float(clip.get("surprise_score", 5.0))
    context_val = clip.get("context")
    if context_val is None:
        context_val = clip.get("standalone_score")
    context = float(context_val if context_val is not None else 5.0)  # standalone_score acts as the context/information metric in Pydantic schema
    audio_score = float(clip.get("audio_score", 5.0))
    heuristic_component = float(clip.get("heuristic_score", 5.0))
    sentiment_component = float(clip.get("sentiment_intensity_score", 5.0))

    llm_keys = ["curiosity", "hook", "reaction", "surprise", "context"]
    llm_scores = {
        "curiosity": curiosity,
        "hook": hook,
        "reaction": reaction,
        "surprise": surprise,
        "context": context
    }

    llm_weight_sum = sum(w.get(k, 0.0) for k in llm_keys)
    audio_weight = w.get("audio_signal", 0.0)
    # sentiment_signal defaults to 0.0 for any category that hasn't opted in —
    # so this is a no-op on virality scores until you add the key to a category's weights.
    sentiment_weight = w.get("sentiment_signal", 0.0)
    remaining_weight = max(0.0, 1.0 - llm_weight_sum - audio_weight - sentiment_weight)

    if llm_weight_sum > 0:
        llm_component = sum((w.get(k, 0.0) / llm_weight_sum) * llm_scores[k] for k in llm_keys)
    else:
        llm_component = 5.0

    combined = (
        (llm_weight_sum * llm_component) +
        (audio_weight * audio_score) +
        (sentiment_weight * sentiment_component) +
        (remaining_weight * heuristic_component)
    )

    # Penalize low-substance clips relative to the share of filler content
    low_substance_ratio = clip.get("low_substance_ratio", 0.0)
    if low_substance_ratio > 0.0:
        combined *= (1.0 - low_substance_ratio)

    return combined


# ============================================================
# STAGE 2: LLM batch scoring (lean schema, Instructor-guaranteed JSON)
# Tries each configured provider in order (NVIDIA NIM, then Groq) — same
# schema, same category-specific prompt, same Instructor/legacy structure
# for both, since both speak the OpenAI-compatible chat.completions API.
# ============================================================

def _score_via_instructor(provider: Dict[str, str], system_prompt: str, user_content: str, response_model) -> Optional[Dict[str, Any]]:
    """Instructor + Pydantic guaranteed-schema attempt against one provider. Raises on failure."""
    import instructor
    from openai import OpenAI
    import httpx

    is_local = provider["name"] == "ollama"

    # Local inference (Ollama) and NVIDIA NIM need a much longer timeout
    timeout_seconds = 420.0 if (is_local or provider["name"] == "nvidia_nim") else 30.0

    base_client = OpenAI(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
        http_client=httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=10.0)),
        max_retries=0,
    )
    # Mode.JSON  → writes JSON into the content field.
    # Mode.TOOLS → uses the function-call protocol.
    # NVIDIA NIM, Ollama, AND OpenRouter (Gemini-family models) all handle
    # Mode.JSON far more reliably than Mode.TOOLS for this schema — Gemini's
    # tool-calling schema translation through OpenRouter was failing Instructor's
    # validation on ~3/3 attempts per call (confirmed via token-usage logs: each
    # retry roughly doubled/tripled in size from the growing failed-attempt
    # history, before falling back to legacy parsing). Mode.TOOLS is reserved
    # for providers with genuinely strong native function-calling (e.g. Groq,
    # if it's ever re-added to the active provider list) — it should NOT be
    # the default fallback for any provider not explicitly listed as JSON-mode.
    import instructor
    # JSON_MODE_PROVIDERS = ("nvidia_nim", "ollama", "openrouter")
    JSON_MODE_PROVIDERS = ("nvidia_nim", "ollama")
    mode = instructor.Mode.JSON if provider["name"] in JSON_MODE_PROVIDERS else instructor.Mode.TOOLS
    client = instructor.from_openai(base_client, mode=mode)

    logger.info(f"[{provider['name']}] Calling [{provider['model']}] via Instructor (mode={mode.value})...")

    # Local models: lower temperature tightens JSON schema adherence without sacrificing
    # reasoning quality (temperature controls token sampling randomness, not judgment).
    # Smaller max_tokens is safe because we're not generating sentence_analysis.
    temperature = 0.25 if is_local else 0.6
    if is_local:
        max_tokens = 6000
    elif provider["name"] == "openrouter":
        # NOTE: previously capped at 1000 to avoid OpenRouter's 402 pre-auth cost
        # check on low-credit accounts — but 1000 tokens is nowhere near enough
        # to complete a multi-moment JSON response (Stage 1 can return 20-30
        # moments), so every response was getting truncated mid-generation and
        # failing Instructor's schema validation on every attempt. Raised to a
        # middle ground that should comfortably fit a full response while still
        # keeping the pre-auth cost check from tripping on very low balances.
        # If you still see 402s, top up OpenRouter credits rather than lowering
        # this back down — 1000 was the actual cause of the retry storm.
        max_tokens = 16384
    else:
        max_tokens = 16384  # give NVIDIA NIM enough tokens to complete response

    result, completion = client.chat.completions.create_with_completion(
        model=provider["model"],
        response_model=response_model,
        max_retries=2,
        temperature=temperature,
        top_p=0.95,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        extra_body=provider.get("extra_body") or {},
    )

    if completion and hasattr(completion, "usage") and completion.usage:
        prompt_tokens = getattr(completion.usage, "prompt_tokens", 0)
        completion_tokens = getattr(completion.usage, "completion_tokens", 0)
        total_tokens = getattr(completion.usage, "total_tokens", 0)
        logger.info(
            f"[{provider['name']}] Pipeline Token Usage: Prompt={prompt_tokens}, "
            f"Completion={completion_tokens}, Total={total_tokens} (model='{provider['model']}')"
        )

    data = result.model_dump()
    if not data:
        logger.warning(f"[{provider['name']}] Instructor returned empty data.")
        return None

    logger.info(f"[{provider['name']}] Successfully parsed guaranteed-schema response via Instructor.")
    return data


class JSONParsingFallbackError(ValueError):
    """Custom exception raised when an LLM provider returns non-JSON/malformed JSON, to trigger repair fallback."""
    def __init__(self, raw_content: str, message: str):
        super().__init__(message)
        self.raw_content = raw_content


def _score_via_legacy_parse(provider: Dict[str, str], system_prompt: str, user_content: str, response_model=None) -> Optional[Dict[str, Any]]:
    """Manual streaming + string-parse attempt against one provider. Raises on failure."""
    from openai import OpenAI
    import httpx
    import re

    is_local = provider["name"] == "ollama"
    timeout_seconds = 420.0 if (is_local or provider["name"] == "nvidia_nim") else 30.0
    temperature = 0.25 if is_local else 0.6
    if is_local:
        max_tokens = 6000
    elif provider["name"] == "openrouter":
        max_tokens = 16384
    else:
        max_tokens = 16384

    client = OpenAI(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
        http_client=httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=10.0)),
        max_retries=0,
    )

    logger.info(f"[{provider['name']}] Calling [{provider['model']}] (legacy manual-parse path)...")

    completion = client.chat.completions.create(
        model=provider["model"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=temperature,
        top_p=0.95,
        max_tokens=max_tokens,
        extra_body=provider.get("extra_body") or {},
        stream=False,
    )

    if hasattr(completion, "usage") and completion.usage:
        prompt_tokens = getattr(completion.usage, "prompt_tokens", 0)
        completion_tokens = getattr(completion.usage, "completion_tokens", 0)
        total_tokens = getattr(completion.usage, "total_tokens", 0)
        logger.info(
            f"[{provider['name']}] Legacy Parse Token Usage: Prompt={prompt_tokens}, "
            f"Completion={completion_tokens}, Total={total_tokens} (model='{provider['model']}')"
        )

    content = (completion.choices[0].message.content or "").strip()
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    content = content.strip()

    if not content:
        logger.warning(f"[{provider['name']}] returned empty content.")
        return None

    logger.debug(f"[{provider['name']}] [RAW LLM RESPONSE] ({len(content)} chars):\n{content}")
    preview = content[:400].replace('\n', ' ')
    logger.info(f"[{provider['name']}] [LLM RAW PREVIEW] ({len(content)} chars): {preview}...")

    # Attempt to repair common JSON malformation (like unquoted string values) before parsing
    repaired_content = content
    for field in ["clip_strategy", "hook_line", "reason"]:
        pattern = rf'"{field}"\s*:\s*([^"\s{{\[][^,}}\n]*)'
        def repl(match):
            val = match.group(1).strip()
            if val.endswith('"'):
                val = val[:-1]
            return f'"{field}": "{val}"'
        repaired_content = re.sub(pattern, repl, repaired_content)

    try:
        data = json.loads(repaired_content)
    except json.JSONDecodeError as json_err:
        logger.error(
            f"[{provider['name']}] JSON parse failed at char {json_err.pos} "
            f"(line {json_err.lineno} col {json_err.colno}). Raw content around failure: "
            f"...{repaired_content[max(0, json_err.pos-80):json_err.pos+80]}..."
        )
        raise JSONParsingFallbackError(content, str(json_err))

    if not data:
        logger.warning(f"[{provider['name']}] returned empty or invalid JSON.")
        return None

    if response_model:
        try:
            if hasattr(response_model, "model_validate"):
                response_model.model_validate(data)
            else:
                response_model(**data)
            logger.info(f"[{provider['name']}] Successfully parsed and validated batch response (legacy path).")
        except Exception as e:
            logger.error(f"[{provider['name']}] Legacy parse succeeded but failed Pydantic validation: {e}")
            raise JSONParsingFallbackError(content, str(e))
    else:
        logger.info(f"[{provider['name']}] Successfully parsed batch response (legacy path).")
        
    return data


def _repair_json_via_provider(provider: Dict[str, Any], raw_malformed_text: str, response_model) -> Optional[Dict[str, Any]]:
    """Uses a fallback provider to repair/parse malformed JSON text into the target response model."""
    logger.info(f"[{provider['name']}] Attempting to repair malformed JSON from previous provider...")

    # We construct a simple prompt to extract the structured data
    repair_system_prompt = (
        "You are a strict data-extraction assistant. Your job is to take the provided text "
        "containing a list of moments and format it into a valid JSON object matching the requested schema. "
        "Do not invent new timestamps, keep the exact timestamps, hook lines, and details from the input."
    )

    repair_user_content = (
        f"Here is the text containing candidate moments:\n\n"
        f"{raw_malformed_text}\n\n"
        f"Parse and extract these moments into the requested JSON schema."
    )

    # First try instructor
    if _INSTRUCTOR_AVAILABLE and _PYDANTIC_AVAILABLE:
        try:
            data = _score_via_instructor(provider, repair_system_prompt, repair_user_content, response_model)
            if data:
                logger.info(f"[{provider['name']}] Successfully repaired JSON using Instructor!")
                return data
        except Exception as e:
            logger.warning(f"[{provider['name']}] Repair via Instructor failed: {e}. Trying legacy parse repair...")

    # Try legacy parse
    try:
        data = _score_via_legacy_parse(provider, repair_system_prompt, repair_user_content, response_model)
        if data:
            logger.info(f"[{provider['name']}] Successfully repaired JSON using legacy manual-parse!")
            return data
    except Exception as e:
        logger.error(f"[{provider['name']}] Repair via legacy parse failed: {e}")

    return None


def _call_llm_with_fallback(
    system_prompt: str, user_content: str, response_model,
    model_override: Optional[str] = None,
    provider_priority: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Tries all configured LLM providers in order with Instructor/legacy path and returns parsed response.

    model_override: swaps the model on the OpenRouter provider entry only.
    provider_priority: reorders providers for THIS call only (e.g. ["nvidia_nim", "openrouter"]
    puts NVIDIA first for Stage 2, while the global default keeps OpenRouter first everywhere
    else). Anything not named in provider_priority still gets appended after, in its normal
    order, so fallback resilience is preserved either way.
    """
    all_providers = [p for p in _get_llm_providers() if p["name"] not in _RATE_LIMITED_PROVIDERS]
    if provider_priority:
        ordered = [p for name in provider_priority for p in all_providers if p["name"] == name]
        remaining = [p for p in all_providers if p["name"] not in provider_priority]
        providers = ordered + remaining
    else:
        providers = all_providers
    if model_override:
        for p in providers:
            if p["name"] == "openrouter":
                p["model"] = model_override
    if not providers:
        logger.warning("No LLM providers available (all are rate-limited or unconfigured).")
        return None

    malformed_json_text: Optional[str] = None

    for provider in providers:
        # If we have malformed JSON text from a previous provider, try to repair it using this provider!
        if malformed_json_text:
            try:
                repaired_data = _repair_json_via_provider(provider, malformed_json_text, response_model)
                if repaired_data:
                    return repaired_data
            except Exception as repair_err:
                logger.error(f"[{provider['name']}] JSON repair attempt failed: {repair_err}")
                if "rate limit" in str(repair_err).lower() or "429" in str(repair_err).lower() or "quota" in str(repair_err).lower() or "credit" in str(repair_err).lower():
                    logger.warning(f"Provider '{provider['name']}' hit rate-limit/quota during repair. Blacklisting it.")
                    _RATE_LIMITED_PROVIDERS.add(provider["name"])
            logger.warning(f"[{provider['name']}] Could not repair JSON. Falling back to running from scratch on this provider...")

        # --- Instructor Path ---
        instructor_success = False
        if _INSTRUCTOR_AVAILABLE and _PYDANTIC_AVAILABLE:
            try:
                data = _score_via_instructor(provider, system_prompt, user_content, response_model)
                if data:
                    return data
                instructor_success = True
            except Exception as e:
                import openai
                try:
                    from pydantic import ValidationError
                except ImportError:
                    ValidationError = type("ValidationError", (Exception,), {})
                    
                logger.error(f"[{provider['name']}] Instructor exception type: {type(e).__name__}")
                    
                # If rate-limited or quota exceeded, blacklist provider
                if "rate limit" in str(e).lower() or "429" in str(e).lower() or "quota" in str(e).lower() or "credit" in str(e).lower():
                    logger.warning(f"Provider '{provider['name']}' hit rate-limit/quota: {e}. Blacklisting it.")
                    _RATE_LIMITED_PROVIDERS.add(provider["name"])
                    continue
                    
                # If instructor exhausted retries due to schema validation failure, the provider cannot handle the schema.
                if isinstance(e, ValidationError) or "validation" in str(e).lower() or "schema" in str(e).lower() or type(e).__name__ == "InstructorRetryException":
                    logger.error(f"[{provider['name']}] Instructor failed validation after retries: {e}. Skipping legacy parse and advancing to next provider.")
                    continue
                
                # If it's a network timeout, connection, or rate limit error, don't waste time on legacy parse
                if isinstance(e, (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError)) or provider["name"] in _RATE_LIMITED_PROVIDERS:
                    logger.error(f"[{provider['name']}] Instructor path failed with connection/timeout/rate-limit ({e}). Skipping legacy parse for this provider.")
                    continue
                else:
                    logger.error(f"[{provider['name']}] Instructor path failed ({e}). Trying legacy parse on same provider...")
                    try:
                        data = _score_via_legacy_parse(provider, system_prompt, user_content, response_model)
                        if data:
                            return data
                    except JSONParsingFallbackError as json_err:
                        malformed_json_text = json_err.raw_content
                        logger.error(f"[{provider['name']}] Legacy parse failed with malformed JSON. Storing raw response for repair fallback.")
                    except Exception as legacy_err:
                        logger.error(f"[{provider['name']}] Legacy parse path failed ({legacy_err}).")

        # --- Legacy Fallback Path (only if instructor was not attempted) ---
        if not instructor_success and not (_INSTRUCTOR_AVAILABLE and _PYDANTIC_AVAILABLE) and provider["name"] not in _RATE_LIMITED_PROVIDERS:
            try:
                data = _score_via_legacy_parse(provider, system_prompt, user_content, response_model)
                if data:
                    return data
            except JSONParsingFallbackError as json_err:
                malformed_json_text = json_err.raw_content
                logger.error(f"[{provider['name']}] Legacy parse failed with malformed JSON. Storing raw response for repair fallback.")
            except Exception as e:
                logger.error(f"[{provider['name']}] Legacy parse path failed ({e}).")
                if "rate limit" in str(e).lower() or "429" in str(e).lower() or "quota" in str(e).lower() or "credit" in str(e).lower():
                    logger.warning(f"Provider '{provider['name']}' hit rate-limit/quota during legacy parse. Blacklisting it.")
                    _RATE_LIMITED_PROVIDERS.add(provider["name"])

        logger.warning(f"Provider '{provider['name']}' exhausted. Trying next provider...")

    logger.error("ALL CONFIGRURED LLM PROVIDERS FAILED OR RETURNED INVALID SCHEMA! Returning None to trigger heuristic fallback/abandonment.")
    return None


def call_nvidia_nim_batch_scoring(candidates: List[Dict[str, Any]], video_id: str, category: str) -> Dict[str, Any]:
    """
    Stage 2: Batch-evaluate and rank candidate clips using the lean,
    category-specific schema, trying each configured LLM provider in order
    (NVIDIA NIM, then Groq) before giving up and using the heuristic fallback.

    For each provider: primary path is Instructor + Pydantic (guaranteed valid
    JSON, auto-retry on validation failure); fallback path is manual streaming
    + string parsing (kept for resilience if instructor/pydantic aren't
    installed, or if a provider's Instructor call fails for another reason).
    Only after every provider has been tried does this drop to the heuristic
    fallback — same behavior/name kept as before (build_fallback), just now
    reached less often.
    """

    def build_fallback() -> Dict[str, Any]:
        fallback_clips = []
        for cand in candidates[:5]:
            fake_raw = {
                "start_time": cand["start_time"],
                "end_time": cand["end_time"],
                "clip_strategy": (
                    CATEGORY_CONFIG.get(category)
                    or CATEGORY_CONFIG[DEFAULT_CATEGORY]
                )["clip_strategy"],
                "hook_line": cand["text"][:60],
                "reason": "Heuristic fallback — no LLM provider available or all returned invalid data.",
                "curiosity_score": cand.get("heuristic_score", 5.0),
                "hook_score": cand.get("heuristic_score", 5.0),
                "heuristic_score": cand.get("heuristic_score", 5.0),
            }
            fallback_clips.append(fake_raw)
        return {
            "video_id": str(video_id),
            "language": "en",
            "overall_summary": "Heuristic fallback summary.",
            "clips": fallback_clips,
        }

    providers = _get_llm_providers()
    if not providers:
        logger.warning("No LLM provider API keys configured (NVIDIA_API_KEY / GROQ_API_KEY). Using heuristic fallback.")
        return build_fallback()

    candidates_str = ""
    for idx, cand in enumerate(candidates):
        candidates_str += (
            f"--- CANDIDATE {idx+1} ---\n"
            f"Original Time Window: {cand['start_time']} seconds to {cand['end_time']} seconds\n"
            f"Transcript:\n{cand['text']}\n\n"
        )

    user_content = (
        f"Here is the video_id: \"{video_id}\"\n"
        f"Here are the candidate segments:\n\n{candidates_str}"
    )

    for provider in providers:
        is_local = provider["name"] == "ollama"
        # Build the right prompt variant for this provider:
        #   lean=True  → Ollama: no sentence_analysis output requirement
        #   lean=False → Cloud: full sentence_analysis included
        system_prompt = build_system_prompt(category, lean=is_local)

        # --- PRIMARY PATH for this provider: Instructor + Pydantic ---
        if _INSTRUCTOR_AVAILABLE and _PYDANTIC_AVAILABLE:
            try:
                data = _score_via_instructor(provider, system_prompt, user_content, ClipResponseModel)
                if data:
                    return data
            except Exception as e:
                logger.error(f"[{provider['name']}] Instructor path failed ({e}). Trying legacy parse on same provider...")

        # --- FALLBACK PATH for this provider: manual streaming + string parsing ---
        try:
            data = _score_via_legacy_parse(provider, system_prompt, user_content)
            if data:
                return data
        except Exception as e:
            logger.error(f"[{provider['name']}] Legacy parse path failed ({e}).")

        logger.warning(f"Provider '{provider['name']}' exhausted (Instructor + legacy both failed). Trying next provider...")

    logger.error("All configured LLM providers failed for batch scoring. Using heuristic fallback.")
    return build_fallback()


def detect_intro_preview_boundary(segments: Optional[List[Dict[str, Any]]]) -> float:
    """
    Detects if a highlights preview is stitched onto the front of a podcast.
    Compares early segments (< 120s) with later segments (>= 120s) in the transcript.
    Returns the start time of the first main-content segment (or 0.0 if not detected).
    """
    if not segments:
        return 0.0

    def _word_set(text: str) -> set:
        return set((text or "").lower().split())

    def _jaccard(a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    INTRO_END_CUTOFF = 120.0
    MATCH_THRESHOLD = 0.60
    MIN_MATCHES = 3

    early = [s for s in segments if s.get("start_time", 0.0) < INTRO_END_CUTOFF]
    later = [s for s in segments if s.get("start_time", 0.0) >= INTRO_END_CUTOFF]

    if not early or not later:
        return 0.0

    matched_early_ends = []
    for eseg in early:
        ews = _word_set(eseg.get("text", ""))
        # Only match actual statements, skipping short filler words (like 'yes', 'okay')
        if len(ews) < 4:
            continue
        for lseg in later:
            if _jaccard(ews, _word_set(lseg.get("text", ""))) >= MATCH_THRESHOLD:
                matched_early_ends.append(eseg.get("end_time", 0.0))
                break

    if len(matched_early_ends) >= MIN_MATCHES:
        intro_boundary = max(matched_early_ends)
        logger.warning(
            f"Intro/highlights-preview detected: first {intro_boundary:.1f}s of transcript contains "
            f"{len(matched_early_ends)} near-duplicate segments that reappear later. "
            f"Treating this as the intro boundary."
        )
        return intro_boundary

    return 0.0


def log_reasoning(video_id: str, text: str):
    try:
        os.makedirs("logs", exist_ok=True)
        log_file = f"logs/video_{video_id}_reasoning.txt"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception as e:
        logger.error(f"Failed to write to reasoning log: {e}")


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def rank_candidates(

    candidates: List[Dict[str, Any]],
    video_id: str = "unknown",
    limit: int = 5,
    category: Optional[str] = None,   # None = auto-classify
    audio_path: Optional[str] = None,  # enables the real audio-signal layer
    transcript_lines: Optional[List[Dict[str, Any]]] = None,  # per-line speaker data, enables exchange validation
) -> List[Dict[str, Any]]:
    """
    Ranks candidates using the upgraded pipeline:
    1. Score all candidates using fast heuristics (supporting signal only).
    2. Deduplicate/filter to top 10 by overlap.
    3. Auto-classify content category via embeddings (LLM verifies low-confidence cases).
    4. Call Llama Nemotron with a lean, category-specific prompt/schema via Instructor
       (guaranteed valid JSON, auto-retries on validation failure).
    5. Enrich LLM output in Python: snap boundaries, attach real audio-signal scores,
       validate speaker-exchange completeness for interview_discussion.
    6. Apply category-specific virality weights across LLM + audio + heuristic signals.
    7. Return the top 'limit' clips.
    """
    if not candidates:
        return []

    # Clear and initialize reasoning log
    try:
        log_file = f"logs/video_{video_id}_reasoning.txt"
        if os.path.exists(log_file):
            os.remove(log_file)
    except Exception:
        pass

    log_reasoning(video_id, f"=== RUN LOG FOR VIDEO {video_id} ===\n")

    # --- INTRO PREVIEW DEDUPLICATION ---
    # Some podcasts stitch a "highlights reel" (30-90s of out-of-order clips) onto
    # the front of the full interview. Detect and drop those duplicated early segments
    # so they don't pollute the candidate pool or produce spurious top-ranked clips.
    intro_boundary = detect_intro_preview_boundary(transcript_lines)
    if intro_boundary > 0.0:
        filtered = [c for c in candidates if c.get("start_time", 0.0) >= intro_boundary]
        if filtered:
            logger.warning(f"Intro stripper active: Excluded {len(candidates) - len(filtered)} candidates starting before {intro_boundary:.1f}s.")
            candidates = filtered
        else:
            logger.warning("All candidates were inside the intro window — keeping originals to avoid empty pool.")

    # --- AUTO CLASSIFICATION ---
    if category is None or category not in CATEGORY_CONFIG:
        if category is not None:
            logger.warning(f"Unknown category '{category}' passed in, auto-classifying instead.")
        full_text = " ".join(c["text"] for c in candidates)
        category = classify_content_category(full_text)
        logger.info(f"Auto-classified video_id={video_id} as category='{category}'")

    log_reasoning(video_id, f"Auto-classified category: {category}\n")

    # Reconstruct transcript_lines if not provided
    if not transcript_lines:
        logger.info("No transcript_lines provided, reconstructing from candidates.")
        transcript_lines = []
        for idx, cand in enumerate(candidates):
            transcript_lines.append({
                "start_time": cand.get("start_time", idx * 10.0),
                "end_time": cand.get("end_time", (idx + 1) * 10.0),
                "text": cand.get("text", ""),
                "speaker": cand.get("speaker", "Speaker 1")
            })

    # Prepare formatted segments list
    formatted_segments = []
    for idx, seg in enumerate(transcript_lines):
        formatted_segments.append({
            "id": idx,
            "speaker": seg.get("speaker", "Speaker 1"),
            "start_time": float(seg.get("start_time", 0.0)),
            "end_time": float(seg.get("end_time", 0.0)),
            "text": seg.get("text", "")
        })

    # --- STAGE 1: MOMENT DISCOVERY (Prompt A) in Chunks ---
    logger.info(f"Stage 1: Discovering moments in transcript for category='{category}' in chunks...")
    moments = []
    
    # Using sliding window chunking to ensure stories crossing boundaries aren't sliced in half
    CHUNK_SIZE = 150
    OVERLAP = 20
    step = CHUNK_SIZE - OVERLAP
    
    i = 0
    while i < len(formatted_segments):
        chunk_start = i
        chunk_end = min(i + CHUNK_SIZE, len(formatted_segments))
        
        logger.info(f"Processing Stage 1 Chunk: segments {chunk_start} to {chunk_end-1}...")
        
        chunk_segs = formatted_segments[chunk_start:chunk_end]
        segments_str = ""
        for s in chunk_segs:
            segments_str += f"[Segment {s['id']}] ({s['speaker']}, {s['start_time']:.2f}s - {s['end_time']:.2f}s): {s['text']}\n"

        user_content_finder = (
            f"Here is the video_id: \"{video_id}\"\n"
            f"Here are the transcript segments (Segments {chunk_start} to {chunk_end-1}):\n\n{segments_str}"
        )

        system_prompt_finder = build_moment_finder_prompt(category)
        finder_result = _call_llm_with_fallback(
            system_prompt_finder, user_content_finder, MomentFinderResponse,
            model_override=getattr(settings, "OPENROUTER_MODEL_DISCOVERY", None),
            provider_priority=["openrouter", "nvidia_nim", "ollama"],
        )

        raw_moments = []
        if isinstance(finder_result, list):
            raw_moments = finder_result
        elif isinstance(finder_result, dict):
            raw_moments = finder_result.get("moments", [])

        chunk_moments_count = 0
        for m in raw_moments:
            if not isinstance(m, dict):
                continue
            try:
                start_id = int(m.get("start_segment_id", 0))
                end_id = int(m.get("end_segment_id", 0))
                moments.append({
                    "start_segment_id": start_id,
                    "end_segment_id": end_id,
                    "opportunity_type": m.get("opportunity_type") or m.get("type") or "standalone_insight",
                    "key_idea": m.get("key_idea") or m.get("reasoning") or "Viral opportunity",
                    "mandatory_anchor_line": m.get("mandatory_anchor_line", ""),
                    "mandatory_context_lines": m.get("mandatory_context_lines", []),
                    "why_viral": m.get("why_viral") or m.get("reasoning") or "Viral moment",
                })
                chunk_moments_count += 1
            except Exception:
                continue

        logger.info(f"Chunk {chunk_start}-{chunk_end-1} returned {chunk_moments_count} moments.")
        
        if chunk_end == len(formatted_segments):
            break
        i += step

    # Deduplicate moments with the exact same segment range
    seen = set()
    unique_moments = []
    for m in moments:
        key = (m["start_segment_id"], m["end_segment_id"])
        if key not in seen:
            seen.add(key)
            unique_moments.append(m)
    moments = unique_moments

    if not moments:
        logger.warning("Moment Finder returned zero moments. Generating a fallback moment.")
        if formatted_segments:
            moments = [{
                "start_segment_id": 0,
                "end_segment_id": len(formatted_segments) - 1,
                "opportunity_type": "standalone_insight",
                "key_idea": "Fallback full-transcript opportunity",
                "why_viral": "Fallback"
            }]

    logger.info(f"Moment Finder discovered {len(moments)} opportunity/opportunities:\n{json.dumps(moments, indent=2)}")

    log_reasoning(video_id, "=== STAGE 1: DISCOVERED MOMENTS ===")
    if moments:
        for idx, m in enumerate(moments):
            log_reasoning(video_id, f"Moment {idx+1}:")
            log_reasoning(video_id, f"  Proposed Segments: Segment {m.get('start_segment_id')} to Segment {m.get('end_segment_id')}")
            log_reasoning(video_id, f"  Key Idea: {m.get('key_idea')}")
            log_reasoning(video_id, f"  Why Viral: {m.get('why_viral')}\n")
    else:
        log_reasoning(video_id, "No moments discovered in Stage 1.\n")

    # --- STAGE 2: MOMENT EDITING (Prompt B) ---
    logger.info(f"Stage 2: Editing discovered moments...")
    # Process moments in batches of 3 to avoid output max_tokens truncation and improve LLM instruction accuracy
    raw_clips = []
    MOMENT_BATCH_SIZE = 3
    
    for i in range(0, len(moments), MOMENT_BATCH_SIZE):
        batch_moments = moments[i:i + MOMENT_BATCH_SIZE]
        logger.info(f"Editing moment batch {i // MOMENT_BATCH_SIZE + 1} ({len(batch_moments)} moments)...")
        
        batch_moments_str = ""
        for idx, m in enumerate(batch_moments):
            moment_num = i + idx + 1
            batch_moments_str += (
                f"=== DISCOVERED MOMENT {moment_num} ===\n"
                f"Opportunity Type: {m.get('opportunity_type')}\n"
                f"Proposed Segment Range: Segment {m.get('start_segment_id')} to Segment {m.get('end_segment_id')}\n"
                f"Key Idea: {m.get('key_idea')}\n"
                f"Mandatory Anchor Line (DO NOT DROP): {m.get('mandatory_anchor_line')}\n"
                f"Mandatory Context Lines (DO NOT DROP — these make the anchor line make sense): {m.get('mandatory_context_lines') or '(none — anchor is self-contained)'}\n"
                f"Why Viral: {m.get('why_viral')}\n\n"
            )

        # --- BUG FIX: build the transcript context for THIS batch's moments only,
        # pulled fresh from formatted_segments — do NOT reuse `segments_str`. That
        # variable is a Stage 1 while-loop variable that, in Python, leaks into this
        # outer scope holding only the text of the LAST chunk processed during moment
        # discovery (whatever chunk happened to be nearest the end of the transcript).
        # Every Stage 2 batch was silently being handed that same stale final-chunk
        # text regardless of which moments it was actually trying to edit — so any
        # moment whose segment range fell outside that last chunk (which is most of
        # them, for any transcript longer than ~150 segments) got no matching
        # transcript text at all. This is why the editor sometimes explicitly said
        # "transcript for segments X-Y not provided in the snippet" and scored those
        # moments 0 across the board, and why some early moments never appeared in
        # Stage 2 output at all.
        BUFFER = 5  # segments of padding around each moment, for sentence-boundary context
        needed_ids = set()
        for m in batch_moments:
            s_id = max(0, int(m.get("start_segment_id", 0)) - BUFFER)
            e_id = min(len(formatted_segments) - 1, int(m.get("end_segment_id", 0)) + BUFFER)
            needed_ids.update(range(s_id, e_id + 1))

        batch_segments_str = ""
        for seg_id in sorted(needed_ids):
            s = formatted_segments[seg_id]
            batch_segments_str += f"[Segment {s['id']}] ({s['speaker']}, {s['start_time']:.2f}s - {s['end_time']:.2f}s): {s['text']}\n"

        user_content_editor = (
            f"Here is the video_id: \"{video_id}\"\n"
            f"Here are the original transcript segments relevant to this batch:\n\n{batch_segments_str}\n"
            f"Here are the discovered narrative/viral moments to edit (Batch {i // MOMENT_BATCH_SIZE + 1}):\n\n{batch_moments_str}"
        )

        system_prompt_editor = build_moment_editor_prompt(category)
        editor_result = _call_llm_with_fallback(
            system_prompt_editor, user_content_editor, EditedClipsResponse,
            provider_priority=["nvidia_nim", "openrouter", "ollama"],
        )
        
        if editor_result and "clips" in editor_result:
            batch_clips = editor_result["clips"]
            # Carry the originating moment's mandatory anchor/context lines forward onto
            # each edited clip. The editor's own output schema doesn't echo these back, so
            # without this, nothing downstream could ever check whether the editor actually
            # kept them — the "mandatory" rule was prompt-only. Match by position when the
            # counts line up (the common case, one clip per moment); otherwise fall back to
            # whichever moment's segment range the edited clip overlaps most.
            for c_idx, c in enumerate(batch_clips):
                if len(batch_clips) == len(batch_moments):
                    source_moment = batch_moments[c_idx]
                else:
                    c_start = c.get("start_segment_id", 0)
                    c_end = c.get("end_segment_id", 0)
                    source_moment = max(
                        batch_moments,
                        key=lambda mm: max(0, min(c_end, mm["end_segment_id"]) - max(c_start, mm["start_segment_id"]))
                    )
                c["mandatory_anchor_line"] = source_moment.get("mandatory_anchor_line", "")
                c["mandatory_context_lines"] = source_moment.get("mandatory_context_lines", [])
            raw_clips.extend(batch_clips)

    logger.info(f"Moment Editor output {len(raw_clips)} edited clips in total:\n{json.dumps(raw_clips, indent=2)}")

    log_reasoning(video_id, "=== STAGE 2: EDITED CLIPS FROM LLM ===")
    if raw_clips:
        for idx, c in enumerate(raw_clips):
            log_reasoning(video_id, f"Clip {idx+1}:")
            log_reasoning(video_id, f"  Segments: Segment {c.get('start_segment_id') or c.get('edited_start_segment_id') or c.get('edited_start_segment')} to Segment {c.get('end_segment_id') or c.get('edited_end_segment_id') or c.get('edited_end_segment')}")
            log_reasoning(video_id, f"  Hook Line: {c.get('hook_line')}")
            log_reasoning(video_id, f"  Reasoning: {c.get('reason')}")
            log_reasoning(video_id, f"  Strategy: {c.get('clip_strategy') or c.get('editing_strategy')}")
            log_reasoning(video_id, f"  Style: {c.get('editorial_style')}")
            log_reasoning(video_id, f"  Scores: Curiosity={c.get('curiosity_score')}, Hook={c.get('hook_score')}, Surprise={c.get('surprise_score')}, Emotion={c.get('emotion_score')}, Reaction={c.get('reaction_score')}, Standalone={c.get('standalone_score')}\n")
    else:
        log_reasoning(video_id, "No clips generated in Stage 2.\n")

    # Fallback to simple fallback clips if editor returned nothing
    if not raw_clips:
        logger.warning("Moment Editor returned zero clips. Using fallback heuristic windows.")
        fallback_data = call_nvidia_nim_batch_scoring(candidates, video_id, category)
        raw_clips = []
        for fc in fallback_data.get("clips", []):
            start_f = float(fc.get("start_time", 0.0))
            end_f = float(fc.get("end_time", 0.0))
            start_id = 0
            end_id = len(formatted_segments) - 1
            for idx, s in enumerate(formatted_segments):
                if abs(s["start_time"] - start_f) < 2.0:
                    start_id = idx
                if abs(s["end_time"] - end_f) < 2.0:
                    end_id = idx
            
            raw_clips.append({
                "start_segment_id": start_id,
                "end_segment_id": end_id,
                "clip_strategy": fc.get("clip_strategy", "payoff"),
                "editorial_style": "standalone_insight",
                "hook_line": fc.get("hook_line", ""),
                "reason": fc.get("reason", "Fallback heuristic"),
                "curiosity_score": fc.get("curiosity_score", 5),
                "hook_score": fc.get("hook_score", 5),
                "surprise_score": 5,
                "emotion_score": 5,
                "reaction_score": 5,
                "standalone_score": 5,
                "mandatory_anchor_line": "",
                "mandatory_context_lines": [],
            })

    # Prepare for enrichment
    final_clips = []
    for clip in raw_clips:
        try:
            start_seg = clip.get("start_segment_id") or clip.get("edited_start_segment_id") or clip.get("edited_start_segment") or 0
            end_seg = clip.get("end_segment_id") or clip.get("edited_end_segment_id") or clip.get("edited_end_segment") or 0

            question_reanchored = False
            if category == "interview_discussion":
                question_idx = find_nearest_setup_question(start_seg, formatted_segments, max_lookback=10)
                if question_idx is not None:
                    candidate_duration = formatted_segments[end_seg]["end_time"] - formatted_segments[question_idx]["start_time"]
                    cfg_bounds_check = CATEGORY_CONFIG.get(category) or CATEGORY_CONFIG[DEFAULT_CATEGORY]
                    if candidate_duration <= float(cfg_bounds_check.get("max_duration_sec", 60.0)) * 1.5:
                        start_seg = question_idx
                        question_reanchored = True

            # --- ANCHOR / CONTEXT LINE ENFORCEMENT ---
            # Skipped for mandatory_context_lines when the Q&A re-anchor above already
            # fixed the setup problem more cheaply — no need to also drag in a distant
            # named context line once the question itself re-establishes context.
            # The anchor line itself is still checked either way as a safety net.
            # Code-level check that the editor didn't cut out the sentence(s) Stage 1
            # flagged as mandatory (e.g. dropping "Netflix" from a "97% rejected" clip).
            cfg_bounds_for_clip = CATEGORY_CONFIG.get(category) or CATEGORY_CONFIG[DEFAULT_CATEGORY]
            line_enforcement = enforce_mandatory_lines(
                start_seg=start_seg,
                end_seg=end_seg,
                mandatory_anchor_line=clip.get("mandatory_anchor_line", ""),
                mandatory_context_lines=[] if question_reanchored else clip.get("mandatory_context_lines", []),
                formatted_segments=formatted_segments,
                max_duration_sec=float(cfg_bounds_for_clip.get("max_duration_sec", 60.0)),
            )
            start_seg, end_seg = line_enforcement["start_seg"], line_enforcement["end_seg"]
            dropped_mandatory_lines = line_enforcement["dropped_lines"]

            # Check for silent gaps (> 3.0s) between consecutive segments and truncate the clip before the gap
            for idx in range(start_seg, end_seg):
                curr_end = formatted_segments[idx]["end_time"]
                next_start = formatted_segments[idx+1]["start_time"]
                if next_start - curr_end > 3.0:
                    logger.info(f"Detected silent gap of {next_start - curr_end:.2f}s between segment {idx} and {idx+1}. Truncating clip end_segment to {idx}.")
                    end_seg = idx
                    break

            start_seg = max(0, min(start_seg, len(formatted_segments) - 1))
            end_seg = max(0, min(end_seg, len(formatted_segments) - 1))
            if start_seg > end_seg:
                start_seg, end_seg = end_seg, start_seg

            clip_start = formatted_segments[start_seg]["start_time"]
            clip_end = formatted_segments[end_seg]["end_time"]
            excerpt = " ".join(formatted_segments[i]["text"] for i in range(start_seg, end_seg + 1))

            raw_clip_format = {
                "start_time": clip_start,
                "end_time": clip_end,
                "clip_strategy": clip.get("clip_strategy", "payoff"),
                "hook_line": clip.get("hook_line", ""),
                "reason": clip.get("reason", ""),
                "curiosity_score": clip.get("curiosity_score", 5),
                "hook_score": clip.get("hook_score", 5),
                "surprise_score": clip.get("surprise_score", 5),
                "emotion_score": clip.get("emotion_score", 5),
                "reaction_score": clip.get("reaction_score", 5),
                "standalone_score": clip.get("standalone_score", 5),
                "transcript_excerpt": excerpt,
                "category": category,
            }

            raw_clip_format["heuristic_score"] = heuristic_pre_filter_score(excerpt)

            enriched = enrich_clip(raw_clip_format, candidates, category, video_id, audio_path=audio_path)

            if dropped_mandatory_lines:
                preview = "; ".join(l[:60] for l in dropped_mandatory_lines)
                logger.warning(f"Clip [{clip_start}-{clip_end}] could not fit mandatory line(s) within duration budget: {preview}")
                enriched["needs_manual_review"] = True
                enriched["reason"] = (enriched.get("reason", "") + f" [FLAGGED: Dropped mandatory anchor/context line: {preview}]").strip()


            if category == "interview_discussion":
                is_valid, invalid_reason = validate_exchange_completeness(
                    enriched["start_time"], enriched["end_time"], transcript_lines
                )
                if not is_valid:
                    logger.warning(f"Clip [{enriched['start_time']}-{enriched['end_time']}] failed exchange validation: {invalid_reason}")
                    enriched["needs_manual_review"] = True
                    enriched["reason"] = (enriched.get("reason", "") + f" [FLAGGED: {invalid_reason}]").strip()
                    virality = compute_virality_score(enriched, category) * 0.5
                else:
                    virality = compute_virality_score(enriched, category)
            else:
                virality = compute_virality_score(enriched, category)

            enriched["virality_score"] = round(virality, 2)
            enriched["narrative_summary"] = clip.get("reason", "")
            enriched["why_viewers_keep_watching"] = clip.get("reason", "")
            enriched["section_type"] = clip.get("editorial_style", category)
            enriched["suggested_title"] = clip.get("hook_line", "Untitled")[:40]

            final_clips.append(enriched)
        except Exception as e:
            logger.error(f"Error mapping candidate output clip: {e}")

    # --- HARD SAFETY / SANITY GATES BEFORE RANKING ---
    # IMPORTANT DISTINCTION:
    #   - Content safety and absurd duration are TRUE hard drops — these clips are
    #     never usable regardless of how "viral" they scored.
    #   - Boundary-repair failure and low-substance flags are SOFT gates — a clip
    #     can be the single most viral moment in the whole video and still fail to
    #     auto-repair cleanly (e.g. it sits right at a real edit cut in the source
    #     footage). Previously these were hard-dropped via `continue`, meaning the
    #     best moment in a video could silently vanish from the results with no way
    #     back. Now they're kept in the ranked list with a virality penalty and a
    #     loud `needs_manual_review` / `auto_render_blocked` flag — a human decides
    #     whether to hand-trim it, instead of the pipeline deciding for them by
    #     deleting it.
    BOUNDARY_UNCLEAN_PENALTY = 0.65   # moderate penalty — still rankable, clearly flagged
    LOW_SUBSTANCE_HARD_PENALTY = 0.35  # steeper penalty — this signal is stronger evidence the clip is genuinely weak

    safe_and_valid_clips = []
    log_reasoning(video_id, "=== PIPELINE FILTERS & VALIDATIONS ===")
    for clip in final_clips:
        start_t = clip["start_time"]
        end_t = clip["end_time"]
        dur = clip["duration_sec"]

        # 1. Content Safety Check — TRUE hard drop, no exceptions.
        is_safe, safety_reason = check_content_safety(clip["transcript_excerpt"])
        if not is_safe:
            msg = f"Clip [{start_t:.2f}s - {end_t:.2f}s] PULLED: Content safety violation - {safety_reason}"
            logger.warning(msg)
            log_reasoning(video_id, msg)
            continue

        # 2. Absurd Duration Check — TRUE hard drop (can't be rendered as one coherent clip at all).
        cfg = (
            CATEGORY_CONFIG.get(clip.get("category"))
            or CATEGORY_CONFIG.get(category)
            or CATEGORY_CONFIG[DEFAULT_CATEGORY]
        )
        min_allowed = float(cfg.get("min_duration_sec", 15.0))
        max_allowed = float(cfg.get("max_duration_sec", 60.0))

        if dur < 5.0 or dur > 120.0 or dur < (min_allowed * 0.4) or dur > (max_allowed * 1.6):
            msg = f"Clip [{start_t:.2f}s - {end_t:.2f}s] PULLED: Absurd duration {dur:.2f}s (Allowed Limits: {min_allowed}s - {max_allowed}s)"
            logger.warning(msg)
            log_reasoning(video_id, msg)
            continue

        # 2b. Boundary-repair failure — SOFT gate. Kept, penalized, flagged loudly.
        if clip.get("auto_render_blocked"):
            clip["virality_score"] = round(clip["virality_score"] * BOUNDARY_UNCLEAN_PENALTY, 2)
            clip["needs_manual_review"] = True
            msg = (
                f"Clip [{start_t:.2f}s - {end_t:.2f}s] KEPT (flagged, penalty applied) — "
                f"Unrepairable sentence boundaries: {clip.get('reason')}"
            )
            logger.warning(msg)
            log_reasoning(video_id, msg)
            safe_and_valid_clips.append(clip)
            continue

        # 2c. Low-substance hard-block — SOFT gate, steeper penalty than boundary issues
        # since this signal (mostly backchannel/filler) is stronger evidence of a weak clip.
        if clip.get("low_substance_ratio", 0.0) > LOW_SUBSTANCE_HARD_BLOCK_THRESHOLD:
            clip["virality_score"] = round(clip["virality_score"] * LOW_SUBSTANCE_HARD_PENALTY, 2)
            clip["needs_manual_review"] = True
            msg = (
                f"Clip [{start_t:.2f}s - {end_t:.2f}s] KEPT (flagged, steep penalty applied) — "
                f"Low-substance: {clip.get('reason')}"
            )
            logger.warning(msg)
            log_reasoning(video_id, msg)
            safe_and_valid_clips.append(clip)
            continue

        log_reasoning(video_id, f"Clip [{start_t:.2f}s - {end_t:.2f}s] PASSED filters (Duration: {dur:.2f}s)")
        safe_and_valid_clips.append(clip)

    log_reasoning(video_id, "")

    # Sort by virality score descending
    safe_and_valid_clips.sort(key=lambda x: x["virality_score"], reverse=True)

    # --- FINAL TIMELINE DEDUPLICATION ---
    final_ranked_clips = deduplicate_timeline(safe_and_valid_clips, max_overlap_ratio=0.20)

    # Assign ranks
    for idx, c in enumerate(final_ranked_clips):
        c["rank"] = idx + 1

    # Log final outputs
    log_reasoning(video_id, "=== FINAL RANKED SUGGESTED CLIPS ===")
    for idx, c in enumerate(final_ranked_clips[:limit]):
        log_reasoning(video_id, f"Rank {idx+1}:")
        log_reasoning(video_id, f"  Time: {c['start_time']:.2f}s - {c['end_time']:.2f}s (Duration: {c['duration_sec']:.2f}s)")
        log_reasoning(video_id, f"  Title/Hook: {c['suggested_title']}")
        log_reasoning(video_id, f"  Virality Score: {c['virality_score']}")
        log_reasoning(video_id, f"  Why it works: {c['why_viewers_keep_watching']}\n")

    return final_ranked_clips[:limit]