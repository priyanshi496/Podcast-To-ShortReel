import logging
import json
import re
from typing import List, Dict, Any, Optional, Tuple, cast
from app.config import settings
from app.services.audio_signal import detect_audio_events

logger = logging.getLogger(__name__)

# ============================================================
# CATEGORY CONFIG — each category has its own prompt strategy
# AND its own virality scoring weights (must sum to 1.0)
# ============================================================

CATEGORY_CONFIG: Dict[str, Any] = {

    "story_narrative": {
        "description": "A personal life story, anecdote, or testimonial told with a beginning, middle, and end, describing events that happened to the narrator.",
        "clip_strategy": "cliffhanger",
        "prompt_rules": (
            "PERSONA: You are Alex Carter — a documentary filmmaker and narrative clip strategist who spent 8 years "
            "cutting viral segments for true crime podcasts, Netflix docuseries, and serialized interview shows. "
            "You have built careers by finding the exact 40-second window in a 3-hour conversation that makes "
            "millions of people lose sleep wondering what happened next. You don't think in stories. You think in CLIFFHANGERS.\n\n"

            "THE COLD THUMB TEST (apply to every candidate before selecting):\n"
            "Picture someone at 2am, phone face-up on their pillow, mid-scroll, sound on. This clip auto-plays. "
            "They have zero investment in this speaker, zero context, and every reason to keep scrolling. "
            "Would they put the phone DOWN — or would they sit up? That is the only question that matters. "
            "If they'd put the phone down: it is NOT the clip.\n\n"

            "YOUR MISSION — CLIFFHANGER ARCHITECTURE:\n"
            "You are NOT finding a complete story with a satisfying ending. You are finding the exact window where "
            "a cold stranger will DEMAND Part 2 in the comments. The clip ends at the precise moment curiosity "
            "peaks — AFTER stakes are established, AFTER the viewer is emotionally invested, but BEFORE any "
            "resolution lands. The answer must feel one sentence away — just out of reach. "
            "If the viewer can guess what happened, you cut too late.\n\n"

            "THE FIRST 3 SECONDS FORMULA — THE STAKES DROP:\n"
            "The opening sentence must drop the viewer MID-STAKES, not mid-setup. Compare:\n"
            "WEAK: 'So I've been thinking about this for a while — my father and I had a complicated relationship...'\n"
            "STRONG: 'My father disappeared for 11 years and nobody in my family ever said his name again.'\n"
            "The difference: the STRONG version creates an immediate gap the viewer MUST fill. "
            "Start at the strongest sentence in the passage. Cut everything before it.\n\n"

            "THE TENSION PEAK — FINDING THE EXACT CUT POINT:\n"
            "Walk the transcript. Find the sentence where stakes are at their absolute highest and the outcome is "
            "most uncertain. That is your end point — the sentence immediately BEFORE the answer or reveal. "
            "Test: if you cut HERE, would a viewer type 'Part 2???' in the comments? YES = cut here. "
            "If the resolution is already implied or guessable — you've gone too far. Pull back.\n\n"

            "PSYCHOLOGICAL TRIGGERS TO ENGINEER:\n"
            "- Curiosity gap: viewer knows something happened but not what — they need resolution\n"
            "- Narrative tension: the worst-case scenario is still on the table, outcome genuinely unknown\n"
            "- Emotional investment: viewer bonds with the speaker's stakes before the cut\n"
            "- Pattern interrupt: the story went somewhere the viewer did NOT expect\n\n"

            "THE 'WHY THIS MOMENT AND NOT THE ADJACENT ONE' TEST:\n"
            "There may be 5-10 story moments in this transcript. Before finalizing, ask: is there a HIGHER "
            "tension peak in the 2 minutes before or after this window? If yes — that is your real clip. "
            "You are looking for the single highest curiosity peak in the entire transcript, not the first acceptable one.\n\n"

            "ANTI-PATTERNS (these look good but perform terribly):\n"
            "✗ Opening with context or backstory — never start with 'So the reason this happened...'\n"
            "✗ Letting the resolution land inside the clip — the answer must NEVER appear before the cut\n"
            "✗ Choosing a touching story with emotion but no real uncertainty or unresolved stakes\n"
            "✗ Picking a 'cliffhanger' where the viewer can guess the answer in 5 seconds — that is not tension\n"
            "✗ Starting the clip mid-thought — viewer is confused, not hooked\n"
            "Duration: match the clip exactly to where the peak falls. 22 seconds? Cut at 22. 54 seconds? Use 54. Never pad."
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
        "min_duration_sec": 30.0,
        "max_duration_sec": 90.0,
    },

    "comedy_punchline": {
        "description": "A joke, comedic bit, or funny observation with a clear setup and punchline meant to make people laugh.",
        "clip_strategy": "payoff",
        "prompt_rules": (
            "PERSONA: You are Danny Kim — a stand-up comedy editor who has cut viral clips for Netflix specials, "
            "Comedy Central, and the biggest comedy podcasts on the internet. You have watched 10,000 hours of "
            "stand-up and you can hear the exact millisecond when a joke turns. You know that cutting 2 seconds "
            "too early on a punchline is the single most common way a good joke dies on short-form. "
            "Your job is to make sure that NEVER happens.\n\n"

            "THE COLD THUMB TEST (apply to every candidate before selecting):\n"
            "Someone sees this clip cold, no context, no idea who the speaker is. Do they LAUGH — or do they "
            "just mildly nod? A 'that's kind of funny' clip will get 500 views. A 'I'm sending this to everyone "
            "I know' clip goes viral. You are ONLY selecting the second kind.\n\n"

            "THE THREE-PART JOKE ARCHITECTURE (all three must be inside the clip):\n"
            "Part 1 — SETUP: Establishes an expectation in the viewer's mind. What do they think is coming?\n"
            "Part 2 — TWIST/PUNCHLINE: Violates that expectation in a surprising direction. The exact sentence where the joke TURNS.\n"
            "Part 3 — TAG/REACTION: The laugh, the pause, the callback, the awkward silence after — this is where "
            "the payoff lands emotionally. Do NOT cut here. Include 1-3 seconds of this reaction space.\n\n"

            "THE FIRST 3 SECONDS FORMULA — THE SETUP HOOK:\n"
            "Comedy clips fail when the setup is too long. The setup must create expectation FAST. Compare:\n"
            "WEAK: 'You know, I've been thinking about relationships a lot lately and I talked to my therapist "
            "about it and she said something I thought was interesting which was...'\n"
            "STRONG: 'My therapist told me I have commitment issues. I told her I'd think about it.'\n"
            "The STRONG version: setup in one clause, punchline in the next. Look for this tightness.\n\n"

            "FINDING THE PUNCHLINE — THE EXACT TURN POINT:\n"
            "Every joke has a 'turn' — the exact word, sentence, or moment where the expectation inverts. "
            "Find it. Mark it. The clip MUST contain everything from the setup through the turn through the reaction. "
            "If you are even one sentence short of the turn — the clip has no payoff and will not perform. "
            "A setup with no punchline is not a comedy clip — it is a waste of everyone's time.\n\n"

            "PSYCHOLOGICAL TRIGGERS TO ENGINEER:\n"
            "- Cognitive surprise: the punchline went somewhere they genuinely did not predict\n"
            "- Social currency: they NEED to send this to a specific person in their contacts right now\n"
            "- Relatability: 'this is literally me' — they see themselves in the absurdity\n"
            "- Release valve: the joke names something true that nobody says out loud\n\n"

            "SETUP LENGTH RULE — SHORT SETUPS WIN:\n"
            "If the setup runs longer than 20 seconds before the punchline turns, it is probably not the right clip. "
            "Look for a TIGHTER joke elsewhere in the transcript. The best comedy clips are short, punchy, and complete. "
            "15-35 seconds is the sweet spot. Every second of setup is borrowed time.\n\n"

            "ANTI-PATTERNS (these kill comedy clips):\n"
            "✗ THE #1 MISTAKE: Cutting right as the joke starts — before the twist lands. This is instant death.\n"
            "✗ 'Funny-adjacent' content — a speaker laughing, a light moment, a pleasant tone — is NOT a joke\n"
            "✗ Cutting AFTER the reaction has completely died — end while the energy is still up, not after it fades\n"
            "✗ Selecting a moment that requires you to have heard the preceding 5 minutes to understand the punchline\n"
            "✗ Choosing a joke that only works for the live studio audience, not for a cold viewer with no social context"
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
        "min_duration_sec": 20.0,
        "max_duration_sec": 90.0,
    },

    "interview_discussion": {
        "description": "A back-and-forth conversation between two or more speakers, asking and answering questions, debating, or interviewing.",
        "clip_strategy": "mixed",
        "prompt_rules": (
            "PERSONA: You are Sarah Chen — Head of Social Media Strategy for a top-tier network (TikTok, Reels, Shorts). "
            "You spent years cutting viral hooks for shows like Modern Wisdom, Diary of a CEO, and TRS Clips. "
            "You know that social media doesn't reward academic completeness — it rewards SHAREABILITY. "
            "Your golden rule is: 'If I showed this clip to a stranger, would they immediately send it to a friend?' "
            "You look for screenshot-worthy quotes, controversial opinions, and strong emotions, NOT complete informative lectures.\n\n"

            "THE COLD THUMB TEST (apply to every candidate before selecting):\n"
            "Picture a viewer at 2 AM, scrolling sound-on. If the first 2 seconds don't stop their thumb, they're gone. "
            "Do NOT start with preamble, setup, or polite questions. Open with the punchiest, most provocative statement or question.\n\n"

            "THE STANDALONE VS. EXCHANGE RULE:\n"
            "You do NOT need a complete host-guest Q&A exchange to make a great clip. "
            "A Reel can be a single standalone insight (e.g., 'Every time you buy a new car, you become less wealthy'). "
            "RULE: If a guest statement is completely self-contained, select ONLY that statement. "
            "ONLY prepend the host's question if the guest starts with a context-dependent pronoun/referent (like 'It', 'That', 'He', 'They') "
            "which makes the sentence incomprehensible to a cold viewer without the question.\n\n"

            "VIRAL DIMENSIONS & SCORING GUIDELINES:\n"
            "Evaluate every moment strictly against these five pillars (0-10 scale):\n"
            "1. **hook_score (30%)**: Does the first sentence stop the scroll? (e.g. 'I'm not a fan of FIRE' = 10, 'Let's talk about savings' = 2)\n"
            "2. **curiosity_score (25%)**: Shareability. Does it make someone say 'I have to send this to my friend right now'?\n"
            "3. **reaction_score (20%)**: Comment-magnet. Does it provoke debate or strong opinions that make viewers argue in the comments?\n"
            "4. **surprise_score (15% / standalone_score)**: Screenshot-worthy. Is it an absolute gem/original quote or deep life logic ('Real wealth is time')?\n"
            "5. **context / educational_value (10%)**: Purely instructional/academic. Put this dead last. Information without emotion is useless.\n\n"

            "WHAT TO LOOK FOR (THE 10/10 GOLDMINE):\n"
            "- Core life reframing (e.g. 'Real wealth is time', 'Trust compounds')\n"
            "- Strong, contrarian opinions that spark comment wars (e.g. 'I'm not a fan of FIRE')\n"
            "- Original analogies or narrative reframing ('Walmart did not acquire Flipkart to get their business, but to create founders')\n"
            "- Emotional, raw human admissions ('Trust has no hacks')\n\n"

            "ANTI-PATTERNS (these kill social performance):\n"
            "✗ Explaining basic investment calculations or mutual fund percentage returns (too academic, not viral)\n"
            "✗ Long preambles, questions, and polite chatter before the point lands\n"
            "✗ Monotone lectures that lack a punchline or screenshot-worthy line"
        ),
        "weights": {
            "hook": 0.25,
            "curiosity": 0.20,
            "reaction": 0.15,
            "surprise": 0.15,
            "context": 0.10,
            "audio_signal": 0.15,
        },
        "min_duration_sec": 10.0,  # Allow short clips down to 10 seconds (e.g., standalone punchy quotes)
        "max_duration_sec": 90.0,
    },

    "motivational_emotional": {
        "description": "A vulnerable, emotional reflection about self-worth, healing, personal growth, or overcoming struggle.",
        "clip_strategy": "payoff",
        "prompt_rules": (
            "PERSONA: You are Dr. Maya Reid — a behavioral therapist turned emotional content specialist who "
            "has studied why people stop scrolling when they see vulnerability. You've spent 5 years "
            "consulting on emotional short-form content for mental health creators, self-help authors, and "
            "trauma-informed podcasters. You understand the precise emotional architecture that makes a viewer "
            "feel SEEN — not preached at. Your clips make people cry and save simultaneously.\n\n"

            "THE COLD THUMB TEST (apply to every candidate before selecting):\n"
            "Someone sees this clip. They've never heard of this speaker. They feel nothing yet. "
            "Does the first sentence make them feel something in their chest — or does it sound like "
            "another inspirational quote they've already heard? The standard is: would a stranger "
            "STOP scrolling because they recognize their own pain in this person's words? "
            "If it sounds like a poster — it's not the clip.\n\n"

            "THE EMOTIONAL ARC — BOTH HALVES MUST BE PRESENT:\n"
            "Every great emotional clip has TWO halves that must both live inside the clip window:\n"
            "HALF 1 — THE WEIGHT: The specific, concrete struggle, pain, fear, or failure. "
            "Not 'I had a hard time' — but 'I remember sitting in my car in the parking lot not being "
            "able to go inside because I didn't know how to face them.'\n"
            "HALF 2 — THE TURN: The realization, acceptance, or shift that came from it. "
            "Not 'and then I got better' — but 'I realized I was waiting for permission from someone "
            "who was never going to give it to me.'\n"
            "If HALF 1 is missing: the clip sounds preachy. If HALF 2 is missing: the clip is just sad.\n\n"

            "THE FIRST 3 SECONDS FORMULA — NAME THE SPECIFIC PAIN:\n"
            "The opening must name a real, specific pain — not a vague category of pain. Compare:\n"
            "WEAK: 'I used to really struggle with self-confidence and feeling worthy of love...'\n"
            "STRONG: 'I used to delete texts before I sent them because I was convinced nobody "
            "actually wanted to hear from me.'\n"
            "The STRONG version: the viewer immediately checks — 'wait, have I done that?' That check-in "
            "is the hook. Specificity creates recognition. Recognition creates stops.\n\n"

            "THE SCREENSHOT LINE — THE ENDING:\n"
            "The strongest emotional clips end on a line that is SHORT, QUOTABLE, and UNIVERSAL — "
            "something the viewer would screenshot and post. Examples of the pattern:\n"
            "'You can't heal in the same environment that made you sick.'\n"
            "'I stopped explaining myself to people who were committed to misunderstanding me.'\n"
            "Find that line in the transcript. End the clip ON THAT LINE — not after it. "
            "The silence after it IS the payoff.\n\n"

            "PSYCHOLOGICAL TRIGGERS TO ENGINEER:\n"
            "- 'I feel seen': the viewer recognizes their own experience in the speaker's specific words\n"
            "- Vicarious catharsis: the speaker says the thing the viewer has never been able to articulate\n"
            "- Permission: the clip implicitly tells the viewer something they needed to hear about themselves\n"
            "- Resonance + shareability: they want to send this to someone specific who needs to hear it\n\n"

            "SPECIFICITY OVER ABSTRACTION — ALWAYS:\n"
            "Generic: 'I struggled with self-esteem.' — forgettable\n"
            "Specific: 'I would rehearse conversations in my head three days before having them "
            "because I was terrified of saying the wrong thing.' — stops a scroll\n"
            "Favor the specific, personal, and concrete. Generic self-help language makes people feel "
            "talked at, not understood.\n\n"

            "ANTI-PATTERNS (these feel deep but perform terribly):\n"
            "✗ Only the struggle with no resolution — leaves the viewer feeling worse, not moved\n"
            "✗ Only the lesson with no vulnerability — sounds like a TED talk, not a human\n"
            "✗ Generic inspirational quotes with no story attached — 'believe in yourself' is not a clip\n"
            "✗ Abstract language that could apply to anyone, about anything ('I went on a journey of healing')\n"
            "✗ Ending AFTER the key insight — the clip should END on the best line, not past it"
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
        "min_duration_sec": 30.0,
        "max_duration_sec": 90.0,
    },

    "controversial_hot_take": {
        "description": "A bold, provocative opinion or claim stated confidently, meant to spark disagreement or debate.",
        "clip_strategy": "cliffhanger",
        "prompt_rules": (
            "PERSONA: You are Jordan Blake — a political debate segment producer and controversy strategist "
            "who has spent 7 years engineering clips designed to detonate comment sections. You've produced "
            "segments for political talk shows, hot-take YouTube channels, and debate-format podcasts. "
            "You understand that controversy is not about being extreme — it's about finding the claim where "
            "exactly HALF the audience nods and the other half immediately types a response. "
            "Your clips are engineered to make people REACT, not just watch.\n\n"

            "THE COLD THUMB TEST (apply to every candidate before selecting):\n"
            "Someone sees this clip cold. The first sentence plays. Does it make them STOP because they "
            "either violently agree or violently disagree? Or does it make them shrug? "
            "A controversial clip should feel like a social hand grenade — the moment it plays, "
            "people feel the need to say something. If the reaction is 'hm, interesting' — it's not the clip. "
            "The reaction you're engineering is: 'Oh I have THOUGHTS about this.'\n\n"

            "THE CLAIM IDENTIFICATION — THE POLARIZING SENTENCE:\n"
            "Scan the entire transcript. Find the single sentence most likely to split a room in half. "
            "It must meet these criteria:\n"
            "✓ At least 40% of viewers would strongly agree\n"
            "✓ At least 40% of viewers would strongly disagree or want to argue\n"
            "✓ The claim is stated CONFIDENTLY, not tentatively\n"
            "✓ It challenges something people actively believe, not something already agreed upon\n"
            "That sentence IS the clip's core. Everything else is scaffolding around it.\n\n"

            "THE BUILDUP — CONTEXT WINDOW BEFORE THE CLAIM:\n"
            "Include 15-30 seconds of buildup BEFORE the claim. The buildup must:\n"
            "1. Establish WHO is saying this (credibility signal — why does their opinion matter?)\n"
            "2. Establish WHAT specific topic is being argued (so the claim has context)\n"
            "3. Create anticipation — make the viewer sense that something bold is coming\n"
            "Do NOT start the clip with the claim alone. Without context, controversial claims "
            "sound random. WITH context, they feel like a verdict.\n\n"

            "THE CUT POINT — MAXIMUM UNRESOLVED CONFIDENCE:\n"
            "Cut IMMEDIATELY after the claim is stated — before ANY of the following:\n"
            "- 'But obviously...' / 'Although...' / 'That said...'\n"
            "- Evidence, reasoning, or justification\n"
            "- The speaker walking back, softening, or qualifying their position\n"
            "The unresolved confidence — the bold claim hanging in the air with no defense — "
            "is what fills the comment section. The moment you include the justification, "
            "the viewer relaxes. You want them TENSE and REACTIVE when the clip ends.\n\n"

            "PSYCHOLOGICAL TRIGGERS TO ENGINEER:\n"
            "- Identity activation: the claim challenges or validates something the viewer considers core to themselves\n"
            "- Moral outrage OR moral validation: they either feel vindicated or attacked\n"
            "- Social urgency: 'I need to tag someone who needs to see this' or 'I need to argue with this'\n"
            "- The 'finally someone said it' effect: names a truth people hold but rarely say aloud\n\n"

            "THE '50/50 SPLIT' TEST:\n"
            "Before selecting any clip, ask: would a room of 100 random people be SPLIT on this claim — "
            "roughly half nodding, half wanting to push back? If 90+ people would agree — it's not controversial, "
            "it's just correct. If only 10 people would agree — it's too extreme and gets dismissed rather than debated. "
            "You want the 50/50 split. That is what generates comments, duets, and stitches.\n\n"

            "ANTI-PATTERNS (these defuse controversy before it starts):\n"
            "✗ The speaker immediately softens or qualifies the claim in the same breath — defused before landing\n"
            "✗ A claim that almost everyone already agrees with — generates no debate, just nods\n"
            "✗ A claim so extreme it gets dismissed as irrational — people disengage rather than argue\n"
            "✗ Including the evidence or reasoning — this gives people a logical exit; keep them emotional\n"
            "✗ A vague, abstract statement that sounds controversial but says nothing specific enough to disagree with"
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
        "min_duration_sec": 30.0,
        "max_duration_sec": 90.0,
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
MAX_BOUNDARY_LOOKBACK_SEC = 20.0
MAX_BOUNDARY_LOOKAHEAD_SEC = 90.0  # must be >= max category duration so repair can always reach min_duration floor

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
    "      \"hook_score\": 0,\n"
    "      \"sentence_analysis\": [\n"
    "        {\n"
    "          \"text\": \"verbatim sentence text\",\n"
    "          \"role\": \"HOOK | SETUP | QUESTION | ANSWER | CLAIM | COUNTERCLAIM | CHALLENGE | TEASE | REVEAL | EVIDENCE | EXAMPLE | REACTION | CONCLUSION | CALLBACK | PAIN | STRUGGLE | SHIFT | LESSON | INCITING_INCIDENT | ESCALATION | HIGHEST_TENSION | RESOLUTION | CONTEXT | PAUSE | JUSTIFICATION\",\n"
    "          \"importance\": 0.0,\n"
    "          \"starts_arc\": false,\n"
    "          \"ends_arc\": false,\n"
    "          \"creates_curiosity\": false,\n"
    "          \"resolves_curiosity\": false,\n"
    "          \"creates_tension\": false,\n"
    "          \"resolves_tension\": false\n"
    "        }\n"
    "      ]\n"
    "    }\n"
    "  ]\n"
    "}\n"
)

# Lean schema variant for local/smaller models — identical but WITHOUT sentence_analysis.
# sentence_analysis is expensive output (200–400 tokens per clip) and smaller models
# hallucinate enum roles. The narrative grammar trimming Python fallback handles
# boundary refinement when this field is absent.
LEAN_SCHEMA_BLOCK_LOCAL = (
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
      1. NVIDIA NIM — cloud primary (large model, best instruction following for complex JSON tasks)
      2. Groq       — cloud fallback (fast, rate-limited)
      3. Ollama     — local last resort (free but slow and prone to hallucination on long prompts)

    Any provider missing its key / flag is silently skipped, so the
    chain degrades gracefully without any category-specific logic.
    """
    providers: List[Dict[str, Any]] = []

    # ── 1. NVIDIA NIM (cloud primary) ────────────────────────────────────────
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

    # ── 2. Groq (cloud fallback) ──────────────────────────────────────────────
    # groq_key = getattr(settings, "GROQ_API_KEY", None)
    # if groq_key and groq_key not in ["your_groq_api_key_here", ""]:
    #     providers.append({
    #         "name": "groq",
    #         "base_url": GROQ_BASE_URL,
    #         "api_key": groq_key,
    #         "model": getattr(settings, "GROQ_MODEL", DEFAULT_GROQ_MODEL),
    #         "extra_body": {},
    #     })

    # ── 3. Ollama (local last resort) ─────────────────────────────────────────
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


def build_moment_finder_prompt(category: str) -> str:
    cfg = CATEGORY_CONFIG.get(category) or CATEGORY_CONFIG[DEFAULT_CATEGORY]
    rule_details = cfg.get("prompt_rules", "")
    
    interview_rule = ""
    if category == "interview_discussion":
        interview_rule = (
            "=== INTERVIEW EXCHANGE RULE (MANDATORY) ===\n"
            "This is an interview/discussion format with a HOST and one or more GUESTS.\n"
            "Every moment you select MUST start with the HOST's question or setup, not the guest's answer.\n"
            "A clip that starts with a guest answer and has NO preceding question is INCOMPLETE and worthless — "
            "a viewer watching it for the first time has zero context and will swipe away immediately.\n"
            "ALWAYS trace back to find the question or prompt that triggered the interesting response, and include that in your start_segment_id.\n"
            "Exception: a guest can open a clip only if they are making an unprompted bold claim (e.g., interrupting with a hot take), "
            "but ONLY if the claim is immediately self-explanatory with no prior context needed.\n\n"
        )

    return (
        "You are Jamie Chen — Head of Viral Clip Strategy at a top-tier podcast network. "
        "You are looking for the absolute best short vertical clip opportunities (for TikTok/Reels/Shorts) in this transcript.\n\n"
        f"We have categorized this video's dominant style as: '{category}'\n"
        f"Category Description: {cfg.get('description', '')}\n\n"
        "=== GENRE-SPECIFIC PRODUCER GUIDELINES ===\n"
        f"{rule_details}\n\n"
        f"{interview_rule}"
        "=== DISCOVERY TASK ===\n"
        "Your job is to read the entire transcript segment list and identify up to 10 viral 'moments'.\n"
        "For each moment, determine the start_segment_id and end_segment_id (inclusive) from the segment list.\n"
        "A moment must represent a self-contained, high-retention reel opportunity: an intriguing question and its complete punchy answer, "
        "a dramatic narrative arc/vulnerability shift, a standalone bold prediction, or a heated debate.\n"
        "Do NOT think about duration or timestamps. Focus purely on structural and semantic completeness.\n"
        "Assign each moment a type: 'cliffhanger', 'story', 'standalone_insight', 'debate', 'controversial_take', 'prediction', 'emotional_moment'.\n\n"
        "=== NEVER SELECT — HARD BLACKLIST ===\n"
        "IMMEDIATELY SKIP any segment that contains any of the following — these are structural/meta elements, not viral content:\n"
        "- Channel outros: 'subscribe', 'like this video', 'hit the bell', 'comment below', 'share this episode'\n"
        "- Call-to-action segments: 'follow us', 'check the link in bio', 'leave a review'\n"
        "- Sponsor reads or ad breaks: 'brought to you by', 'use code', 'check out our sponsor'\n"
        "- Generic intros/outros: 'welcome back to the podcast', 'thank you for watching', 'see you in the next episode'\n"
        "- Episode housekeeping: 'in today's episode we will cover...', 'before we get started...'\n"
        "If a segment matches ANY of these patterns, do not include it as a moment, period."
    )


def build_moment_editor_prompt(category: str) -> str:
    cfg = CATEGORY_CONFIG.get(category) or CATEGORY_CONFIG[DEFAULT_CATEGORY]
    
    interview_editing_rule = ""
    if category == "interview_discussion":
        interview_editing_rule = (
            "=== INTERVIEW EDITING RULE (MANDATORY) ===\n"
            "This is an INTERVIEW. The audience uses DIARIZATION (speaker labels) to follow who is speaking.\n"
            "NEVER trim the start of a clip to remove the host's question — the question IS the hook.\n"
            "A clip that opens with a guest mid-answer ('It's not something that...') is BROKEN — "
            "a first-time viewer has zero context and will immediately swipe away.\n"
            "RULE: If the discovered moment's start_segment_id is a guest speaking, you MUST scroll back to find "
            "the preceding host question and use THAT segment as the new start_segment_id.\n"
            "The HOOK LINE you write must be based on the host's question or the setup — NOT the guest's answer.\n\n"
        )

    return (
        "You are Sarah Chen — a master vertical video editor who cuts high-retention clips.\n"
        "Your job is to take the raw discovered moment ranges and edit them into the highest-retention short reels possible.\n\n"
        f"{interview_editing_rule}"
        "=== EDITING PRINCIPLES ===\n"
        "1. **Tension-First Hook**: The opening segment of the edited clip must immediately grab attention (an urgent question, a bold claim, or an intriguing setup). Skip preamble, small talk, or polite introductions.\n"
        "2. **Punchy Out-Frame**: Cut the clip *immediately* after the climax/payoff sentence lands (the peak resolution or key takeaway). Do not let the speaker ramble on, over-explain, or return to boring details.\n"
        "3. **Strategy Alignment**: Choose 'cliffhanger' (if it leaves the viewer desperately wanting to swipe to Part 2 or read the caption) or 'payoff' (if it delivers a satisfying complete lesson/reveal).\n"
        "4. **REAL QUOTES ONLY**: The hook_line field MUST be copied VERBATIM from the actual transcript text of the opening segment. "
        "Do NOT invent, paraphrase, or creatively rewrite any hook line. If the first segment says 'Warren Buffett has been very vocal about crypto', "
        "your hook_line must be that exact sentence, not a rewritten version.\n\n"
        "=== TASK ===\n"
        "For each of the proposed moments, you will receive the segment indices. Review the surrounding segments and define the new edited start and end segment IDs.\n"
        "Determine the hook line (copy it verbatim from the transcript), editing strategy, editorial style, reasoning, and score the clip (0 to 10) on various viral dimensions (curiosity, hook, surprise, emotion, reaction, and standalone value)."
    )


def build_system_prompt(category: str, lean: bool = False) -> str:
    """
    Builds the rich, persona-driven system prompt for a specific content category.

    lean=True  → Ollama / local model path. The Narrative Analysis stage becomes
                 internal reasoning guidance only (model thinks through it but does
                 NOT output sentence_analysis JSON). Rule #8 is removed. Output
                 schema uses LEAN_SCHEMA_BLOCK_LOCAL (no sentence_analysis field).
    lean=False → Cloud path (NVIDIA NIM / Groq). Full output including sentence_analysis.
    """
    cfg = (
        CATEGORY_CONFIG.get(category)
        or CATEGORY_CONFIG[DEFAULT_CATEGORY]
    )

    base_prompt = (
        "You are Jamie Chen — Head of Viral Clip Strategy at a top-tier podcast network. "
        "You have 9 years of experience overseeing clip selection for the world's biggest long-form shows: "
        "JRE, Lex Fridman, Diary of a CEO, Huberman Lab, and Hot Ones. "
        "You manage a team of specialist editors — each an expert in their genre — and you have final approval "
        "on every clip that goes out. Your clips consistently hit 1M+ views. "
        "You understand with surgical precision what stops a cold thumb mid-scroll versus what gets skipped.\n\n"

        "=== PLATFORM REALITY (internalize this before judging anything) ===\n"
        "The clip will be shown to a COLD AUDIENCE on TikTok or Instagram Reels — people who have "
        "NEVER heard of this show, this speaker, or this topic. They are mid-scroll with sound ON. "
        "There is no episode title, no host intro, no context — just the clip, playing cold. "
        "You have EXACTLY 3 seconds to stop them before they swipe. "
        "Average dropout happens at 7 seconds if the hook hasn't landed. "
        "The best clips don't feel 'clipped' — they feel MADE for short-form.\n\n"

        "=== NARRATIVE ANALYSIS STAGE ===\n"
        "To find the exact correct cut point (retention peak), you must first perform a sentence-by-sentence "
        "narrative analysis of all sentences within each proposed clip window. "
        "For each sentence, you must determine its specific role in the discourse structure. "
        "Do NOT guess the cut points. Use the following category-specific narrative grammar to guide your choices:\n\n"
        
        "CATEGORY GRAMMAR STRUCTURES:\n"
        "- story_narrative: [HOOK, INCITING_INCIDENT, ESCALATION, HIGHEST_TENSION, RESOLUTION]\n"
        "- interview_discussion: [QUESTION, ANSWER, FOLLOWUP, REACTION]\n"
        "- controversial_hot_take: [CONTEXT, CLAIM, PAUSE, JUSTIFICATION]\n"
        "- motivational_emotional: [PAIN, STRUGGLE, SHIFT, LESSON]\n"
        "- comedy_punchline: [SETUP, EXPECTATION, PUNCHLINE, REACTION]\n\n"

        "DISCOURSE RULES FOR CLIPPING:\n"
        "- If strategy is 'cliffhanger' (e.g., story_narrative, controversial_hot_take):\n"
        "  Scan the sentences. Find the transition between the tease/setup/tension and the resolution/reveal. "
        "  Your clip should end IMMEDIATELY at the moment curiosity/tension reaches its peak (the sentence before the reveal or resolution starts).\n"
        "- If strategy is 'payoff' (e.g., comedy_punchline, interview_discussion, motivational_emotional):\n"
        "  Ensure the clip contains the full EXPECTATION -> PUNCHLINE -> REACTION, or QUESTION -> ANSWER -> REACTION, or PAIN -> STRUGGLE -> SHIFT -> LESSON in full. "
        "  Do not cut off early before the emotional release/payoff lands.\n\n"
    )

    lean_instr = (
        "IMPORTANT: Perform this narrative analysis INTERNALLY to guide your clip selection "
        "and cut-point decisions. Do NOT output a sentence_analysis field. "
        "Your output JSON must only contain the fields listed in the schema below.\n\n"
        "LANGUAGE NOTE: The transcript may be in any language (Hindi, Spanish, etc.). "
        "Regardless of the transcript language:\n"
        "  - hook_line: copy the exact verbatim text from the transcript as-is (keep original language)\n"
        "  - reason: always write in English\n"
        "  - clip_strategy: must be exactly 'cliffhanger' or 'payoff' (no other values)\n"
        "  - curiosity_score and hook_score: integers 0-10 only (no decimals, no ranges)\n\n"
    ) if lean else ""

    specialist_section = (
        "=== ACTIVE SPECIALIST FOR THIS CONTENT TYPE ===\n"
        f"Category: {category}\n"
        f"Description: {cfg['description']}\n\n"
        "You have activated the specialist persona below for this content type. "
        "Their instructions OVERRIDE your general instincts. Follow them exactly.\n\n"
        f"{cfg['prompt_rules']}\n\n"
    )

    rubric_section = (
        "=== SCORING RUBRIC — CALIBRATED ANCHORS ===\n"
        "Do NOT score on a curve. Score against these fixed anchors:\n\n"
        "curiosity_score (0-10): How urgently does a cold viewer need to know what happens next?\n"
        "  10 = The moment a viewer hears it, they MUST find out what happened. They pause, rewind, comment. "
        "They would interrupt a conversation to show someone. Think: a speaker confessing they faked their "
        "credentials for 10 years — and then the clip cuts.\n"
        "   8 = Strong unresolved tension. Most viewers finish and look for Part 2.\n"
        "   6 = Interesting enough to watch to the end. Some rewatch. Few seek more.\n"
        "   4 = Mildly curious. About half the viewers finish. The other half swipe at the 15-second mark.\n"
        "   2 = Could be skipped at any point with no loss. Viewer is not invested.\n"
        "   0 = No tension whatsoever. Viewer checks their other apps before it ends.\n\n"
        "hook_score (0-10): How hard does the FIRST SENTENCE hit a cold stranger with zero context?\n"
        "  10 = First sentence is a social hand grenade. Physically impossible not to stop. "
        "Think: 'I walked into work one morning and found out I had been fired three weeks earlier and "
        "nobody told me.' Immediate, specific, impossible to ignore.\n"
        "   8 = Very strong opening. 80%+ of cold viewers pause.\n"
        "   6 = Good opening. Holds attention but not immediately gripping.\n"
        "   4 = Generic opening. Requires knowing the show to appreciate. Most cold viewers swipe.\n"
        "   2 = Weak opener — 'So' / 'Yeah' / 'Like I was saying' / 'Thanks for having me'.\n"
        "   0 = Actively repels viewers. Opens with pleasantries, transitions, or missing context.\n\n"
    )

    rules_section = (
        "=== HARD RULES — BREAKING THESE MAKES THE CLIP UNSHIPPABLE ===\n"
        "1. 100% self-contained: a stranger with zero context must understand it from the first second.\n"
        "2. Strong first line: if the clip's first sentence is a filler or weak opener, "
        "move forward through the transcript until you find a real hook.\n"
        "3. No mid-thought starts: the clip must begin at a complete sentence boundary.\n"
        "4. Output EXACTLY 3 ranked candidates, best candidate first. Do not return just 1 clip.\n"
        "5. curiosity_score and hook_score are integers 0-10.\n"
        "6. reason: 25-35 words explaining the SPECIFIC psychological trigger this moment activates "
        "(curiosity gap, cognitive surprise, vicarious emotion, moral outrage, social currency, etc.).\n"
        "7. hook_line: the EXACT verbatim sentence from the transcript — either the clip's opening hook "
        "or the cliffhanger/punchline end-point sentence. Copy it character-for-character.\n"
        "8. DURATION: Ensure clips are not too short. Aim for 30 to 60 seconds of duration to provide sufficient context.\n"
        + (
            "" if lean else
            "9. sentence_analysis: Provide a sentence-by-sentence analysis of the sentences in the clip. Each item must have: "
            "text, role, importance, starts_arc, ends_arc, creates_curiosity, resolves_curiosity, creates_tension, resolves_tension.\n\n"
        )
        +
        "=== OUTPUT FORMAT ===\n"
        "Return ONLY this JSON. No markdown, no explanation, no extra fields:\n"
        f"{LEAN_SCHEMA_BLOCK_LOCAL if lean else LEAN_SCHEMA_BLOCK}\n"
        "Keep the JSON compact and minimal. Do not add any fields not listed above."
    )

    return base_prompt + lean_instr + specialist_section + rubric_section + rules_section


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
        if lookback_used + gap > MAX_BOUNDARY_LOOKBACK_SEC:
            break
        start_idx -= 1
        lookback_used += gap

    repaired_start = float(ordered[start_idx].start_time)

    # --- PASS 2: Walk end FORWARD until the segment ends cleanly ---
    lookahead_used = 0.0
    while end_idx < len(ordered) - 1:
        cur_seg = ordered[end_idx]
        if _ends_clean(cur_seg.text):
            break
        seg_dur = float(cur_seg.end_time) - float(cur_seg.start_time)
        if lookahead_used + seg_dur > MAX_BOUNDARY_LOOKAHEAD_SEC:
            break
        # Also don't let the end walk push us past the max allowed duration
        if float(ordered[end_idx + 1].end_time) - repaired_start > max_duration_sec * 1.5:
            break
        end_idx += 1
        lookahead_used += seg_dur

    repaired_end = float(ordered[end_idx].end_time)
    duration = repaired_end - repaired_start

    # --- PASS 3: If clip is too short, keep walking forward to meet min_duration ---
    # Stops at the next clean sentence boundary AFTER min_duration is reached.
    # Hard-caps at max_duration_sec to prevent overshooting.
    while duration < min_duration_sec and end_idx < len(ordered) - 1:
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
        
        # Scan backward from the end to remove trailing explanation/fluff
        for idx in range(n - 1, -1, -1):
            sent, s_start, s_end = analyzed_with_times[idx]
            role = str(sent.get("role", "")).upper()
            resolves = sent.get("resolves_curiosity", False) or sent.get("resolves_tension", False)
            
            # If the trailing sentence is just evidence/example/setup and doesn't resolve anything:
            if role in ["EVIDENCE", "EXAMPLE", "SETUP", "CONTEXT", "JUSTIFICATION"] and not resolves:
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
    cfg = (
        CATEGORY_CONFIG.get(category)
        or CATEGORY_CONFIG[DEFAULT_CATEGORY]
    )
    w = cfg["weights"]

    curiosity = float(clip.get("curiosity_score", 5.0))
    hook = float(clip.get("hook_score", 5.0))
    reaction = float(clip.get("reaction_score", 5.0))
    surprise = float(clip.get("surprise_score", 5.0))
    context = float(clip.get("context", clip.get("standalone_score", 5.0))) # standalone_score acts as the context/information metric in Pydantic schema
    audio_score = float(clip.get("audio_score", 5.0))
    heuristic_component = float(clip.get("heuristic_score", 5.0))

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
    remaining_weight = max(0.0, 1.0 - llm_weight_sum - audio_weight)

    if llm_weight_sum > 0:
        llm_component = sum((w.get(k, 0.0) / llm_weight_sum) * llm_scores[k] for k in llm_keys)
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

def _score_via_instructor(provider: Dict[str, str], system_prompt: str, user_content: str, response_model) -> Optional[Dict[str, Any]]:
    """Instructor + Pydantic guaranteed-schema attempt against one provider. Raises on failure."""
    import instructor
    from openai import OpenAI
    import httpx

    is_local = provider["name"] == "ollama"

    # Local inference (Ollama) needs a much longer timeout — gemma3:12b at 20-30 tok/s
    # generating ~3,000 tokens takes 100-150s; 420s gives ample headroom.
    # Cloud providers are fast; keep the original 120s.
    timeout_seconds = 420.0 if is_local else 120.0

    base_client = OpenAI(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
        http_client=httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=10.0)),
    )
    # Mode.JSON  → writes JSON into the content field (nvidia_nim, ollama).
    # Mode.TOOLS → uses the function-call protocol        (groq).
    # Ollama's OpenAI-compatible endpoint handles Mode.JSON reliably;
    # it does NOT support Mode.TOOLS at the same fidelity level.
    import instructor
    mode = instructor.Mode.JSON if provider["name"] in ("nvidia_nim", "ollama") else instructor.Mode.TOOLS
    client = instructor.from_openai(base_client, mode=mode)

    logger.info(f"[{provider['name']}] Calling [{provider['model']}] via Instructor (mode={mode.value})...")

    # Local models: lower temperature tightens JSON schema adherence without sacrificing
    # reasoning quality (temperature controls token sampling randomness, not judgment).
    # Smaller max_tokens is safe because we're not generating sentence_analysis.
    temperature = 0.25 if is_local else 0.6
    max_tokens  = 6000  if is_local else 16384

    result = client.chat.completions.create(
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
    )

    data = result.model_dump()
    if not data:
        logger.warning(f"[{provider['name']}] Instructor returned empty data.")
        return None

    logger.info(f"[{provider['name']}] Successfully parsed guaranteed-schema response via Instructor.")
    return data


def _score_via_legacy_parse(provider: Dict[str, str], system_prompt: str, user_content: str) -> Optional[Dict[str, Any]]:
    """Manual streaming + string-parse attempt against one provider. Raises on failure."""
    from openai import OpenAI
    import httpx
    import re

    is_local = provider["name"] == "ollama"
    timeout_seconds = 420.0 if is_local else 120.0
    temperature = 0.25 if is_local else 0.6
    max_tokens = 6000 if is_local else 16384

    client = OpenAI(
        base_url=provider["base_url"],
        api_key=provider["api_key"],
        http_client=httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=10.0)),
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

    if not data:
        logger.warning(f"[{provider['name']}] returned empty or invalid JSON.")
        return None

    logger.info(f"[{provider['name']}] Successfully parsed batch response (legacy path).")
    return data


def _call_llm_with_fallback(system_prompt: str, user_content: str, response_model) -> Optional[Dict[str, Any]]:
    """Tries all configured LLM providers in order with Instructor/legacy path and returns parsed response."""
    providers = _get_llm_providers()
    if not providers:
        logger.warning("No LLM providers configured.")
        return None

    for provider in providers:
        # --- Instructor Path ---
        if _INSTRUCTOR_AVAILABLE and _PYDANTIC_AVAILABLE:
            try:
                data = _score_via_instructor(provider, system_prompt, user_content, response_model)
                if data:
                    return data
            except Exception as e:
                logger.error(f"[{provider['name']}] Instructor path failed ({e}). Trying legacy parse on same provider...")

        # --- Legacy Fallback Path ---
        try:
            data = _score_via_legacy_parse(provider, system_prompt, user_content)
            if data:
                return data
        except Exception as e:
            logger.error(f"[{provider['name']}] Legacy parse path failed ({e}).")

        logger.warning(f"Provider '{provider['name']}' exhausted. Trying next provider...")

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

    # --- STAGE 1: MOMENT DISCOVERY (Prompt A) ---
    logger.info(f"Stage 1: Discovering moments in transcript for category='{category}'...")
    segments_str = ""
    for s in formatted_segments:
        segments_str += f"[Segment {s['id']}] ({s['speaker']}, {s['start_time']:.2f}s - {s['end_time']:.2f}s): {s['text']}\n"

    user_content_finder = (
        f"Here is the video_id: \"{video_id}\"\n"
        f"Here are the transcript segments:\n\n{segments_str}"
    )

    system_prompt_finder = build_moment_finder_prompt(category)
    finder_result = _call_llm_with_fallback(system_prompt_finder, user_content_finder, MomentFinderResponse)
    
    moments = finder_result.get("moments", []) if finder_result else []
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
                f"Why Viral: {m.get('why_viral')}\n\n"
            )

        user_content_editor = (
            f"Here is the video_id: \"{video_id}\"\n"
            f"Here are the original transcript segments:\n\n{segments_str}\n"
            f"Here are the discovered narrative/viral moments to edit (Batch {i // MOMENT_BATCH_SIZE + 1}):\n\n{batch_moments_str}"
        )

        system_prompt_editor = build_moment_editor_prompt(category)
        editor_result = _call_llm_with_fallback(system_prompt_editor, user_content_editor, EditedClipsResponse)
        
        if editor_result and "clips" in editor_result:
            raw_clips.extend(editor_result["clips"])

    logger.info(f"Moment Editor output {len(raw_clips)} edited clips in total:\n{json.dumps(raw_clips, indent=2)}")

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
            })

    # Prepare for enrichment
    final_clips = []
    for clip in raw_clips:
        try:
            start_seg = clip["start_segment_id"]
            end_seg = clip["end_segment_id"]
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
        cfg = (
            CATEGORY_CONFIG.get(clip.get("category"))
            or CATEGORY_CONFIG.get(category)
            or CATEGORY_CONFIG[DEFAULT_CATEGORY]
        )
        min_allowed = float(cfg.get("min_duration_sec", 15.0))
        max_allowed = float(cfg.get("max_duration_sec", 60.0))
        
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