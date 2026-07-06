import logging
import json
import re
import time
import random
from typing import List, Dict, Any, Optional, Tuple
from app.config import settings
from app.services.audio_signal import detect_audio_events

logger = logging.getLogger(__name__)

# ============================================================
# RETRY WRAPPER — handles 503 / 429 / 502 / 500 from any provider
# Uses exponential backoff with full jitter so bursts don't pile up.
# ============================================================

_RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
_MAX_RETRY_ATTEMPTS   = 4          # total attempts (1 original + 3 retries)
_RETRY_BASE_DELAY_SEC = 2.0        # starting backoff (doubles each retry)
_RETRY_MAX_DELAY_SEC  = 30.0       # cap so we don't wait forever


def _is_retryable_error(exc: Exception) -> bool:
    """Returns True if the exception looks like a transient provider error."""
    msg = str(exc).lower()
    # httpx / openai surface these as status codes in the message
    for code in _RETRYABLE_HTTP_CODES:
        if str(code) in msg:
            return True
    # Common phrases from OpenRouter / Groq error bodies
    retryable_phrases = [
        "service unavailable", "overloaded", "rate limit",
        "too many requests", "bad gateway", "gateway timeout",
        "internal server error", "upstream", "timeout",
    ]
    return any(phrase in msg for phrase in retryable_phrases)


def _call_with_retry(fn, *args, provider_name: str = "?", pass_label: str = "", **kwargs):
    """
    Calls fn(*args, **kwargs) with up to _MAX_RETRY_ATTEMPTS total attempts.
    On a retryable error, waits with exponential backoff + full jitter before
    the next attempt.  Non-retryable errors are re-raised immediately.
    """
    last_exc = None
    for attempt in range(1, _MAX_RETRY_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not _is_retryable_error(exc) or attempt == _MAX_RETRY_ATTEMPTS:
                raise
            # Exponential backoff with full jitter
            base = min(_RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1)), _RETRY_MAX_DELAY_SEC)
            wait = random.uniform(0, base)
            logger.warning(
                f"[{provider_name}] {pass_label} attempt {attempt} failed "
                f"({type(exc).__name__}: {exc}). "
                f"Retrying in {wait:.1f}s (attempt {attempt+1}/{_MAX_RETRY_ATTEMPTS})..."
            )
            time.sleep(wait)
    raise last_exc  # unreachable but keeps linters happy

# ============================================================
# CATEGORY CONFIG — each category has its own prompt strategy
# AND its own virality scoring weights (must sum to 1.0)
# ============================================================

CATEGORY_CONFIG = {

    "story_narrative": {
        "description": "A personal life story, anecdote, or testimonial told with a beginning, middle, and end, describing events that happened to the narrator.",
        "clip_strategy": "cliffhanger",
        "prompt_rules": (
            "This content is NARRATIVE/STORYTELLING. Act like an expert viral short-form storyteller and retention editor.\n"
            "- Your job is NOT to find complete clips with payoffs. Your job is to find the BEST 'Part 1' moments that make viewers immediately want to watch Part 2.\n"
            "- Think like a movie trailer editor. The clip should END exactly at the moment curiosity reaches its highest point (the retention breakpoint).\n"
            "- Never include the answer. CUT immediately after the final question or shocking cliffhanger sentence.\n"
            "- Find the exact sentence where a viewer would most likely comment 'Part 2?' or continue watching if this were split into two reels. Cut immediately after that sentence.\n"
            "- If a story naturally takes 43 seconds before the curiosity peak, return 43 seconds. If the peak happens after only 5 seconds, return 5 seconds. Optimize ONLY for the highest curiosity peak."
        ),
        "weights": {
            "curiosity": 0.30,
            "hook": 0.15,
            "context": 0.15,
            "emotion": 0.15,
            "surprise": 0.05,
            "reaction": 0.05,
            "audio_signal": 0.15,
        },
        "min_duration_sec": 15.0,
        "max_duration_sec": 60.0,
    },

    "comedy_punchline": {
        "description": "A joke, comedic bit, or funny observation with a clear setup and punchline meant to make people laugh.",
        "clip_strategy": "payoff",
        "prompt_rules": (
            "This content is COMEDY/PUNCHLINE. Act like an expert stand-up comedy editor who has cut "
            "thousands of viral comedy clips for TikTok and Reels.\n"
            "- Your job is to find a clip that is FUNNY ON ITS OWN, with zero missing context, the moment it starts playing.\n"
            "- A joke has three parts: the setup (establishes expectation), the twist (violates expectation), "
            "and the tag/reaction (the laugh or the awkward pause after). ALL THREE must be inside the clip.\n"
            "- Identify the exact word or sentence where the joke 'turns' — that's the punchline. The clip must "
            "not cut before this point under any circumstance.\n"
            "- Include 1-3 seconds of tag/reaction room after the punchline (laughter, pause, someone reacting) — "
            "this is where the payoff actually lands emotionally for the viewer, don't cut it off.\n"
            "- Prefer setups that are SHORT. If the setup takes longer than 20 seconds before the twist, it's "
            "probably not a clean clip — look for a shorter, tighter joke elsewhere instead.\n\n"
            "AVOID THESE COMMON MISTAKES:\n"
            "- Do NOT select a clip that ends right as the joke starts, before the twist lands — this is the "
            "single most common comedy-clipping error and it kills retention instantly.\n"
            "- Do NOT select something merely lighthearted or pleasant in tone if it has no actual comedic turn — "
            "'funny-adjacent' is not the same as 'funny.'\n"
            "- Total duration MUST be between 15 and 60 seconds."
        ),
        "weights": {
            "surprise": 0.25,
            "reaction": 0.20,
            "hook": 0.15,
            "curiosity": 0.05,
            "emotion": 0.05,
            "context": 0.05,
            "audio_signal": 0.25,
        },
        "min_duration_sec": 10.0,
        "max_duration_sec": 45.0,
    },

    "interview_discussion": {
        "description": "A back-and-forth conversation between two or more speakers, asking and answering questions, debating, or interviewing.",
        "clip_strategy": "mixed",
        "prompt_rules": (
            "This content is INTERVIEW/DISCUSSION. Act like an expert podcast clip editor who specializes in "
            "cutting Q&A exchanges that feel complete and satisfying in under 60 seconds.\n"
            "- Identify the exact question, challenge, or claim being raised, and the exact answer or pushback "
            "it receives. Both halves of the exchange must live inside your chosen boundaries.\n"
            "- If the exchange resolves within the segment, use PAYOFF and include the resolution in full.\n"
            "- If one speaker raises something genuinely intriguing that is NOT addressed yet within the "
            "transcript window you were given, use CLIFFHANGER and cut right after the intriguing claim — "
            "but only if it's a real unresolved claim, not a mundane question awaiting an obvious answer.\n"
            "- Prefer moments where the answer surprises, contradicts, or complicates the question — a boring, "
            "expected answer to a boring, expected question is not a viral clip even if the exchange is 'complete.'\n"
            "- Total duration MUST be between 15 and 60 seconds.\n\n"
            "AVOID THESE COMMON MISTAKES:\n"
            "- Do NOT select a clip that is just one speaker asking a question with no answer inside the clip. "
            "A clip that ends on '...so what happened next?' with silence or a cut is a BAD clip — it has no "
            "payoff and will not retain viewers.\n"
            "- Do NOT select a clip where the same speaker talks the entire time with no second voice responding "
            "— that is a monologue, not an interview exchange, even if it happens during an interview.\n"
            "- Do NOT select generic small talk or pleasantries ('how are you', 'thanks for having me') even if "
            "it technically has two speakers — it must contain a real question/claim + real answer/reaction."
        ),
        "weights": {
            "curiosity": 0.20,
            "context": 0.20,
            "hook": 0.15,
            "surprise": 0.10,
            "emotion": 0.05,
            "reaction": 0.10,
            "audio_signal": 0.20,
        },
        "min_duration_sec": 15.0,
        "max_duration_sec": 60.0,
    },

    "motivational_emotional": {
        "description": "A vulnerable, emotional reflection about self-worth, healing, personal growth, or overcoming struggle.",
        "clip_strategy": "payoff",
        "prompt_rules": (
            "This content is MOTIVATIONAL/EMOTIONAL. Act like an expert editor for emotional/self-help short-form "
            "content who understands what makes people stop scrolling and feel something.\n"
            "- The clip should carry ONE complete emotional beat: vulnerability or struggle first, then the "
            "realization, acceptance, or turning point. Do not cut mid-realization — the emotional payoff must "
            "land fully within the clip.\n"
            "- The strongest ending line is usually short, quotable, and universal — something a viewer would "
            "screenshot or repeat to themselves. Prioritize ending the clip on that exact line, even if it means "
            "trimming earlier context.\n"
            "- The opening should establish real stakes fast — what was actually hard, painful, or uncertain — "
            "not a vague lead-in. Viewers need to feel the weight of the struggle within the first few seconds.\n"
            "- Favor specific, concrete personal detail over generic self-help language. 'I felt like nobody "
            "would ever like me' is stronger than 'I struggled with self-esteem.'\n"
            "- Total duration MUST be between 15 and 60 seconds.\n\n"
            "AVOID THESE COMMON MISTAKES:\n"
            "- Do NOT select a clip that is only the struggle with no resolution, or only the resolution with no "
            "stakes established — both halves must be present.\n"
            "- Do NOT select purely generic inspirational statements with no personal story attached to them — "
            "specificity is what makes emotional clips land, not abstraction."
        ),
        "weights": {
            "emotion": 0.30,
            "curiosity": 0.10,
            "hook": 0.10,
            "context": 0.10,
            "reaction": 0.05,
            "surprise": 0.05,
            "audio_signal": 0.30,
        },
        "min_duration_sec": 15.0,
        "max_duration_sec": 60.0,
    },

    "controversial_hot_take": {
        "description": "A bold, provocative opinion or claim stated confidently, meant to spark disagreement or debate.",
        "clip_strategy": "cliffhanger",
        "prompt_rules": (
            "This content is CONTROVERSIAL/HOT TAKE. Act like an expert editor for debate-bait short-form clips "
            "designed to fill up the comment section.\n"
            "- Identify the single boldest, most polarizing claim in the transcript — the sentence most likely "
            "to make half the audience nod and the other half want to argue.\n"
            "- End the clip immediately after that claim is stated, before any softening, qualification, "
            "'but obviously...', or walk-back. The unresolved confidence is what drives comments — don't let the "
            "speaker undercut their own claim inside the clip.\n"
            "- start_time: include 15-30 seconds of buildup BEFORE the claim so viewers have enough context to "
            "understand exactly what's being argued and why it matters.\n"
            "- The goal is provoking a reaction (agree/disagree/argue in comments), not winning the argument for "
            "the speaker — do not include rebuttal, evidence, or resolution.\n"
            "- Total duration MUST be between 15 and 60 seconds.\n\n"
            "AVOID THESE COMMON MISTAKES:\n"
            "- Do NOT select a claim that is immediately qualified or softened within the same breath — that "
            "defuses the controversy before the clip even ends.\n"
            "- Do NOT select something merely opinionated but uncontroversial (e.g. a claim almost everyone "
            "already agrees with) — the whole point is a claim that splits the audience."
        ),
        "weights": {
            "hook": 0.20,
            "surprise": 0.15,
            "curiosity": 0.15,
            "emotion": 0.10,
            "reaction": 0.10,
            "context": 0.05,
            "audio_signal": 0.25,
        },
        "min_duration_sec": 15.0,
        "max_duration_sec": 60.0,
    },
}

