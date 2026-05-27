from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any


PAGE_DATA_KEYS = {
    "boards": "d4.paragonCalc.d4.boardNodes",
    "class_boards": "d4.paragonCalc.d4.classBoards",
    "glyphs": "d4.paragonCalc.d4.glyphs",
    "nodes": "d4.paragonCalc.d4.nodes",
    "skill_tags": "d4.paragonCalc.d4.skillTags",
}

QUALITY_TO_NODE_TYPE = {
    0: "normal",
    1: "magic",
    3: "rare",
    5: "legendary",
}

SPECIAL_NODE_IDS = {
    681756: "glyph_socket",
    994337: "board_gate",
}

ATTRIBUTE_KEYS = {
    "strength": "strength",
    "dexterity": "dexterity",
    "intelligence": "intelligence",
    "willpower": "willpower",
    "maximum life": "max_life",
    "life": "max_life",
    "total armor": "armor",
    "armor": "armor",
    "resistance to all elements": "resistance_all",
    "all elements": "resistance_all",
    "resistance": "resistance_all",
    "vulnerable damage": "vulnerable_damage",
    "critical strike damage": "critical_strike_damage",
    "critical strikes damage": "critical_strike_damage",
    "damage to elites": "damage_to_elites",
    "damage to elite monsters": "damage_to_elites",
    "non-physical damage": "non_physical_damage",
    "physical damage": "physical_damage",
    "fire damage": "fire_damage",
    "cold damage": "cold_damage",
    "lightning damage": "lightning_damage",
    "shadow damage": "shadow_damage",
    "poison damage": "poison_damage",
    "damage to crowd controlled enemies": "crowd_controlled_damage",
    "damage to controlled enemies": "crowd_controlled_damage",
    "to controlled enemies": "crowd_controlled_damage",
    "crowd control damage": "crowd_controlled_damage",
    "damage while fortified": "damage_while_fortified",
    "while fortified": "damage_while_fortified",
    "damage while healthy": "damage_while_healthy",
    "while healthy": "damage_while_healthy",
    "damage to healthy enemies": "damage_to_healthy_enemies",
    "to healthy enemies": "damage_to_healthy_enemies",
    "damage to elites": "damage_to_elites",
    "to elites": "damage_to_elites",
    "elite monsters": "damage_to_elites",
    "damage": "damage",
    "attack speed": "attack_speed",
    "cooldown reduction": "cooldown_reduction",
    "life per 5 seconds": "life_per_5_seconds",
    "healing received": "healing_received",
    "movement speed": "movement_speed",
    "lucky hit chance": "lucky_hit_chance",
    "block chance": "block_chance",
    "damage reduction": "damage_reduction",
    "blocked damage reduction": "blocked_damage_reduction",
    "thorns": "thorns",
    "maximum faith": "max_faith",
    "faith": "max_faith",
    "fury": "max_fury",
    "wrath": "max_wrath",
}

RU_STAT_HINTS = {
    "strength": "сила",
    "dexterity": "ловкость",
    "intelligence": "интеллект",
    "willpower": "сила воли",
    "max_life": "максимальный запас здоровья",
    "armor": "броня",
    "resistance_all": "сопротивление всем стихиям",
    "vulnerable_damage": "урон уязвимым целям",
    "critical_strike_damage": "критический урон",
    "damage": "урон",
    "crowd_controlled_damage": "урон по противникам под контролем",
    "damage_while_fortified": "урон в укреплении",
    "damage_while_healthy": "урон при высоком здоровье",
    "damage_to_healthy_enemies": "урон по целям с высоким здоровьем",
    "damage_to_elites": "урон по элитным противникам",
}


def to_identifier(value: str | None, fallback: str) -> str:
    if not value:
        value = fallback
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_") or fallback


