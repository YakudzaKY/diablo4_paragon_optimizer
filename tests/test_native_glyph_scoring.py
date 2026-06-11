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
    scaling_value_per_5: dict[str, object] | None = None,
    skill_tags: list[dict[str, object]] | None = None,
    bonus_text_en: str | None = None,
    radius: dict[str, object] | None = None,
    max_level: int = 100,
) -> dict[str, object]:
    threshold_attributes = []
    if threshold_stat:
        threshold_attributes.append({"stat_key": threshold_stat})
    return {
        "id": glyph_id,
        "class": "synthetic",
        "name": {"en": glyph_id},
        "max_level": max_level,
        "radius": radius or {
            "starting": 3,
            "legendary": 5,
            "upgrade_levels": [25, 50],
        },
        "threshold_attributes": threshold_attributes,
        "skill_tags": skill_tags or [],
        "bonus_text": {
            "en": bonus_text_en or (
                f"Rare Glyph\n{glyph_id}\nRadius Size: 3\n"
                "Additional Bonus:\nSynthetic bonus.\n"
                f"Required (purchased in range): +{requirement:g} {threshold_stat or 'Willpower'}"
            ),
            "ru": "",
        },
        "scaling_value_per_5": scaling_value_per_5,
        "node_bonus": node_bonus,
    }


class NativeGlyphScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.binary = native_binary()
        if cls.binary is None:
            raise unittest.SkipTest("native optimizer binary is not built")

    def write_optimizer_fixture(
        self,
        *,
        root: Path,
        nodes: list[dict[str, object]],
        edges: list[list[str]],
        glyph_sockets: list[str],
        glyphs: list[dict[str, object]],
        weights: dict[str, object],
        starting_stats: dict[str, float] | None = None,
        points: int = 20,
        include_route_steps: bool = False,
        glyph_levels: dict[str, int] | None = None,
        profile_extra: dict[str, object] | None = None,
    ) -> tuple[Path, Path]:
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
        profile = {
            "class": "synthetic",
            "points": points,
            "weights": str(weights_path),
            "data": str(data_root),
            "glyph_levels": glyph_levels if glyph_levels is not None else {glyph_id: 1 for glyph_id in glyph_ids},
            "starting_stats": starting_stats or {},
            "max_routes": 1,
            "candidate_targets": 20,
            "workers": 1,
            "include_route_steps": include_route_steps,
            "no_html": True,
        }
        if profile_extra:
            profile.update(profile_extra)
        write_json(profile_path, profile)
        return profile_path, data_root

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
        glyph_levels: dict[str, int] | None = None,
        profile_extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_path, _ = self.write_optimizer_fixture(
                root=root,
                nodes=nodes,
                edges=edges,
                glyph_sockets=glyph_sockets,
                glyphs=glyphs,
                weights=weights,
                starting_stats=starting_stats,
                points=points,
                include_route_steps=include_route_steps,
                glyph_levels=glyph_levels,
                profile_extra=profile_extra,
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

    def test_schema_lists_glyph_metadata_and_profile_glyph_levels_example(self) -> None:
        magic_bonus = glyph_payload(
            "magic_bonus",
            node_bonus={"node_type": "magic", "bonus_percent": 30.0, "multiplier": 0.3},
        )
        filler = glyph_payload("filler")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, data_root = self.write_optimizer_fixture(
                root=root,
                nodes=[{"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}}],
                edges=[],
                glyph_sockets=[],
                glyphs=[magic_bonus, filler],
                weights={"weights": {}},
                points=1,
            )

            completed = subprocess.run(
                [str(self.binary), "schema", "--class", "synthetic", "--data", str(data_root)],
                cwd=REPO_ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            payload = json.loads(completed.stdout)

        glyphs = {item["id"]: item for item in payload["available_glyphs"]}
        self.assertEqual(glyphs["magic_bonus"]["max_level"], 100)
        self.assertEqual(glyphs["magic_bonus"]["radius"]["starting"], 3)
        self.assertEqual(glyphs["magic_bonus"]["radius"]["upgrade_levels"], [25, 50])
        self.assertEqual(glyphs["magic_bonus"]["node_bonus"]["node_type"], "magic")
        self.assertAlmostEqual(glyphs["magic_bonus"]["node_bonus"]["multiplier"], 0.3)
        self.assertIsNone(glyphs["filler"]["node_bonus"])
        self.assertIn("glyph_levels", payload["profile_schema_example"])
        self.assertEqual(payload["profile_schema_example"]["glyph_levels"]["magic_bonus"], 51)
        tuning_keys = {item["key"] for item in payload["weight_tuning_keys"]["weights"]}
        self.assertEqual(tuning_keys, {"glyph_bonus", "glyph_socket"})
        self.assertNotIn("glyph_bonus", payload["available_stats"])
        self.assertNotIn("glyph_socket", payload["available_stats"])
        example_weights = payload["weight_schema_example"]["weights"]
        self.assertIn("glyph_bonus", example_weights)
        self.assertIn("glyph_socket", example_weights)

    def test_glyph_radius_follows_level_upgrade_levels(self) -> None:
        expected_by_level = {
            1: 3,
            24: 3,
            25: 4,
            49: 4,
            50: 5,
            51: 5,
            100: 5,
        }
        glyph = glyph_payload("radius_test")
        for level, expected_radius in expected_by_level.items():
            with self.subTest(level=level):
                payload = self.run_optimizer(
                    nodes=[
                        {"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                        {"id": "socket_a", "x": 0, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
                    ],
                    edges=[["start", "socket_a"]],
                    glyph_sockets=["socket_a"],
                    glyphs=[glyph],
                    weights={"weights": {"glyph_socket": 5.0}},
                    points=1,
                    glyph_levels={"radius_test": level},
                )

                self.assertEqual(payload["results"][0]["glyphs"][0]["level"], level)
                self.assertEqual(payload["results"][0]["glyphs"][0]["radius"], expected_radius)

    def test_glyph_radius_is_data_driven_for_custom_upgrade_levels(self) -> None:
        glyph = glyph_payload(
            "custom_radius",
            radius={"starting": 2, "legendary": 5, "upgrade_levels": [10, 20, 80]},
        )
        payload = self.run_optimizer(
            nodes=[
                {"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                {"id": "socket_a", "x": 0, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
            ],
            edges=[["start", "socket_a"]],
            glyph_sockets=["socket_a"],
            glyphs=[glyph],
            weights={"weights": {"glyph_socket": 5.0}},
            points=1,
            glyph_levels={"custom_radius": 80},
        )

        glyph_output = payload["results"][0]["glyphs"][0]
        self.assertEqual(glyph_output["level"], 80)
        self.assertEqual(glyph_output["radius"], 5)

    def test_profile_validation_rejects_bad_glyph_levels(self) -> None:
        def run_with_levels(glyph_levels: dict[str, int]) -> subprocess.CompletedProcess[str]:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                profile_path, _ = self.write_optimizer_fixture(
                    root=root,
                    nodes=[
                        {"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                        {"id": "socket_a", "x": 0, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
                    ],
                    edges=[["start", "socket_a"]],
                    glyph_sockets=["socket_a"],
                    glyphs=[glyph_payload("known_glyph")],
                    weights={"weights": {"glyph_socket": 5.0}},
                    points=1,
                    glyph_levels=glyph_levels,
                )
                return subprocess.run(
                    [str(self.binary), "optimize", "--profile", str(profile_path), "--no-html"],
                    cwd=REPO_ROOT,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                )

        cases = [
            ({"unknown_glyph": 1}, "unknown glyph id"),
            ({"known_glyph": 0}, "level out of range"),
            ({"known_glyph": 101}, "level out of range"),
        ]
        for glyph_levels, expected_error in cases:
            with self.subTest(glyph_levels=glyph_levels):
                completed = run_with_levels(glyph_levels)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(expected_error, completed.stderr)

    def test_output_contains_glyph_level_radius_node_bonus_and_legacy_warning(self) -> None:
        magic_bonus = glyph_payload(
            "magic_bonus",
            node_bonus={"node_type": "magic", "bonus_percent": 30.0, "multiplier": 0.3},
        )
        payload = self.run_optimizer(
            nodes=[
                {"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                {"id": "socket_a", "x": 0, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
                {"id": "damage_magic", "x": 0, "y": 2, "type": "magic", "cost": 1, "stats": {"damage": 10}},
            ],
            edges=[["start", "socket_a"], ["socket_a", "damage_magic"]],
            glyph_sockets=["socket_a"],
            glyphs=[magic_bonus],
            weights={"weights": {"damage": 1.0, "glyph_socket": 5.0}},
            points=2,
            glyph_levels={"magic_bonus": 25},
            profile_extra={"legendary_glyphs": True},
        )

        self.assertEqual(payload["glyph_levels"], {"magic_bonus": 25})
        self.assertNotIn("legendary_glyphs", payload)
        self.assertTrue(any("legendary_glyphs" in warning and "deprecated" in warning for warning in payload["warnings"]))
        glyph = payload["results"][0]["glyphs"][0]
        self.assertEqual(glyph["level"], 25)
        self.assertEqual(glyph["radius"], 4)
        self.assertIn("node_bonus_score", glyph)
        self.assertAlmostEqual(glyph["node_bonus_score"], 3.0)

    def test_node_bonus_uses_level_scaling_samples(self) -> None:
        magic_bonus = glyph_payload(
            "magic_bonus",
            node_bonus={
                "node_type": "magic",
                "bonus_percent": 30.0,
                "multiplier": 0.3,
                "level_scaling": {
                    "samples": [
                        {"level": 1, "bonus_percent": 30.0, "multiplier": 0.3},
                        {"level": 100, "bonus_percent": 327.0, "multiplier": 3.27},
                    ]
                },
            },
        )
        payload = self.run_optimizer(
            nodes=[
                {"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                {"id": "socket_a", "x": 0, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
                {"id": "damage_magic", "x": 0, "y": 2, "type": "magic", "cost": 1, "stats": {"damage": 10}},
            ],
            edges=[["start", "socket_a"], ["socket_a", "damage_magic"]],
            glyph_sockets=["socket_a"],
            glyphs=[magic_bonus],
            weights={"weights": {"damage": 1.0, "glyph_socket": 5.0}},
            points=2,
            glyph_levels={"magic_bonus": 74},
        )

        glyph = payload["results"][0]["glyphs"][0]
        self.assertAlmostEqual(glyph["node_bonus"]["bonus_percent"], 249.0)
        self.assertAlmostEqual(glyph["node_bonus"]["multiplier"], 2.49)
        self.assertAlmostEqual(glyph["node_bonus_score"], 24.9)
        self.assertAlmostEqual(payload["results"][0]["glyph_node_bonus_totals"]["damage"], 24.9)

    def test_scaling_value_per_5_uses_level_samples(self) -> None:
        scaled = glyph_payload(
            "scaled",
            threshold_stat="willpower",
            requirement=999,
            skill_tags=[{"name": "Damage"}],
            bonus_text_en=(
                "Rare Glyph\nscaled\nRadius Size: 3\n"
                "For every 5 Willpower purchased within range, you deal +1.0% increased Damage.\n"
                "Additional Bonus:\nSynthetic bonus.\n"
                "Required (purchased in range): +999 Willpower"
            ),
            scaling_value_per_5={
                "value": 1.0,
                "samples": [
                    {"level": 1, "value": 1.0},
                    {"level": 100, "value": 6.5},
                ],
            },
        )
        payload = self.run_optimizer(
            nodes=[
                {"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                {"id": "socket_a", "x": 0, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
                {"id": "willpower_node", "x": 0, "y": 2, "type": "normal", "cost": 1, "stats": {"willpower": 10}},
            ],
            edges=[["start", "socket_a"], ["socket_a", "willpower_node"]],
            glyph_sockets=["socket_a"],
            glyphs=[scaled],
            weights={"weights": {"damage": 1.0, "willpower": 0.0, "glyph_socket": 5.0}},
            points=2,
            glyph_levels={"scaled": 100},
        )

        glyph = payload["results"][0]["glyphs"][0]
        self.assertAlmostEqual(glyph["scaling_value_per_5"], 6.5)
        self.assertAlmostEqual(glyph["score"], 13.0)

    def test_missing_profile_glyph_level_defaults_to_one(self) -> None:
        payload = self.run_optimizer(
            nodes=[
                {"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                {"id": "socket_a", "x": 0, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
            ],
            edges=[["start", "socket_a"]],
            glyph_sockets=["socket_a"],
            glyphs=[glyph_payload("default_level")],
            weights={"weights": {"glyph_socket": 5.0}},
            points=1,
            glyph_levels={},
        )

        glyph = payload["results"][0]["glyphs"][0]
        self.assertEqual(glyph["level"], 1)
        self.assertEqual(glyph["radius"], 3)

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

    def test_magic_amp_potential_guides_route_through_connector_to_node_bonus_magic(self) -> None:
        magic_bonus = glyph_payload(
            "magic_bonus",
            node_bonus={"node_type": "magic", "bonus_percent": 100.0, "multiplier": 1.0},
        )

        def run(magic_amp: float) -> dict[str, object]:
            return self.run_optimizer(
                nodes=[
                    {"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                    {"id": "socket_a", "x": 0, "y": 1, "type": "glyph_socket", "cost": 1, "stats": {}},
                    {"id": "plain_value", "x": 1, "y": 1, "type": "normal", "cost": 1, "stats": {"damage": 7}},
                    {"id": "path_to_bonus", "x": 0, "y": 2, "type": "normal", "cost": 1, "stats": {}},
                    {"id": "bonus_magic", "x": 0, "y": 3, "type": "magic", "cost": 1, "stats": {"damage": 6}},
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
                        "node_bonus": 1.0,
                        "magic_amp": magic_amp,
                        "cluster": 0.0,
                        "detour": 0.0,
                        "max_bonus_multiplier": 1.60,
                    },
                },
                points=3,
                include_route_steps=True,
            )

        without_amp = run(0.0)["results"][0]
        with_amp = run(1.0)["results"][0]

        self.assertEqual(without_amp["local_swaps"], 1)
        self.assertLess(without_amp["local_score_before"], without_amp["score"])
        self.assertEqual(with_amp.get("local_swaps", 0), 0)
        self.assertEqual(with_amp["route_steps"][0]["target"], "bonus_magic")
        self.assertIn("path_to_bonus", with_amp["selected_nodes"])
        self.assertIn("bonus_magic", with_amp["selected_nodes"])
        self.assertNotIn("plain_value", with_amp["selected_nodes"])
        self.assertAlmostEqual(with_amp["glyphs"][0]["node_bonus_score"], 6.0)

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
                    {"id": "plain_value", "x": 1, "y": 1, "type": "normal", "cost": 2, "stats": {"damage": 10.5}},
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

    def test_route_prize_access_potential_reaches_rare_through_low_value_connector(self) -> None:
        payload = self.run_optimizer(
            nodes=[
                {"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                {"id": "plain_dex_a", "x": 1, "y": 0, "type": "normal", "cost": 1, "stats": {"dexterity": 5}},
                {"id": "plain_dex_b", "x": 0, "y": 1, "type": "normal", "cost": 1, "stats": {"dexterity": 5}},
                {"id": "resource_connector", "x": 1, "y": 1, "type": "magic", "cost": 1, "stats": {"max_resource": 2}},
                {
                    "id": "eager_prey",
                    "x": 2,
                    "y": 1,
                    "type": "rare",
                    "cost": 1,
                    "stats": {"dexterity": 10, "max_resource": 2},
                    "requirements": {"strength": 210},
                    "bonus_stats": {"max_resource": 2},
                },
            ],
            edges=[
                ["start", "plain_dex_a"],
                ["start", "plain_dex_b"],
                ["start", "resource_connector"],
                ["resource_connector", "eager_prey"],
            ],
            glyph_sockets=[],
            glyphs=[],
            weights={
                "weights": {"dexterity": 1.45, "max_resource": 0.01},
                "glyph_route": {
                    "cluster": 0.0,
                    "detour": 0.25,
                    "path_efficiency": 0.0,
                },
            },
            starting_stats={"strength": 368},
            points=2,
            include_route_steps=True,
            profile_extra={"max_routes": 1, "candidate_targets": 10},
        )

        result = payload["results"][0]
        self.assertIn("resource_connector", result["selected_nodes"])
        self.assertIn("eager_prey", result["selected_nodes"])
        self.assertNotIn("plain_dex_a", result["selected_nodes"])
        self.assertNotIn("plain_dex_b", result["selected_nodes"])
        self.assertEqual(result["route_steps"][0]["target"], "eager_prey")

    def test_local_prize_access_rescue_swaps_multiple_nodes_for_gated_rare(self) -> None:
        payload = self.run_optimizer(
            nodes=[
                {"id": "start", "x": 0, "y": 0, "type": "normal", "cost": 0, "stats": {}},
                {"id": "plain_value_a", "x": 1, "y": 0, "type": "normal", "cost": 1, "stats": {"damage": 10}},
                {"id": "plain_value_b", "x": 0, "y": 1, "type": "normal", "cost": 1, "stats": {"damage": 10}},
                {"id": "access_connector", "x": 1, "y": 1, "type": "normal", "cost": 1, "stats": {}},
                {
                    "id": "gated_rare",
                    "x": 2,
                    "y": 1,
                    "type": "rare",
                    "cost": 1,
                    "stats": {"damage": 5},
                    "requirements": {"strength": 50},
                    "bonus_stats": {"damage": 16},
                },
            ],
            edges=[
                ["start", "plain_value_a"],
                ["start", "plain_value_b"],
                ["start", "access_connector"],
                ["access_connector", "gated_rare"],
            ],
            glyph_sockets=[],
            glyphs=[],
            weights={
                "weights": {"damage": 1.0},
                "glyph_route": {
                    "cluster": 0.0,
                    "detour": 0.25,
                    "path_efficiency": 0.0,
                },
            },
            starting_stats={"strength": 100},
            points=2,
            include_route_steps=True,
            profile_extra={"max_routes": 1, "candidate_targets": 10},
        )

        result = payload["results"][0]
        self.assertIn("access_connector", result["selected_nodes"])
        self.assertIn("gated_rare", result["selected_nodes"])
        self.assertNotIn("plain_value_a", result["selected_nodes"])
        self.assertNotIn("plain_value_b", result["selected_nodes"])
        self.assertGreater(result["local_swaps"], 0)


if __name__ == "__main__":
    unittest.main()
