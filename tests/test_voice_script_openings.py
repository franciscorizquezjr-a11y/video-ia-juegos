"""Script opening modes (hook_type/opening_text) and speech-speed word budget. No external API calls."""
import json
import unittest
from unittest.mock import Mock, patch

import utils
from script_retention import (
    FirstWordsTaxonomy,
    build_retention_script_prompt,
    ensure_opening_text,
    parse_and_repair_retention_script,
    resolve_hook_type,
    starts_with_formula_opener,
    starts_with_opening,
    strip_formulaic_opener,
    validate_retention_script,
)


def direct_script():
    hook = "You and your friends weld leaks while monsters hunt you."
    return {
        "hook": {"text": hook,
                 "first_words": {"category": "CUSTOM", "text": ""},
                 "game_genre": {"category": "COOP_GAME", "text": ""},
                 "first_punch": {"category": "COOPERATION", "text": "You and your friends weld leaks"},
                 "second_punch": {"category": "THREAT", "text": "while monsters hunt you"}},
        "sentences": [
            {"id": "sentence_01", "text": hook, "block": "HOOK",
             "open_loop": {"type": "THREAT", "id": "loop_01", "resolves": None}},
            {"id": "sentence_02", "text": "One mistake floods the whole sub.", "block": "SETUP"},
            {"id": "sentence_03", "text": "Patch it in time and the monsters starve.", "block": "PAYOFF",
             "open_loop": {"type": "NONE", "id": None, "resolves": "loop_01"}},
            {"id": "sentence_04", "text": "Wishlist Deep Leak on Steam!", "block": "CTA"},
        ],
        "ending": {"payoff_type": "REVEAL", "cta_type": "STEAM_WISHLIST"},
    }


class SpeechSpeedBudgetTests(unittest.TestCase):
    def test_default_budget_is_unchanged(self):
        prompt = build_retention_script_prompt("Game")
        self.assertIn("70 to 80 words for approximately 30s at 1.12x speed (estimated 2.69 words/s", prompt)

    def test_budget_follows_the_voice_speed(self):
        slow = build_retention_script_prompt("Game", 30, speech_speed=1.0)
        self.assertIn("63 to 72 words for approximately 30s at 1x speed (estimated 2.40 words/s", slow)
        fast = build_retention_script_prompt("Game", 30, speech_speed=1.2)
        self.assertIn("75 to 86 words", fast)
        ui_default = build_retention_script_prompt("Game", 45, speech_speed=1.1)
        self.assertIn("103 to 118 words for approximately 45s at 1.1x speed (estimated 2.64 words/s", ui_default)
        # Out-of-range values are clamped like the voice itself; invalid values use the default.
        self.assertIn("75 to 86 words", build_retention_script_prompt("Game", 30, speech_speed=3))
        self.assertIn("70 to 80 words", build_retention_script_prompt("Game", 30, speech_speed="bad"))