def clean_name(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    value = re.split(r"\s+-\s+", value, maxsplit=1)[0].strip()
    value = re.sub(r"\s+\[(?:Paragon Node|Paragon Glyph)\].*$", "", value, flags=re.IGNORECASE).strip()
    return value or None


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_raw_path(input_path: Path, class_slug: str | None) -> Path:
    if input_path.is_file():
        return input_path
    if class_slug:
        candidate = input_path / class_slug / "wowhead_raw.json"
        if candidate.exists():
            return candidate
    direct = input_path / "wowhead_raw.json"
    if direct.exists():
        return direct
    candidates = sorted(input_path.glob("*/wowhead_raw.json"))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SystemExit(f"No wowhead_raw.json found under {input_path}")
    raise SystemExit(f"Multiple raw classes found under {input_path}; pass --class")


def extract_patch_version(raw: dict[str, Any]) -> str | None:
    page_meta = raw.get("page_meta") or {}
    versions = ((page_meta.get("dataEnv") or {}).get("versions") or {})
    if versions:
        return versions.get("9") or next(iter(versions.values()))
    for section in ("glyphs", "nodes"):
        for locale_data in (raw.get("list_pages") or {}).get(section, {}).values():
            versions = (((locale_data.get("page_meta") or {}).get("dataEnv") or {}).get("versions") or {})
            if versions:
                return versions.get("9") or next(iter(versions.values()))
    return None


def list_items(raw: dict[str, Any], section: str, locale: str) -> dict[str, dict[str, Any]]:
    listviews = (((raw.get("list_pages") or {}).get(section) or {}).get(locale) or {}).get("listviews") or []
    data = listviews[0].get("data") if listviews else []
    return {str(item.get("id")): item for item in data or [] if item.get("id") is not None}


def localized_detail_name(raw: dict[str, Any], section: str, item_id: int, locale: str) -> str | None:
    detail = (((raw.get("details") or {}).get(section) or {}).get(str(item_id)) or {}).get(locale) or {}
    return clean_name(detail.get("h1") or detail.get("title"))


def localized_name(
    raw: dict[str, Any],
    list_maps: dict[str, dict[str, dict[str, Any]]],
    section: str,
    item_id: int,
    fallback_en: str | None = None,
) -> dict[str, str | None]:
    key = str(item_id)
    en = localized_detail_name(raw, section, item_id, "en")
    ru = localized_detail_name(raw, section, item_id, "ru")
    en = en or clean_name((list_maps.get("en") or {}).get(key, {}).get("name")) or fallback_en
    ru = ru or clean_name((list_maps.get("ru") or {}).get(key, {}).get("name"))
    return {"en": en, "ru": ru}


def detail_source_url(raw: dict[str, Any], section: str, item_id: int, locale: str = "en") -> str | None:
    return (
        (((raw.get("details") or {}).get(section) or {}).get(str(item_id)) or {})
        .get(locale, {})
        .get("source_url")
    )


def detail_tooltip(raw: dict[str, Any], section: str, item_id: int, locale: str) -> str | None:
    return (
        (((raw.get("details") or {}).get(section) or {}).get(str(item_id)) or {})
        .get(locale, {})
        .get("tooltip_text")
    )


def stat_key_from_phrase(phrase: str) -> str | None:
    text = phrase.lower()
    text = re.sub(r"\[[^\]]+\]", "", text)

    # Priority detection for compound damage types (before aggressive cleaning)
    if "crowd controlled" in text or "controlled enemies" in text or "to controlled" in text:
        return "crowd_controlled_damage"
    if "while fortified" in text:
        return "damage_while_fortified"
    if "while healthy" in text:
        return "damage_while_healthy"
    if "healthy enemies" in text or ("healthy" in text and "to enemies" in text):
        return "damage_to_healthy_enemies"
    if "to elites" in text or "elite monsters" in text:
        return "damage_to_elites"

    cleaned = re.sub(r"\b(to|while|from|of|your|all|another|bonus|if|requirements|met)\b", " ", text)
    cleaned = re.sub(r"[^a-z0-9 -]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for name in sorted(ATTRIBUTE_KEYS, key=len, reverse=True):
        if name in cleaned:
            return ATTRIBUTE_KEYS[name]
    return None


def parse_attribute_line(line: str) -> tuple[str, float] | None:
    line = re.sub(r"<[^>]+>", " ", line)
    line = line.replace("[+]", "").replace("[x]", "")
    line = re.sub(r"\s+", " ", line).strip()
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%?\s*(.+)", line)
    if not match:
        return None
    value = float(match.group(1))
    key = stat_key_from_phrase(match.group(2))
    if not key:
        return None
    return key, value


def parse_stats_from_attributes(attributes: list[str]) -> dict[str, float]:
    stats: dict[str, float] = {}
    for attribute in attributes:
        parsed = parse_attribute_line(attribute)
        if not parsed:
            continue
        key, value = parsed
        stats[key] = stats.get(key, 0.0) + value
    return stats


def parse_stats_from_search_text(search_text: str) -> dict[str, float]:
    text = search_text.lower()
    stats: dict[str, float] = {}

    gate_match = re.search(
        r"\bstrength\s+intelligence\s+willpower\s+dexterity\s+board attachment gate\s+\+?(\d+(?:\.\d+)?)",
        text,
    )
    if gate_match:
        value = float(gate_match.group(1))
        for key in ("strength", "intelligence", "willpower", "dexterity"):
            stats[key] = value

    attr_match = re.search(r"\b(strength|dexterity|intelligence|willpower)\b\s+(?:normal|magic)\s+node\s+\+?(\d+(?:\.\d+)?)", text)
    if attr_match:
        stats[ATTRIBUTE_KEYS[attr_match.group(1)]] = float(attr_match.group(2))

    simple_patterns = [
        (r"^damage\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)%", "damage"),
        (r"\bphysical damage\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)%", "physical_damage"),
        (r"\bvulnerable damage\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)%", "vulnerable_damage"),
        (r"\bcritical strikes? damage\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)%", "critical_strike_damage"),
        (r"\bholy\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)%", "holy_damage"),
        (r"\bto (?:elites|elite)\b.*magic\s+node\s+\+?(\d+(?:\.\d+)?)%", "damage_to_elites"),
        (r"\belite monsters.*magic\s+node\s+\+?(\d+(?:\.\d+)?)%", "damage_to_elites"),
        (r"magic\s+node\s+\+?(\d+(?:\.\d+)?)%.*\bto (?:elites|elite)", "damage_to_elites"),
        (r"\bto controlled enemies\b.*magic\s+node\s+\+?(\d+(?:\.\d+)?)%", "crowd_controlled_damage"),
        (r"\bcrowd control damage.*magic\s+node\s+\+?(\d+(?:\.\d+)?)%", "crowd_controlled_damage"),
        (r"magic\s+node\s+\+?(\d+(?:\.\d+)?)%.*\bto controlled", "crowd_controlled_damage"),
        (r"\bwhile fortified\b.*magic\s+node\s+\+?(\d+(?:\.\d+)?)%", "damage_while_fortified"),
        (r"magic\s+node\s+\+?(\d+(?:\.\d+)?)%.*\bwhile fortified", "damage_while_fortified"),
        (r"\bhealthy damage\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)%.*\bwhile\b", "damage_while_healthy"),
        (r"\bwhile healthy\b.*magic\s+node\s+\+?(\d+(?:\.\d+)?)%", "damage_while_healthy"),
        (r"magic\s+node\s+\+?(\d+(?:\.\d+)?)%.*\bwhile healthy", "damage_while_healthy"),
        (r"\bhealthy damage\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)%.*\bto enemies\b", "damage_to_healthy_enemies"),
        (r"magic\s+node\s+\+?(\d+(?:\.\d+)?)%.*\bto healthy enemies\b", "damage_to_healthy_enemies"),
        (r"\battack speed\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)%", "attack_speed"),
        (r"\bcooldown\s+magic\s+node\s+(\d+(?:\.\d+)?)%\s+reduction", "cooldown_reduction"),
        (r"\bmovement\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)%\s+speed", "movement_speed"),
        (r"\barmor\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)%\s+total", "armor"),
        (r"\bresistance\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)%\s+to all elements", "resistance_all"),
        (r"\blife\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)%\s+maximum", "max_life"),
        (r"\bhealing\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)%\s+received", "healing_received"),
        (r"\bhealing\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)\s+life per 5 seconds", "life_per_5_seconds"),
        (r"\blucky hit\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)%\s+chance", "lucky_hit_chance"),
        (r"\bblock\s+magic\s+node\s+(\d+(?:\.\d+)?)%\s+chance", "block_chance"),
        (r"\bthorns\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)", "thorns"),
        (r"\bfaith\s+magic\s+node\s+\+?(\d+(?:\.\d+)?)\s+maximum", "max_faith"),
    ]
    for pattern, key in simple_patterns:
        match = re.search(pattern, text)
        if match:
            stats[key] = float(match.group(1))
    return stats


def parse_requirements(search_text: str) -> dict[str, float]:
    lower = search_text.lower()
    if "requirements met" not in lower:
        return {}
    requirement_text = lower.split("requirements met", 1)[1]
    requirements: dict[str, float] = {}
    for value, attr in re.findall(r"(\d+(?:\.\d+)?)\s+(strength|dexterity|intelligence|willpower)", requirement_text):
        requirements[ATTRIBUTE_KEYS[attr]] = float(value)
    return requirements


def parse_bonus_stats_from_tooltip(tooltip_text: str | None) -> dict[str, float]:
    if not tooltip_text:
        return {}
    stats: dict[str, float] = {}
    for raw_line in tooltip_text.splitlines():
        line = re.sub(r"<[^>]+>", " ", raw_line)
        line = re.sub(r"\s+", " ", line).strip()
        match = re.search(r"bonus:\s*another\s+(.+?)\s+if requirements met:?", line, flags=re.IGNORECASE)
        if not match:
            continue
        parsed = parse_attribute_line(match.group(1))
        if not parsed:
            continue
        key, value = parsed
        stats[key] = stats.get(key, 0.0) + value
    return stats


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = deepcopy(base)
        for key, value in override.items():
            merged[key] = deep_merge(merged.get(key), value)
        return merged
    return deepcopy(override)


def load_manual_overrides(path: Path | None, class_slug: str) -> dict[str, Any]:
    if not path:
        return {}
    if path.is_file():
        return load_json(path)
    candidate = path / f"{class_slug}.json"
    if candidate.exists():
        return load_json(candidate)
    return {}


def detail_coverage(raw: dict[str, Any]) -> dict[str, Any]:
    coverage: dict[str, Any] = {}
    locales = ["en"]
    if raw.get("locale") and raw["locale"] != "en":
        locales.append(raw["locale"])
    for section in ("glyphs", "nodes"):
        expected = len(list_items(raw, section, "en")) * len(locales)
        ok = 0
        errors = 0
        reused = 0
        for locale_map in ((raw.get("details") or {}).get(section) or {}).values():
            for detail in (locale_map or {}).values():
                if detail.get("fetch_error"):
                    errors += 1
                else:
                    ok += 1
                if detail.get("reused_from_detail_cache") or detail.get("reused_from_previous_raw"):
                    reused += 1
        coverage[section] = {
            "expected": expected,
            "ok": ok,
            "errors": errors,
            "missing": max(expected - ok - errors, 0),
            "reused": reused,
        }
    return coverage


def node_type(source_node_id: int, metadata: dict[str, Any], list_item: dict[str, Any] | None) -> str:
    if source_node_id in SPECIAL_NODE_IDS:
        return SPECIAL_NODE_IDS[source_node_id]
    if metadata.get("hasSocket") or (list_item or {}).get("hasSocket"):
        return "glyph_socket"
    if metadata.get("isGate") or (list_item or {}).get("isGate"):
        return "board_gate"
    quality = metadata.get("quality", (list_item or {}).get("quality", 0))
    return QUALITY_TO_NODE_TYPE.get(int(quality or 0), "normal")


def infer_edges(nodes: list[dict[str, Any]]) -> list[list[str]]:
    by_coord = {(node["x"], node["y"]): node["id"] for node in nodes}
    edges: list[list[str]] = []
    for x, y in sorted(by_coord):
        current = by_coord[(x, y)]
        for dx, dy in ((1, 0), (0, 1)):
            neighbor = by_coord.get((x + dx, y + dy))
            if neighbor:
                edges.append([current, neighbor])
    return edges


def gate_side(x: int, y: int, max_x: int, max_y: int) -> str | None:
    if y == 0:
        return "top"
    if y == max_y:
        return "bottom"
    if x == 0:
        return "left"
    if x == max_x:
        return "right"
    return None


def skill_tag_map(skill_tags: Any) -> dict[str, Any]:
    if isinstance(skill_tags, dict):
        return {str(key): value for key, value in skill_tags.items()}
    if isinstance(skill_tags, list):
        result = {}
        for item in skill_tags:
            if isinstance(item, dict):
                key = item.get("id") or item.get("sno") or item.get("hash") or item.get("name")
                if key is not None:
                    result[str(key)] = item
        return result
    return {}


def normalize_skill_tags(ids: list[Any], tag_lookup: dict[str, Any]) -> list[dict[str, Any]]:
    tags = []
    for tag_id in ids or []:
        raw = tag_lookup.get(str(tag_id))
        if isinstance(raw, dict):
            tags.append({"id": tag_id, "name": raw.get("name"), "raw": raw})
        else:
            tags.append({"id": tag_id, "name": raw if isinstance(raw, str) else None})
    return tags


def rare_attribute_index(class_board: dict[str, Any]) -> dict[str, list[str]]:
    return {
        to_identifier(rare.get("name"), ""): list(rare.get("attributes") or [])
        for rare in class_board.get("rares") or []
        if rare.get("name")
    }


def normalize_boards(raw: dict[str, Any], output_root: Path, overrides: dict[str, Any]) -> tuple[list[str], set[str]]:
    page_data = raw["paragon_calc"]["page_data"]
    class_slug = raw["class"]
    class_id = str(raw["class_id"])
    board_nodes = page_data[PAGE_DATA_KEYS["boards"]]
    nodes_meta = {str(key): value for key, value in page_data[PAGE_DATA_KEYS["nodes"]].items()}
    class_boards = {str(board["sno"]): board for board in page_data[PAGE_DATA_KEYS["class_boards"]][class_id]}
    tag_lookup = skill_tag_map(page_data.get(PAGE_DATA_KEYS["skill_tags"]))
    en_list = list_items(raw, "nodes", "en")
    ru_list = list_items(raw, "nodes", "ru")
    list_maps = {"en": en_list, "ru": ru_list}
    patch_version = extract_patch_version(raw)
    checked_at = raw.get("checked_at")

    board_ids: list[str] = []
    available_stats: set[str] = set()
    for board_sno, class_board in class_boards.items():
        raw_nodes = board_nodes.get(board_sno)
        if not raw_nodes:
            continue

        legendary_source_ids = [
            int(item["node"])
            for item in raw_nodes
            if int((nodes_meta.get(str(item["node"])) or {}).get("quality") or 0) == 5
        ]
        board_name_en = class_board.get("name")
        board_name_ru = None
        if legendary_source_ids:
            names = localized_name(raw, list_maps, "nodes", legendary_source_ids[0], class_board.get("name"))
            board_name_en = board_name_en or names["en"]
            board_name_ru = names["ru"]
        if not board_name_en:
            board_name_en = "Starter Board"
            board_name_ru = "Стартовая доска"

        board_slug = "starter" if not class_board.get("name") else to_identifier(board_name_en, board_sno)
        max_x = max(int(item["x"]) for item in raw_nodes)
        max_y = max(int(item["y"]) for item in raw_nodes)
        rares_by_name = rare_attribute_index(class_board)

        nodes: list[dict[str, Any]] = []
        gates: list[dict[str, Any]] = []
        glyph_sockets: list[str] = []
        start_node_id = None
        type_counts: Counter[str] = Counter()

        for item in sorted(raw_nodes, key=lambda value: (int(value["y"]), int(value["x"]), int(value["node"]))):
            x = int(item["x"])
            y = int(item["y"])
            source_node_id = int(item["node"])
            source_key = str(source_node_id)
            metadata = nodes_meta.get(source_key) or {}
            list_item = en_list.get(source_key)
            current_type = node_type(source_node_id, metadata, list_item)
            type_counts[current_type] += 1
            node_id = f"{board_slug}_{x}_{y}"
            names = localized_name(raw, list_maps, "nodes", source_node_id)

            if current_type == "glyph_socket":
                names = {"en": "Glyph Socket", "ru": "Ячейка символа"}
                glyph_sockets.append(node_id)
            elif current_type == "board_gate":
                names = {"en": "Board Gate", "ru": "Ворота доски"}
            elif metadata.get("isStarter"):
                names = {"en": "Paragon Starting Node", "ru": "Стартовый узел парагона"}
                start_node_id = node_id

            name_key = to_identifier(names.get("en"), "")
            raw_attributes = rares_by_name.get(name_key, [])
            stats = parse_stats_from_attributes(raw_attributes) if raw_attributes else {}
            stats_source = "class_board_attributes" if stats else None
            if not stats:
                stats = parse_stats_from_search_text(metadata.get("searchText") or "")
                stats_source = "search_text" if stats else "empty"

            node_requirements = parse_requirements(metadata.get("searchText") or "")

            bonus_stats: dict[str, float] = {}
            bonus_stats_source: str | None = None
            if stats and current_type in ("rare", "legendary") and node_requirements:
                tooltip = detail_tooltip(raw, "nodes", source_node_id, "en")
                bonus_stats = parse_bonus_stats_from_tooltip(tooltip)
                bonus_stats_source = "tooltip_bonus" if bonus_stats else None

            available_stats.update(stats)
            available_stats.update(bonus_stats)

            node = {
                "id": node_id,
                "source_node_id": source_node_id,
                "x": x,
                "y": y,
                "type": current_type,
                "cost": 0 if bool(metadata.get("isStarter")) else 1,
                "name": names,
                "stats": stats,
                "stats_source": stats_source,
                "bonus_stats": bonus_stats,
                "bonus_stats_source": bonus_stats_source,
                "requirements": node_requirements,
                "is_starting_node": bool(metadata.get("isStarter")),
                "raw": {
                    "quality": metadata.get("quality"),
                    "rarity": metadata.get("rarity"),
                    "icon": metadata.get("icon") or (list_item or {}).get("icon"),
                    "skill_tags": normalize_skill_tags(metadata.get("skillTags") or [], tag_lookup),
                    "glyph_attributes": metadata.get("glyphAttrs") or [],
                    "search_text": metadata.get("searchText"),
                    "list_tags": (list_item or {}).get("tags"),
                    "popularity": (list_item or {}).get("popularity"),
                    "attributes": raw_attributes,
                },
                "source_url": detail_source_url(raw, "nodes", source_node_id) or raw["sources"]["paragon_data"],
            }
            nodes.append(node)

            if current_type == "board_gate":
                gates.append({"id": node_id, "side": gate_side(x, y, max_x, max_y), "x": x, "y": y})

        board = {
            "schema_version": 1,
            "id": board_slug,
            "source_board_id": int(board_sno),
            "name": {"en": board_name_en, "ru": board_name_ru},
            "class": class_slug,
            "width": max_x + 1,
            "height": max_y + 1,
            "start_node": start_node_id,
            "nodes": nodes,
            "edges": infer_edges(nodes),
            "edge_source": "inferred_grid_adjacency",
            "gates": gates,
            "glyph_sockets": glyph_sockets,
            "legendary_nodes": [node["id"] for node in nodes if node["type"] == "legendary"],
            "node_type_counts": dict(type_counts),
            "source_url": raw["sources"]["paragon_data"],
            "checked_at": checked_at,
            "patch_version": patch_version,
        }
        board_override = ((overrides.get("boards") or {}).get(board_slug) or {})
        board = deep_merge(board, board_override)
        write_json(output_root / "boards" / class_slug / f"{board_slug}.json", board)
        board_ids.append(board_slug)

    return board_ids, available_stats


def normalize_glyphs(raw: dict[str, Any], output_root: Path, overrides: dict[str, Any]) -> tuple[list[str], set[str]]:
    page_data = raw["paragon_calc"]["page_data"]
    class_slug = raw["class"]
    class_mask = int(raw["class_mask"])
    tag_lookup = skill_tag_map(page_data.get(PAGE_DATA_KEYS["skill_tags"]))
    en_list = list_items(raw, "glyphs", "en")
    ru_list = list_items(raw, "glyphs", "ru")
    list_maps = {"en": en_list, "ru": ru_list}
    patch_version = extract_patch_version(raw)
    checked_at = raw.get("checked_at")

    glyph_ids: list[str] = []
    available_stats: set[str] = set()
    for glyph_meta in sorted(page_data[PAGE_DATA_KEYS["glyphs"]], key=lambda item: str(item.get("name"))):
        if not (int(glyph_meta.get("classMask") or 0) & class_mask):
            continue
        glyph_sno = int(glyph_meta["sno"])
        names = localized_name(raw, list_maps, "glyphs", glyph_sno, glyph_meta.get("name"))
        glyph_slug = to_identifier(names.get("en"), str(glyph_sno))
        threshold_attrs = []
        for attr in glyph_meta.get("thresholdAttrs") or []:
            key = stat_key_from_phrase(attr.get("attributeName") or "")
            if key:
                available_stats.add(key)
            threshold_attrs.append(
                {
                    "attribute": attr.get("attribute"),
                    "attribute_name": attr.get("attributeName"),
                    "stat_key": key,
                    "class_mask": attr.get("classMask"),
                }
            )

        radius_starting = glyph_meta.get("startingSize")
        upgrade_levels = list(glyph_meta.get("upgradeLevels") or [])
        radius_legendary = radius_starting + len(upgrade_levels) if isinstance(radius_starting, int) else None
        glyph = {
            "schema_version": 1,
            "id": glyph_slug,
            "source_glyph_id": glyph_sno,
            "name": names,
            "class": class_slug,
            "class_mask": glyph_meta.get("classMask"),
            "quality": glyph_meta.get("quality"),
            "rarity": glyph_meta.get("rarity"),
            "max_level": glyph_meta.get("maxLevel"),
            "radius": {
                "starting": radius_starting,
                "legendary": radius_legendary,
                "upgrade_levels": upgrade_levels,
                "source": "wowhead.paragonCalc.glyphs.startingSize+upgradeLevels",
            },
            "threshold_attributes": threshold_attrs,
            "skill_tags": normalize_skill_tags(glyph_meta.get("skillTags") or [], tag_lookup),
            "bonus_text": {
                "en": detail_tooltip(raw, "glyphs", glyph_sno, "en"),
                "ru": detail_tooltip(raw, "glyphs", glyph_sno, "ru"),
            },
            "source_url": detail_source_url(raw, "glyphs", glyph_sno) or raw["sources"]["paragon_data"],
            "checked_at": checked_at,
            "patch_version": patch_version,
            "raw": glyph_meta,
        }
        glyph_override = ((overrides.get("glyphs") or {}).get(glyph_slug) or {})
        glyph = deep_merge(glyph, glyph_override)
        write_json(output_root / "glyphs" / class_slug / f"{glyph_slug}.json", glyph)
        glyph_ids.append(glyph_slug)
    return glyph_ids, available_stats


def normalize(args: argparse.Namespace) -> Path:
    if args.class_slug and args.class_slug.lower() == "all":
        return normalize_all(args)

    raw_path = resolve_raw_path(Path(args.input), args.class_slug)
    raw = load_json(raw_path)
    class_slug = raw["class"]
    output_root = Path(args.out)
    overrides = load_manual_overrides(Path(args.manual_overrides) if args.manual_overrides else None, class_slug)

    board_ids, board_stats = normalize_boards(raw, output_root, overrides)
    glyph_ids, glyph_stats = normalize_glyphs(raw, output_root, overrides)
    available_stats = sorted(board_stats | glyph_stats)

    threshold_counter: Counter[str] = Counter()
    for glyph_file in (output_root / "glyphs" / class_slug).glob("*.json"):
        glyph = load_json(glyph_file)
        for attr in glyph.get("threshold_attributes") or []:
            if attr.get("stat_key"):
                threshold_counter[attr["stat_key"]] += 1

    primary_attributes = [stat for stat, _ in threshold_counter.most_common(1)]
    class_data = {
        "schema_version": 1,
        "class": class_slug,
        "class_id": raw.get("class_id"),
        "class_mask": raw.get("class_mask"),
        "name": raw.get("class_name"),
        "resource": None,
        "primary_attributes": primary_attributes,
        "boards": board_ids,
        "glyphs": glyph_ids,
        "available_stats": available_stats,
        "available_stats_ru_hints": {stat: RU_STAT_HINTS.get(stat) for stat in available_stats if stat in RU_STAT_HINTS},
        "source_url": raw["sources"]["paragon_calc"],
        "checked_at": raw.get("checked_at"),
        "patch_version": extract_patch_version(raw),
        "raw_data_file": str(raw_path),
    }
    class_override = ((overrides.get("classes") or {}).get(class_slug) or {})
    class_data = deep_merge(class_data, class_override)
    write_json(output_root / "classes" / f"{class_slug}.json", class_data)

    manifest = {
        "schema_version": 1,
        "class": class_slug,
        "checked_at": raw.get("checked_at"),
        "patch_version": extract_patch_version(raw),
        "counts": {
            "boards": len(board_ids),
            "glyphs": len(glyph_ids),
            "available_stats": len(available_stats),
        },
        "files": {
            "class": str(output_root / "classes" / f"{class_slug}.json"),
            "boards_dir": str(output_root / "boards" / class_slug),
            "glyphs_dir": str(output_root / "glyphs" / class_slug),
        },
        "source_urls": raw.get("sources"),
        "warnings": raw.get("warnings") or [],
        "detail_coverage": detail_coverage(raw),
        "detail_errors_count": len(raw.get("detail_errors") or []),
        "detail_errors": raw.get("detail_errors") or [],
        "reuse_events_count": len(raw.get("reuse_events") or []),
        "reuse_events": raw.get("reuse_events") or [],
    }
    write_json(output_root / "manifest" / f"{class_slug}.json", manifest)
    return output_root / "manifest" / f"{class_slug}.json"


def normalize_all(args: argparse.Namespace) -> Path:
    input_path = Path(args.input)
    if input_path.is_file():
        raise SystemExit("--class all expects --in to be a raw data directory")
    raw_files = sorted(input_path.glob("*/wowhead_raw.json"))
    if not raw_files:
        raise SystemExit(f"No class raw files found under {input_path}")

    results = []
    for raw_file in raw_files:
        raw = load_json(raw_file)
        child_args = argparse.Namespace(
            input=str(raw_file),
            out=args.out,
            class_slug=None,
            manual_overrides=args.manual_overrides,
        )
        manifest_path = normalize(child_args)
        manifest = load_json(manifest_path)
        results.append(
            {
                "class": raw.get("class"),
                "manifest": str(manifest_path),
                "counts": manifest.get("counts"),
                "warnings": manifest.get("warnings") or [],
                "detail_errors_count": manifest.get("detail_errors_count", 0),
            }
        )

    output_root = Path(args.out)
    summary = {
        "schema_version": 1,
        "classes": [item["class"] for item in results],
        "counts": {
            "classes": len(results),
            "boards": sum((item.get("counts") or {}).get("boards", 0) for item in results),
            "glyphs": sum((item.get("counts") or {}).get("glyphs", 0) for item in results),
        },
        "results": results,
    }
    summary_path = output_root / "manifest" / "all.json"
    write_json(summary_path, summary)
    return summary_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize raw Wowhead paragon data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    normalize_parser = subparsers.add_parser("normalize", help="Build normalized JSON references")
    normalize_parser.add_argument("--in", dest="input", required=True, help="Raw file or raw data directory")
    normalize_parser.add_argument("--out", required=True, help="Output directory for normalized references")
    normalize_parser.add_argument(
        "--class",
        dest="class_slug",
        default=None,
        help="Class slug if --in contains multiple classes, or all",
    )
    normalize_parser.add_argument(
        "--manual-overrides",
        default=None,
        help="Optional JSON file or directory with <class>.json overrides for semi-manual normalization",
    )
    normalize_parser.set_defaults(func=normalize)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