DEFAULT_CATEGORY = "story_narrative"

# ============================================================
# GENERIC (CATEGORY-AGNOSTIC) PIPELINE TUNABLES
# ============================================================

# Stage 1 used to hard-cap at 6 candidates regardless of video length or category.
# That meant a 44-minute video with 591 windows only ever let 6 of them compete for
# the LLM's attention. Raised so richer/longer content gets a fair shot.
STAGE1_CANDIDATE_POOL_SIZE = 18

# How far (in seconds) we're willing to extend a clip's start backward or end forward
# to reach a clean sentence boundary. Applies to every category equally.
MAX_BOUNDARY_LOOKBACK_SEC = 12.0
MAX_BOUNDARY_LOOKAHEAD_SEC = 12.0

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

def check_sentence_boundary(text: str) -> bool:
    """
    Checks if the transcript text ends on a clean sentence/clause boundary.
    Flags clips that end on conjunctions or lack terminal punctuation.
    """
    if not text:
        return True
    
    text = text.strip()
    
    # Check for terminal punctuation or ellipsis
    terminal_punctuations = ('.', '?', '!', '"', "'", '…', '...')
    if not text.endswith(terminal_punctuations):
        # Check if the last word is a conjunction or preposition
        last_words = text.lower().split()
        if last_words:
            last_word = last_words[-1].strip(',.?!;:"\'')
            conjunctions = {"and", "but", "or", "so", "because", "although", "while", "if", "then", "the", "a", "an", "of", "to", "with"}
            if last_word in conjunctions:
                return False
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

# Lean schema — only fields Nemotron MUST judge. Everything else is derived in Python.
LEAN_SCHEMA_BLOCK = (
    "{\n"
    "  \"video_id\": \"string\",\n"
    "  \"clips\": [\n"
    "    {\n"
    "      \"start_time\": 0.0,\n"
    "      \"end_time\": 0.0,\n"
    "      \"clip_strategy\": \"cliffhanger | payoff\",\n"
    "      \"hook_line\": \"string\",\n"
    "      \"reason\": \"string\",\n"
    "      \"curiosity_score\": 0,\n"
    "      \"hook_score\": 0\n"
    "    }\n"
    "  ]\n"
    "}\n"
)


# ============================================================
# OPTIONAL DEPENDENCIES (each degrades gracefully if missing)
# ============================================================

try:
    from pydantic import BaseModel, Field
    from typing import Literal

    class NarrativeUnitModel(BaseModel):
        unit_id: int
        section_type: Literal[
            "story", "debate", "lesson", "personal_experience", "failure", "success",
            "analogy", "mindset_shift", "prediction", "reveal", "quote", "argument",
            "twist", "confession", "question_answer"
        ]
        start_sentence: str
        end_sentence: str
        summary: str = Field(description="One-sentence summary of this segment.")
        hook: str = Field(description="The starting hook or main question of this unit.")
        buildup: str = Field(description="The build-up leading to the climax or key claim.")
        climax: str = Field(description="The climax/clash/key statement of the unit.")
        reveal: str = Field(description="The main payoff or answer/conclusion.")
        resolution: str = Field(description="How the story or debate resolved.")
        tension_point: str = Field(description="The highest tension sentence or peak curiosity point.")

    class NarrativeMapResponse(BaseModel):
        units: List[NarrativeUnitModel]

    class RetentionPeakModel(BaseModel):
        unit_id: int
        cut_after_sentence: str = Field(description="The exact sentence immediately after which we should stop the clip.")
        cut_rationale: str = Field(description="Detailed psychological rationale for cutting at this sentence.")
        estimated_duration_sec: float = Field(description="Estimated duration in seconds (usually 15-60s).")
        curiosity_score: int = Field(ge=0, le=100)
        scroll_stop_score: int = Field(ge=0, le=100)
        part2_score: int = Field(ge=0, le=100)
        rewatch_score: int = Field(ge=0, le=100)
        standalone_score: int = Field(ge=0, le=100)
        cliffhanger_score: int = Field(ge=0, le=100)
        why_viewers_keep_watching: str = Field(description="A sentence explaining why viewers will keep watching this clip.")
        suggested_title: str = Field(description="A hooky, clicky title for the reel.")

    class RetentionPeaksResponse(BaseModel):
        peaks: List[RetentionPeakModel]

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
    _SBERT_AVAILABLE = False

_embedding_model = None
_anchor_embeddings = None
_anchor_keys = None


def _load_embedding_classifier():
    """Lazy-loads the sentence-transformer model + category anchor embeddings once."""
    global _embedding_model, _anchor_embeddings, _anchor_keys
    if _embedding_model is not None:
        return
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

    best_category = max(votes, key=votes.get)
    if votes[best_category] == 0:
        return DEFAULT_CATEGORY

    logger.info(f"Last-resort heuristic classifier votes: {votes} -> chose '{best_category}'")
    return best_category


