"""
test_script_retention.py — Unit tests for Hook Construction & Retention Construction engine
"""

import json
import unittest
from script_retention import (
    FirstWordsTaxonomy,
    GameGenreTaxonomy,
    FirstPunchTaxonomy,
    SecondPunchTaxonomy,
    NarrativeBlockTaxonomy,
    SentenceTypeTaxonomy,
    AttentionDeviceTaxonomy,
    InformationFunctionTaxonomy,
    OpenLoopTaxonomy,
    normalize_category,
    build_retention_script_prompt,
    validate_retention_script,
    parse_and_repair_retention_script,
    RetentionScript,
)


def test_category_normalization():
    assert normalize_category("here is a new", FirstWordsTaxonomy.ALL) == FirstWordsTaxonomy.HERE_IS_A_NEW
    assert normalize_category("COOP_GAME", GameGenreTaxonomy.ALL) == GameGenreTaxonomy.COOP_GAME
    assert normalize_category("4 player coop", GameGenreTaxonomy.ALL) == GameGenreTaxonomy.FOUR_PLAYER_COOP
    assert normalize_category("totally_unknown_category", FirstPunchTaxonomy.ALL) == FirstPunchTaxonomy.CUSTOM


def test_validate_valid_retention_script():
    sample_data = {
        "hook": {
            "text": "Here's a new 4-player co-op horror game where you repair an abandoned station while something hunts you through the vents.",
            "first_words": {"category": "HERE_IS_A_NEW", "text": "Here's a new"},
            "game_genre": {"category": "FOUR_PLAYER_COOP", "text": "4-player co-op horror game"},
            "first_punch": {"category": "COOPERATION", "text": "where you repair an abandoned station"},
            "second_punch": {"category": "THREAT", "text": "while something hunts you through the vents"},
            "estimated_duration_seconds": 3.4
        },
        "sentences": [
            {
                "id": "sentence_01",
                "text": "Here's a new 4-player co-op horror game where you repair an abandoned station while something hunts you through the vents.",
                "block": "HOOK",
                "sentence_type": "HOOK",
                "attention_device": "CONTRAST",
                "information_function": "THREAT",
                "open_loop": {"type": "THREAT", "id": "loop_01", "resolves": None},
                "escalation_level": 1,
                "visual_beat": "Squad exploring dark space station"
            },
            {
                "id": "sentence_02",
                "text": "One of you monitors the radar while your friends fix the power generators.",
                "block": "SETUP",
                "sentence_type": "ACTION",
                "attention_device": "NONE",
                "information_function": "MECHANIC",
                "open_loop": {"type": "NONE", "id": None, "resolves": None},
                "escalation_level": 1,
                "visual_beat": "Player monitoring radar"
            },
            {
                "id": "sentence_03",
                "text": "But the creature gets faster every time you make noise.",
                "block": "FIRST_ESCALATION",
                "sentence_type": "ESCALATION",
                "attention_device": "BUT",
                "information_function": "CONSEQUENCE",
                "open_loop": {"type": "ESCALATION", "id": "loop_02", "resolves": None},
                "escalation_level": 2,
                "visual_beat": "Monster dashing down corridor"
            },
            {
                "id": "sentence_04",
                "text": "And the only way out is to sacrifice one teammate to unlock the airlock.",
                "block": "PAYOFF",
                "sentence_type": "PAYOFF",
                "attention_device": "REVEAL",
                "information_function": "TWIST",
                "open_loop": {"type": "NONE", "id": None, "resolves": "loop_01"},
                "escalation_level": 3,
                "visual_beat": "Airlock closing with creature inside"
            },
            {
                "id": "sentence_05",
                "text": "This is Station 9 on Steam. Wishlist it today!",
                "block": "CTA",
                "sentence_type": "CTA",
                "attention_device": "NONE",
                "information_function": "OBJECTIVE",
                "open_loop": {"type": "NONE", "id": None, "resolves": "loop_02"},
                "escalation_level": 3,
                "visual_beat": "Game logo and Steam store page"
            }
        ],
        "ending": {
            "payoff_type": "THREAT_REVEAL",
            "cta_type": "STEAM_WISHLIST"
        }
    }

    is_valid, errors, warnings = validate_retention_script(sample_data)
    assert is_valid is True
    assert len(errors) == 0

    # Parse into RetentionScript model
    script_obj = parse_and_repair_retention_script(sample_data)
    assert isinstance(script_obj, RetentionScript)
    assert script_obj.hook.first_words.category == "HERE_IS_A_NEW"
    assert script_obj.hook.second_punch.category == "THREAT"
    assert len(script_obj.sentences) == 5
    assert script_obj.word_count > 40
    assert script_obj.estimated_duration > 15.0


