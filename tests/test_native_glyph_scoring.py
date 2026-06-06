import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def native_binary() -> Path | None:
    for name in ("paragon_optimize.exe", "paragon_optimize"):
        candidate = REPO_ROOT / "bin" / name
        if candidate.exists():
            return candidate
    return None


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def glyph_payload(
    glyph_id: str,
    *,
    threshold_stat: str | None = None,
    requirement: float = 999,
    node_bonus: dict[str, object] | None = None,
) -> dict[str, object]:
    threshold_attributes = []
    if threshold_stat:
        threshold_attributes.append({"stat_key": threshold_stat})
    return {
        "id": glyph_id,
        "class": "synthetic",
        "name": {"en": glyph_id},
        "max_level": 100,
        "radius": {
            "starting": 3,
            "legendary": 5,
            "upgrade_levels": [25, 50],
        },
        "threshold_attributes": threshold_attributes,
        "skill_tags": [],
        "bonus_text": {
            "en": (
                f"Rare Glyph\n{glyph_id}\nRadius Size: 3\n"
                "Additional Bonus:\nSynthetic bonus.\n"
                f"Required (purchased in range): +{requirement:g} {threshold_stat or 'Willpower'}"
            ),
            "ru": "",
        },
        "node_bonus": node_bonus,
    }


class NativeGlyphScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.binary = native_binary()
        if cls.binary is None:
            raise unittest.SkipTest("native optimizer binary is not built")

    def run_optimizer(
        self,
        *,
        nodes: list[dict[str, object]],
        edges: list[list[str]],
        glyph_sockets: list[str],
        glyphs: list[dict[str, object]],
        weights: dict[str, object],
        starting_stats: dict[str, float] | None = None,
        points: int = 20,
        include_route_steps: bool = False,
    ) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root = root / "data" / "normalized"
            glyph_ids = [str(glyph["id"]) for glyph in glyphs]
            write_json(
                data_root / "classes" / "synthetic.json",
                {
                    "class": "synthetic",
                    "name": {"en": "Synthetic"},
                    "available_stats": ["damage", "willpower"],
                    "available_boards": ["starter"],
                    "boards": ["starter"],
                    "glyphs": glyph_ids,
                    "primary_attributes": ["willpower"],
                },
            )
            write_json(
                data_root / "boards" / "synthetic" / "starter.json",
                {
                    "id": "starter",
                    "class": "synthetic",
                    "name": {"en": "Starter"},
                    "width": 20,
                    "height": 8,
                    "start_node": "start",
                    "nodes": nodes,
                    "edges": edges,
                    "gates": [],
                    "glyph_sockets": glyph_sockets,
                    "legendary_nodes": [],
                },
            )
            for glyph in glyphs:
                write_json(data_root / "glyphs" / "synthetic" / f"{glyph['id']}.json", glyph)

            weights_path = root / "weights.json"
            write_json(weights_path, weights)
            profile_path = root / "profile.json"
            write_json(
                profile_path,
                {
                    "class": "synthetic",
                    "points": points,
                    "weights": str(weights_path),
                    "data": str(data_root),
                    "glyph_levels": {glyph_id: 1 for glyph_id in glyph_ids},
                    "starting_stats": starting_stats or {},
                    "max_routes": 1,
                    "candidate_targets": 20,
                    "workers": 1,
                    "include_route_steps": include_route_steps,
                    "no_html": True,
                },
            )

            command = [str(self.binary), "optimize", "--profile", str(profile_path), "--no-html"]
            if include_route_steps:
                command.append("--include-route-steps")
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            return json.loads(completed.stdout)

    def test_node_bonus_glyph_prefers_socket_with_stronger_matching_nodes_even_when_requirement_unmet(self) -> None:
        magic_bonus = glyph_payload(
            "magic_bonus",
            threshold_stat="willpower",
            requirement=999,
            node_bonus={"node_type": "magic", "bonus_percent": 30.0, "multiplier": 0.3},
        )
        filler = glyph_payload("filler")
        payload = self.run_optimizer(
            nodes=[
                {"id": "start", "x": 5, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                {"id": "socket_a", "x": 0, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
                {"id": "strong_magic", "x": 0, "y": 2, "type": "magic", "cost": 1, "stats": {"damage": 10}},
                {"id": "socket_b", "x": 10, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
                {"id": "weak_magic", "x": 10, "y": 2, "type": "magic", "cost": 1, "stats": {"damage": 1}},
            ],
            edges=[
                ["start", "socket_a"],
                ["socket_a", "strong_magic"],
                ["start", "socket_b"],
                ["socket_b", "weak_magic"],
            ],
            glyph_sockets=["socket_a", "socket_b"],
            glyphs=[magic_bonus, filler],
            weights={"weights": {"damage": 1.0, "glyph_socket": 5.0}},
        )

        glyphs = {item["socket"]: item for item in payload["results"][0]["glyphs"]}
        self.assertEqual(glyphs["socket_a"]["glyph"], "magic_bonus")
        self.assertFalse(glyphs["socket_a"]["requirement_met"])
        self.assertAlmostEqual(glyphs["socket_a"]["node_bonus_score"], 3.0)

    def test_node_bonus_assignment_uses_active_rare_bonus_stats_in_candidate_score(self) -> None:
        rare_bonus = glyph_payload(
            "rare_bonus",
            threshold_stat="willpower",
            requirement=999,
            node_bonus={"node_type": "rare", "bonus_percent": 25.0, "multiplier": 0.25},
        )
        filler = glyph_payload("filler")
        payload = self.run_optimizer(
            nodes=[
                {"id": "start", "x": 5, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                {"id": "socket_a", "x": 0, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
                {
                    "id": "high_base_rare",
                    "x": 0,
                    "y": 2,
                    "type": "rare",
                    "cost": 1,
                    "stats": {"damage": 10},
                    "requirements": {"willpower": 1},
                    "bonus_stats": {},
                },
                {"id": "socket_b", "x": 10, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
                {
                    "id": "high_bonus_rare",
                    "x": 10,
                    "y": 2,
                    "type": "rare",
                    "cost": 1,
                    "stats": {"damage": 1},
                    "requirements": {"willpower": 1},
                    "bonus_stats": {"damage": 100},
                },
            ],
            edges=[
                ["start", "socket_a"],
                ["socket_a", "high_base_rare"],
                ["start", "socket_b"],
                ["socket_b", "high_bonus_rare"],
            ],
            glyph_sockets=["socket_a", "socket_b"],
            glyphs=[rare_bonus, filler],
            weights={"weights": {"damage": 1.0, "glyph_socket": 5.0}},
            starting_stats={"willpower": 1},
        )

        glyphs = {item["socket"]: item for item in payload["results"][0]["glyphs"]}
        self.assertEqual(glyphs["socket_b"]["glyph"], "rare_bonus")
        self.assertAlmostEqual(glyphs["socket_b"]["node_bonus_score"], 25.25)

    def test_candidate_requirement_uses_effective_stats_after_its_node_bonus(self) -> None:
        magic_bonus = glyph_payload(
            "magic_bonus",
            threshold_stat="willpower",
            requirement=40,
            node_bonus={"node_type": "magic", "bonus_percent": 30.0, "multiplier": 0.3},
        )
        payload = self.run_optimizer(
            nodes=[
                {"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                {"id": "socket_a", "x": 0, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
                {"id": "willpower_magic", "x": 0, "y": 2, "type": "magic", "cost": 1, "stats": {"willpower": 35}},
            ],
            edges=[["start", "socket_a"], ["socket_a", "willpower_magic"]],
            glyph_sockets=["socket_a"],
            glyphs=[magic_bonus],
            weights={"weights": {"willpower": 1.0, "glyph_socket": 5.0}},
        )

        glyph = payload["results"][0]["glyphs"][0]
        self.assertEqual(glyph["glyph"], "magic_bonus")
        self.assertTrue(glyph["requirement_met"])
        self.assertAlmostEqual(glyph["stat_in_radius"], 45.5)
        self.assertAlmostEqual(glyph["node_bonus_score"], 10.5)

    def test_final_node_bonus_score_is_kept_when_route_node_bonus_hint_is_disabled(self) -> None:
        magic_bonus = glyph_payload(
            "magic_bonus",
            node_bonus={"node_type": "magic", "bonus_percent": 30.0, "multiplier": 0.3},
        )
        payload = self.run_optimizer(
            nodes=[
                {"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                {"id": "socket_a", "x": 0, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
                {"id": "willpower_magic", "x": 0, "y": 2, "type": "magic", "cost": 1, "stats": {"willpower": 35}},
            ],
            edges=[["start", "socket_a"], ["socket_a", "willpower_magic"]],
            glyph_sockets=["socket_a"],
            glyphs=[magic_bonus],
            weights={
                "weights": {"willpower": 1.0, "glyph_socket": 5.0},
                "glyph_route": {"node_bonus": 0.0},
            },
            points=2,
        )

        glyph = payload["results"][0]["glyphs"][0]
        self.assertEqual(glyph["glyph"], "magic_bonus")
        self.assertAlmostEqual(glyph["node_bonus_score"], 10.5)
        self.assertIn("willpower_magic", payload["results"][0]["selected_nodes"])

    def test_route_node_bonus_hint_prefers_strengthened_nodes_in_radius(self) -> None:
        magic_bonus = glyph_payload(
            "magic_bonus",
            node_bonus={"node_type": "magic", "bonus_percent": 30.0, "multiplier": 0.3},
        )

        def run(node_bonus_hint: float) -> dict[str, object]:
            return self.run_optimizer(
                nodes=[
                    {"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                    {"id": "socket_a", "x": 0, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
                    {"id": "plain_value", "x": 1, "y": 1, "type": "normal", "cost": 2, "stats": {"damage": 11}},
                    {"id": "path_to_bonus", "x": 0, "y": 2, "type": "normal", "cost": 1, "stats": {}},
                    {"id": "bonus_magic", "x": 0, "y": 3, "type": "magic", "cost": 1, "stats": {"damage": 10}},
                ],
                edges=[
                    ["start", "socket_a"],
                    ["socket_a", "plain_value"],
                    ["socket_a", "path_to_bonus"],
                    ["path_to_bonus", "bonus_magic"],
                ],
                glyph_sockets=["socket_a"],
                glyphs=[magic_bonus],
                weights={
                    "weights": {"damage": 1.0, "glyph_socket": 5.0},
                    "glyph_route": {
                        "node_bonus": node_bonus_hint,
                        "cluster": 0.0,
                        "detour": 0.0,
                        "max_bonus_multiplier": 1.60,
                    },
                },
                points=3,
                include_route_steps=True,
            )

        without_hint = run(0.0)["results"][0]
        with_hint = run(1.0)["results"][0]

        self.assertIn("plain_value", without_hint["selected_nodes"])
        self.assertNotIn("bonus_magic", without_hint["selected_nodes"])
        self.assertIn("bonus_magic", with_hint["selected_nodes"])
        self.assertNotIn("plain_value", with_hint["selected_nodes"])
        self.assertAlmostEqual(with_hint["glyphs"][0]["node_bonus_score"], 3.0)

    def test_route_node_bonus_hint_includes_potential_rare_bonus_stats(self) -> None:
        rare_bonus = glyph_payload(
            "rare_bonus",
            node_bonus={"node_type": "rare", "bonus_percent": 25.0, "multiplier": 0.25},
        )

        def run(node_bonus_hint: float) -> dict[str, object]:
            return self.run_optimizer(
                nodes=[
                    {"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                    {"id": "socket_a", "x": 0, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
                    {"id": "plain_value", "x": 1, "y": 1, "type": "normal", "cost": 2, "stats": {"damage": 8}},
                    {"id": "path_to_rare", "x": 0, "y": 2, "type": "normal", "cost": 1, "stats": {}},
                    {
                        "id": "bonus_rare",
                        "x": 0,
                        "y": 3,
                        "type": "rare",
                        "cost": 1,
                        "stats": {"damage": 1},
                        "requirements": {"willpower": 999},
                        "bonus_stats": {"damage": 10},
                    },
                ],
                edges=[
                    ["start", "socket_a"],
                    ["socket_a", "plain_value"],
                    ["socket_a", "path_to_rare"],
                    ["path_to_rare", "bonus_rare"],
                ],
                glyph_sockets=["socket_a"],
                glyphs=[rare_bonus],
                weights={
                    "weights": {"damage": 1.0, "glyph_socket": 5.0},
                    "glyph_route": {
                        "node_bonus": node_bonus_hint,
                        "cluster": 0.0,
                        "detour": 0.0,
                        "max_bonus_multiplier": 1.60,
                    },
                },
                points=3,
            )

        without_hint = run(0.0)["results"][0]
        with_hint = run(1.0)["results"][0]

        self.assertIn("plain_value", without_hint["selected_nodes"])
        self.assertNotIn("bonus_rare", without_hint["selected_nodes"])
        self.assertIn("bonus_rare", with_hint["selected_nodes"])
        self.assertNotIn("plain_value", with_hint["selected_nodes"])


if __name__ == "__main__":
    unittest.main()