class OpeningPromptTests(unittest.TestCase):
    def test_steam_default_keeps_the_formula(self):
        prompt = build_retention_script_prompt("Game", hook_type="steam_new_game")
        self.assertEqual(prompt, build_retention_script_prompt("Game"))
        self.assertIn("First Words: Formulaic opening phrase (category: HERE_IS_A_NEW", prompt)
        self.assertNotIn("OPENING MODE", prompt)
        # Unknown values (e.g. the wizard's "caveman") behave like the default.
        self.assertEqual(build_retention_script_prompt("Game", hook_type="caveman"), prompt)

    def test_so_in_this_new_game_is_biased_to_its_formula(self):
        prompt = build_retention_script_prompt("Game", hook_type="so_in_this_new_game")
        self.assertIn("First Words MUST use category SO_IN_THIS_NEW", prompt)
        self.assertIn('"first_words": { "category": "SO_IN_THIS_NEW", "text": "So in this new" }', prompt)

    def test_direct_mode_forbids_formula_openers(self):
        prompt = build_retention_script_prompt("Game", hook_type="direct")
        self.assertIn("Do NOT start with a formulaic opener", prompt)
        for opener in ('"Here\'s a new..."', '"So in this new..."', '"Imagine..."'):
            self.assertIn(opener, prompt)
        self.assertIn("first 3-5 spoken words must already show the strongest USP", prompt)
        self.assertIn("6 to 12 words MAX", prompt)
        self.assertNotIn('"category": "HERE_IS_A_NEW"', prompt)
        self.assertNotIn("Here's a new co-op game where you weld leaks", prompt)
        self.assertIn('"first_words": { "category": "CUSTOM", "text": "" }', prompt)

    def test_custom_mode_requires_the_opening_verbatim(self):
        opening = 'Big news, "co-op" fans!'
        prompt = build_retention_script_prompt("Game", hook_type="custom", opening_text=opening)
        self.assertIn('MUST begin EXACTLY with this opening text, verbatim: "Big news, \\"co-op\\" fans!"', prompt)
        self.assertIn("Word limits include the opening text (4 words)", prompt)
        self.assertIn('"text": "Big news, \\"co-op\\" fans! You and your friends weld leaks while monsters hunt."', prompt)
        self.assertNotIn("Here's a new co-op game", prompt)

    def test_custom_without_text_falls_back_to_default(self):
        self.assertEqual(resolve_hook_type("custom", "  "), "steam_new_game")
        self.assertEqual(build_retention_script_prompt("Game", hook_type="custom"), build_retention_script_prompt("Game"))

    def test_caveman_formula_still_wins(self):
        base = build_retention_script_prompt("Game", script_mode="caveman")
        self.assertEqual(build_retention_script_prompt("Game", script_mode="caveman", hook_type="direct"), base)
        custom = build_retention_script_prompt("Game", script_mode="caveman", hook_type="custom", opening_text="Trapster presents")
        self.assertIn('before the caveman formula: "Trapster presents"', custom)
        self.assertIn("New [game genre] game explained by caveman.", custom)