# ============================================================
# LLM PROVIDER FALLBACK CHAIN (category-agnostic)
# ============================================================
# OpenRouter, NVIDIA NIM, and Groq all expose an OpenAI-compatible chat.completions API,
# so the exact same prompts, Instructor/Pydantic guaranteed-schema path, and
# legacy manual-parse path work unchanged for all — only base_url, api_key,
# and model name differ. This means the fallback is purely an infra concern:
# it applies identically no matter which CATEGORY_CONFIG entry is active.
#
# PROVIDER ORDER: NVIDIA NIM (primary) → OpenRouter (fallback) → Groq (tertiary)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "google/gemma-4-31b-it:free"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


def _get_llm_providers() -> List[Dict[str, str]]:
    """
    Returns an ordered list of usable LLM provider configs.
    Provider order: NVIDIA NIM (primary) → OpenRouter (fallback) → Groq (tertiary).
    A provider is only included if its API key is actually configured.
    """
    providers: List[Dict[str, str]] = []

    # ── PRIMARY: NVIDIA NIM ──────────────────────────────────────────────────
    nvidia_key = getattr(settings, "NVIDIA_API_KEY", None)
    if nvidia_key and nvidia_key not in ["your_nvidia_api_key_here", ""]:
        providers.append({
            "name": "nvidia_nim",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key": nvidia_key,
            "model": getattr(settings, "NVIDIA_MODEL", "openai/gpt-oss-120b"),
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            "extra_headers": {},
        })

    # ── FALLBACK 1: OpenRouter ───────────────────────────────────────────────
    openrouter_key = getattr(settings, "OPENROUTER_API_KEY", None)
    if openrouter_key and openrouter_key not in ["your_openrouter_api_key_here", ""]:
        providers.append({
            "name": "openrouter",
            "base_url": OPENROUTER_BASE_URL,
            "api_key": openrouter_key,
            "model": getattr(settings, "OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL),
            "extra_body": {},
            "extra_headers": {
                "HTTP-Referer": "https://github.com/podcast-to-shortreel",
                "X-Title": "Podcast To ShortReel",
            },
        })

    # ── FALLBACK 2: Groq ─────────────────────────────────────────────────────
    groq_key = getattr(settings, "GROQ_API_KEY", None)
    if groq_key and groq_key not in ["your_groq_api_key_here", ""]:
        providers.append({
            "name": "groq",
            "base_url": GROQ_BASE_URL,
            "api_key": groq_key,
            "model": getattr(settings, "GROQ_MODEL", DEFAULT_GROQ_MODEL),
            "extra_body": {},
            "extra_headers": {},
        })

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
        default_headers=provider.get("extra_headers") or {},
        http_client=httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)),
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

    raw = completion.choices[0].message.content.strip()
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

    providers = _get_llm_providers()
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
# PROMPT BUILDING
# ============================================================

