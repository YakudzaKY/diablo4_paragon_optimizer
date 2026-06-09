import json
import unittest

from crawler.normalize import (
    infer_edges,
    node_type,
    parse_bonus_stats_from_tooltip,
    parse_legendary_bonus,
    parse_node_bonus,
    parse_requirements,
    parse_scaling_value_per_5,
    parse_stats_from_attributes,
    parse_stats_from_search_text,
)
from crawler.wowhead_crawler import (
    extract_json_script,
    filter_list_page_by_class,
    infer_class_name_from_items,
    parse_wowhead_page_data_script,
)


class WowheadCrawlerParsingTests(unittest.TestCase):
    def test_parse_wowhead_page_data_script(self) -> None:
        script = '''
        WH.setPageData("d4.paragonCalc.d4.nodes", {"1":{"searchText":"normal node"}});
        WH.setPageData("d4.paragonCalc.d4.glyphs", [{"sno":1029491,"name":"Control"}]);
        '''

        parsed = parse_wowhead_page_data_script(script)

        self.assertEqual(parsed["d4.paragonCalc.d4.nodes"]["1"]["searchText"], "normal node")
        self.assertEqual(parsed["d4.paragonCalc.d4.glyphs"][0]["name"], "Control")

    def test_extract_json_script(self) -> None:
        payload = [{"template": "d4-paragon-glyph", "data": [{"id": 1, "name": "Control"}]}]
        html = (
            '<script type="application/json" id="data.page.listPage.listviews">'
            f"{json.dumps(payload)}"
            "</script>"
        )

        self.assertEqual(extract_json_script(html, "data.page.listPage.listviews"), payload)

    def test_infer_edges_uses_grid_adjacency(self) -> None:
        nodes = [
            {"id": "a", "x": 0, "y": 0},
            {"id": "b", "x": 1, "y": 0},
            {"id": "c", "x": 1, "y": 1},
            {"id": "d", "x": 3, "y": 3},
        ]

        self.assertEqual(infer_edges(nodes), [["a", "b"], ["b", "c"]])

    def test_node_type_handles_special_nodes(self) -> None:
        self.assertEqual(node_type(681756, {}, None), "glyph_socket")
        self.assertEqual(node_type(994337, {}, None), "board_gate")
        self.assertEqual(node_type(123, {"quality": 5}, None), "legendary")

    def test_parse_stats_from_class_board_attributes(self) -> None:
        stats, lost = parse_stats_from_attributes(["+10 Strength", "4.0% Maximum Life", "+3.0% Total Armor"])

        self.assertEqual(lost, [])
        self.assertEqual(stats["strength"], 10)
        self.assertEqual(stats["max_life"], 4)
        self.assertEqual(stats["armor"], 3)

    def test_parse_stats_from_search_text_common_magic_nodes(self) -> None:
        cases = [
            ("strength intelligence willpower dexterity board attachment gate +5 all classes", {
                "strength": 5,
                "intelligence": 5,
                "willpower": 5,
                "dexterity": 5,
            }),
            ("life magic node 2.0% maximum all classes", {"max_life": 2}),
            ("vulnerable damage magic node +6.25% all classes", {"vulnerable_damage": 6.25}),
            ("critical strikes damage magic node +7.5% strike all classes", {"critical_strike_damage": 7.5}),
            ("healing magic node +3.0% received all classes", {"healing_received": 3}),
            ("healthy damage magic node +6.3% while barbarian, druid, necromancer, paladin", {"damage_while_healthy": 6.3}),
            ("healthy damage magic node +6.3% to enemies necromancer, rogue", {"damage_to_healthy_enemies": 6.3}),
            # form / school damage magic nodes use qualified stat keys
            ("earth damage magic node +5.0% druid", {"earth_damage": 5.0}),
            ("storm damage magic node +5.0% druid", {"storm_damage": 5.0}),
            ("core damage magic node +7.0% druid, rogue", {"core_damage": 7.0}),
            ("shapeshifting damage magic node +5.0% druid", {"shapeshifting_damage": 5.0}),
            ("werewolf damage magic node +5.0% druid", {"werewolf_damage": 5.0}),
            ("werebear damage magic node +5.0% while in form druid", {"werebear_damage": 5.0}),
            ("poison damage magic node +5.0% to poisoned enemies druid", {"damage_to_poisoned": 5.0}),
            ("fire damage magic node +5% sorcerer", {"fire_damage": 5}),
            ("cold damage magic node +5% sorcerer", {"cold_damage": 5}),
            ("lightning damage magic node +5% sorcerer", {"lightning_damage": 5}),
            ("shadow damage magic node +5% necromancer", {"shadow_damage": 5}),
            ("shadow damage over time magic node +7.5% necromancer", {"shadow_damage": 7.5}),
            ("non-physical damage magic node +5.0% rogue, sorcerer, spiritborn", {"non_physical_damage": 5.0}),
        ]

        for search_text, expected in cases:
            with self.subTest(search_text=search_text):
                self.assertEqual(parse_stats_from_search_text(search_text), expected)

    def test_parse_bonus_stats_from_rare_tooltip(self) -> None:
        tooltip = """Rare Node
Iron Strength
+15.0% Damage to Elites
+10 Strength
Bonus: Another +15.0% Damage to Elites if requirements met:
190 Willpower
Barbarian, Paladin"""

        bonus, lost = parse_bonus_stats_from_tooltip(tooltip)
        self.assertEqual(lost, [])
        self.assertEqual(bonus, {"damage_to_elites": 15.0})

    def test_parse_requirements_class_specific_from_tooltip(self) -> None:
        slayer_tooltip = (
            "Rare Node \nSlayer\n+3.0% Total Armor\n4.0% Maximum Life\n"
            "Bonus: Another +3.0% Total Armor if requirements met:\n"
            "210 Strength (Rogue, Spiritborn) \n700 Strength, 190 Willpower (Barbarian) \n"
            "700 Willpower, 190 Intelligence (Druid) \nBarbarian, Druid, Rogue, Spiritborn"
        )
        self.assertEqual(
            parse_requirements("", "druid", slayer_tooltip),
            {"willpower": 700.0, "intelligence": 190.0},
        )
        self.assertEqual(
            parse_requirements("", "barbarian", slayer_tooltip),
            {"strength": 700.0, "willpower": 190.0},
        )
        self.assertEqual(parse_requirements("", "rogue", slayer_tooltip), {"strength": 210.0})
        self.assertEqual(parse_requirements("", "spiritborn", slayer_tooltip), {"strength": 210.0})

        # Single stat class-specific
        single = "Bonus: Another +10% Damage if requirements met:\n210 Intelligence \nDruid"
        self.assertEqual(parse_requirements("", "druid", single), {"intelligence": 210.0})

        # Shared paren list + another class
        shared = (
            "Bonus: Another +X if requirements met:\n"
            "190 Intelligence (Druid, Rogue, Spiritborn, Warlock) \n190 Willpower (Necromancer)"
        )
        self.assertEqual(parse_requirements("", "druid", shared), {"intelligence": 190.0})
        self.assertEqual(parse_requirements("", "necromancer", shared), {"willpower": 190.0})

        # Universal (no class tags) falls back to numbers found
        uni = "bonus: another if requirements met: 700 willpower 190 intelligence"
        self.assertEqual(parse_requirements(uni), {"willpower": 700.0, "intelligence": 190.0})

        # Mangled searchText still works when tooltip is supplied
        mangled = "healing ... bonus: another if requirements met: 210 strength (rogue, spiritborn) 700 strength, 190 willpower (barbarian) willpower, intelligence (druid) ..."
        self.assertEqual(
            parse_requirements(mangled, "druid", slayer_tooltip),
            {"willpower": 700.0, "intelligence": 190.0},
        )

    def test_parse_glyph_node_bonus_from_english_bonus_text(self) -> None:
        cases = [
            (
                "Grants +30.0% bonus to all Magic nodes within range.",
                {"node_type": "magic", "bonus_percent": 30.0, "multiplier": 0.3, "source": "bonus_text.en"},
            ),
            (
                "Grants +25.0% bonus to all Rare nodes within range.",
                {"node_type": "rare", "bonus_percent": 25.0, "multiplier": 0.25, "source": "bonus_text.en"},
            ),
            (
                "Grants +20.0% bonus to all Normal nodes within range.",
                {"node_type": "normal", "bonus_percent": 20.0, "multiplier": 0.2, "source": "bonus_text.en"},
            ),
        ]

        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_node_bonus({"en": text, "ru": None}), expected)

    def test_parse_glyph_node_bonus_level_scaling_samples(self) -> None:
        parsed = parse_node_bonus(
            {"en": "Grants +30.0% bonus to all Magic nodes within range.", "ru": None},
            {
                74: "Grants +249.0% bonus to all Magic nodes within range.",
                150: "Grants +477.0% bonus to all Magic nodes within range.",
            },
        )

        self.assertIsNotNone(parsed)
        samples = parsed["level_scaling"]["samples"]
        self.assertEqual([sample["level"] for sample in samples], [1, 74, 150])
        self.assertEqual([sample["bonus_percent"] for sample in samples], [30.0, 249.0, 477.0])
        self.assertEqual([sample["multiplier"] for sample in samples], [0.3, 2.49, 4.77])

    def test_parse_scaling_value_per_5_level_samples(self) -> None:
        parsed = parse_scaling_value_per_5(
            {
                "en": (
                    "For every 5 Willpower purchased within range, you deal +0.5% increased Nature Damage."
                    "Additional Bonus: You deal 18% increased Nature Damage."
                ),
                "ru": None,
            },
            {
                74: "For every 5 Willpower purchased within range, you deal +7.8% increased Nature Damage.",
                150: "For every 5 Willpower purchased within range, you deal +15.4% increased Nature Damage.",
            },
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["value"], 0.5)
        self.assertEqual([sample["level"] for sample in parsed["samples"]], [1, 74, 150])
        self.assertEqual([sample["value"] for sample in parsed["samples"]], [0.5, 7.8, 15.4])

    def test_parse_scaling_value_per_5_without_plus_sign(self) -> None:
        parsed = parse_scaling_value_per_5(
            {"en": "For every 5 Dexterity purchased within range, your Earth Skills gain 1.5% Critical Strike Damage.", "ru": None},
            {100: "For every 5 Dexterity purchased within range, your Earth Skills gain 13.0% Critical Strike Damage."},
        )

        self.assertIsNotNone(parsed)
        self.assertEqual([sample["value"] for sample in parsed["samples"]], [1.5, 13.0])

    def test_parse_legendary_bonus_level_samples(self) -> None:
        parsed = parse_legendary_bonus(
            {
                "en": (
                    "Legendary Bonus: Increase Nature Magic skill damage by 0.5% [x] . "
                    "Required: Legendary Upgrade (unlocks at Level 50)"
                ),
                "ru": None,
            },
            {
                100: "Legendary Bonus: Increase Nature Magic skill damage by 10.4% [x] .",
                150: "Legendary Bonus: Increase Nature Magic skill damage by 15.4% [x] .",
            },
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["value"], 0.5)
        self.assertEqual([sample["level"] for sample in parsed["level_scaling"]["samples"]], [1, 100, 150])
        self.assertEqual([sample["value"] for sample in parsed["level_scaling"]["samples"]], [0.5, 10.4, 15.4])

    def test_parse_glyph_node_bonus_returns_none_without_matching_effect(self) -> None:
        bonus_text = {
            "en": "For every 5 Dexterity purchased within range, you deal +2.0% increased Critical Strike Damage.",
            "ru": "За каждые 5 ед. ловкости, открытых в радиусе действия, наносимый критический урон увеличивается на 2.0% .",
        }

        self.assertIsNone(parse_node_bonus(bonus_text))

    def test_infer_and_filter_class_names_from_list_items(self) -> None:
        list_page = {
            "source_url": "https://example.test",
            "listviews": [
                {
                    "data": [
                        {"id": 1, "playerClassNames": "Rogue, Paladin"},
                        {"id": 2, "playerClassNames": "Paladin"},
                        {"id": 3, "playerClassNames": "Warlock"},
                    ]
                }
            ],
        }

        self.assertEqual(infer_class_name_from_items(list_page["listviews"][0]["data"]), "Paladin")
        filtered = filter_list_page_by_class(list_page, "Paladin")
        self.assertEqual([item["id"] for item in filtered["listviews"][0]["data"]], [1, 2])


if __name__ == "__main__":
    unittest.main()