class OpeningValidationAndRepairTests(unittest.TestCase):
    def test_validator_accepts_a_direct_script(self):
        ok, errors, warnings = validate_retention_script(direct_script(), hook_type="direct")
        self.assertTrue(ok)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        # The default mode still reports the missing formula components.
        _, _, default_warnings = validate_retention_script(direct_script())
        self.assertIn("Hook missing explicit 'first_words' component.", default_warnings)

    def test_validator_flags_formula_in_direct_and_hook_over_12_words(self):
        data = direct_script()
        data["hook"]["text"] = "Here's a new Steam game where you and your friends weld leaks while monsters hunt."
        _, _, warnings = validate_retention_script(data, hook_type="direct")
        self.assertIn("Direct opening selected but the hook starts with a formulaic opener.", warnings)
        self.assertTrue(any("exceeds recommended maximum" in w for w in warnings))

    def test_direct_repair_never_forces_here_is_a_new(self):
        data = direct_script()
        del data["hook"]["first_words"]
        script = parse_and_repair_retention_script(json.dumps(data), hook_type="direct")
        self.assertEqual(script.hook.first_words.category, FirstWordsTaxonomy.CUSTOM)
        self.assertEqual(script.hook.first_words.text, "")
        self.assertTrue(script.clean_script_text.startswith("You and your friends weld leaks"))
        # Without hook_type the historical default remains.
        legacy = parse_and_repair_retention_script(json.dumps(data))
        self.assertEqual(legacy.hook.first_words.category, FirstWordsTaxonomy.HERE_IS_A_NEW)

    def test_direct_repair_removes_a_stray_formula_opener(self):
        data = direct_script()
        stray = "Here's a new Steam game where you and your friends weld leaks while monsters hunt you."
        data["hook"]["text"] = stray
        data["sentences"][0]["text"] = stray
        data["hook"]["first_words"] = {"category": "HERE_IS_A_NEW", "text": "Here's a new"}
        script = parse_and_repair_retention_script(data, hook_type="direct")
        self.assertEqual(script.sentences[0].text, "You and your friends weld leaks while monsters hunt you.")
        self.assertEqual(script.hook.text, script.sentences[0].text)
        self.assertEqual(script.hook.first_words.category, FirstWordsTaxonomy.CUSTOM)
        self.assertIn("Formulaic opener removed for the direct opening mode.", script.validation_warnings)
        heuristic = parse_and_repair_retention_script("Here's a new Steam game; you dig tunnels. Lava rises. Wishlist now!",
                                                      hook_type="direct")
        self.assertEqual(heuristic.clean_script_text, "You dig tunnels. Lava rises. Wishlist now!")
        self.assertEqual(heuristic.hook.first_words.category, FirstWordsTaxonomy.CUSTOM)
        self.assertEqual(heuristic.hook.game_genre.text, "")

    def test_custom_repair_prefixes_missing_opening(self):
        opening = "Here's a new Steam game."
        script = parse_and_repair_retention_script(json.dumps(direct_script()), hook_type="custom", opening_text=opening)
        self.assertTrue(script.clean_script_text.startswith("Here's a new Steam game. You and your friends weld leaks"))
        self.assertTrue(script.hook.text.startswith(opening))
        self.assertEqual(script.hook.first_words.text, "Here's a new Steam game")
        self.assertIn("Required opening text was missing and has been added at the start.", script.validation_warnings)
        self.assertEqual(len(script.sentences), 4)

        plain = parse_and_repair_retention_script("You weld leaks. Monsters hunt you. Wishlist now!",
                                                  hook_type="custom", opening_text="Trapster presents")
        self.assertEqual(plain.clean_script_text, "Trapster presents. You weld leaks. Monsters hunt you. Wishlist now!")
        self.assertEqual(plain.hook.first_words.category, FirstWordsTaxonomy.CUSTOM)
        self.assertEqual(plain.hook.first_words.text, "Trapster presents")

    def test_custom_repair_keeps_a_present_opening_and_never_edits_voiced_text(self):
        data = direct_script()
        data["sentences"][0]["text"] = "big news for co-op fans; you weld leaks while monsters hunt."
        script = parse_and_repair_retention_script(data, hook_type="custom", opening_text="Big news for co-op fans")
        self.assertTrue(script.sentences[0].text.startswith("Big news for co-op fans; you weld leaks"))
        self.assertNotIn("Required opening text was missing and has been added at the start.", script.validation_warnings)
        voiced = parse_and_repair_retention_script("You weld leaks. Monsters hunt you.", hook_type="custom",
                                                   opening_text="Trapster presents",
                                                   words=[{"word": "You", "start": 0, "end": .2}])
        self.assertEqual(voiced.clean_script_text, "You weld leaks. Monsters hunt you.")
        # Hook components must describe what is spoken, or the word timings drift.
        self.assertNotEqual(voiced.hook.first_words.text, "Trapster presents")
        voiced_json = parse_and_repair_retention_script(json.dumps(direct_script()), hook_type="custom",
                                                        opening_text="Trapster presents",
                                                        words=[{"word": "You", "start": 0, "end": .2}])
        self.assertTrue(voiced_json.clean_script_text.startswith("You and your friends"))
        self.assertEqual(voiced_json.hook.first_words.text, "")


class OpeningHelperTests(unittest.TestCase):
    def test_strip_formula_opener_is_conservative(self):
        self.assertEqual(strip_formulaic_opener("Here's a new Steam game where you build boats."), "You build boats.")
        self.assertEqual(strip_formulaic_opener("So in this new game, monsters hunt you."), "Monsters hunt you.")
        self.assertEqual(strip_formulaic_opener("Here's a new Steam game that lets you fly."), "You fly.")
        for unchanged in ("Here's a new Steam game called Boat where you sail.", "Here's a new Steam game.",
                          "You build boats while sharks circle."):
            self.assertEqual(strip_formulaic_opener(unchanged), unchanged)
        self.assertTrue(starts_with_formula_opener("Imagine a world of trains."))
        self.assertFalse(starts_with_formula_opener("You build boats."))

    def test_ensure_opening_text(self):
        self.assertEqual(ensure_opening_text("You build boats.", "Here's a new Steam game"),
                         "Here's a new Steam game. You build boats.")
        self.assertEqual(ensure_opening_text("here's a new steam game; you build boats.", "Here is a new Steam game!"),
                         "Here is a new Steam game! You build boats.")
        self.assertEqual(ensure_opening_text("You build boats.", "Check out this game where"),
                         "Check out this game where you build boats.")
        self.assertEqual(ensure_opening_text("Steam Deck players love it.", "Trapster presents"),
                         "Trapster presents. Steam Deck players love it.")
        self.assertTrue(starts_with_opening("Here is a new Steam game where you fly.", "Here's a new Steam game"))
        self.assertFalse(starts_with_opening("Here's a new game.", "Here"))


