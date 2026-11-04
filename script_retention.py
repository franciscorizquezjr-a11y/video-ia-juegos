"""
script_retention.py — Modular Hook Construction & Sentence-Level Retention Engine

Implements structured composition of viral hooks and retention narrative architecture
for Trapster video scripts:
1. Modular Hook Construction:
   - First Words (formulaic opening)
   - Game Genre (hook presentation)
   - First Punch (action, role, objective, gameplay fantasy)
   - Second Punch (contrast, threat, twist, complication, mystery)
2. Narrative Retention Blocks:
   - Setup
   - First Escalation
   - Second Escalation
   - Payoff
   - CTA
3. Sentence-Level Classification & Tracking:
   - Sentence Type
   - Attention Device
   - Information Function
   - Open Loops (tracking creation and resolution)
   - Escalation Levels & Visual Beats
4. 20 Core Retention Principles Prompt Engine
5. Automated Validation & Self-Repair Fallback Engine
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Set
from pydantic import BaseModel, Field

logger = logging.getLogger("script_retention")

# =============================================================================
# 1. TAXONOMY & NORMALIZED CATEGORIES (SINGLE SOURCE OF TRUTH)
# =============================================================================

class FirstWordsTaxonomy:
    HERE_IS_A_NEW = "HERE_IS_A_NEW"
    SO_IN_THIS_NEW = "SO_IN_THIS_NEW"
    IN_THIS_NEW = "IN_THIS_NEW"
    THIS_NEW = "THIS_NEW"
    IMAGINE_A = "IMAGINE_A"
    WHAT_IF = "WHAT_IF"
    CHECK_OUT_THIS = "CHECK_OUT_THIS"
    THERE_IS_A_NEW = "THERE_IS_A_NEW"
    YOU_PLAY_AS = "YOU_PLAY_AS"
    CUSTOM = "CUSTOM"

    ALL: Set[str] = {
        HERE_IS_A_NEW,
        SO_IN_THIS_NEW,
        IN_THIS_NEW,
        THIS_NEW,
        IMAGINE_A,
        WHAT_IF,
        CHECK_OUT_THIS,
        THERE_IS_A_NEW,
        YOU_PLAY_AS,
        CUSTOM,
    }


class GameGenreTaxonomy:
    COOP_GAME = "COOP_GAME"
    FOUR_PLAYER_COOP = "FOUR_PLAYER_COOP"
    TWO_PLAYER_COOP = "TWO_PLAYER_COOP"
    STEAM_GAME = "STEAM_GAME"
    NEW_STEAM_GAME = "NEW_STEAM_GAME"
    HORROR_GAME = "HORROR_GAME"
    MULTIPLAYER_HORROR = "MULTIPLAYER_HORROR"
    SURVIVAL_GAME = "SURVIVAL_GAME"
    SURVIVAL_CRAFTING = "SURVIVAL_CRAFTING"
    OPEN_WORLD_GAME = "OPEN_WORLD_GAME"
    SIMULATION_GAME = "SIMULATION_GAME"
    ROGUELIKE_GAME = "ROGUELIKE_GAME"
    EXTRACTION_SHOOTER = "EXTRACTION_SHOOTER"
    PARTY_GAME = "PARTY_GAME"
    UPCOMING_GAME = "UPCOMING_GAME"
    STRATEGY_GAME = "STRATEGY_GAME"
    ACTION_RPG = "ACTION_RPG"
    CUSTOM = "CUSTOM"

    ALL: Set[str] = {
        COOP_GAME,
        FOUR_PLAYER_COOP,
        TWO_PLAYER_COOP,
        STEAM_GAME,
        NEW_STEAM_GAME,
        HORROR_GAME,
        MULTIPLAYER_HORROR,
        SURVIVAL_GAME,
        SURVIVAL_CRAFTING,
        OPEN_WORLD_GAME,
        SIMULATION_GAME,
        ROGUELIKE_GAME,
        EXTRACTION_SHOOTER,
        PARTY_GAME,
        UPCOMING_GAME,
        STRATEGY_GAME,
        ACTION_RPG,
        CUSTOM,
    }


class FirstPunchTaxonomy:
    ACTION = "ACTION"
    ROLE = "ROLE"
    OBJECTIVE = "OBJECTIVE"
    SURVIVAL = "SURVIVAL"
    EXPLORATION = "EXPLORATION"
    COOPERATION = "COOPERATION"
    CREATION = "CREATION"
    DISCOVERY = "DISCOVERY"
    COMPETITION = "COMPETITION"
    CUSTOM = "CUSTOM"

    ALL: Set[str] = {
        ACTION,
        ROLE,
        OBJECTIVE,
        SURVIVAL,
        EXPLORATION,
        COOPERATION,
        CREATION,
        DISCOVERY,
        COMPETITION,
        CUSTOM,
    }


class SecondPunchTaxonomy:
    CONTRAST = "CONTRAST"
    THREAT = "THREAT"
    TWIST = "TWIST"
    COMPLICATION = "COMPLICATION"
    MYSTERY = "MYSTERY"
    STAKES = "STAKES"
    UNEXPECTED_MECHANIC = "UNEXPECTED_MECHANIC"
    WORLD_BUILDING = "WORLD_BUILDING"
    NONE = "NONE"
    CUSTOM = "CUSTOM"

    ALL: Set[str] = {
        CONTRAST,
        THREAT,
        TWIST,
        COMPLICATION,
        MYSTERY,
        STAKES,
        UNEXPECTED_MECHANIC,
        WORLD_BUILDING,
        NONE,
        CUSTOM,
    }


class NarrativeBlockTaxonomy:
    HOOK = "HOOK"
    SETUP = "SETUP"
    FIRST_ESCALATION = "FIRST_ESCALATION"
    SECOND_ESCALATION = "SECOND_ESCALATION"
    PAYOFF = "PAYOFF"
    CTA = "CTA"

    ALL: Set[str] = {
        HOOK,
        SETUP,
        FIRST_ESCALATION,
        SECOND_ESCALATION,
        PAYOFF,
        CTA,
    }


class SentenceTypeTaxonomy:
    HOOK = "HOOK"
    SETUP = "SETUP"
    ACTION = "ACTION"
    TWIST = "TWIST"
    CONSEQUENCE = "CONSEQUENCE"
    REVEAL = "REVEAL"
    QUESTION = "QUESTION"
    OPEN_LOOP = "OPEN_LOOP"
    ESCALATION = "ESCALATION"
    PAYOFF = "PAYOFF"
    CTA = "CTA"
    CUSTOM = "CUSTOM"

    ALL: Set[str] = {
        HOOK,
        SETUP,
        ACTION,
        TWIST,
        CONSEQUENCE,
        REVEAL,
        QUESTION,
        OPEN_LOOP,
        ESCALATION,
        PAYOFF,
        CTA,
        CUSTOM,
    }


class AttentionDeviceTaxonomy:
    BUT = "BUT"
    THEN = "THEN"
    UNTIL = "UNTIL"
    EXCEPT = "EXCEPT"
    BECAUSE = "BECAUSE"
    SUDDENLY = "SUDDENLY"
    WORST_PART = "WORST_PART"
    SECRET = "SECRET"
    WARNING = "WARNING"
    QUESTION = "QUESTION"
    CONTRAST = "CONTRAST"
    REVEAL = "REVEAL"
    NONE = "NONE"
    CUSTOM = "CUSTOM"

    ALL: Set[str] = {
        BUT,
        THEN,
        UNTIL,
        EXCEPT,
        BECAUSE,
        SUDDENLY,
        WORST_PART,
        SECRET,
        WARNING,
        QUESTION,
        CONTRAST,
        REVEAL,
        NONE,
        CUSTOM,
    }


class InformationFunctionTaxonomy:
    WORLD = "WORLD"
    CHARACTER = "CHARACTER"
    MECHANIC = "MECHANIC"
    OBJECTIVE = "OBJECTIVE"
    THREAT = "THREAT"
    TWIST = "TWIST"
    CONSEQUENCE = "CONSEQUENCE"
    REWARD = "REWARD"
    CUSTOM = "CUSTOM"

    ALL: Set[str] = {
        WORLD,
        CHARACTER,
        MECHANIC,
        OBJECTIVE,
        THREAT,
        TWIST,
        CONSEQUENCE,
        REWARD,
        CUSTOM,
    }


class OpenLoopTaxonomy:
    NONE = "NONE"
    INFORMATION_GAP = "INFORMATION_GAP"
    THREAT = "THREAT"
    MYSTERY = "MYSTERY"
    CONSEQUENCE = "CONSEQUENCE"
    UNRESOLVED_ACTION = "UNRESOLVED_ACTION"
    REVEAL_ANTICIPATION = "REVEAL_ANTICIPATION"
    ESCALATION = "ESCALATION"
    CUSTOM = "CUSTOM"

    ALL: Set[str] = {
        NONE,
        INFORMATION_GAP,
        THREAT,
        MYSTERY,
        CONSEQUENCE,
        UNRESOLVED_ACTION,
        REVEAL_ANTICIPATION,
        ESCALATION,
        CUSTOM,
    }


SYNONYM_MAP: Dict[str, str] = {
    "4_PLAYER_COOP": GameGenreTaxonomy.FOUR_PLAYER_COOP,
    "2_PLAYER_COOP": GameGenreTaxonomy.TWO_PLAYER_COOP,
    "3_PLAYER_COOP": GameGenreTaxonomy.FOUR_PLAYER_COOP,
    "4_PLAYER": GameGenreTaxonomy.FOUR_PLAYER_COOP,
    "2_PLAYER": GameGenreTaxonomy.TWO_PLAYER_COOP,
    "COOP": GameGenreTaxonomy.COOP_GAME,
    "HERE_IS_NEW": FirstWordsTaxonomy.HERE_IS_A_NEW,
    "HERES_A_NEW": FirstWordsTaxonomy.HERE_IS_A_NEW,
    "SO_IN_THIS": FirstWordsTaxonomy.SO_IN_THIS_NEW,
    "IN_THIS": FirstWordsTaxonomy.IN_THIS_NEW,
}


def normalize_category(val: Any, allowed: Set[str], fallback: str = "CUSTOM") -> str:
    """Safely normalizes a string category to standard uppercase format, matching allowed sets."""
    if not val:
        return fallback
    clean = str(val).strip().upper().replace(" ", "_").replace("-", "_")
    if clean.startswith("4_"):
        clean = "FOUR_" + clean[2:]
    elif clean.startswith("2_"):
        clean = "TWO_" + clean[2:]

    if clean in allowed:
        return clean
    if clean in SYNONYM_MAP and SYNONYM_MAP[clean] in allowed:
        return SYNONYM_MAP[clean]

    # Fuzzy match attempts
    for a in allowed:
        if clean in a or a in clean:
            return a
    return fallback


# =============================================================================
# 1b. OPENING MODES (hook_type) & SPEECH-RATE WORD BUDGET
# =============================================================================
# hook_type values. "steam_new_game" stays the default (required by Trapster campaigns).
HOOK_TYPE_STEAM_NEW_GAME = "steam_new_game"
HOOK_TYPE_SO_IN_THIS_NEW_GAME = "so_in_this_new_game"
HOOK_TYPE_DIRECT = "direct"      # no formulaic opener: the first words already state the USP
HOOK_TYPE_CUSTOM = "custom"      # the script starts exactly with the creator's opening_text
HOOK_TYPES: Tuple[str, ...] = (
    HOOK_TYPE_STEAM_NEW_GAME,
    HOOK_TYPE_SO_IN_THIS_NEW_GAME,
    HOOK_TYPE_DIRECT,
    HOOK_TYPE_CUSTOM,
)

# Effective spoken rate including breathing/pauses at 1x voice speed.
WORDS_PER_SECOND_AT_1X = 2.4
DEFAULT_SPEECH_SPEED = 1.12
SPEECH_SPEED_MIN = 0.7   # ElevenLabs voice speed range
SPEECH_SPEED_MAX = 1.2
DIRECT_HOOK_MAX_WORDS = 12
DEFAULT_HOOK_MAX_WORDS = 14

_OPENER_HEAD = (
    r"(?:so\s+)?in\s+this(?:\s+brand)?\s+new"
    r"|here(?:'s|’s|s|\s+is)\s+(?:a|an)(?:\s+brand)?\s+new"
    r"|there(?:'s|’s|s|\s+is)\s+(?:a|an)(?:\s+brand)?\s+new"
    r"|check\s+out\s+this(?:\s+brand)?\s+new"
    r"|this\s+is\s+(?:a|an)(?:\s+brand)?\s+new"
    r"|imagine\s+(?:a|an)(?:\s+brand)?(?:\s+new)?"
)
# "<formula> [up to 3 words] game" at the very start of a script.
_STRIP_OPENER_RE = re.compile(r"^\s*(?:" + _OPENER_HEAD + r")\s+(?:[\w\-]+\s+){0,3}?game\b", re.IGNORECASE)
_FORMULA_START_RE = re.compile(r"^\s*[\"'“]?\s*(?:" + _OPENER_HEAD + r"|imagine|what\s+if)\b", re.IGNORECASE)

_TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
# Only used to compare an opener with a script ("Here is" == "Here's").
_COMPARE_CONTRACTIONS: Dict[str, Tuple[str, ...]] = {
    "heres": ("here", "is"), "theres": ("there", "is"), "thats": ("that", "is"),
    "whats": ("what", "is"), "whos": ("who", "is"), "wheres": ("where", "is"),
    "hows": ("how", "is"), "its": ("it", "is"), "lets": ("let", "us"),
    "youre": ("you", "are"), "theyre": ("they", "are"), "im": ("i", "am"),
    "ive": ("i", "have"), "youve": ("you", "have"), "youll": ("you", "will"),
    "dont": ("do", "not"), "doesnt": ("does", "not"), "didnt": ("did", "not"),
    "cant": ("can", "not"), "cannot": ("can", "not"), "isnt": ("is", "not"),
    "arent": ("are", "not"),
}
_CONNECTOR_WORDS = {
    "where", "that", "with", "about", "and", "but", "or", "so", "because", "while", "when",
    "if", "to", "the", "a", "an", "of", "in", "on", "for", "as", "than", "then", "until",
    "who", "which", "whose", "like", "into", "from", "your", "my", "our", "this",
}
_COMMON_STARTERS = {
    "you", "your", "the", "a", "an", "this", "these", "those", "it", "it's", "its", "every",
    "each", "one", "there", "they", "their", "we", "our", "and", "but", "so", "now", "then",
    "when", "if", "as", "all", "everyone", "everything", "nobody", "no", "some", "here",
}


def normalize_opening_text(opening_text: Any) -> str:
    """Single-line, whitespace-collapsed opening text (empty when not provided)."""
    if not opening_text:
        return ""
    return re.sub(r"\s+", " ", str(opening_text)).strip()[:300]


def resolve_hook_type(hook_type: Any, opening_text: Any = "") -> str:
    """Normalizes hook_type. Unknown values and "custom" without text use the default."""
    mode = str(hook_type or "").strip().lower()
    if mode not in HOOK_TYPES:
        return HOOK_TYPE_STEAM_NEW_GAME
    if mode == HOOK_TYPE_CUSTOM and not normalize_opening_text(opening_text):
        logger.warning("hook_type 'custom' without opening_text: using the default Steam opener.")
        return HOOK_TYPE_STEAM_NEW_GAME
    return mode


def normalize_speech_speed(speech_speed: Any) -> float:
    """Voice speed used for the word budget, clamped to the ElevenLabs range."""
    try:
        value = float(speech_speed)
    except (TypeError, ValueError):
        return DEFAULT_SPEECH_SPEED
    if value != value or value in (float("inf"), float("-inf")):
        return DEFAULT_SPEECH_SPEED
    return min(SPEECH_SPEED_MAX, max(SPEECH_SPEED_MIN, value))


def words_per_second(speech_speed: Any = DEFAULT_SPEECH_SPEED) -> float:
    return WORDS_PER_SECOND_AT_1X * normalize_speech_speed(speech_speed)


def starts_with_formula_opener(text: str) -> bool:
    """True when text opens with a formula ("Here's a new...", "So in this new...", "Imagine...")."""
    return bool(_FORMULA_START_RE.match(text or ""))


def _capitalize_first(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1:]
        if not ch.isspace() and ch not in "\"'“‘(":
            break
    return text


def _soft_lower_first(text: str) -> str:
    """Lowercase the first word only when it is a common word (keeps names and "I")."""
    m = re.match(r"\s*([A-Za-z][\w'’]*)", text)
    if m and m.group(1).lower().replace("’", "'") in _COMMON_STARTERS:
        start = m.start(1)
        return text[:start] + text[start].lower() + text[start + 1:]
    return text


def strip_formulaic_opener(text: str) -> str:
    """Removes a leading "Here's a new Steam game" / "So in this new game" style opener.

    Conservative: the opener is only removed when the rest still reads as a sentence
    ("...game; you build" / "...game. You build" / "...game where you build" /
    "...game that lets you build"). Otherwise the text is returned unchanged.
    """
    original = (text or "").strip()
    m = _STRIP_OPENER_RE.match(original)
    if not m:
        return original
    rest = original[m.end():]
    body = None
    punct = re.match(r"^\s*[;:,.!?—–-]+\s*(.*)$", rest, re.DOTALL)
    if punct:
        body = punct.group(1)
    else:
        connector = re.match(r"^\s+(?:where|in\s+which)\s+(.*)$", rest, re.IGNORECASE | re.DOTALL)
        if connector:
            body = connector.group(1)
        else:
            lets = re.match(r"^\s+that\s+(?:lets|has|makes)\s+you\s+(.*)$", rest, re.IGNORECASE | re.DOTALL)
            if lets:
                body = "you " + lets.group(1)
    if body is None or not re.search(r"\w", body):
        return original
    return _capitalize_first(body.strip())


def _canonical_tokens(text: str) -> List[Tuple[str, int, int]]:
    """(token, index of the source token, char end of the source token)."""
    out: List[Tuple[str, int, int]] = []
    for idx, m in enumerate(_TOKEN_RE.finditer(text or "")):
        tok = m.group(0).lower().replace("’", "").replace("'", "")
        for piece in _COMPARE_CONTRACTIONS.get(tok, (tok,)):
            out.append((piece, idx, m.end()))
    return out


def opening_prefix_end(text: str, opening_text: str) -> Optional[int]:
    """Char index where the opening ends in text, or None when text does not start with it.
    Comparison ignores case, punctuation and contractions ("Here is" == "Here's")."""
    op = [t for t, _, _ in _canonical_tokens(opening_text)]
    if not op:
        return None
    tx = _canonical_tokens(text)
    if len(tx) < len(op) or [t for t, _, _ in tx[:len(op)]] != op:
        return None
    # The opener must end on a whole source token ("here" must not match "here's").
    if len(tx) > len(op) and tx[len(op)][1] == tx[len(op) - 1][1]:
        return None
    return tx[len(op) - 1][2]


def starts_with_opening(text: str, opening_text: str) -> bool:
    return opening_prefix_end(text, normalize_opening_text(opening_text)) is not None


def _join_opening(opening: str, body: str) -> str:
    if re.search(r"[.!?…][\"'”’)]*$", opening):
        return f"{opening} {_capitalize_first(body)}"
    words = re.findall(r"[A-Za-z']+", opening)
    if opening.endswith((",", ";", ":", "—", "–", "-")) or (words and words[-1].lower() in _CONNECTOR_WORDS):
        return f"{opening} {_soft_lower_first(body)}"
    return f"{opening}. {_capitalize_first(body)}"


def ensure_opening_text(text: str, opening_text: str) -> str:
    """Returns text starting with opening_text verbatim (words, order and case).

    - Already present (same words, any case/punctuation): the opener words are restored
      exactly, keeping the script's own continuation.
    - Missing: the opener is prefixed with a natural join.
    """
    op = normalize_opening_text(opening_text)
    body = (text or "").strip()
    if not op:
        return body
    if not body:
        return op
    core = re.sub(r"[\s.!?;:,]+$", "", op)
    tail = op[len(core):].strip()
    end = opening_prefix_end(body, op)
    if end is None:
        return _join_opening(op, body)
    if not core:
        return body
    rest = body[end:]
    lead = re.match(r"^\s*[;:,.!?]+", rest)
    if tail and lead:
        after = rest[lead.end():].lstrip()
        if re.search(r"[.!?]", tail):
            after = _capitalize_first(after)
        return (core + tail + (" " + after if after else "")).strip()
    if tail and not rest.strip():
        return core + tail
    return core + rest


def _first_words_category_for(text: str) -> Optional[str]:
    """First-words category detected at the start of text, or None for a direct opening."""
    t = (text or "").strip().lower().replace("’", "'")
    checks = [
        (r"^so in this new\b", FirstWordsTaxonomy.SO_IN_THIS_NEW),
        (r"^in this new\b", FirstWordsTaxonomy.IN_THIS_NEW),
        (r"^here(?:'s|s| is) (?:a|an) (?:brand )?new\b", FirstWordsTaxonomy.HERE_IS_A_NEW),
        (r"^there(?:'s|s| is) (?:a|an) (?:brand )?new\b", FirstWordsTaxonomy.THERE_IS_A_NEW),
        (r"^check out this\b", FirstWordsTaxonomy.CHECK_OUT_THIS),
        (r"^this (?:is a )?new\b", FirstWordsTaxonomy.THIS_NEW),
        (r"^imagine\b", FirstWordsTaxonomy.IMAGINE_A),
        (r"^what if\b", FirstWordsTaxonomy.WHAT_IF),
        (r"^you play as\b", FirstWordsTaxonomy.YOU_PLAY_AS),
    ]
    for pattern, category in checks:
        if re.match(pattern, t):
            return category
    return None


def hook_max_words(hook_type: Any = HOOK_TYPE_STEAM_NEW_GAME, opening_text: Any = "") -> int:
    mode = resolve_hook_type(hook_type, opening_text)
    if mode == HOOK_TYPE_DIRECT:
        return DIRECT_HOOK_MAX_WORDS
    if mode == HOOK_TYPE_CUSTOM:
        return max(DEFAULT_HOOK_MAX_WORDS, len(normalize_opening_text(opening_text).split()) + 8)
    return DEFAULT_HOOK_MAX_WORDS


# =============================================================================
# 2. PYDANTIC DATA STRUCTURES
# =============================================================================

class FirstWordsComponent(BaseModel):
    category: str = FirstWordsTaxonomy.HERE_IS_A_NEW
    text: str = "Here's a new"
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration: Optional[float] = None


class GameGenreComponent(BaseModel):
    category: str = GameGenreTaxonomy.COOP_GAME
    text: str = "co-op game"
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration: Optional[float] = None


class FirstPunchComponent(BaseModel):
    category: str = FirstPunchTaxonomy.ACTION
    text: str = ""
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration: Optional[float] = None


class SecondPunchComponent(BaseModel):
    category: str = SecondPunchTaxonomy.NONE
    text: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration: Optional[float] = None


class HookStructure(BaseModel):
    text: str
    first_words: FirstWordsComponent
    game_genre: GameGenreComponent
    first_punch: FirstPunchComponent
    second_punch: Optional[SecondPunchComponent] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration: Optional[float] = None
    estimated_duration_seconds: float = 3.0


class OpenLoopStructure(BaseModel):
    type: str = OpenLoopTaxonomy.NONE
    id: Optional[str] = None
    resolves: Optional[str] = None


class PhraseClauseStructure(BaseModel):
    id: str
    text: str
    delimiter_trigger: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration: Optional[float] = None
    word_count: int = 0


class SentenceStructure(BaseModel):
    id: str
    text: str
    block: str = NarrativeBlockTaxonomy.SETUP
    sentence_type: str = SentenceTypeTaxonomy.ACTION
    attention_device: str = AttentionDeviceTaxonomy.NONE
    information_function: str = InformationFunctionTaxonomy.MECHANIC
    open_loop: OpenLoopStructure = Field(default_factory=OpenLoopStructure)
    escalation_level: int = 1
    visual_beat: Optional[str] = None
    clauses: List[PhraseClauseStructure] = Field(default_factory=list)
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration: Optional[float] = None


class EndingStructure(BaseModel):
    payoff_type: str = "THREAT_REVEAL"
    cta_type: str = "STEAM_WISHLIST"


class RetentionScript(BaseModel):
    hook: HookStructure
    sentences: List[SentenceStructure] = Field(default_factory=list)
    ending: EndingStructure = Field(default_factory=EndingStructure)
    clean_script_text: str = ""
    word_count: int = 0
    estimated_duration: float = 0.0
    validation_warnings: List[str] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return self.model_dump()


# =============================================================================
# 3. 20 CORE RETENTION PRINCIPLES & PROMPT BUILDER
# =============================================================================

RETENTION_CORE_PRINCIPLES = """
=== 20 CORE RETENTION PRINCIPLES (MANDATORY INSTRUCTION) ===
1. The first 2-3 seconds must immediately communicate what the game is and provide a compelling reason to keep watching.
2. Every sentence must earn its place. It must introduce meaningful information, create curiosity, escalate the situation, reveal something, increase stakes, or deliver a payoff.
3. Never use filler transitions such as "and then", "after that", "in this game", or "you can also" unless they introduce meaningful new information.
4. Every sentence should create at least one of the following whenever naturally possible:
   - A new question.
   - An expectation.
   - A contrast.
   - A consequence.
   - A new piece of interesting information.
   - A reason to hear the next sentence.