def build_system_prompt(category: str) -> str:
    """Builds the rich, persona-driven system prompt for a specific content category."""
    cfg = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG[DEFAULT_CATEGORY])

    return (
        "You are Jamie, a senior short-form content strategist with 8+ years cutting viral clips "
        "for the world's biggest podcasts — JRE, Lex Fridman, Hot Ones, Diary of a CEO, and similar. "
        "You have an obsessive understanding of what stops a cold audience mid-scroll on TikTok and Instagram Reels.\n\n"

        "PLATFORM CONTEXT (read this before judging anything):\n"
        "- The clip will be shown to a COLD AUDIENCE — people who have never heard of this show, "
        "this guest, or this topic. They are mid-scroll with their sound ON. You have exactly 3 seconds "
        "to stop them before they swipe away. There is no host introduction, no context, no episode title — "
        "just the clip, starting cold.\n"
        "- Average TikTok viewer drops off after 7 seconds unless the hook lands. Your clip must earn "
        "every additional second.\n"
        "- The best clips feel like they were MADE for short-form — not ripped from a longer video.\n\n"

        f"CONTENT CATEGORY: {category}\n"
        f"What this means: {cfg['description']}\n\n"

        "YOUR MISSION FOR THIS CATEGORY (follow exactly — this overrides your general instincts):\n"
        f"{cfg['prompt_rules']}\n\n"

        "SCORING RUBRIC (use these definitions when assigning scores 0-10):\n"
        "curiosity_score:\n"
        "  10 = A cold stranger MUST know what happens next. They would screenshot, share, or comment 'Part 2?'\n"
        "   7 = Intriguing enough to watch to the end, but not urgent.\n"
        "   4 = Mildly interesting. Some viewers finish, most swipe.\n"
        "   0 = No reason to keep watching after the first 5 seconds.\n"
        "hook_score:\n"
        "  10 = First sentence immediately grabs attention. No context needed.\n"
        "   7 = Good opening, but takes 5-8 seconds to really pull the viewer in.\n"
        "   4 = Adequate opening but forgettable. Requires prior knowledge of the show.\n"
        "   0 = Opens with pleasantries, setup, or a question with no immediate payoff.\n\n"

        "HARD RULES (breaking these makes the clip unshippable):\n"
        "- Only select moments that are 100% self-contained — a stranger with zero context must understand it instantly.\n"
        "- The clip MUST have a strong first line — if the first sentence is 'Yeah' / 'So' / 'Like I was saying' — "
        "it is NOT a valid clip start, move forward until you hit a real hook.\n"
        "- Output multiple ranked candidates, best first.\n"
        "- curiosity_score and hook_score are 0-10 integers.\n"
        "- reason must explain in 25-35 words WHY this specific moment is viral-worthy — "
        "what psychological trigger does it activate? (curiosity, shock, laughter, relatability, controversy?)\n"
        "- hook_line must be the EXACT verbatim sentence from the transcript that is either the clip's "
        "opening hook or the cliffhanger/punchline end-point (per your strategy above).\n\n"

        "Return ONLY this JSON. No markdown, no explanation, no extra fields:\n"
        f"{LEAN_SCHEMA_BLOCK}\n"
        "Keep the JSON compact and minimal. Do not add any fields not listed above."
    )


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
    a real exchange (2+ speakers, doesn't end mid-question with no answer).

    Returns (is_valid, reason_if_invalid). If transcript_lines is not
    provided, validation is skipped and always returns (True, "").

    Diarization fallback: if all lines share the same speaker label (broken
    diarization), falls back to Q/A turn detection via punctuation + silence gaps.
    """
    if not transcript_lines:
        return True, ""

    lines_in_window = [
        l for l in transcript_lines
        if l.get("start_time", 0) >= start and l.get("end_time", 0) <= end
    ]

    if len(lines_in_window) < 2:
        return False, "Clip window contains fewer than 2 transcript lines — too short to be a real exchange."

    speakers = {l.get("speaker") for l in lines_in_window if l.get("speaker")}

    if len(speakers) < 2:
        # --- DIARIZATION FALLBACK ---
        # Whisper-small doesn't diarize. If all lines are labelled the same speaker,
        # use Q/A pattern + silence gap detection instead of hard-failing every clip.
        if _detect_qa_turns(lines_in_window):
            logger.debug(
                f"Clip [{start}-{end}]: Single-speaker label but Q/A turn pattern detected — "
                f"treating as valid exchange (diarization fallback)."
            )
            # Don't penalise; fall through to the end-check
        else:
            return False, "Clip contains only one speaker — not a back-and-forth exchange."

    last_line_text = (lines_in_window[-1].get("text") or "").strip()
    if last_line_text.endswith("?"):
        return False, "Clip ends mid-question with no answer captured inside the window."

    return True, ""


# ============================================================
# SENTENCE-BOUNDARY REPAIR (category-agnostic)
# Fixes: "clip starts/ends mid-sentence" — this was the root cause behind both
# clip_14 and clip_15 shipping broken. Works purely off segment text/timing,
# so it applies identically whether the category is comedy, interview, story, etc.
# ============================================================

def _ends_clean(text: str) -> bool:
    """True if text ends on real terminal punctuation (a finished thought)."""
    text = (text or "").strip()
    if not text:
        return True
    return text.endswith(('.', '?', '!', '"', "'", '…', '...'))


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

    - Walks the start backward while the PREVIOUS segment doesn't end cleanly
      (i.e. the chosen start is actually mid-sentence, continuing an earlier thought).
    - Walks the end forward while the CURRENT segment doesn't end cleanly.
    - Both walks are capped by MAX_BOUNDARY_LOOKBACK_SEC / MAX_BOUNDARY_LOOKAHEAD_SEC
      so a single dangling clause can't drag a clip on for a full minute.
    - Also re-checks the category's min/max duration bounds after repair.

    Returns (repaired_start, repaired_end, is_clean). is_clean=False means we could
    not fully repair it within the lookback/lookahead/duration budget — the caller
    should treat this clip as unsalvageable rather than shipping it anyway.
    """
    if not segs:
        return start, end, True  # nothing to validate against; trust caller's bounds

    ordered = sorted(segs, key=lambda s: s.start_time)

    def _closest_idx(t: float, key: str) -> int:
        best_i, best_diff = 0, float("inf")
        for i, s in enumerate(ordered):
            diff = abs(getattr(s, key) - t)
            if diff < best_diff:
                best_diff = diff
                best_i = i
        return best_i

    start_idx = _closest_idx(start, "start_time")
    end_idx = _closest_idx(end, "end_time")
    if end_idx < start_idx:
        end_idx = start_idx

    # Walk start backward until the segment BEFORE it ends cleanly (or budget runs out)
    lookback_used = 0.0
    while start_idx > 0:
        prev_seg = ordered[start_idx - 1]
        if _ends_clean(prev_seg.text):
            break
        gap = ordered[start_idx].start_time - prev_seg.start_time
        if lookback_used + gap > MAX_BOUNDARY_LOOKBACK_SEC:
            break
        start_idx -= 1
        lookback_used += gap

    # Walk end forward until the current segment itself ends cleanly (or budget runs out)
    lookahead_used = 0.0
    while end_idx < len(ordered) - 1:
        cur_seg = ordered[end_idx]
        if _ends_clean(cur_seg.text):
            break
        next_seg = ordered[end_idx + 1]
        gap = next_seg.end_time - cur_seg.end_time
        if lookahead_used + gap > MAX_BOUNDARY_LOOKAHEAD_SEC:
            break
        end_idx += 1
        lookahead_used += gap

    repaired_start = ordered[start_idx].start_time
    repaired_end = ordered[end_idx].end_time
    duration = repaired_end - repaired_start

    starts_clean = (start_idx == 0) or _ends_clean(ordered[start_idx - 1].text)
    ends_clean = _ends_clean(ordered[end_idx].text)
    # Give some slack either side of the category's configured bounds since repair
    # can legitimately need to extend a bit further than the original guess.
    within_bounds = (min_duration_sec * 0.5) <= duration <= (max_duration_sec * 1.5)

    is_clean = starts_clean and ends_clean and within_bounds
    return repaired_start, repaired_end, is_clean


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
    strategy = raw_clip.get("clip_strategy", CATEGORY_CONFIG[category]["clip_strategy"])

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
                    diff = abs(s.start_time - start)
                    if diff < closest_start_diff:
                        closest_start_diff = diff
                        snapped_start = s.start_time

                # 2. End alignment (suspense cut / cliffhanger):
                # Search for the exact segment that contains the cliffhanger/hook text
                hook_clean = hook.lower().strip()
                hook_seg = None

                if hook_clean:
                    for s in segs:
                        s_text_clean = s.text.lower().strip()
                        if hook_clean in s_text_clean or s_text_clean in hook_clean:
                            if abs(s.end_time - end) < 20.0:
                                hook_seg = s
                                break

                    if not hook_seg:
                        hook_words = set(hook_clean.split())
                        max_overlap = 0
                        for s in segs:
                            s_words = set(s.text.lower().split())
                            overlap = len(hook_words.intersection(s_words))
                            if overlap > max_overlap and overlap >= 2:
                                if abs(s.end_time - end) < 20.0:
                                    max_overlap = overlap
                                    hook_seg = s

                if hook_seg:
                    snapped_end = hook_seg.end_time
                    logger.info(f"Precise cliffhanger snap: '{hook}' -> Segment [{hook_seg.start_time}s - {hook_seg.end_time}s]")
                else:
                    closest_end_diff = float("inf")
                    snapped_end = end
                    for s in segs:
                        diff = abs(s.end_time - end)
                        if diff < closest_end_diff:
                            closest_end_diff = diff
                            snapped_end = s.end_time

                start = snapped_start
                end = snapped_end

                # --- SENTENCE-BOUNDARY REPAIR (applies to every category) ---
                # The snapping above only finds the nearest segment edge, which is not
                # necessarily a complete sentence — this is what produced clips that
                # opened or closed mid-clause. This repair pass walks outward from the
                # snapped boundaries until they land on real sentence starts/ends.
                cfg_bounds = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG[DEFAULT_CATEGORY])
                repaired_start, repaired_end, boundaries_clean = repair_sentence_boundaries(
                    start, end, segs,
                    min_duration_sec=cfg_bounds.get("min_duration_sec", 15.0),
                    max_duration_sec=cfg_bounds.get("max_duration_sec", 60.0),
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
    cfg = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG[DEFAULT_CATEGORY])
    min_dur = cfg.get("min_duration_sec", 15.0)
    max_dur = cfg.get("max_duration_sec", 60.0)
    
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
    cfg = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG[DEFAULT_CATEGORY])
    w = cfg["weights"]

    curiosity = float(clip.get("curiosity_score", 5.0))
    hook = float(clip.get("hook_score", 5.0))
    audio_score = float(clip.get("audio_score", 5.0))
    heuristic_component = float(clip.get("heuristic_score", 5.0))

    llm_weight_sum = w.get("curiosity", 0.0) + w.get("hook", 0.0)
    audio_weight = w.get("audio_signal", 0.0)
    remaining_weight = max(0.0, 1.0 - llm_weight_sum - audio_weight)

    if llm_weight_sum > 0:
        llm_component = (
            (w["curiosity"] / llm_weight_sum) * curiosity +
            (w["hook"] / llm_weight_sum) * hook
        )
    else:
        llm_component = 5.0

    combined = (
        (llm_weight_sum * llm_component) +
        (audio_weight * audio_score) +
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

# ============================================================
# NEW 3-PASS PIPELINE IMPLEMENTATION
# ============================================================

SECTION_TYPE_WEIGHTS = {
    "story": {"curiosity": 0.20, "scroll_stop": 0.20, "part2": 0.25, "rewatch": 0.10, "standalone": 0.15, "cliffhanger": 0.10},
    "reveal": {"curiosity": 0.20, "scroll_stop": 0.15, "part2": 0.30, "rewatch": 0.10, "standalone": 0.15, "cliffhanger": 0.10},
    "debate": {"curiosity": 0.15, "scroll_stop": 0.20, "part2": 0.20, "rewatch": 0.15, "standalone": 0.20, "cliffhanger": 0.10},
    "lesson": {"curiosity": 0.15, "scroll_stop": 0.15, "part2": 0.15, "rewatch": 0.25, "standalone": 0.20, "cliffhanger": 0.10},
    "confession": {"curiosity": 0.15, "scroll_stop": 0.25, "part2": 0.20, "rewatch": 0.15, "standalone": 0.15, "cliffhanger": 0.10},
    "prediction": {"curiosity": 0.20, "scroll_stop": 0.20, "part2": 0.20, "rewatch": 0.10, "standalone": 0.20, "cliffhanger": 0.10},
    "default": {"curiosity": 0.20, "scroll_stop": 0.20, "part2": 0.20, "rewatch": 0.10, "standalone": 0.15, "cliffhanger": 0.15}
}

def _get_instructor_mode(provider_name: str):
    """
    Returns the correct instructor mode for each provider.
    - NVIDIA NIM and Groq: output JSON in the content field (Mode.JSON)
    - OpenRouter: supports tool-call protocol (Mode.TOOLS)
    Using Mode.TOOLS with NIM/Groq raises 'No tool calls found' since those
    models write JSON into the content block rather than emitting function-call objects.
    """
    import instructor
    if provider_name in ("nvidia_nim", "groq"):
        return instructor.Mode.JSON
    return instructor.Mode.TOOLS  # openrouter


def _run_pass1_via_instructor(provider: Dict[str, str], system_prompt: str, user_content: str) -> Optional[Dict[str, Any]]:
    import instructor
    from openai import OpenAI
    import httpx

    base_client = OpenAI(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
        default_headers=provider.get("extra_headers") or {},
        http_client=httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0)),
    )
    mode = _get_instructor_mode(provider["name"])
    client = instructor.from_openai(base_client, mode=mode)

    logger.info(f"[{provider['name']}] Calling Pass 1 [{provider['model']}] via Instructor (mode={mode.value})...")
    result = _call_with_retry(
        client.chat.completions.create,
        model=provider["model"],
        response_model=NarrativeMapResponse,
        max_retries=1,
        temperature=0.4,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        provider_name=provider["name"],
        pass_label="Pass 1 (Instructor)",
    )
    return result.model_dump()