def test_open_loop_tracking_and_warnings():
    # Test invalid loop resolution: resolves a loop that never opened
    bad_data = {
        "hook": {"text": "Hook text"},
        "sentences": [
            {
                "id": "sentence_01",
                "text": "Here is a game.",
                "block": "HOOK",
                "sentence_type": "HOOK",
                "open_loop": {"type": "NONE", "id": None, "resolves": "loop_99"}  # loop_99 does not exist!
            }
        ],
        "ending": {"payoff_type": "REVEAL", "cta_type": "CTA"}
    }
    is_valid, errors, warnings = validate_retention_script(bad_data)
    assert any("resolves loop 'loop_99' before it was declared" in w for w in warnings)


def test_heuristic_fallback_from_plain_text():
    plain_text = (
        "Here's a new 4-player co-op horror game where you and your squad fix a submarine but something is hunting you. "
        "One player pilots the sub while the others repair hull leaks. "
        "Until the sonar starts picking up massive movement outside. "
        "You have to escape before the water breaches the engine room. "
        "This is Deep Fear on Steam. Tag your friends and wishlist now!"
    )
    script_obj = parse_and_repair_retention_script(plain_text, game_name="Deep Fear")
    assert isinstance(script_obj, RetentionScript)
    assert script_obj.hook.text.startswith("Here's a new")
    assert script_obj.hook.first_words.category == "HERE_IS_A_NEW"
    assert script_obj.hook.game_genre.category == "HORROR_GAME"
    assert script_obj.hook.second_punch is not None
    assert len(script_obj.sentences) >= 4
    assert script_obj.word_count > 30
    assert "Deep Fear" in script_obj.clean_script_text


def test_prompt_builder_includes_retention_rules():
    prompt = build_retention_script_prompt(
        game_name="Void Horizon",
        target_duration=30.0,
        tone="viral_hype",
        game_info="A sci-fi roguelike space exploration game.",
        briefing_text="Focus on the black hole physics.",
    )
    assert "20 CORE RETENTION PRINCIPLES" in prompt
    assert "MODULAR HOOK CONSTRUCTION" in prompt
    assert "First Words" in prompt
    assert "Game Genre" in prompt
    assert "First Punch" in prompt
    assert "Second Punch" in prompt
    assert "RETENTION NARRATIVE BLOCKS" in prompt
    assert "SENTENCE-LEVEL RETENTION LABELS" in prompt
    assert "MANDATORY LINGUISTIC PROFILE & VOCABULARY" in prompt
    assert "open_loop" in prompt
    assert "Void Horizon" in prompt


def test_backwards_compatibility_legacy_script():
    legacy_json = {
        "step": 3,
        "step_name": "script_and_voiceover",
        "game_name": "Brofish",
        "script_text": "Here's a new Steam game where you build underwater bases with your squad. But terrifying sea monsters wake up at night. Escape before your oxygen runs out. Wishlist Brofish on Steam!",
        "word_count": 31,
        "estimated_duration": 13.5
    }
    # Ensure parse_and_repair_retention_script can process legacy script_text
    script_obj = parse_and_repair_retention_script(
        legacy_json["script_text"],
        game_name=legacy_json["game_name"],
        target_duration=legacy_json["estimated_duration"]
    )
    assert isinstance(script_obj, RetentionScript)
    assert script_obj.hook.first_words.category == "HERE_IS_A_NEW"
    assert script_obj.hook.game_genre.category == "STEAM_GAME"
    assert len(script_obj.sentences) == 4
    assert script_obj.word_count > 25