5. Do not answer a curiosity loop immediately if delaying the answer increases curiosity without reducing clarity.
6. Escalate throughout the script. The second half should contain information that is more interesting, surprising, dangerous, useful, or emotionally compelling than the initial setup.
7. Do not reveal the strongest mechanic, twist, or payoff at the beginning unless it is necessary to understand the game or is itself the strongest hook.
8. Use concrete language. Prefer actions, consequences, and specific mechanics over generic adjectives.
9. Every major verbal beat should have a corresponding visual beat whenever suitable footage is available.
10. Never fabricate mechanics, events, features, characters, story elements, or gameplay scenarios.
11. The ending should deliver a meaningful payoff or resolve an earlier curiosity loop before introducing the CTA whenever the available content permits it.
12. Do not make every sentence sound artificially dramatic. Vary sentence length, rhythm, and narrative intensity to create natural narration.
13. Avoid repetitive sentence structures, repeated attention devices, and unnecessary repetition of words or information.
14. The script should feel like one coherent, progressively interesting narrative, not a list of game features.
15. Do not explain why the game is fun. Demonstrate why it is interesting through what happens in the game.
16. Prioritize clarity and authenticity over exaggerated claims or artificial suspense.
17. Do not introduce curiosity gaps that cannot be satisfied by the available game information or footage.
18. Do not force a cliffhanger at the end if a strong payoff or a natural ending is more appropriate.
19. Avoid repeating the hook in the setup. Each new sentence must move the script forward.
20. Optimize the entire retention curve, not just the opening seconds.
"""


# Fragments of the default prompt that change with the opening mode.
_PROMPT_FIRST_WORDS = "1. First Words: Formulaic opening phrase (category: HERE_IS_A_NEW, SO_IN_THIS_NEW, IN_THIS_NEW, THIS_NEW, IMAGINE_A, WHAT_IF)."
_PROMPT_GENRE = "2. Game Genre: Presentation of the game (category:"
_PROMPT_HOOK_TARGET = "Target hook spoken duration: 3.0 - 5.0 seconds (10 to 14 words MAX, including the opening prefix; MUST NEVER exceed 14 words)."
_PROMPT_USP_POSITION = "- Put that USP in the FIRST spoken sentence, immediately after the required opening phrase."
_PROMPT_HOOK_LIMIT = "breaks the 14-word limit"
_PROMPT_MARS_EXAMPLE = "a clearer hook is \"Here's a new Steam game where you blast tunnels through Mars.\""
_PROMPT_EXAMPLE_TEXT = "\"text\": \"Here's a new co-op game where you weld leaks while monsters hunt.\","
_PROMPT_EXAMPLE_FIRST_WORDS = "\"first_words\": { \"category\": \"HERE_IS_A_NEW\", \"text\": \"Here's a new\" },"
_PROMPT_EXAMPLE_GENRE = "\"game_genre\": { \"category\": \"COOP_GAME\", \"text\": \"co-op game\" },"
_PROMPT_EXAMPLE_FIRST_PUNCH = "\"first_punch\": { \"category\": \"COOPERATION\", \"text\": \"where you weld leaks\" },"
_PROMPT_SECTION_2 = "=== 2. RETENTION NARRATIVE BLOCKS"
_FORBIDDEN_OPENERS = (
    '"Here\'s a new...", "There\'s a new...", "So in this new...", "In this new...", '
    '"This is a new...", "Check out this...", "Imagine...", "What if..."'
)


def _json_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _apply_opening_mode_to_prompt(prompt: str, hook_mode: str, opening: str) -> str:
    """Adapts the modular hook section and the JSON example to the selected opening mode.
    The default steam_new_game prompt is left untouched."""
    if hook_mode == HOOK_TYPE_SO_IN_THIS_NEW_GAME:
        example = "So in this new co-op game, you weld leaks while monsters hunt."
        block = (
            "OPENING MODE: \"SO IN THIS NEW GAME\"\n"
            "- First Words MUST use category SO_IN_THIS_NEW: begin the hook with \"So in this new\" followed by the genre "
            "and immediately by the strongest USP (e.g. \"So in this new co-op game, you...\"). Phrasing example only.\n"
            "- Do not stack another opener (\"Here's a new...\") before or after it.\n\n"
        )
        prompt = prompt.replace(_PROMPT_EXAMPLE_TEXT, f"\"text\": {_json_str(example)},")
        prompt = prompt.replace(_PROMPT_EXAMPLE_FIRST_WORDS, "\"first_words\": { \"category\": \"SO_IN_THIS_NEW\", \"text\": \"So in this new\" },")
        prompt = prompt.replace(_PROMPT_EXAMPLE_FIRST_PUNCH, "\"first_punch\": { \"category\": \"COOPERATION\", \"text\": \"you weld leaks\" },")
        return prompt.replace(_PROMPT_SECTION_2, block + _PROMPT_SECTION_2, 1)

    if hook_mode == HOOK_TYPE_DIRECT:
        example = "You and your friends weld leaks while monsters hunt you."
        block = (
            "OPENING MODE: DIRECT (NO FORMULAIC OPENER) - OVERRIDES ANY OPENING SEEN IN REFERENCES\n"
            f"- Do NOT start with a formulaic opener: no {_FORBIDDEN_OPENERS} and no greeting or channel intro.\n"
            "- The first 3-5 spoken words must already show the strongest USP or payoff: the action, fantasy or consequence "
            "the player wants (e.g. \"You fly a submarine made of trash...\" - phrasing example only, never invent mechanics).\n"
            f"- Hook sentence: {DIRECT_HOOK_MAX_WORDS} words MAX. Name the genre only if it strengthens the promise.\n"
            "- In the JSON hook set first_words.category = \"CUSTOM\" and first_words.text = \"\"; set game_genre.text to the exact "
            "genre words spoken in the hook, or \"\" if the hook does not name a genre.\n\n"
        )
        prompt = prompt.replace(
            _PROMPT_FIRST_WORDS,
            "1. First Words: NONE in this mode. Do not use any formulaic opening phrase; the hook starts directly with the USP (first_words.category = CUSTOM, first_words.text = \"\").",
        )
        prompt = prompt.replace(_PROMPT_GENRE, "2. Game Genre (optional in this mode): Presentation of the game (category:")
        prompt = prompt.replace(
            _PROMPT_HOOK_TARGET,
            f"Target hook spoken duration: 2.0 - 4.0 seconds (6 to {DIRECT_HOOK_MAX_WORDS} words MAX; MUST NEVER exceed {DIRECT_HOOK_MAX_WORDS} words).",
        )
        prompt = prompt.replace(
            _PROMPT_USP_POSITION,
            "- Put that USP in the FIRST 3-5 spoken words of the FIRST sentence: there is no opening phrase in this mode.",
        )
        prompt = prompt.replace(_PROMPT_HOOK_LIMIT, f"breaks the {DIRECT_HOOK_MAX_WORDS}-word limit")
        prompt = prompt.replace(_PROMPT_MARS_EXAMPLE, "a clearer hook is \"You blast tunnels through Mars.\"")
        prompt = prompt.replace(_PROMPT_EXAMPLE_TEXT, f"\"text\": {_json_str(example)},")
        prompt = prompt.replace(_PROMPT_EXAMPLE_FIRST_WORDS, "\"first_words\": { \"category\": \"CUSTOM\", \"text\": \"\" },")
        prompt = prompt.replace(_PROMPT_EXAMPLE_GENRE, "\"game_genre\": { \"category\": \"COOP_GAME\", \"text\": \"\" },")
        prompt = prompt.replace(_PROMPT_EXAMPLE_FIRST_PUNCH, "\"first_punch\": { \"category\": \"COOPERATION\", \"text\": \"You and your friends weld leaks\" },")
        prompt = prompt.replace("\"second_punch\": { \"category\": \"THREAT\", \"text\": \"while monsters hunt\" },",
                                "\"second_punch\": { \"category\": \"THREAT\", \"text\": \"while monsters hunt you\" },")
        return prompt.replace(_PROMPT_SECTION_2, block + _PROMPT_SECTION_2, 1)

    if hook_mode == HOOK_TYPE_CUSTOM:
        op_words = len(opening.split())
        limit = hook_max_words(HOOK_TYPE_CUSTOM, opening)
        core = re.sub(r"[\s.!?;:,]+$", "", opening)
        example = ensure_opening_text("You and your friends weld leaks while monsters hunt.", opening)
        block = (
            "OPENING MODE: CUSTOM OPENING TEXT (MANDATORY) - OVERRIDES ANY OTHER OPENING RULE OR REFERENCE\n"
            f"- The script (hook.text and sentence_01.text) MUST begin EXACTLY with this opening text, verbatim: {_json_str(opening)}\n"
            "- Same words, same order. Do not paraphrase, translate, shorten, repeat or quote it, and do not add another "
            "formulaic opener before or after it.\n"
            "- Then continue naturally with the strongest USP, so the opening and the promise read as one smooth spoken line.\n"
            f"- Word limits include the opening text ({op_words} words): hook {limit} words MAX and the total word target below.\n"
            f"- In the JSON hook set first_words.category = \"CUSTOM\" and first_words.text = {_json_str(core)}.\n\n"
        )
        prompt = prompt.replace(
            _PROMPT_FIRST_WORDS,
            f"1. First Words: the creator's REQUIRED opening text, verbatim (category: CUSTOM): {_json_str(opening)}",
        )
        prompt = prompt.replace(
            _PROMPT_HOOK_TARGET,
            f"Target hook spoken duration: 3.0 - 5.0 seconds (10 to {limit} words MAX, including the required opening text; MUST NEVER exceed {limit} words).",
        )
        prompt = prompt.replace(_PROMPT_HOOK_LIMIT, f"breaks the {limit}-word limit")
        prompt = prompt.replace(
            _PROMPT_MARS_EXAMPLE,
            "a clearer hook is " + _json_str(ensure_opening_text("You blast tunnels through Mars.", opening)),
        )
        prompt = prompt.replace(_PROMPT_EXAMPLE_TEXT, f"\"text\": {_json_str(example)},")
        prompt = prompt.replace(_PROMPT_EXAMPLE_FIRST_WORDS, f"\"first_words\": {{ \"category\": \"CUSTOM\", \"text\": {_json_str(core)} }},")
        prompt = prompt.replace(_PROMPT_EXAMPLE_GENRE, "\"game_genre\": { \"category\": \"COOP_GAME\", \"text\": \"\" },")
        prompt = prompt.replace(_PROMPT_EXAMPLE_FIRST_PUNCH, "\"first_punch\": { \"category\": \"COOPERATION\", \"text\": \"you and your friends weld leaks\" },")
        return prompt.replace(_PROMPT_SECTION_2, block + _PROMPT_SECTION_2, 1)

    return prompt


def build_retention_script_prompt(
    game_name: str,
    target_duration: float = 30.0,
    tone: str = "viral_hype",
    game_info: str = "",
    briefing_text: str = "",
    viral_references: str = "",
    script_mode: str = "normal",
    game_genre: str = "",
    hook_type: str = HOOK_TYPE_STEAM_NEW_GAME,
    opening_text: str = "",
    speech_speed: float = DEFAULT_SPEECH_SPEED,
) -> str:
    """
    Constructs the high-retention structured prompt enforcing Hook modularity,
    retention architecture, sentence-level tagging, and open loop tracking.

    hook_type: "steam_new_game" (default formula), "so_in_this_new_game",
    "direct" (no formulaic opener) or "custom" (starts exactly with opening_text).
    speech_speed: the voice speed that will be used; it sets the word budget.
    """
    speed = normalize_speech_speed(speech_speed)
    opening = normalize_opening_text(opening_text)
    hook_mode = resolve_hook_type(hook_type, opening)
    if script_mode == "caveman" and hook_mode != HOOK_TYPE_CUSTOM:
        # The caveman hook formula replaces the opener (a custom opener still leads).
        hook_mode = HOOK_TYPE_STEAM_NEW_GAME
    # Effective spoken rate includes breathing/pauses: 2.4 words/s at 1x, scaled by the
    # voice speed. At 1.12x this yields ~80 words / 30s; allow shorter phrasing.
    max_words = max(1, int(target_duration * WORDS_PER_SECOND_AT_1X * speed))
    min_words = max(1, int(max_words * 0.875))
    hook_limit = hook_max_words(hook_mode, opening)
    default_hook_line = (
        f"- Target Hook Sentence: 10 to 14 words MAX, including the opening prefix (target at most 5.0 seconds at {speed:g}x speed). "
        "End it with a period, exclamation mark or question mark before sentence two."
    )
    if hook_mode == HOOK_TYPE_DIRECT:
        hook_line = (
            f"- Target Hook Sentence: 6 to {DIRECT_HOOK_MAX_WORDS} words MAX, with NO opening prefix (target at most 4.0 seconds at {speed:g}x speed). "
            "End it with a period, exclamation mark or question mark before sentence two."
        )
    elif hook_mode == HOOK_TYPE_CUSTOM:
        hook_line = (
            f"- Target Hook Sentence: 10 to {hook_limit} words MAX, including the required opening text (target at most 5.0 seconds at {speed:g}x speed). "
            "End it with a period, exclamation mark or question mark before sentence two."
        )
    else:
        hook_line = default_hook_line

    prompt = f"""You are an elite viral gaming copywriter and retention director for TikTok, Reels, and YouTube Shorts (Trapster).