def _run_pass1_via_legacy(provider: Dict[str, str], system_prompt: str, user_content: str) -> Optional[Dict[str, Any]]:
    from openai import OpenAI
    import httpx

    client = OpenAI(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
        default_headers=provider.get("extra_headers") or {},
        http_client=httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0)),
    )

    logger.info(f"[{provider['name']}] Calling Pass 1 [{provider['model']}] (legacy manual-parse)...")
    completion = _call_with_retry(
        client.chat.completions.create,
        model=provider["model"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.4,
        max_tokens=4096,
        stream=False,
        provider_name=provider["name"],
        pass_label="Pass 1 (legacy)",
    )
    content = completion.choices[0].message.content.strip()
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    content = content.strip()
    return json.loads(content)

def _run_pass2_via_instructor(provider: Dict[str, str], system_prompt: str, user_content: str) -> Optional[Dict[str, Any]]:
    import instructor
    from openai import OpenAI
    import httpx

    base_client = OpenAI(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
        default_headers=provider.get("extra_headers") or {},
        http_client=httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0)),
    )
    mode = _get_instructor_mode(provider["name"])
    client = instructor.from_openai(base_client, mode=mode)

    logger.info(f"[{provider['name']}] Calling Pass 2 [{provider['model']}] via Instructor (mode={mode.value})...")
    result = _call_with_retry(
        client.chat.completions.create,
        model=provider["model"],
        response_model=RetentionPeaksResponse,
        max_retries=1,
        temperature=0.4,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        provider_name=provider["name"],
        pass_label="Pass 2 (Instructor)",
    )
    return result.model_dump()