def test_hook_without_second_punch():
    sample_data = {
        "hook": {
            "text": "Imagine a simulation game where you manage a cozy medieval tavern.",
            "first_words": {"category": "IMAGINE_A", "text": "Imagine a"},
            "game_genre": {"category": "SIMULATION_GAME", "text": "simulation game"},
            "first_punch": {"category": "CREATION", "text": "where you manage a cozy medieval tavern"},
            "second_punch": None,
            "estimated_duration_seconds": 2.9
        },
        "sentences": [
            {
                "id": "sentence_01",
                "text": "Imagine a simulation game where you manage a cozy medieval tavern.",
                "block": "HOOK",
                "sentence_type": "HOOK",
                "attention_device": "NONE",
                "information_function": "MECHANIC",
                "open_loop": {"type": "NONE", "id": None, "resolves": None},
                "escalation_level": 1,
                "visual_beat": "Warm tavern with patrons drinking ale"
            },
            {
                "id": "sentence_02",
                "text": "You brew unique beers and upgrade your rooms for adventurers.",
                "block": "SETUP",
                "sentence_type": "ACTION",
                "attention_device": "NONE",
                "information_function": "MECHANIC",
                "open_loop": {"type": "NONE", "id": None, "resolves": None},
                "escalation_level": 1,
                "visual_beat": "Brewing beer mini-game"
            },
            {
                "id": "sentence_03",
                "text": "Check out Tavern Master on Steam!",
                "block": "CTA",
                "sentence_type": "CTA",
                "attention_device": "NONE",
                "information_function": "OBJECTIVE",
                "open_loop": {"type": "NONE", "id": None, "resolves": None},
                "escalation_level": 2,
                "visual_beat": "Steam store page"
            }
        ],
        "ending": {"payoff_type": "REWARD", "cta_type": "STEAM_WISHLIST"}
    }
    is_valid, errors, warnings = validate_retention_script(sample_data)
    assert is_valid is True
    script_obj = parse_and_repair_retention_script(sample_data)
    assert script_obj.hook.second_punch is None or script_obj.hook.second_punch.category == "NONE"


def test_markdown_wrapped_json_parsing():
    raw_markdown = """
```json
{
  "hook": {
    "text": "So in this new roguelike game you control a sentient sword that feeds on enemy souls.",
    "first_words": { "category": "SO_IN_THIS_NEW", "text": "So in this new" },
    "game_genre": { "category": "ROGUELIKE_GAME", "text": "roguelike game" },
    "first_punch": { "category": "ROLE", "text": "you control a sentient sword" },
    "second_punch": { "category": "TWIST", "text": "that feeds on enemy souls" },
    "estimated_duration_seconds": 3.1
  },
  "sentences": [
    {
      "id": "sentence_01",
      "text": "So in this new roguelike game you control a sentient sword that feeds on enemy souls.",
      "block": "HOOK",
      "sentence_type": "HOOK",
      "attention_device": "CONTRAST",
      "information_function": "CHARACTER",
      "open_loop": { "type": "THREAT", "id": "loop_01", "resolves": null },
      "escalation_level": 1,
      "visual_beat": "Glowing sword slashing enemies"
    },
    {
      "id": "sentence_02",
      "text": "Every kill gives you new abilities until your wielder is consumed by darkness.",
      "block": "FIRST_ESCALATION",
      "sentence_type": "ESCALATION",
      "attention_device": "UNTIL",
      "information_function": "CONSEQUENCE",
      "open_loop": { "type": "NONE", "id": null, "resolves": "loop_01" },
      "escalation_level": 2,
      "visual_beat": "Dark aura consuming knight"
    }
  ],
  "ending": { "payoff_type": "TWIST", "cta_type": "STEAM_WISHLIST" }
}
```
"""
    script_obj = parse_and_repair_retention_script(raw_markdown, game_name="SoulBlade")
    assert isinstance(script_obj, RetentionScript)
    assert script_obj.hook.first_words.category == "SO_IN_THIS_NEW"
    assert script_obj.hook.game_genre.category == "ROGUELIKE_GAME"
    assert len(script_obj.sentences) == 2


if __name__ == "__main__":
    print("Running retention unit tests...")
    test_category_normalization()
    test_validate_valid_retention_script()
    test_open_loop_tracking_and_warnings()
    test_heuristic_fallback_from_plain_text()
    test_prompt_builder_includes_retention_rules()
    test_backwards_compatibility_legacy_script()
    test_hook_without_second_punch()
    test_markdown_wrapped_json_parsing()
    print("ALL RETENTION UNIT TESTS PASSED! [OK]")
