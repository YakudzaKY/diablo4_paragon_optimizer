import json
import unittest

from paragon_optimizer.crawler.normalize import (
    infer_edges,
    node_type,
    parse_bonus_stats_from_tooltip,
    parse_stats_from_attributes,
    parse_stats_from_search_text,
)
from paragon_optimizer.crawler.wowhead_crawler import (
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
        stats = parse_stats_from_attributes(["+10 Strength", "4.0% Maximum Life", "+3.0% Total Armor"])

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

        self.assertEqual(parse_bonus_stats_from_tooltip(tooltip), {"damage_to_elites": 15.0})

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