def _run_pass2_via_legacy(provider: Dict[str, str], system_prompt: str, user_content: str) -> Optional[Dict[str, Any]]:
    from openai import OpenAI
    import httpx

    client = OpenAI(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
        default_headers=provider.get("extra_headers") or {},
        http_client=httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0)),
    )

    logger.info(f"[{provider['name']}] Calling Pass 2 [{provider['model']}] (legacy manual-parse)...")
    completion = _call_with_retry(
        client.chat.completions.create,
        model=provider["model"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.4,
        max_tokens=4096,
        stream=False,
        provider_name=provider["name"],
        pass_label="Pass 2 (legacy)",
    )
    content = completion.choices[0].message.content.strip()
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    content = content.strip()
    return json.loads(content)

def narrative_map_transcript(full_transcript_lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # 5-minute chunks (300s) — keeps each LLM call well within the free model's
    # effective context budget when combined with the large system prompt.
    # Previously 600s; halving this cuts payload size in half per call.
    chunk_size_sec = 300.0

    # Max characters of transcript text sent per chunk.
    # ~5000 chars ≈ 1250 tokens; leaves plenty of room for the system prompt
    # (~800 tokens) and the JSON output within an 8k context window.
    CHUNK_TEXT_CHAR_LIMIT = 5000
    
    # Group lines by their start time
    chunks = []
    current_chunk_lines = []
    current_chunk_end = chunk_size_sec
    
    for line in full_transcript_lines:
        if line.get("start_time", 0.0) >= current_chunk_end:
            if current_chunk_lines:
                chunks.append(current_chunk_lines)
                current_chunk_lines = []
            while current_chunk_end <= line.get("start_time", 0.0):
                current_chunk_end += chunk_size_sec
        current_chunk_lines.append(line)
        
    if current_chunk_lines:
        chunks.append(current_chunk_lines)
        
    all_units = []
    unit_counter = 1
    
    for idx, chunk_lines in enumerate(chunks):
        logger.info(f"Mapping narrative units for chunk {idx+1}/{len(chunks)}...")
        raw_chunk_text = " ".join(
            f"[{line.get('speaker', 'Speaker')}] {line.get('text', '')}"
            for line in chunk_lines
        )
        # Hard-cap the text sent to the model so we never blow the context window.
        # If the chunk is longer than the limit, truncate at the last full word.
        if len(raw_chunk_text) > CHUNK_TEXT_CHAR_LIMIT:
            truncated = raw_chunk_text[:CHUNK_TEXT_CHAR_LIMIT]
            last_space = truncated.rfind(" ")
            chunk_text = truncated[:last_space] if last_space > 0 else truncated
            logger.info(
                f"Chunk {idx+1}: text truncated from {len(raw_chunk_text)} "
                f"to {len(chunk_text)} chars to stay within context budget."
            )
        else:
            chunk_text = raw_chunk_text
        
        providers = _get_llm_providers()
        if not providers:
            logger.warning("No LLM providers configured for Pass 1.")
            return []

        system_prompt = (
            "You are Marcus, a veteran documentary and podcast editor with 12 years of experience "
            "identifying the most compelling, short-formable moments in long-form content. "
            "You have cut thousands of viral clips for comedy specials, interview podcasts, "
            "spiritual/self-help shows, true crime, and debate-style content.\n\n"

            "YOUR JOB — NARRATIVE UNIT MAPPING:\n"
            "Scan the transcript and identify every distinct narrative unit that could potentially "
            "become a viral short-form clip. A narrative unit is a self-contained moment with its "
            "own internal arc — it has a beginning (why we care), a middle (tension or buildup), "
            "and an end (payoff, punchline, revelation, or cliffhanger).\n\n"

            "CLIPABILITY FILTER — Only flag units that pass ALL of these tests:\n"
            "✓ A cold stranger (who has never heard of this show) would understand it within 5 seconds\n"
            "✓ It contains at least ONE of: a surprising fact, an emotional confession, a punchline, "
            "a controversial claim, a personal story with stakes, or a jaw-dropping reveal\n"
            "✓ It does NOT start with pleasantries, sponsor reads, topic transitions, or off-topic filler\n"
            "✓ It does NOT require watching a previous clip to make sense\n"
            "SKIP any unit that fails even one of these tests — do not include boring transitions, "
            "generic Q&A openers, or content that is purely setup with no payoff.\n\n"

            "UNIT TYPE DEFINITIONS AND WHAT TO EXTRACT FOR EACH:\n\n"

            "story / personal_experience / failure / success:\n"
            "  → These are narrative arcs. A person tells something that HAPPENED to them.\n"
            "  → hook: The sentence that makes you think 'wait what happened?' (e.g. 'The night I lost everything...')\n"
            "  → tension_point: The exact sentence where stakes are highest or the outcome is most uncertain\n"
            "  → climax: The moment of highest drama or decision\n"
            "  → reveal: What actually happened / the answer / the outcome\n"
            "  → For cliffhanger clips: cut AFTER tension_point, BEFORE reveal\n\n"

            "analogy / lesson / mindset_shift:\n"
            "  → These are insight moments. Someone explains something in a way you've never heard before.\n"
            "  → hook: The counterintuitive or surprising premise (e.g. 'Most people think X — they're completely wrong')\n"
            "  → climax: The core insight or reframe that changes how you see things\n"
            "  → tension_point: The moment before the insight lands — maximum curiosity\n"
            "  → A good lesson clip ends ON the insight, not after it\n\n"

            "debate / argument / question_answer:\n"
            "  → These are exchange moments between two voices (or implied challenge/response).\n"
            "  → hook: The question, challenge, or provocative claim that opens the exchange\n"
            "  → tension_point: The moment the challenge lands or the most direct confrontation\n"
            "  → climax: The most surprising or forceful response/rebuttal\n"
            "  → reveal: The resolution or final position — only include if resolved within 60 seconds\n"
            "  → CRITICAL: Both the question AND the answer must fit in a 60-second window\n\n"

            "twist / reveal / prediction:\n"
            "  → These are 'wait, WHAT?' moments. Something unexpected is dropped.\n"
            "  → hook: The setup or expectation being established\n"
            "  → tension_point: The moment right before the reveal — maximum suspense\n"
            "  → climax / reveal: The unexpected fact, outcome, or twist itself\n"
            "  → Best as cliffhangers: cut right at the moment of maximum suspense\n\n"

            "confession:\n"
            "  → Someone admits something vulnerable, embarrassing, or deeply personal.\n"
            "  → hook: The admission or vulnerable opening line\n"
            "  → tension_point: The deepest point of vulnerability — where the speaker is most exposed\n"
            "  → climax: The realization, acceptance, or lesson that came from it\n"
            "  → Great confession clips end on a line a viewer would screenshot\n\n"

            "CRITICAL FIELD RULES:\n"
            "- start_sentence and end_sentence MUST be exact verbatim sentences from the transcript — "
            "copy them character-for-character so we can locate them with string matching.\n"
            "- Do NOT invent or paraphrase. If you cannot find an exact match, use the closest real sentence.\n"
            "- summary: One sentence describing what this unit is about and why it's interesting.\n"
            "- If the transcript contains sponsor reads, intros/outros, or pure small talk "
            "('How are you?', 'Thanks for having me', 'Welcome back'), skip them entirely.\n\n"

            "Return ONLY the requested JSON format. No markdown, no explanation."
        )
        user_content = f"Here is the transcript segment to analyze:\n\n{chunk_text}"

        chunk_units = []
        for provider in providers:
            try:
                if _INSTRUCTOR_AVAILABLE and _PYDANTIC_AVAILABLE:
                    data = _run_pass1_via_instructor(provider, system_prompt, user_content)
                else:
                    data = _run_pass1_via_legacy(provider, system_prompt, user_content)
                if data and "units" in data:
                    chunk_units = data["units"]
                    break
            except Exception as e:
                logger.error(f"[{provider['name']}] Pass 1 failed on chunk {idx+1}: {e}")
                
        for unit in chunk_units:
            unit["unit_id"] = unit_counter
            unit_counter += 1
            all_units.append(unit)

        # Brief pause between chunks to avoid slamming the fallback provider
        # (OpenRouter free tier has very tight per-minute token limits).
        if idx < len(chunks) - 1:
            time.sleep(2.0)

    return all_units

def select_retention_peaks(narrative_units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    providers = _get_llm_providers()
    if not providers or not narrative_units:
        logger.warning("No LLM providers configured for Pass 2, or narrative units empty.")
        return []

    # Build a section-type summary to inject genre-aware cutting rules into Pass 2
    section_types_present = list({u.get("section_type", "story") for u in narrative_units})
    section_type_cutting_rules = {
        "story": (
            "STORY/PERSONAL EXPERIENCE units → Use CLIFFHANGER strategy. "
            "Cut AFTER the tension_point — right when the stakes are highest and the outcome is most uncertain. "
            "NEVER include the resolution or reveal inside the clip. "
            "The viewer should be left thinking 'but what happened next?!'"
        ),
        "personal_experience": (
            "PERSONAL EXPERIENCE units → Use CLIFFHANGER strategy. "
            "Cut right after the moment where the person's situation is most uncertain or most painful. "
            "Do not resolve the story — make the viewer desperate for the ending."
        ),
        "failure": (
            "FAILURE units → Use CLIFFHANGER strategy. "
            "Cut at the lowest point — the moment of maximum failure, humiliation, or loss. "
            "Do NOT include the recovery or lesson. Leave the viewer in the pit."
        ),
        "success": (
            "SUCCESS units → Use PAYOFF strategy. "
            "Include both the struggle AND the breakthrough. "
            "End on the exact sentence that delivers the emotional payoff — the win, the realization, the transformation."
        ),
        "debate": (
            "DEBATE/ARGUMENT units → Use PAYOFF strategy. "
            "BOTH the challenge AND the response must be inside the clip. "
            "Cut after the most surprising or decisive rebuttal. "
            "NEVER cut mid-challenge with no answer — that is a broken clip."
        ),
        "question_answer": (
            "Q&A units → Use PAYOFF strategy. "
            "The question AND the full answer must both be inside the clip. "
            "Cut after the answer lands — ideally after the most surprising part of the answer. "
            "A clip that ends on a question with no answer is INVALID."
        ),
        "lesson": (
            "LESSON/MINDSET SHIFT units → Use PAYOFF strategy. "
            "End on the exact insight sentence — the 'aha moment' that reframes everything. "
            "This sentence should be quotable, universal, and screenshot-worthy. "
            "Do not end BEFORE the insight lands."
        ),
        "analogy": (
            "ANALOGY units → Use PAYOFF strategy. "
            "End on the moment the analogy snaps into place — when the listener goes 'oh wow.' "
            "The analogy must be fully explained before the cut."
        ),
        "mindset_shift": (
            "MINDSET SHIFT units → Use PAYOFF strategy. "
            "End on the reframe — the sentence that flips the viewer's assumption. "
            "It must be self-contained: a viewer with no prior context must fully understand the shift."
        ),
        "reveal": (
            "REVEAL units → Use CLIFFHANGER strategy. "
            "Cut right BEFORE the reveal is fully explained — at maximum suspense. "
            "Leave the viewer screaming internally. The moment of maximum 'wait WHAT?' is the cut point."
        ),
        "twist": (
            "TWIST units → Use CLIFFHANGER strategy. "
            "Cut immediately AFTER the twist is dropped but BEFORE any explanation, reaction, or processing. "
            "The raw shock is the hook — don't dilute it with aftermath."
        ),
        "confession": (
            "CONFESSION units → Use PAYOFF strategy. "
            "End on the most vulnerable, quotable line — the sentence a viewer would screenshot and share. "
            "Must include both the admission AND the emotional resolution (acceptance/lesson/realization)."
        ),
        "prediction": (
            "PREDICTION units → Use PAYOFF strategy. "
            "End right after the prediction is stated confidently. "
            "Include enough context so the viewer understands what's being predicted and why it's bold."
        ),
        "argument": (
            "ARGUMENT units → Use PAYOFF strategy (or CLIFFHANGER if unresolved). "
            "Include the full argument — setup, the provocative claim, and the strongest supporting point. "
            "End at maximum conviction — before any walk-back or qualification."
        ),
        "quote": (
            "QUOTE units → Use PAYOFF strategy. "
            "The quote must be fully delivered. End right after the quote lands, with 1-2 seconds of reaction "
            "if available. Never cut mid-quote."
        ),
    }

    cutting_rules_for_present_types = "\n".join(
        f"  • {section_type_cutting_rules.get(st, 'Use PAYOFF strategy. End on the strongest, most self-contained moment.')}"
        for st in section_types_present
    )

    system_prompt = (
        "You are Dr. Reena Shah — a behavioral psychologist AND viral content editor. "
        "You have a PhD in attention and decision-making, and you've spent the last decade applying "
        "that knowledge to short-form video. You know EXACTLY what happens in a human brain in the "
        "3 seconds before someone swipes away, and you know how to engineer a clip that prevents it.\n\n"

        "YOUR JOB — RETENTION PEAK SELECTION:\n"
        "For each narrative unit below, determine the single best cut point that maximizes viewer "
        "retention and engagement on TikTok and Instagram Reels. Your audience is COLD — they have "
        "never seen this show, never heard of the speaker, and have zero patience for slow intros.\n\n"

        "PLATFORM PSYCHOLOGY (internalize this before judging anything):\n"
        "- First 3 seconds: The viewer decides whether to stay or swipe. The opening must be viscerally "
        "compelling — shocking, funny, vulnerable, or counterintuitive.\n"
        "- Seconds 3-15: The viewer is on probation. Every sentence must justify staying. Boring = swipe.\n"
        "- Seconds 15-60: If they're still watching, they're invested. But they'll still bail if the "
        "payoff doesn't come or if the clip drags.\n"
        "- Comments are gold: clips that generate 'Part 2?', 'Wait WHAT?', or heated debate comments "
        "get boosted by the algorithm. Engineer for comments.\n\n"

        "GENRE-SPECIFIC CUTTING RULES (apply these based on each unit's section_type):\n"
        f"{cutting_rules_for_present_types}\n\n"

        "GENERAL ANTI-PATTERNS — these are the most common clipping mistakes. Avoid them:\n"
        "✗ Cutting right BEFORE a punchline lands (comedy killer — the viewer gets no payoff)\n"
        "✗ Cutting mid-sentence or mid-thought (broken clips feel amateurish and kill trust)\n"
        "✗ Ending on a question with no answer inside the clip (viewers feel cheated)\n"
        "✗ Starting with 'Yeah', 'So', 'Like I was saying', 'As I mentioned' (no hook)\n"
        "✗ Including the host intro or show pleasantries ('Welcome back!', 'How are you?')\n"
        "✗ Selecting a moment that only makes sense if you've watched the full episode\n"
        "✗ Choosing 'safe' clips that are mildly interesting but not emotionally activating\n\n"

        "SCORING RUBRIC — use exact definitions, not gut feel:\n"
        "curiosity_score (0-100): How badly does a cold viewer NEED to know what happens next?\n"
        "  100 = They would comment 'Part 2???' immediately. Cannot stop thinking about it.\n"
        "   70 = Genuinely wants to know more. Would click a 'Part 2' link.\n"
        "   40 = Mildly curious. Might remember it, might not.\n"
        "    0 = Zero unresolved tension. No reason to seek more information.\n"
        "scroll_stop_score (0-100): How hard does the FIRST LINE of this clip hit a cold scroller?\n"
        "  100 = First sentence is so shocking/funny/vulnerable that swiping is physically impossible.\n"
        "   70 = Strong opener. Most viewers pause.\n"
        "   40 = Decent opener. Some viewers pause, many swipe.\n"
        "    0 = Forgettable opener. Almost everyone swipes immediately.\n"
        "part2_score (0-100): How likely is a viewer to comment 'Part 2?' or actively seek the full video?\n"
        "  100 = They WILL search for the rest of this conversation. Guaranteed.\n"
        "   70 = High chance they seek more. 60%+ would look for Part 2.\n"
        "   40 = Some interest in more, but not urgent.\n"
        "    0 = The clip is complete. No Part 2 needed or desired.\n"
        "rewatch_score (0-100): How likely is a viewer to watch this clip a second (or third) time?\n"
        "  100 = They save it and send it to 3 people. They watch it again immediately.\n"
        "   70 = They watch it twice. They might save it.\n"
        "   40 = One watch is enough. Forgettable after viewing.\n"
        "    0 = Would not rewatch under any circumstances.\n"
        "standalone_score (0-100): Can a person with ZERO context understand and enjoy this clip?\n"
        "  100 = Perfectly self-contained. No prior knowledge needed. Works for anyone, anywhere.\n"
        "   70 = Mostly self-contained. One or two things might be unclear but the core works.\n"
        "   40 = Requires some context. A newcomer might be confused.\n"
        "    0 = Completely incomprehensible without the full episode.\n"
        "cliffhanger_score (0-100): How effectively does the clip END at a moment of unresolved tension?\n"
        "  100 = Ends at the exact peak of tension. The next sentence would answer everything — we cut before it.\n"
        "   70 = Ends at a good tension point. Some unresolved curiosity.\n"
        "   40 = Mildly cliffhanger-ish. Ends a bit too early or too late.\n"
        "    0 = Completely resolved. No tension remaining at the end.\n\n"

        "OUTPUT REQUIREMENTS:\n"
        "- cut_after_sentence: EXACT verbatim sentence from the transcript. Copy character-for-character.\n"
        "- cut_rationale: 2-3 sentences explaining the psychological mechanism — WHY does cutting here "
        "maximize retention? What specific emotion or cognitive state does it leave the viewer in?\n"
        "- why_viewers_keep_watching: One sharp sentence from a viewer psychology POV — "
        "e.g. 'The viewer is left in a state of cognitive dissonance — their belief about X was just "
        "challenged and they need resolution.'\n"
        "- suggested_title: A hooky, clickable title (8 words max) that would stop someone mid-scroll. "
        "Use numbers, provocative adjectives, or open loops. e.g. 'He Lost Everything In One Night' or "
        "'The Truth About Karma Nobody Tells You'\n"
        "- estimated_duration_sec: Realistic estimate in seconds (15-60 range).\n\n"

        "Return ONLY the requested JSON format. No markdown, no extra commentary."
    )
    user_content = f"Here are the narrative units to evaluate:\n\n{json.dumps(narrative_units, indent=2)}"

    # ── BATCH PROCESSING ──────────────────────────────────────────────────────
    # Sending ALL units in one call can produce very large payloads on long
    # podcasts (30+ units = 10k+ tokens of JSON). We batch into groups of 6
    # units so each call stays within budget. Results are merged afterward.
    PASS2_BATCH_SIZE = 6

    # Slim each unit down to the fields Pass 2 actually needs — drop the
    # raw transcript excerpt fields that were only needed for Pass 1 matching.
    PASS2_KEEP_FIELDS = {
        "unit_id", "section_type", "summary", "hook",
        "buildup", "climax", "reveal", "resolution", "tension_point",
    }

    def _slim_unit(u: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in u.items() if k in PASS2_KEEP_FIELDS}

    unit_batches = [
        [_slim_unit(u) for u in narrative_units[i: i + PASS2_BATCH_SIZE]]
        for i in range(0, len(narrative_units), PASS2_BATCH_SIZE)
    ]
    logger.info(
        f"Pass 2: {len(narrative_units)} units split into "
        f"{len(unit_batches)} batch(es) of up to {PASS2_BATCH_SIZE}."
    )

    all_peaks: List[Dict[str, Any]] = []

    for batch_idx, batch in enumerate(unit_batches):
        logger.info(f"Pass 2: processing batch {batch_idx+1}/{len(unit_batches)}...")
        batch_user_content = (
            f"Here are the narrative units to evaluate (batch "
            f"{batch_idx+1}/{len(unit_batches)}):\n\n"
            f"{json.dumps(batch, indent=2)}"
        )
        batch_peaks: List[Dict[str, Any]] = []
        for provider in providers:
            try:
                if _INSTRUCTOR_AVAILABLE and _PYDANTIC_AVAILABLE:
                    data = _run_pass2_via_instructor(provider, system_prompt, batch_user_content)
                else:
                    data = _run_pass2_via_legacy(provider, system_prompt, batch_user_content)
                if data and "peaks" in data:
                    batch_peaks = data["peaks"]
                    break
            except Exception as e:
                logger.error(f"[{provider['name']}] Pass 2 batch {batch_idx+1} failed: {e}")

        if batch_peaks:
            all_peaks.extend(batch_peaks)
        else:
            logger.warning(f"Pass 2 batch {batch_idx+1} returned no peaks — skipping.")

        # Small courtesy delay between batches to avoid hammering the free tier
        if batch_idx < len(unit_batches) - 1:
            time.sleep(1.5)

    if not all_peaks:
        logger.error("All Pass 2 batches returned no peaks.")
    else:
        logger.info(f"Pass 2 complete. Total peaks collected: {len(all_peaks)}.")

    return all_peaks

def find_closest_segment_idx(sentence: str, segments: List[Dict[str, Any]], search_from_idx: int = 0) -> int:
    """
    Finds the index of the segment that most likely contains or matches the given sentence.
    """
    clean_sentence = re.sub(r"[^\w\s]", "", sentence.lower()).strip()
    if not clean_sentence:
        return search_from_idx

    best_idx = search_from_idx
    best_score = 0.0

    for i in range(search_from_idx, len(segments)):
        seg_text = re.sub(r"[^\w\s]", "", segments[i].get("text", "").lower()).strip()
        if not seg_text:
            continue
        
        if clean_sentence in seg_text or seg_text in clean_sentence:
            return i
            
        words_s = set(clean_sentence.split())
        words_seg = set(seg_text.split())
        overlap = len(words_s & words_seg)
        union = len(words_s | words_seg)
        score = overlap / union if union > 0 else 0.0
        
        if score > best_score:
            best_score = score
            best_idx = i

    return best_idx

def compute_narrative_virality_score(clip: Dict[str, Any], section_type: str) -> float:
    weights = SECTION_TYPE_WEIGHTS.get(section_type, SECTION_TYPE_WEIGHTS["default"])
    
    curiosity = float(clip.get("curiosity_score", 50.0))
    scroll_stop = float(clip.get("scroll_stop_score", 50.0))
    part2 = float(clip.get("part2_score", 50.0))
    rewatch = float(clip.get("rewatch_score", 50.0))
    standalone = float(clip.get("standalone_score", 50.0))
    cliffhanger = float(clip.get("cliffhanger_score", 50.0))
    
    base_score = (
        (curiosity * weights["curiosity"]) +
        (scroll_stop * weights["scroll_stop"]) +
        (part2 * weights["part2"]) +
        (rewatch * weights["rewatch"]) +
        (standalone * weights["standalone"]) +
        (cliffhanger * weights["cliffhanger"])
    ) / 10.0
    
    audio_score = float(clip.get("audio_score", 5.0))
    combined = (base_score * 0.8) + (audio_score * 0.2)
    
    low_substance_ratio = clip.get("low_substance_ratio", 0.0)
    if low_substance_ratio > 0.0:
        combined *= (1.0 - low_substance_ratio)
        
    return round(combined, 2)


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
    Ranks candidates using the 3-Pass Narrative Intelligence Pipeline:
    Pass 1: Map narrative units from the entire transcript.
    Pass 2: Select retention peaks ('Part 2' cut points) for narrative units.
    Pass 3: Resolve exact timestamps, apply boundary repair, and score candidates.
    """
    if not transcript_lines:
        logger.warning("No transcript lines available for Pass 1 narrative mapping.")
        return []

    # 1. Combine segments to get full transcript text
    full_transcript = " ".join(f"[{line.get('speaker', 'Speaker')}] {line.get('text', '')}" for line in transcript_lines)
    
    # Pass 1: Narrative Map
    logger.info("Executing Pass 1: Narrative Mapping on transcript segments...")
    units = narrative_map_transcript(transcript_lines)
    if not units:
        logger.warning("Pass 1 returned no narrative units.")
        return []
    logger.info(f"Pass 1 complete. Identified {len(units)} narrative units.")

    # Pass 2: Retention Peaks
    logger.info("Executing Pass 2: Selecting retention peaks for units...")
    peaks = select_retention_peaks(units)
    if not peaks:
        logger.warning("Pass 2 returned no retention peaks.")
        return []
    logger.info(f"Pass 2 complete. Selected {len(peaks)} retention peaks.")

    # Pass 3: Timestamp Resolution, Sentence-Boundary Snapping, and Scoring
    logger.info("Executing Pass 3: Resolving timestamps, boundary repair, and scoring...")
    final_clips = []
    
    # Map units by unit_id for quick lookup
    units_by_id = {u["unit_id"]: u for u in units}
    
    for peak in peaks:
        try:
            unit_id = peak["unit_id"]
            if unit_id not in units_by_id:
                continue
            unit = units_by_id[unit_id]
            
            # Fuzzy match start sentence and cut sentence to segment index
            start_idx = find_closest_segment_idx(unit["start_sentence"], transcript_lines, 0)
            cut_idx = find_closest_segment_idx(peak["cut_after_sentence"], transcript_lines, start_idx)
            
            start_time = transcript_lines[start_idx]["start_time"]
            end_time = transcript_lines[cut_idx]["end_time"]
            
            # Slide start time forward if duration is too long
            max_duration = 60.0
            if (end_time - start_time) > max_duration:
                while start_idx < cut_idx and (end_time - transcript_lines[start_idx]["start_time"]) > max_duration:
                    start_idx += 1
                start_time = transcript_lines[start_idx]["start_time"]
                
            # Align boundaries and perform sentence-boundary repair
            # Using database segments for precise boundary snapping
            segs_list = []
            if video_id != "unknown":
                from app.db import SessionLocal
                from app import models
                db = SessionLocal()
                try:
                    segs_list = db.query(models.TranscriptSegment).filter(
                        models.TranscriptSegment.video_id == int(video_id)
                    ).order_by(models.TranscriptSegment.start_time).all()
                except Exception as db_err:
                    logger.error(f"Error fetching TranscriptSegments from DB: {db_err}")
                finally:
                    db.close()
            
            min_dur, max_dur = 15.0, 60.0
            
            if segs_list:
                repaired_start, repaired_end, boundaries_clean = repair_sentence_boundaries(
                    start_time, end_time, segs_list,
                    min_duration_sec=min_dur,
                    max_duration_sec=max_dur
                )
                start_time, end_time = repaired_start, repaired_end
            else:
                boundaries_clean = True
                
            duration = round(end_time - start_time, 2)
            
            # Excerpt text
            excerpt_list = [
                s.get("text", "") for s in transcript_lines 
                if s.get("start_time", 0.0) >= start_time and s.get("end_time", 0.0) <= end_time
            ]
            full_excerpt = " ".join(excerpt_list)
            excerpt = full_excerpt[:220]
            
            # Safety checks
            is_safe, safety_reason = check_content_safety(full_excerpt)
            if not is_safe:
                logger.warning(f"Clip [{start_time}-{end_time}] pulled: safety violation {safety_reason}")
                continue
                
            # Audio analysis
            audio_signals = detect_audio_events(audio_path, start_time, end_time) if audio_path else {
                "energy_spike_ratio": 1.0, "pitch_variance": 0.0,
                "has_laughter": False, "has_applause": False, "audio_score": 5.0,
            }
            
            # Low substance check
            low_substance_ratio = compute_low_substance_ratio(full_excerpt)
            auto_render_blocked = False
            needs_review = False
            flagged_reason = peak["cut_rationale"]
            
            if not boundaries_clean:
                needs_review = True
                auto_render_blocked = True
                flagged_reason += " [BLOCKED: Unrepairable sentence boundaries]"
                
            if low_substance_ratio > LOW_SUBSTANCE_REVIEW_THRESHOLD:
                needs_review = True
                flagged_reason += f" [FLAGGED: Low-substance filler {low_substance_ratio:.0%}]"
                if low_substance_ratio > LOW_SUBSTANCE_HARD_BLOCK_THRESHOLD:
                    auto_render_blocked = True
            
            # Construct candidate clip dict
            clip_dict = {
                "start_time": round(start_time, 2),
                "end_time": round(end_time, 2),
                "duration_sec": duration,
                "category": unit["section_type"],
                "clip_strategy": "cliffhanger" if "cliffhanger" in peak["cut_rationale"].lower() else "payoff",
                "curiosity_score": peak["curiosity_score"],
                "hook_score": peak["scroll_stop_score"],
                "emotion_score": peak["part2_score"],
                "surprise_score": peak["rewatch_score"],
                "reaction_score": peak["standalone_score"],
                "context_completeness": peak["cliffhanger_score"],
                # New fields
                "scroll_stop_score": peak["scroll_stop_score"],
                "part2_score": peak["part2_score"],
                "rewatch_score": peak["rewatch_score"],
                "standalone_score": peak["standalone_score"],
                "cliffhanger_score": peak["cliffhanger_score"],
                "section_type": unit["section_type"],
                "narrative_summary": unit["summary"],
                "cut_rationale": peak["cut_rationale"],
                
                "audio_score": audio_signals["audio_score"],
                "has_laughter": audio_signals["has_laughter"],
                "has_applause": audio_signals["has_applause"],
                "energy_spike_ratio": audio_signals["energy_spike_ratio"],
                
                "why_viewers_keep_watching": peak["why_viewers_keep_watching"],
                "reason": flagged_reason,
                "hook_line": peak["cut_after_sentence"],
                "transcript_excerpt": excerpt,
                "suggested_title": peak["suggested_title"],
                "suggested_caption": excerpt[:100],
                "needs_manual_review": needs_review,
                "auto_render_blocked": auto_render_blocked,
                "best_aspect_ratio": "9:16",
                "low_substance_ratio": low_substance_ratio,
            }
            
            # Calculate combined virality score using new weighted formula
            virality = compute_narrative_virality_score(clip_dict, unit["section_type"])
            clip_dict["virality_score"] = virality
            
            final_clips.append(clip_dict)
        except Exception as ex:
            logger.error(f"Error resolving peak candidate: {ex}")
            
    # Sort by virality score descending
    final_clips.sort(key=lambda x: x["virality_score"], reverse=True)
    
    # Deduplicate timeline (max 20% overlap)
    final_ranked_clips = deduplicate_timeline(final_clips, max_overlap_ratio=0.20)
    
    # Assign ranks
    for idx, c in enumerate(final_ranked_clips):
        c["rank"] = idx + 1
        
    return final_ranked_clips[:limit]