Your objective is to generate a structured, viral short-form promo script in natural spoken English for '{game_name}'.

{RETENTION_CORE_PRINCIPLES}

SIMPLE VERBS THROUGHOUT THE VIDEO: Use common, concrete action verbs in the hook AND every later explanation of a mechanic: build, dig, drive, shoot, jump, carry, fix, hide, fight, find or escape. Choose the simplest verb that accurately describes what the player does, then state its object and any essential context. Avoid fancy synonyms, jargon, ambiguous slang and figurative verbs when a plain verb is clearer. Describe each mechanic so a listener understands the action on the first hearing; prioritize clarity over sounding intense. This vocabulary rule applies to every script mode and the entire narration, taking precedence over stylistic requests for more intense verbs.

=== 1. MODULAR HOOK CONSTRUCTION (0 - 5.0s) ===
Construct the opening sentence by naturally combining 4 distinct components:
1. First Words: Formulaic opening phrase (category: HERE_IS_A_NEW, SO_IN_THIS_NEW, IN_THIS_NEW, THIS_NEW, IMAGINE_A, WHAT_IF).
2. Game Genre: Presentation of the game (category: STEAM_GAME, NEW_STEAM_GAME, COOP_GAME, FOUR_PLAYER_COOP, HORROR_GAME, MULTIPLAYER_HORROR, SURVIVAL_GAME, OPEN_WORLD_GAME, SIMULATION_GAME).
3. First Punch: Concrete gameplay fantasy, action, or core premise (category: ACTION, ROLE, OBJECTIVE, SURVIVAL, EXPLORATION, COOPERATION, CREATION, DISCOVERY).
4. Second Punch: Adds contrast, complication, danger, or twist (category: CONTRAST, THREAT, TWIST, COMPLICATION, MYSTERY, STAKES, UNEXPECTED_MECHANIC, WORLD_BUILDING, NONE).
Do NOT mechanically concatenate them. Adapt connecting words, prepositions, and grammar so the hook sounds 100% natural when spoken aloud.
Target hook spoken duration: 3.0 - 5.0 seconds (10 to 14 words MAX, including the opening prefix; MUST NEVER exceed 14 words). Keep vocabulary short and pauses minimal. Word counts are estimates; actual spoken timing depends on the selected voice.

