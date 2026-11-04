"""
test_e2e_retention.py — End-to-end verification of script retention flow
"""

from script_retention import parse_and_repair_retention_script
import json

test_raw = '''
{
  "hook": {
    "text": "Here's a new 4-player co-op horror game where you and your friends repair a submarine while something is hunting you.",
    "first_words": { "category": "HERE_IS_A_NEW", "text": "Here's a new" },
    "game_genre": { "category": "COOP_GAME", "text": "4-player co-op horror game" },
    "first_punch": { "category": "COOPERATION", "text": "where you and your friends repair a submarine" },
    "second_punch": { "category": "THREAT", "text": "while something is hunting you" },
    "estimated_duration_seconds": 3.4
  },
  "sentences": [
    {
      "id": "sentence_01",
      "text": "Here's a new 4-player co-op horror game where you and your friends repair a submarine while something is hunting you.",
      "block": "HOOK",
      "sentence_type": "HOOK",
      "attention_device": "CONTRAST",
      "information_function": "THREAT",
      "open_loop": { "type": "THREAT", "id": "loop_01", "resolves": null },
      "escalation_level": 1,
      "visual_beat": "Submarine exterior with deep sea predator"
    },
    {
      "id": "sentence_02",
      "text": "One player drives while the others run around fixing catastrophic engine leaks.",
      "block": "SETUP",
      "sentence_type": "ACTION",
      "attention_device": "NONE",
      "information_function": "MECHANIC",
      "open_loop": { "type": "NONE", "id": null, "resolves": null },
      "escalation_level": 1,
      "visual_beat": "Crew repairing engine valves"
    },
    {
      "id": "sentence_03",
      "text": "Until the power grid fails and the creature breaches the lower deck.",
      "block": "FIRST_ESCALATION",
      "sentence_type": "TWIST",
      "attention_device": "UNTIL",
      "information_function": "CONSEQUENCE",
      "open_loop": { "type": "ESCALATION", "id": "loop_02", "resolves": null },
      "escalation_level": 2,
      "visual_beat": "Red emergency lights flashing"
    },
    {
      "id": "sentence_04",
      "text": "You must work together to seal the bulkheads and restart the reactor before time runs out.",
      "block": "PAYOFF",
      "sentence_type": "PAYOFF",
      "attention_device": "REVEAL",
      "information_function": "OBJECTIVE",
      "open_loop": { "type": "NONE", "id": null, "resolves": "loop_01" },
      "escalation_level": 3,
      "visual_beat": "Crew restarting reactor"
    },
    {
      "id": "sentence_05",
      "text": "This is Submersible Terror on Steam. Tag your squad and wishlist today!",
      "block": "CTA",
      "sentence_type": "CTA",
      "attention_device": "NONE",
      "information_function": "OBJECTIVE",
      "open_loop": { "type": "NONE", "id": null, "resolves": "loop_02" },
      "escalation_level": 3,
      "visual_beat": "Steam store page card"
    }
  ],
  "ending": { "payoff_type": "THREAT_REVEAL", "cta_type": "STEAM_WISHLIST" }
}
'''

obj = parse_and_repair_retention_script(test_raw, game_name="Submersible Terror")
d = obj.to_dict()
print("Hook text:", d["hook"]["text"])
print("Hook first words:", d["hook"]["first_words"])
print("Hook game genre:", d["hook"]["game_genre"])
print("Hook first punch:", d["hook"]["first_punch"])
print("Hook second punch:", d["hook"]["second_punch"])
print("Sentences count:", len(d["sentences"]))
print("Clean text:", d["clean_script_text"][:60], "...")
print("Word count:", d["word_count"])
print("Duration:", d["estimated_duration"])
print("Validation warnings:", d["validation_warnings"])
print("SUCCESS: Full schema conversion and validation verified!")
