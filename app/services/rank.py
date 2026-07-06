import logging
import json
import re
from typing import List, Dict, Any, Optional, Tuple
from app.config import settings
from app.services.audio_signal import detect_audio_events

logger = logging.getLogger(__name__)

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

    class ClipCandidateModel(BaseModel):
        start_time: float
        end_time: float
        clip_strategy: Literal["cliffhanger", "payoff"]
        hook_line: str
        reason: str = Field(max_length=200)
        curiosity_score: int = Field(ge=0, le=10)
        hook_score: int = Field(ge=0, le=10)

    class ClipResponseModel(BaseModel):
        video_id: str
        clips: List[ClipCandidateModel]

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
# NVIDIA NIM and Groq both expose an OpenAI-compatible chat.completions API,
# so the exact same prompts, Instructor/Pydantic guaranteed-schema path, and
# legacy manual-parse path work unchanged for either — only base_url, api_key,
# and model name differ. This means the fallback is purely an infra concern:
# it applies identically no matter which CATEGORY_CONFIG entry is active.

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# A solid general-purpose default if GROQ_MODEL isn't set in settings/.env.
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


def _get_llm_providers() -> List[Dict[str, str]]:
    """
    Returns an ordered list of usable LLM provider configs: NVIDIA NIM first
    (matches existing default/behavior), Groq second as a fallback. A provider
    is only included if its API key is actually configured. If NVIDIA is
    missing/misconfigured, Groq becomes the primary automatically — no
    category-specific logic involved.
    """
    providers: List[Dict[str, str]] = []

    nvidia_key = getattr(settings, "NVIDIA_API_KEY", None)
    if nvidia_key and nvidia_key not in ["your_nvidia_api_key_here", ""]:
        providers.append({
            "name": "nvidia_nim",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key": nvidia_key,
            "model": getattr(settings, "NVIDIA_MODEL", "nvidia/nemotron-3-super-120b-a12b"),
            # NVIDIA's NIM Nemotron endpoint accepts this reasoning-suppression flag;
            # Groq's endpoint does not, so this is only applied per-provider below.
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        })

    groq_key = getattr(settings, "GROQ_API_KEY", None)
    if groq_key and groq_key not in ["your_groq_api_key_here", ""]:
        providers.append({
            "name": "groq",
            "base_url": GROQ_BASE_URL,
            "api_key": groq_key,
            "model": getattr(settings, "GROQ_MODEL", DEFAULT_GROQ_MODEL),
            "extra_body": {},
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
    """Builds the lean Nemotron system prompt for a specific content category."""
    cfg = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG[DEFAULT_CATEGORY])

    return (
        "You are a short-form podcast clip selector.\n\n"
        "Goal: Find the most viral clip candidates from a video podcast transcript.\n\n"
        f"Content category: {category} — {cfg['description']}\n\n"
        "CLIP STRATEGY (follow exactly):\n"
        f"{cfg['prompt_rules']}\n\n"
        "Rules:\n"
        "- Only select moments that work as standalone clips.\n"
        "- Prefer clips with a strong opening within the first 3 seconds.\n"
        "- Output multiple ranked candidates.\n"
        "- curiosity_score and hook_score are 0-10 integers.\n"
        "- reason must be ONE short sentence, max 15 words.\n"
        "- hook_line must be the exact line where the clip should start or end (per the strategy above).\n\n"
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

def _score_via_instructor(provider: Dict[str, str], system_prompt: str, user_content: str) -> Optional[Dict[str, Any]]:
    """Instructor + Pydantic guaranteed-schema attempt against one provider. Raises on failure."""
    import instructor
    from openai import OpenAI
    import httpx

    base_client = OpenAI(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
        http_client=httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)),
    )
    client = instructor.from_openai(base_client, mode=instructor.Mode.TOOLS)

    logger.info(f"[{provider['name']}] Calling [{provider['model']}] via Instructor (guaranteed schema)...")

    result: ClipResponseModel = client.chat.completions.create(
        model=provider["model"],
        response_model=ClipResponseModel,
        max_retries=2,
        temperature=0.6,
        top_p=0.95,
        max_tokens=7000,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )

    data = result.model_dump()
    if not data.get("clips"):
        logger.warning(f"[{provider['name']}] Instructor returned zero clips.")
        return None

    logger.info(f"[{provider['name']}] Successfully parsed guaranteed-schema response via Instructor.")
    return data