HOOK PRIORITY: STRONGEST USP -> PLAYER FANTASY -> SCROLL STOP
- Before writing, silently identify supported USPs from the Steam information and footage notes. Rank them by appeal to this game's target player: distinctive playable action, desirable fantasy, immediate visual clarity, and surprise or meaningful stakes. Select ONE strongest USP, not the first listed feature or a generic genre description.
- Put that USP in the FIRST spoken sentence, immediately after the required opening phrase. Never save the most attractive promise for sentence two, a later reveal or the CTA.
- Translate it into a concrete PLAYER FANTASY: what YOU get to do, become, create, control, risk or survive. Make the player picture themselves performing that action and think "I want to play this". Use a vivid action and tangible object or situation instead of "unique mechanics".
- Maximize SCROLL STOP with the game's real unexpected combination, power fantasy, playful chaos, challenge or consequence. Choose the angle that fits its actual appeal; do not force danger or chaos onto a cozy game.
- Privately compare a few concise hook candidates and choose the clearest, most desirable player promise with the strongest immediate curiosity. Do not output candidates, rankings or reasoning; return only the requested JSON.
- Keep one dominant promise. A second punch is optional: use category NONE if another clause dilutes the USP or breaks the 14-word limit. Do not sacrifice the strongest USP to mechanically include every component.
- CLARITY OVER COMPRESSION: The hook length limit is not an invitation to cram several ideas into fewer words. Use natural, complete spoken phrasing with a clear action, object and player fantasy that a listener understands on the first hearing. Never remove essential context or connecting words just to fit more information. If the idea does not fit, simplify it, choose one action, or move supporting details to sentence two; keep the existing word limit.
- Avoid compressed, ambiguous phrases such as "where you blast Martian earth": the listener cannot clearly picture the action or why it is desirable. If the supplied facts describe digging tunnels on Mars with explosives, a clearer hook is "Here's a new Steam game where you blast tunnels through Mars." This is a phrasing example only; never invent that mechanic for another game. Before finalizing, check that a player can immediately explain what they will do and why they want to do it.
- Ground every claim in supplied game facts. Never invent features, multiplayer modes, stakes, rewards or novelty/exclusivity. References supply rhythm, not facts or an opening that overrides this USP priority. Avoid empty hype, vague teasers and misleading clickbait.
- Sentence two must develop that same promise with concrete gameplay or a consequence, creating a natural reason to keep watching.

=== 2. RETENTION NARRATIVE BLOCKS (AFTER HOOK) ===
Organize subsequent sentences into progressive blocks:
- SETUP: Establishes context, player roles, or main mechanic without slow explanations. (For multiplayer games: delegate distinct tasks to friends!).
- FIRST_ESCALATION: Introduces an escalating factor, new problem, consequence, or mechanic that raises stakes.
- SECOND_ESCALATION: Elevates intensity, shows severe consequences, or introduces a surprising dynamic.
- PAYOFF: Delivers resolution or reward to curiosity before the CTA.
- CTA: Punchy call to action (e.g. 'This is {game_name} on Steam. Wishlist now!').