class GenerateScriptOpeningTests(unittest.TestCase):
    def generate(self, model_payload, **kwargs):
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": json.dumps(model_payload)}}]}
        with patch.object(utils, "search_steam_game", return_value="Weld leaks in a sinking sub."), \
             patch.object(utils, "load_viral_script_examples", return_value=""), \
             patch.object(utils._api_usage, "usage_post", return_value=response) as post:
            result = utils.generate_v2_script("Deep Leak", groq_api_key="fake", **kwargs)
        return result, post.call_args.kwargs["json"]["messages"]

    def test_generation_passes_opening_mode_and_speed_to_the_prompt(self):
        result, messages = self.generate(direct_script(), hook_type="direct", speech_speed=1.0)
        self.assertIn("OPENING MODE: DIRECT", messages[1]["content"])
        self.assertIn("63 to 72 words", messages[1]["content"])
        self.assertIn("Follow the user prompt's OPENING MODE exactly", messages[0]["content"])
        self.assertTrue(result["script"].startswith("You and your friends weld leaks"))
        self.assertEqual(result["retention_data"]["hook"]["first_words"]["category"], "CUSTOM")

    def test_custom_opening_is_prefixed_and_kept_verbatim(self):
        opening = "Big news, co-op fans: here is Deep Leak."
        result, messages = self.generate(direct_script(), hook_type="custom", opening_text=opening)
        self.assertIn("MUST begin EXACTLY with this opening text", messages[1]["content"])
        # Commas, colons and "here is" survive the TTS clean-up inside the opener only.
        self.assertTrue(result["script"].startswith("Big news, co-op fans: here is Deep Leak. You and your friends"))
        self.assertIn("Wishlist Deep Leak on Steam!", result["script"])

    def test_default_generation_is_unchanged(self):
        result, messages = self.generate(direct_script())
        self.assertNotIn("OPENING MODE", messages[1]["content"])
        self.assertNotIn("OPENING MODE", messages[0]["content"])
        self.assertIn("70 to 80 words", messages[1]["content"])

    def test_apply_opening_per_mode(self):
        text = "Here's a new Steam game where you build boats."
        self.assertEqual(utils.apply_opening(text, "steam_new_game"), utils.ensure_steam_hook_prefix(text))
        self.assertEqual(utils.apply_opening(text, None), utils.ensure_steam_hook_prefix(text))
        self.assertEqual(utils.apply_opening("You dig.", "so_in_this_new_game"),
                         utils.ensure_hook_type_prefix("You dig.", "so_in_this_new_game"))
        self.assertEqual(utils.apply_opening(text, "direct"), "You build boats.")
        self.assertEqual(utils.apply_opening("You build boats, fast.", "custom", "Trapster, presents"),
                         "Trapster, presents. You build boats; fast.")
        self.assertEqual(utils.apply_opening("Me build boat.", "caveman"), "Me build boat.")

    def test_custom_opening_keeps_its_own_trailing_punctuation(self):
        # The voice cleaner turns ':' / ',' into ';' in the body, never inside the mandated opener.
        self.assertEqual(utils.apply_opening("You build towers, then you defend them.", "custom", "Wishlist alert:"),
                         "Wishlist alert: you build towers; then you defend them.")
        self.assertEqual(utils.apply_opening("Wishlist alert: you build towers.", "custom", "Wishlist alert:"),
                         "Wishlist alert: you build towers.")
        self.assertEqual(utils.apply_opening("You build, then defend.", "custom", "Hey, gamers —"),
                         "Hey, gamers — you build; then defend.")
        # Opener already spoken without its colon: the colon is restored.
        self.assertEqual(ensure_opening_text("wishlist ALERT you build towers.", "Wishlist alert:"),
                         "Wishlist alert: you build towers.")
        # A final full stop is not forced into a sentence that continues.
        self.assertEqual(ensure_opening_text("Here is a new Steam game where you fly.", "Here's a new Steam game."),
                         "Here's a new Steam game where you fly.")


if __name__ == "__main__":
    unittest.main()