def _score_via_legacy_parse(provider: Dict[str, str], system_prompt: str, user_content: str) -> Optional[Dict[str, Any]]:
    """Manual streaming + string-parse attempt against one provider. Raises on failure."""
    from openai import OpenAI
    import httpx
    import re

    client = OpenAI(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
        http_client=httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)),
    )

    logger.info(f"[{provider['name']}] Calling [{provider['model']}] (legacy manual-parse path)...")
    completion = client.chat.completions.create(
        model=provider["model"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.6,
        top_p=0.95,
        max_tokens=5000,
        extra_body=provider.get("extra_body") or {},
        stream=True,
    )

    full_content = ""
    for chunk in completion:
        if not chunk.choices:
            continue
        if chunk.choices[0].delta.content is not None:
            full_content += chunk.choices[0].delta.content

    content = full_content.strip()
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
        return None

    if not data.get("clips"):
        logger.warning(f"[{provider['name']}] returned JSON with no clips.")
        return None

    logger.info(f"[{provider['name']}] Successfully parsed batch response (legacy path).")
    return data


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
                "clip_strategy": CATEGORY_CONFIG.get(category, CATEGORY_CONFIG[DEFAULT_CATEGORY])["clip_strategy"],
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

    system_prompt = build_system_prompt(category)

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
        # --- PRIMARY PATH for this provider: Instructor + Pydantic ---
        if _INSTRUCTOR_AVAILABLE and _PYDANTIC_AVAILABLE:
            try:
                data = _score_via_instructor(provider, system_prompt, user_content)
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

    logger.info(f"Ranking {len(candidates)} candidates for category='{category}'. Stage 1 heuristic pre-filter...")

    # Stage 1: Heuristic scoring (supporting signal only)
    scored_candidates = []
    for cand in candidates:
        h_score = heuristic_pre_filter_score(cand["text"])
        scored_candidates.append({**cand, "heuristic_score": h_score})

    scored_candidates.sort(key=lambda x: x["heuristic_score"], reverse=True)

    # Deduplicate by overlap (max 20%)
    top_candidates = []
    for cand in scored_candidates:
        overlap_found = False
        s1, e1 = cand["start_time"], cand["end_time"]
        dur1 = e1 - s1
        for existing in top_candidates:
            s2, e2 = existing["start_time"], existing["end_time"]
            dur2 = e2 - s2
            intersection = max(0.0, min(e1, e2) - max(s1, s2))
            if intersection > 0.0:
                overlap_ratio = intersection / min(dur1, dur2)
                if overlap_ratio > 0.2:
                    overlap_found = True
                    break
        if not overlap_found:
            top_candidates.append(cand)
            if len(top_candidates) >= STAGE1_CANDIDATE_POOL_SIZE:
                break

    logger.info(f"Stage 1 complete. Batch scoring {len(top_candidates)} candidates using Llama Nemotron...")

    # Stage 2: Lean batch LLM scoring (Instructor-guaranteed)
    nim_result = call_nvidia_nim_batch_scoring(top_candidates, video_id, category)
    raw_clips = nim_result.get("clips", [])
    final_clips = []

    for clip in raw_clips:
        try:
            clip_start = float(clip.get("start_time", 0.0))
            match = next(
                (c for c in top_candidates if c["start_time"] <= clip_start <= c["end_time"]),
                None
            )
            clip["heuristic_score"] = match["heuristic_score"] if match else 5.0

            enriched = enrich_clip(clip, top_candidates, category, video_id, audio_path=audio_path)

            # --- SPEAKER-TURN / EXCHANGE VALIDATION ---
            # Fixes: "LLM picked a clip that's just a question, no answer"
            if category == "interview_discussion":
                is_valid, invalid_reason = validate_exchange_completeness(
                    enriched["start_time"], enriched["end_time"], transcript_lines
                )
                if not is_valid:
                    logger.warning(f"Clip [{enriched['start_time']}-{enriched['end_time']}] failed exchange validation: {invalid_reason}")
                    enriched["needs_manual_review"] = True
                    enriched["reason"] = (enriched.get("reason", "") + f" [FLAGGED: {invalid_reason}]").strip()
                    virality = compute_virality_score(enriched, category) * 0.5  # heavy penalty, not a hard drop
                else:
                    virality = compute_virality_score(enriched, category)
            else:
                virality = compute_virality_score(enriched, category)

            enriched["virality_score"] = round(virality, 2)
            final_clips.append(enriched)
        except Exception as e:
            logger.error(f"Error mapping candidate output clip: {e}")

    # --- HARD SAFETY / SANITY GATES BEFORE RANKING ---
    safe_and_valid_clips = []
    for clip in final_clips:
        start_t = clip["start_time"]
        end_t = clip["end_time"]
        dur = clip["duration_sec"]
        
        # 1. Content Safety Check
        is_safe, safety_reason = check_content_safety(clip["transcript_excerpt"])
        if not is_safe:
            logger.warning(
                f"Clip [{start_t:.2f}s - {end_t:.2f}s] PULLED from ranking competition due to "
                f"content safety violation: {safety_reason}"
            )
            continue

        # 1b. Sentence-Boundary Gate — applies to every category equally.
        # This is the fix for the bug that let clip_14 and clip_15 render despite
        # cutting off mid-sentence: needs_manual_review used to be advisory-only.
        # Now a clip we couldn't repair to clean boundaries never reaches render.
        if clip.get("auto_render_blocked"):
            logger.warning(
                f"Clip [{start_t:.2f}s - {end_t:.2f}s] PULLED from ranking competition — "
                f"unrepairable mid-sentence boundary: {clip.get('reason')}"
            )
            continue

        # 2. Absurd Duration Check
        cfg = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG[DEFAULT_CATEGORY])
        min_allowed = cfg.get("min_duration_sec", 15.0)
        max_allowed = cfg.get("max_duration_sec", 60.0)
        
        # Pull clip if it is less than 5 seconds or more than 120 seconds,
        # or extremely outside the allowed category limits (e.g. < 40% of min or > 160% of max)
        if dur < 5.0 or dur > 120.0 or dur < (min_allowed * 0.4) or dur > (max_allowed * 1.6):
            logger.warning(
                f"Clip [{start_t:.2f}s - {end_t:.2f}s] PULLED from ranking competition due to "
                f"absurd duration: {dur:.2f}s (Category Limits: {min_allowed}s - {max_allowed}s)"
            )
            continue
            
        safe_and_valid_clips.append(clip)

    # Sort by virality score descending
    safe_and_valid_clips.sort(key=lambda x: x["virality_score"], reverse=True)

    # --- FINAL TIMELINE DEDUPLICATION ---
    # After snapping, clips may drift back to overlapping. Dedup on the final enriched list.
    final_ranked_clips = deduplicate_timeline(safe_and_valid_clips, max_overlap_ratio=0.20)

    # Assign ranks
    for idx, c in enumerate(final_ranked_clips):
        c["rank"] = idx + 1

    return final_ranked_clips[:limit]