=== 3. SENTENCE-LEVEL RETENTION LABELS ===
For every sentence (including Sentence #1 which is the Hook):
- id: 'sentence_01', 'sentence_02', etc.
- text: exact spoken text (NO timestamps, NO brackets, NO double dashes, NO ellipses).
- block: HOOK | SETUP | FIRST_ESCALATION | SECOND_ESCALATION | PAYOFF | CTA
- sentence_type: HOOK | SETUP | ACTION | TWIST | CONSEQUENCE | REVEAL | QUESTION | OPEN_LOOP | ESCALATION | PAYOFF | CTA
- attention_device: BUT | THEN | UNTIL | EXCEPT | BECAUSE | SUDDENLY | WORST_PART | SECRET | WARNING | QUESTION | CONTRAST | REVEAL | NONE
- information_function: WORLD | CHARACTER | MECHANIC | OBJECTIVE | THREAT | TWIST | CONSEQUENCE | REWARD
- open_loop:
    * type: NONE | INFORMATION_GAP | THREAT | MYSTERY | CONSEQUENCE | UNRESOLVED_ACTION | REVEAL_ANTICIPATION | ESCALATION
    * id: unique id e.g. 'loop_01' (if opening a curiosity loop, otherwise null)
    * resolves: id of a previously opened loop this sentence resolves e.g. 'loop_01' (otherwise null)
- escalation_level: 1, 2, 3, etc.
- visual_beat: specific gameplay action to display for this sentence.

=== 4. MANDATORY LINGUISTIC PROFILE & VOCABULARY RULES (BUDDY HYPE) ===
CRITICAL: You must strictly adhere to this exact linguistic profile, tone of voice, and vocabulary:

1. TONE & REGISTER ("BUDDY HYPE" / CO-OP CAMARADERIE):
   - Voice: Speak like an excited gamer friend in a Discord call or on TikTok who just found an insane game and is hyped to show it to their friend.
   - 0% Corporate PR: Never sound like a PR press release, television commercial, Kickstarter pitch, game reviewer, or game designer.
   - Natural Spoken English: Always use natural spoken contractions ("Here's", "you'll", "it's", "don't", "there's", "you're", "can't").
   - Multiplayer Inclusivity: Address the viewer and their group directly as "you and your friends" or "you and your squad".
   - Chaotic Role Delegation: Use playful, concrete role division (e.g. "one of you pilots the sub while your friends scramble to weld leaks", "one person flies while everyone else panics").
   - Playful Stakes: Ground tension in funny, desperate emergent gameplay chaos ("everything falls apart in seconds", "one mistake wipes the entire run", "you will inevitably crash"), never solemn or melodramatic tragedy.

2. MANDATORY KINETIC ACTION VERBS:
   - Use visceral, physical verbs of manual labor, physics, movement, and tangible danger:
     build, rebuild, hammer, weld, scavenge, loot, drive, steer, paddle, slap, smash, dodge, parry, sneak, toss, fling, drag, fix, repair, clutch, wipe out, blow up, survive.
   - Strictly avoid weak, passive, or cognitive verbs ("consider", "appreciate", "understand", "showcases", "features", "offers").

3. CONCRETE & TACTILE NOUNS (ZERO ABSTRACTION):
   - Ground every statement in physical objects, hazards, tools, and real-world consequences.
   - NEVER use abstract game design jargon: do NOT say "gameplay loop", "progression system", "mechanics", "procedural generation", "lore", or "level design".
   - Use tactile, physical nouns: rust, scrap, planks, cement blocks, leaks, fires, torpedoes, sharks, blizzard, monsters, alarms, radiation, shotgun, tape.

4. STRICTLY PROHIBITED WORDS & MARKETING CLICHÉS (ZERO CORPORATE BS):
   - BANNED HYPE ADJECTIVES: "amazing", "incredible", "unbelievable", "epic", "revolutionary", "masterpiece", "breathtaking", "stunning", "unique experience", "thrilling".
   - BANNED MARKETING CLICHÉS: "delve into", "immerse yourself", "experience the thrill", "embark on a journey", "explore the vast world", "testament to", "utilize", "boasts", "features an array of", "in this digital realm".
   - Golden Rule: Never tell the audience a game is "fun", "epic", or "amazing"—SHOW the chaotic mechanic or consequence that makes it fun.

5. SPOKEN FLOW CONNECTORS:
   - Use natural spoken connectors: "But", "Until", "Except", "The problem is", "The catch?", "The worst part?", "And if that's not enough".
   - Never use formal written transitions like "Furthermore", "Moreover", "In addition", or "However".

=== DURATION & WORD COUNT ===
- Target Total Words: {min_words} to {max_words} words for approximately {target_duration:g}s at {speed:g}x speed (estimated {WORDS_PER_SECOND_AT_1X * speed:.2f} words/s including natural pauses).
{hook_line}
- Tone: {tone}

=== GAME INFORMATION ===
Game Name: {game_name}
Game Description & Steam Details:
{game_info}

Creator Briefing & Footage Notes:
{briefing_text}

=== VIRAL REFERENCE EXAMPLES (STYLE & PACING INSPIRATION) ===
{viral_references}

=== REQUIRED OUTPUT FORMAT (STRICT JSON ONLY) ===
You must respond ONLY with a valid JSON object adhering to this exact schema (no markdown wrap, no conversational filler):
{{
  "hook": {{
    "text": "Here's a new co-op game where you weld leaks while monsters hunt.",
    "first_words": {{ "category": "HERE_IS_A_NEW", "text": "Here's a new" }},
    "game_genre": {{ "category": "COOP_GAME", "text": "co-op game" }},
    "first_punch": {{ "category": "COOPERATION", "text": "where you weld leaks" }},
    "second_punch": {{ "category": "THREAT", "text": "while monsters hunt" }},
    "estimated_duration_seconds": 4.2
  }},
  "sentences": [
    {{
      "id": "sentence_01",
      "text": "Here's a new co-op game where you weld leaks while monsters hunt.",
      "block": "HOOK",
      "sentence_type": "HOOK",
      "attention_device": "CONTRAST",
      "information_function": "THREAT",
      "open_loop": {{ "type": "THREAT", "id": "loop_01", "resolves": null }},
      "escalation_level": 1,
      "visual_beat": "Squad exploring dark space station with creature in shadows"
    }},
    {{
      "id": "sentence_02",
      "text": "One of you monitors the radar while your friends fix the power generators.",
      "block": "SETUP",
      "sentence_type": "ACTION",
      "attention_device": "NONE",
      "information_function": "MECHANIC",
      "open_loop": {{ "type": "NONE", "id": null, "resolves": null }},
      "escalation_level": 1,
      "visual_beat": "Player at radar screen communicating with team"
    }}
  ],
  "ending": {{
    "payoff_type": "THREAT_REVEAL",
    "cta_type": "STEAM_WISHLIST"
  }}
}}
"""
    if script_mode != "caveman":
        prompt = _apply_opening_mode_to_prompt(prompt, hook_mode, opening)
    if script_mode == "caveman":
        hook_rules = """=== 1. CAVEMAN HOOK (0 - 5.0s) ===
The exact opening formula is: "New [game genre] game explained by caveman."
Replace [game genre] with a short, accurate English genre from the game information
or the supplied genre. Never speak the brackets. Never add a twist to this hook.
Use this exact hook as sentence_01. In the existing JSON hook schema set:
first_words.text = "New", game_genre.text = "[genre] game",
first_punch.text = "explained by caveman", second_punch = null.
Keep existing category enums. Put the first gameplay surprise in sentence_02.

"""
        if hook_mode == HOOK_TYPE_CUSTOM:
            hook_rules += (
                "REQUIRED OPENING TEXT: the script MUST begin EXACTLY with this creator-provided text, verbatim, "
                f"before the caveman formula: {_json_str(opening)}\n\n"
            )
        start = prompt.index("=== 1. MODULAR HOOK")
        end = prompt.index("=== 2. RETENTION")
        prompt = prompt[:start] + hook_rules + prompt[end:]
        start = prompt.index("=== 4. MANDATORY LINGUISTIC")
        end = prompt.index("=== DURATION & WORD COUNT ===")
        caveman_rules = """=== 4. CAVEMAN LINGUISTIC PROFILE ===
Speak English as a funny caveman explaining the real game's unique selling points.
Use very short, simple sentences: usually 2-7 words. One concrete idea per sentence.
Use basic words, tangible nouns, direct action verbs and playful primitive phrasing.
NO conjunctions inside sentences: no and, but, or, because, while, until, although,
if, so, which or that linking clauses. Split connected ideas into separate sentences.
A sentence MAY start with a simple connector such as "But", "Then", "And" or "So".
Examples of phrasing only: "Me build boat. Boat tiny. But sea monster big."
Never copy those example mechanics unless the actual game supports them.
Keep the SAME retention architecture: setup, escalating problems, surprising USP,
payoff, game-name reveal and short Steam CTA. Every sentence must earn the next:
open a concrete question or consequence, delay its answer, escalate, then resolve it.
Do not turn the script into a disconnected list of features. Show cause and effect
across separate sentences. Ground every USP in Steam information or footage notes.
Avoid corporate hype, abstract game-design jargon and invented features.
Keep sentence-level labels, open-loop IDs, resolutions and visual beats in the JSON.
The hook formula overrides modular-hook rules and reference openings. This profile
overrides Buddy Hype, complex connectors and compound sentences in all references.

"""
        prompt = prompt[:start] + caveman_rules + prompt[end:]
        prompt = prompt.replace(
            hook_line,
            "- Target Hook Sentence: use only the exact caveman formula, ending with a period."
            if hook_mode != HOOK_TYPE_CUSTOM else
            "- Target Hook Sentence: the required opening text, then the exact caveman formula, ending with a period.",
        )
        prompt += f"\n\nGenre supplied by creator: {game_genre.strip() or 'Infer a short accurate genre from the game information.'}\n"
        prompt += "The JSON above illustrates field names only. Replace ALL example text with caveman phrasing and the exact caveman hook."
    return prompt.strip()


# =============================================================================
# 4. VALIDATION ENGINE
# =============================================================================

def validate_retention_script(
    data: Dict[str, Any],
    hook_type: str = HOOK_TYPE_STEAM_NEW_GAME,
    opening_text: str = "",
) -> Tuple[bool, List[str], List[str]]:
    """
    Validates a structured retention script dictionary.
    Returns: (is_valid, fatal_errors, quality_warnings)
    - Fatal errors invalidate the structure and trigger recovery.
    - Quality warnings are diagnostic insights for future analytics.
    hook_type "direct" accepts hooks without a formulaic opener (no first_words/genre
    required); "custom" checks that the script starts with opening_text.
    """
    errors: List[str] = []
    warnings: List[str] = []
    opening = normalize_opening_text(opening_text)
    hook_mode = resolve_hook_type(hook_type, opening)
    direct_like = hook_mode in (HOOK_TYPE_DIRECT, HOOK_TYPE_CUSTOM)
    max_hook_words = hook_max_words(hook_mode, opening)

    if not isinstance(data, dict):
        return False, ["Script data is not a valid dictionary."], []

    # 1. Hook Validation
    hook = data.get("hook")
    if not isinstance(hook, dict):
        errors.append("Missing 'hook' dictionary in script data.")
    else:
        hook_text = (hook.get("text") or "").strip()
        if not hook_text:
            errors.append("Hook 'text' is empty.")
        else:
            w_count = len(hook_text.split())
            if w_count < 5:
                warnings.append(f"Hook text is very short ({w_count} words).")
            elif w_count > max_hook_words:
                warnings.append(f"Hook text exceeds recommended maximum ({w_count} words).")
            if hook_mode == HOOK_TYPE_DIRECT and starts_with_formula_opener(hook_text):
                warnings.append("Direct opening selected but the hook starts with a formulaic opener.")

        fw = hook.get("first_words")
        if not direct_like and (not isinstance(fw, dict) or not fw.get("text")):
            warnings.append("Hook missing explicit 'first_words' component.")

        gg = hook.get("game_genre")
        if not direct_like and (not isinstance(gg, dict) or not gg.get("text")):
            warnings.append("Hook missing explicit 'game_genre' component.")

        fp = hook.get("first_punch")
        if not isinstance(fp, dict) or not fp.get("text"):
            warnings.append("Hook missing explicit 'first_punch' component.")

    # 2. Sentences Validation
    sentences = data.get("sentences")
    if not isinstance(sentences, list) or len(sentences) == 0:
        errors.append("Missing or empty 'sentences' list.")
    else:
        seen_sentence_ids: Set[str] = set()
        seen_loop_ids: Set[str] = set()
        resolved_loop_ids: Set[str] = set()

        for idx, s in enumerate(sentences):
            if not isinstance(s, dict):
                errors.append(f"Sentence at index {idx} is not a dictionary.")
                continue

            sid = s.get("id") or f"sentence_{idx+1:02d}"
            if sid in seen_sentence_ids:
                warnings.append(f"Duplicate sentence id '{sid}'.")
            seen_sentence_ids.add(sid)

            stext = (s.get("text") or "").strip()
            if not stext:
                errors.append(f"Sentence '{sid}' text is empty.")

            # Validate open loops
            oloop = s.get("open_loop")
            if isinstance(oloop, dict):
                lid = oloop.get("id")
                if lid:
                    if lid in seen_loop_ids:
                        warnings.append(f"Open loop id '{lid}' reused multiple times.")
                    seen_loop_ids.add(lid)

                resolves_id = oloop.get("resolves")
                if resolves_id:
                    if resolves_id not in seen_loop_ids:
                        warnings.append(f"Sentence '{sid}' resolves loop '{resolves_id}' before it was declared.")
                    else:
                        resolved_loop_ids.add(resolves_id)

        # Check for lingering open loops
        unresolved = seen_loop_ids - resolved_loop_ids
        if unresolved:
            warnings.append(f"Curiosity loop(s) left unresolved in script: {list(unresolved)}.")

    if hook_mode == HOOK_TYPE_CUSTOM:
        first_text = ""
        if isinstance(sentences, list) and sentences and isinstance(sentences[0], dict):
            first_text = sentences[0].get("text") or ""
        if not first_text and isinstance(hook, dict):
            first_text = hook.get("text") or ""
        if first_text and not starts_with_opening(first_text, opening):
            warnings.append("Script does not start with the required opening text.")

    is_valid = len(errors) == 0
    return is_valid, errors, warnings


# =============================================================================
# 5. CLAUSE SEGMENTATION & TIMING ALIGNMENT ENGINE
# =============================================================================

def split_sentence_into_clauses(text: str, sentence_id: str = "s") -> List[PhraseClauseStructure]:
    """
    Deconstructs a narrative sentence into micro-phrases/clauses.
    Delimiters include:
    - Punctuation: ., ,, ;, :, !, ?, em-dashes (—, –), double hyphens (--), spaced hyphens ( - )
    - Conjunctions / connectives: 'and', 'but', 'while', 'because', 'although', 'though', 'since',
      'until', 'unless', 'or', 'so', 'whereas', and Spanish equivalents ('y', 'pero', 'mientras', 'porque', 'o').
    Preserves exact phrase text and tracks the triggering delimiter.
    """
    if not text or not text.strip():
        return []

    raw = text.strip()
    pattern = re.compile(
        r'(?P<punct>[,.;:!?—–]+(?:\s*(?:and|but|while|or|so|because|although|though|since|until|unless|y|pero|mientras|porque)\b)?)'
        r'|(?P<subconj>\b(?:while|but|because|although|though|since|until|unless|mientras|pero|porque)\b)'
        r'|(?P<coord>\b(?:and|or|so|y|o)\b)',
        re.IGNORECASE
    )

    segments: List[Tuple[str, Optional[str]]] = []
    last_idx = 0
    pending_trigger: Optional[str] = None

    for match in pattern.finditer(raw):
        start, end = match.span()
        chunk_before = raw[last_idx:start].strip()
        matched_str = match.group(0).strip()

        # Coordinate conjunctions (and, or, so, y, o) without preceding punctuation:
        # Avoid breaking short noun/adjective pairs (e.g. "you and me", "wood and stone", "hide and seek")
        if match.group('coord') and not match.group('punct'):
            words_before = chunk_before.split()
            rest_after = raw[end:].strip()
            words_after = rest_after.split()
            if len(words_before) < 2 or len(words_after) < 2:
                continue

        if chunk_before:
            segments.append((chunk_before, pending_trigger))

        if match.group('subconj') or match.group('coord'):
            pending_trigger = matched_str.lower()
            last_idx = start
        elif match.group('punct'):
            punct_part = re.match(r'^[,.;:!?—–]+', matched_str)
            conj_part = matched_str[punct_part.end():].strip() if punct_part else ""
            pending_trigger = conj_part.lower() if conj_part else "punct"
            if conj_part:
                conj_start = raw.lower().find(conj_part.lower(), start)
                last_idx = conj_start if conj_start != -1 else end
            else:
                last_idx = end

    tail = raw[last_idx:].strip()
    tail = re.sub(r'^[,.;:!?—–\s]+', '', tail).strip()
    tail_clean = re.sub(r'[,.;:!?—–\s]+$', '', tail).strip()
    if tail_clean:
        segments.append((tail_clean, pending_trigger))

    clauses: List[PhraseClauseStructure] = []
    c_num = 1
    for s_text, trig in segments:
        clean_s = re.sub(r'^[,.;:!?—–\s]+', '', s_text)
        clean_s = re.sub(r'[,.;:!?—–\s]+$', '', clean_s).strip()
        if clean_s:
            w_cnt = len(clean_s.split())
            clauses.append(
                PhraseClauseStructure(
                    id=f"{sentence_id}_c{c_num:02d}",
                    text=clean_s,
                    delimiter_trigger=trig,
                    word_count=w_cnt,
                )
            )
            c_num += 1

    return clauses


def populate_clauses_and_timings(
    retention: RetentionScript,
    words: Optional[List[Dict[str, Any]]] = None,
    total_duration: Optional[float] = None,
) -> RetentionScript:
    """
    Populates:
    1. Phrase/clause breakdowns (`clauses`) for every body sentence (excluding HOOK).
    2. Spoken timestamps (`start_time`, `end_time`, `duration`) for:
       - Each of the 4 Hook parts (first_words, game_genre, first_punch, second_punch)
       - The Hook as a whole
       - Each clause in every non-hook sentence
       - Each sentence as a whole
    If word-level alignment `words` is available (ElevenLabs / Whisper), computes exact speech timestamps.
    Otherwise, computes precise estimated timestamps based on word counts and conversational cadence.
    """
    # 1. Ensure clauses are generated for all body sentences
    for s in retention.sentences:
        if s.block == NarrativeBlockTaxonomy.HOOK:
            s.clauses = []
        else:
            if not s.clauses or len(s.clauses) == 0:
                s.clauses = split_sentence_into_clauses(s.text, sentence_id=s.id)

    # 2. Case A: Exact audio word alignment available
    if words and len(words) > 0:
        def _norm(w: Any) -> str:
            return re.sub(r'[^a-z0-9]', '', str(w).lower())

        norm_words = [(_norm(w.get("word", "")), float(w.get("start", 0.0)), float(w.get("end", 0.0))) for w in words]
        total_w = len(norm_words)
        curr_idx = 0

        def match_phrase(p_text: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
            nonlocal curr_idx
            if not p_text or not p_text.strip() or curr_idx >= total_w:
                return None, None, None
            p_tokens = [_norm(tok) for tok in p_text.split() if _norm(tok)]
            if not p_tokens:
                return None, None, None

            start_idx = curr_idx
            # Resync check: lookahead up to 10 words for first token
            for look in range(curr_idx, min(curr_idx + 10, total_w)):
                if norm_words[look][0] == p_tokens[0] or p_tokens[0] in norm_words[look][0] or norm_words[look][0] in p_tokens[0]:
                    start_idx = look
                    break

            matched_tokens = 0
            w_idx = start_idx
            while w_idx < total_w and matched_tokens < len(p_tokens):
                w_tok = norm_words[w_idx][0]
                t_tok = p_tokens[matched_tokens]
                if w_tok == t_tok or w_tok in t_tok or t_tok in w_tok:
                    matched_tokens += 1
                    w_idx += 1
                else:
                    if w_idx + 1 < total_w and (norm_words[w_idx+1][0] == t_tok or t_tok in norm_words[w_idx+1][0]):
                        w_idx += 1
                    else:
                        w_idx += 1

            end_idx = min(max(start_idx, w_idx - 1), total_w - 1)
            s_time = round(norm_words[start_idx][1], 2)
            e_time = round(norm_words[end_idx][2], 2)
            dur = round(max(0.0, e_time - s_time), 2)
            curr_idx = end_idx + 1
            return s_time, e_time, dur

        # A1. Hook Parts
        if retention.hook:
            fw = retention.hook.first_words
            if fw and fw.text:
                st, et, d = match_phrase(fw.text)
                fw.start_time, fw.end_time, fw.duration = st, et, d

            gg = retention.hook.game_genre
            if gg and gg.text:
                st, et, d = match_phrase(gg.text)
                gg.start_time, gg.end_time, gg.duration = st, et, d

            fp = retention.hook.first_punch
            if fp and fp.text:
                st, et, d = match_phrase(fp.text)
                fp.start_time, fp.end_time, fp.duration = st, et, d

            sp = retention.hook.second_punch
            if sp and sp.text:
                st, et, d = match_phrase(sp.text)
                sp.start_time, sp.end_time, sp.duration = st, et, d

            h_starts = [x for x in [getattr(fw, 'start_time', None), getattr(gg, 'start_time', None), getattr(fp, 'start_time', None)] if x is not None]
            h_ends = [x for x in [getattr(fp, 'end_time', None), getattr(sp, 'end_time', None) if sp else None] if x is not None]
            if h_starts and h_ends:
                retention.hook.start_time = min(h_starts)
                retention.hook.end_time = max(h_ends)
                retention.hook.duration = round(retention.hook.end_time - retention.hook.start_time, 2)

        # A2. Body Sentences & Clauses
        for s in retention.sentences:
            if s.block == NarrativeBlockTaxonomy.HOOK:
                s.start_time = retention.hook.start_time
                s.end_time = retention.hook.end_time
                s.duration = retention.hook.duration
                continue

            for c in s.clauses:
                st, et, d = match_phrase(c.text)
                c.start_time, c.end_time, c.duration = st, et, d

            c_starts = [c.start_time for c in s.clauses if c.start_time is not None]
            c_ends = [c.end_time for c in s.clauses if c.end_time is not None]
            if c_starts and c_ends:
                s.start_time = min(c_starts)
                s.end_time = max(c_ends)
                s.duration = round(s.end_time - s.start_time, 2)

        return retention

    # 3. Case B: If no audio words provided, check if valid timestamps already exist
    has_existing_hook_times = retention.hook and retention.hook.start_time is not None
    has_existing_clause_times = any(
        any(c.start_time is not None for c in s.clauses)
        for s in retention.sentences if s.block != NarrativeBlockTaxonomy.HOOK
    )
    if has_existing_hook_times and has_existing_clause_times:
        return retention

    tot_dur = total_duration or retention.estimated_duration or 30.0
    all_words_cnt = max(1, sum(len(s.text.split()) for s in retention.sentences))
    sec_per_word = max(0.28, tot_dur / (all_words_cnt + len(retention.sentences) * 0.8))

    curr_time = 0.0

    # B1. Hook Parts
    if retention.hook:
        fw = retention.hook.first_words
        if fw and (fw.text or "").strip():  # direct openings have no first words
            w_c = max(1, len(fw.text.split()))
            dur = round(max(0.4, w_c * sec_per_word), 2)
            fw.start_time = round(curr_time, 2)
            fw.end_time = round(curr_time + dur, 2)
            fw.duration = dur
            curr_time += dur

        gg = retention.hook.game_genre
        if gg and (gg.text or "").strip():  # genre is optional in direct openings
            w_c = max(1, len(gg.text.split()))
            dur = round(max(0.5, w_c * sec_per_word), 2)
            gg.start_time = round(curr_time, 2)
            gg.end_time = round(curr_time + dur, 2)
            gg.duration = dur
            curr_time += dur

        fp = retention.hook.first_punch
        if fp:
            w_c = max(1, len(fp.text.split()))
            dur = round(max(0.8, w_c * sec_per_word), 2)
            fp.start_time = round(curr_time, 2)
            fp.end_time = round(curr_time + dur, 2)
            fp.duration = dur
            curr_time += dur

        sp = retention.hook.second_punch
        if sp and sp.text:
            w_c = max(1, len(sp.text.split()))
            dur = round(max(0.6, w_c * sec_per_word), 2)
            sp.start_time = round(curr_time, 2)
            sp.end_time = round(curr_time + dur, 2)
            sp.duration = dur
            curr_time += dur

        retention.hook.start_time = 0.0
        retention.hook.end_time = round(curr_time, 2)
        retention.hook.duration = round(curr_time, 2)
        curr_time += 0.25

    # B2. Body Sentences & Clauses
    for s in retention.sentences:
        if s.block == NarrativeBlockTaxonomy.HOOK:
            s.start_time = retention.hook.start_time
            s.end_time = retention.hook.end_time
            s.duration = retention.hook.duration
            continue

        s_start = curr_time
        for c in s.clauses:
            c_cnt = max(1, c.word_count or len(c.text.split()))
            c_dur = round(max(0.4, c_cnt * sec_per_word), 2)
            c.start_time = round(curr_time, 2)
            c.end_time = round(curr_time + c_dur, 2)
            c.duration = c_dur
            pause = 0.18 if c.delimiter_trigger in ('comma', 'punct', 'while', 'but', 'and') else 0.1
            curr_time += c_dur + pause

        s.start_time = round(s_start, 2)
        s.end_time = round(curr_time, 2)
        s.duration = round(s.end_time - s.start_time, 2)
        curr_time += 0.2

    return retention


# =============================================================================
# 6. REPAIR & DETERMINISTIC FALLBACK PARSER
# =============================================================================

def parse_and_repair_retention_script(
    raw_content: Any,
    game_name: str = "Game",
    target_duration: float = 30.0,
    words: Optional[List[Dict[str, Any]]] = None,
    hook_type: str = HOOK_TYPE_STEAM_NEW_GAME,
    opening_text: str = "",
) -> RetentionScript:
    """
    Robust parser and fallback builder:
    1. If input is JSON / dict: normalizes all categories, ensures valid structure.
    2. If input is raw text / markdown: extracts JSON or uses rule-based heuristics
       to dissect the text into hook components, sentences, clauses, and retention blocks.
    3. Guarantees a fully valid RetentionScript object with detailed clause breakdown
       and timestamps for both Hook components and clauses.

    Opening modes (hook_type): "direct" never forces HERE_IS_A_NEW and removes a stray
    formula opener; "custom" prefixes opening_text when the model omitted it. The text is
    only rewritten while no word alignment is given (voiced scripts must match the audio).
    """
    opening = normalize_opening_text(opening_text)
    hook_mode = resolve_hook_type(hook_type, opening)
    direct_like = hook_mode in (HOOK_TYPE_DIRECT, HOOK_TYPE_CUSTOM)
    parsed_dict: Optional[Dict[str, Any]] = None

    if isinstance(raw_content, dict):
        parsed_dict = raw_content
    elif isinstance(raw_content, str):
        text = raw_content.strip()
        # Attempt to strip markdown code blocks
        if "```" in text:
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
            if match:
                text = match.group(1).strip()
        try:
            parsed_dict = json.loads(text)
        except Exception:
            parsed_dict = None

    if parsed_dict and isinstance(parsed_dict, dict) and "hook" in parsed_dict:
        # Normalize and construct
        try:
            hook_raw = parsed_dict.get("hook", {})
            fw_raw = hook_raw.get("first_words", {})
            gg_raw = hook_raw.get("game_genre", {})
            fp_raw = hook_raw.get("first_punch", {})
            sp_raw = hook_raw.get("second_punch")

            # Direct/custom openings have no formula: never default to HERE_IS_A_NEW.
            fw_fallback = FirstWordsTaxonomy.CUSTOM if direct_like else FirstWordsTaxonomy.HERE_IS_A_NEW
            hook_obj = HookStructure(
                text=hook_raw.get("text", "").strip(),
                first_words=FirstWordsComponent(
                    category=normalize_category(fw_raw.get("category"), FirstWordsTaxonomy.ALL, fw_fallback),
                    text=(fw_raw.get("text") or "") if direct_like else fw_raw.get("text", "Here's a new"),
                    start_time=fw_raw.get("start_time"),
                    end_time=fw_raw.get("end_time"),
                    duration=fw_raw.get("duration"),
                ),
                game_genre=GameGenreComponent(
                    category=normalize_category(gg_raw.get("category"), GameGenreTaxonomy.ALL, GameGenreTaxonomy.COOP_GAME),
                    text=gg_raw.get("text", "" if direct_like else "game"),
                    start_time=gg_raw.get("start_time"),
                    end_time=gg_raw.get("end_time"),
                    duration=gg_raw.get("duration"),
                ),
                first_punch=FirstPunchComponent(
                    category=normalize_category(fp_raw.get("category"), FirstPunchTaxonomy.ALL, FirstPunchTaxonomy.ACTION),
                    text=fp_raw.get("text", ""),
                    start_time=fp_raw.get("start_time"),
                    end_time=fp_raw.get("end_time"),
                    duration=fp_raw.get("duration"),
                ),
                second_punch=SecondPunchComponent(
                    category=normalize_category(sp_raw.get("category"), SecondPunchTaxonomy.ALL, SecondPunchTaxonomy.NONE) if sp_raw else SecondPunchTaxonomy.NONE,
                    text=sp_raw.get("text") if sp_raw else None,
                    start_time=sp_raw.get("start_time") if sp_raw else None,
                    end_time=sp_raw.get("end_time") if sp_raw else None,
                    duration=sp_raw.get("duration") if sp_raw else None,
                ) if sp_raw else None,
                start_time=hook_raw.get("start_time"),
                end_time=hook_raw.get("end_time"),
                duration=hook_raw.get("duration"),
                estimated_duration_seconds=float(hook_raw.get("estimated_duration_seconds") or 3.2),
            )

            # Sentences
            sentences_list: List[SentenceStructure] = []
            raw_sentences = parsed_dict.get("sentences", [])
            for idx, s in enumerate(raw_sentences):
                ol_raw = s.get("open_loop", {}) or {}
                sid = s.get("id") or f"sentence_{idx+1:02d}"
                raw_clauses = s.get("clauses", []) or []
                parsed_clauses = [
                    PhraseClauseStructure(
                        id=c.get("id") or f"{sid}_c{cidx+1:02d}",
                        text=c.get("text", "").strip(),
                        delimiter_trigger=c.get("delimiter_trigger"),
                        start_time=c.get("start_time"),
                        end_time=c.get("end_time"),
                        duration=c.get("duration"),
                        word_count=int(c.get("word_count") or len(c.get("text", "").split())),
                    )
                    for cidx, c in enumerate(raw_clauses)
                    if isinstance(c, dict) and c.get("text")
                ]
                sentences_list.append(
                    SentenceStructure(
                        id=sid,
                        text=s.get("text", "").strip(),
                        block=normalize_category(s.get("block"), NarrativeBlockTaxonomy.ALL, NarrativeBlockTaxonomy.SETUP),
                        sentence_type=normalize_category(s.get("sentence_type"), SentenceTypeTaxonomy.ALL, SentenceTypeTaxonomy.ACTION),
                        attention_device=normalize_category(s.get("attention_device"), AttentionDeviceTaxonomy.ALL, AttentionDeviceTaxonomy.NONE),
                        information_function=normalize_category(s.get("information_function"), InformationFunctionTaxonomy.ALL, InformationFunctionTaxonomy.MECHANIC),
                        open_loop=OpenLoopStructure(
                            type=normalize_category(ol_raw.get("type"), OpenLoopTaxonomy.ALL, OpenLoopTaxonomy.NONE),
                            id=ol_raw.get("id"),
                            resolves=ol_raw.get("resolves"),
                        ),
                        escalation_level=int(s.get("escalation_level", 1)),
                        visual_beat=s.get("visual_beat"),
                        clauses=parsed_clauses,
                        start_time=s.get("start_time"),
                        end_time=s.get("end_time"),
                        duration=s.get("duration"),
                    )
                )

            ending_raw = parsed_dict.get("ending", {})
            ending_obj = EndingStructure(
                payoff_type=ending_raw.get("payoff_type", "REVEAL"),
                cta_type=ending_raw.get("cta_type", "STEAM_WISHLIST"),
            )

            repair_notes = _repair_opening(hook_obj, sentences_list, hook_mode, opening, allow_text_edits=not words)

            clean_text = " ".join([s.text for s in sentences_list if s.text]).strip()
            if not clean_text and hook_obj.text:
                clean_text = hook_obj.text

            w_count = len(clean_text.split())
            est_dur = round(w_count / 2.4, 1)

            is_valid, errors, warnings = validate_retention_script({
                "hook": hook_obj.model_dump(),
                "sentences": [s.model_dump() for s in sentences_list],
                "ending": ending_obj.model_dump(),
            }, hook_type=hook_mode, opening_text=opening)

            ret_script = RetentionScript(
                hook=hook_obj,
                sentences=sentences_list,
                ending=ending_obj,
                clean_script_text=clean_text,
                word_count=w_count,
                estimated_duration=est_dur,
                validation_warnings=warnings + errors + repair_notes,
            )

            # Ensure clauses & speech/estimated timings are populated
            return populate_clauses_and_timings(ret_script, words=words, total_duration=target_duration)

        except Exception as err:
            logger.warning(f"Error parsing structured retention json: {err}. Falling back to heuristic breakdown.")

    # Rule-Based Heuristic Fallback
    raw_str = str(raw_content) if raw_content else ""
    return _build_heuristic_retention_script(raw_str, game_name, target_duration, words=words,
                                             hook_type=hook_mode, opening_text=opening)


def _repair_opening(
    hook_obj: HookStructure,
    sentences_list: List[SentenceStructure],
    hook_mode: str,
    opening: str,
    allow_text_edits: bool = True,
) -> List[str]:
    """Applies the opening mode to a parsed script in place. Returns repair notes."""
    notes: List[str] = []
    if hook_mode not in (HOOK_TYPE_DIRECT, HOOK_TYPE_CUSTOM):
        return notes
    first = sentences_list[0] if sentences_list else None
    fw = hook_obj.first_words

    if hook_mode == HOOK_TYPE_CUSTOM:
        core = re.sub(r"[\s.!?;:,]+$", "", opening)
        source = first.text if first is not None else hook_obj.text
        was_present = starts_with_opening(source, opening)
        if allow_text_edits:
            fixed = ensure_opening_text(source, opening)
            if fixed != source:
                if first is not None:
                    hook_matches_first = hook_obj.text.strip() == first.text.strip()
                    first.text = fixed
                    first.clauses = []
                    if hook_matches_first or not starts_with_opening(hook_obj.text, opening):
                        hook_obj.text = ensure_opening_text(hook_obj.text or source, opening)
                else:
                    hook_obj.text = fixed
                if not was_present:
                    notes.append("Required opening text was missing and has been added at the start.")
        now_present = starts_with_opening(first.text if first is not None else hook_obj.text, opening)
        # Only describe the opener as first words when it is really in the (spoken) text.
        if now_present and (not was_present or not (fw.text or "").strip()):
            category = _first_words_category_for(opening) or FirstWordsTaxonomy.CUSTOM
            hook_obj.first_words = FirstWordsComponent(category=category, text=core)
        return notes

    # Direct: no formula opener, never a forced HERE_IS_A_NEW.
    if allow_text_edits:
        targets = [first] if first is not None else []
        stripped_any = False
        for sentence in targets:
            stripped = strip_formulaic_opener(sentence.text)
            if stripped != sentence.text.strip():
                if hook_obj.text.strip() == sentence.text.strip():
                    hook_obj.text = stripped
                sentence.text = stripped
                sentence.clauses = []
                stripped_any = True
        stripped_hook = strip_formulaic_opener(hook_obj.text)
        if stripped_hook != hook_obj.text.strip():
            hook_obj.text = stripped_hook
            stripped_any = True
        if stripped_any:
            notes.append("Formulaic opener removed for the direct opening mode.")
    hook_text = (hook_obj.text or (first.text if first is not None else "")).strip()
    lowered = hook_text.lower()
    if fw.text and not lowered.startswith(fw.text.strip().lower()):
        fw.text = ""
    if not (fw.text or "").strip():
        fw.category = FirstWordsTaxonomy.CUSTOM
    gg = hook_obj.game_genre
    if gg.text and gg.text.strip().lower() not in lowered:
        gg.text = ""
    return notes


def _build_heuristic_retention_script(
    text: str,
    game_name: str,
    target_duration: float,
    words: Optional[List[Dict[str, Any]]] = None,
    hook_type: str = HOOK_TYPE_STEAM_NEW_GAME,
    opening_text: str = "",
) -> RetentionScript:
    """Heuristic fallback that extracts hook components, sentences, and clauses from raw plain text."""
    opening = normalize_opening_text(opening_text)
    hook_mode = resolve_hook_type(hook_type, opening)
    direct_like = hook_mode in (HOOK_TYPE_DIRECT, HOOK_TYPE_CUSTOM)
    repair_notes: List[str] = []
    # Clean up markdown / quotes
    cleaned = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    cleaned = re.sub(r'[\r\n]+', ' ', cleaned).strip().strip('"').strip("'")
    if not cleaned:
        if direct_like:
            cleaned = f"You jump straight into the action in {game_name}. Build, survive, and explore with your squad. Wishlist on Steam!"
        else:
            cleaned = f"Here's a new game called {game_name} where you jump straight into the action. Build, survive, and explore with your squad. Wishlist on Steam!"

    # Opening modes may adjust the text while it has not been voiced yet.
    if not words:
        if hook_mode == HOOK_TYPE_CUSTOM:
            had_opening = starts_with_opening(cleaned, opening)
            cleaned = ensure_opening_text(cleaned, opening)
            if not had_opening:
                repair_notes.append("Required opening text was missing and has been added at the start.")
        elif hook_mode == HOOK_TYPE_DIRECT:
            stripped = strip_formulaic_opener(cleaned)
            if stripped != cleaned:
                cleaned = stripped
                repair_notes.append("Formulaic opener removed for the direct opening mode.")

    # Split into sentences
    raw_sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', cleaned) if s.strip()]
    if not raw_sentences:
        raw_sentences = [cleaned]

    hook_sentence = raw_sentences[0]

    # Heuristic Hook Extraction
    fw_cat = FirstWordsTaxonomy.HERE_IS_A_NEW
    fw_text = "Here's a new"
    if direct_like:
        # Only report first words that are really spoken; never assume HERE_IS_A_NEW.
        if hook_mode == HOOK_TYPE_CUSTOM and starts_with_opening(cleaned, opening):
            fw_cat = _first_words_category_for(opening) or FirstWordsTaxonomy.CUSTOM
            fw_text = re.sub(r"[\s.!?;:,]+$", "", opening)
        else:
            detected = _first_words_category_for(hook_sentence)
            fw_cat = detected or FirstWordsTaxonomy.CUSTOM
            fw_text = ""
            if detected:
                m = re.match(r"\s*((?:\S+\s+){0,3}?new\b|imagine\b|what\s+if\b|check\s+out\s+this\b|you\s+play\s+as\b)", hook_sentence, re.I)
                fw_text = m.group(1).strip() if m else ""
    elif re.search(r"so in this new", hook_sentence, re.I):
        fw_cat = FirstWordsTaxonomy.SO_IN_THIS_NEW
        fw_text = "So in this new"
    elif re.search(r"in this new", hook_sentence, re.I):
        fw_cat = FirstWordsTaxonomy.IN_THIS_NEW
        fw_text = "In this new"
    elif re.search(r"imagine a", hook_sentence, re.I):
        fw_cat = FirstWordsTaxonomy.IMAGINE_A
        fw_text = "Imagine a"
    elif re.search(r"what if", hook_sentence, re.I):
        fw_cat = FirstWordsTaxonomy.WHAT_IF
        fw_text = "What if"

    genre_cat = GameGenreTaxonomy.COOP_GAME
    genre_text = "co-op game"
    if re.search(r"horror", hook_sentence, re.I):
        genre_cat = GameGenreTaxonomy.HORROR_GAME
        genre_text = "horror game"
    elif re.search(r"survival", hook_sentence, re.I):
        genre_cat = GameGenreTaxonomy.SURVIVAL_GAME
        genre_text = "survival game"
    elif re.search(r"steam game", hook_sentence, re.I):
        genre_cat = GameGenreTaxonomy.STEAM_GAME
        genre_text = "Steam game"
    elif direct_like and not re.search(r"co-?op", hook_sentence, re.I):
        # Do not invent a genre that is not spoken (it would also skew word timings).
        genre_cat = GameGenreTaxonomy.CUSTOM
        genre_text = ""

    # Second punch heuristic
    sp_cat = SecondPunchTaxonomy.NONE
    sp_text = None
    sp_match = re.search(r"\b(but|while|except|where everything|and nobody|to stay alive)\b.*", hook_sentence, re.I)
    if sp_match:
        sp_cat = SecondPunchTaxonomy.THREAT if "hunt" in hook_sentence.lower() or "kill" in hook_sentence.lower() else SecondPunchTaxonomy.CONTRAST
        sp_text = sp_match.group(0).strip().rstrip('.!?')

    fp_text = hook_sentence
    if sp_text and sp_text in hook_sentence:
        fp_text = hook_sentence.split(sp_text)[0].strip()

    # Clean fp_text to remove leading first_words and genre_text so components don't overlap
    clean_fp = fp_text
    fw_m = re.search(re.escape(fw_text), clean_fp, re.I)
    if fw_m:
        clean_fp = clean_fp[fw_m.end():].strip()
    genre_m = re.search(re.escape(genre_text), clean_fp, re.I)
    if genre_m:
        clean_fp = clean_fp[genre_m.end():].strip()
    clean_fp = re.sub(r'^(?:a\s+new\s+)?(?:(?:\d+|two|three|four|five|eight)-player\s+)?(?:co-op\s+)?(?:horror|survival|action|steam\s+game)?\s*', '', clean_fp, flags=re.I).strip()
    if clean_fp:
        fp_text = clean_fp

    hook_obj = HookStructure(
        text=hook_sentence,
        first_words=FirstWordsComponent(category=fw_cat, text=fw_text),
        game_genre=GameGenreComponent(category=genre_cat, text=genre_text),
        first_punch=FirstPunchComponent(category=FirstPunchTaxonomy.ACTION, text=fp_text),
        second_punch=SecondPunchComponent(category=sp_cat, text=sp_text) if sp_text else None,
        estimated_duration_seconds=round(len(hook_sentence.split()) / 2.5, 1),
    )

    sentences_list: List[SentenceStructure] = []
    total_s = len(raw_sentences)

    for idx, s_txt in enumerate(raw_sentences):
        sid = f"sentence_{idx+1:02d}"
        if idx == 0:
            block = NarrativeBlockTaxonomy.HOOK
            stype = SentenceTypeTaxonomy.HOOK
            att = AttentionDeviceTaxonomy.CONTRAST if sp_cat != SecondPunchTaxonomy.NONE else AttentionDeviceTaxonomy.NONE
            ol = OpenLoopStructure(type=OpenLoopTaxonomy.THREAT if sp_cat == SecondPunchTaxonomy.THREAT else OpenLoopTaxonomy.INFORMATION_GAP, id="loop_01", resolves=None)
            esc = 1
        elif idx == total_s - 1:
            block = NarrativeBlockTaxonomy.CTA
            stype = SentenceTypeTaxonomy.CTA
            att = AttentionDeviceTaxonomy.NONE
            ol = OpenLoopStructure(type=OpenLoopTaxonomy.NONE, id=None, resolves="loop_01" if total_s <= 3 else None)
            esc = 3
        elif idx == total_s - 2 and total_s >= 4:
            block = NarrativeBlockTaxonomy.PAYOFF
            stype = SentenceTypeTaxonomy.PAYOFF
            att = AttentionDeviceTaxonomy.REVEAL
            ol = OpenLoopStructure(type=OpenLoopTaxonomy.NONE, id=None, resolves="loop_01")
            esc = 3
        elif idx == 1:
            block = NarrativeBlockTaxonomy.SETUP
            stype = SentenceTypeTaxonomy.ACTION
            att = AttentionDeviceTaxonomy.NONE
            ol = OpenLoopStructure(type=OpenLoopTaxonomy.NONE, id=None, resolves=None)
            esc = 1
        elif idx == 2:
            block = NarrativeBlockTaxonomy.FIRST_ESCALATION
            stype = SentenceTypeTaxonomy.ESCALATION
            att = AttentionDeviceTaxonomy.BUT if s_txt.lower().startswith("but") else AttentionDeviceTaxonomy.THEN
            ol = OpenLoopStructure(type=OpenLoopTaxonomy.ESCALATION, id="loop_02", resolves=None)
            esc = 2
        else:
            block = NarrativeBlockTaxonomy.SECOND_ESCALATION
            stype = SentenceTypeTaxonomy.TWIST
            att = AttentionDeviceTaxonomy.UNTIL
            ol = OpenLoopStructure(type=OpenLoopTaxonomy.NONE, id=None, resolves="loop_02")
            esc = 2

        sentences_list.append(
            SentenceStructure(
                id=sid,
                text=s_txt,
                block=block,
                sentence_type=stype,
                attention_device=att,
                information_function=InformationFunctionTaxonomy.MECHANIC,
                open_loop=ol,
                escalation_level=esc,
                visual_beat=f"Gameplay scene showcasing {game_name}",
            )
        )

    clean_text = " ".join(raw_sentences).strip()
    w_count = len(clean_text.split())
    est_dur = round(w_count / 2.4, 1)

    ret_obj = RetentionScript(
        hook=hook_obj,
        sentences=sentences_list,
        ending=EndingStructure(payoff_type="REVEAL", cta_type="STEAM_WISHLIST"),
        clean_script_text=clean_text,
        word_count=w_count,
        estimated_duration=est_dur,
        validation_warnings=["Heuristically generated retention structure from plain text."] + repair_notes,
    )
    return populate_clauses_and_timings(ret_obj, words=words, total_duration=target_duration)
