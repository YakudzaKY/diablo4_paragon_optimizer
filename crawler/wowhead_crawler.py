from __future__ import annotations

import argparse
import hashlib
import html
import json
import random
import re
import sys
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


WOWHEAD_BASE = "https://www.wowhead.com/diablo-4"
WOWHEAD_NETHER_BASE = "https://nether.wowhead.com"
# Wowhead/CloudFront currently accepts a plain browser-like UA more reliably than
# a full Chrome fingerprint when requests come from urllib.
USER_AGENT = "Mozilla/5.0"

CLASS_IDS = {
    "sorcerer": 0,
    "druid": 1,
    "barbarian": 2,
    "rogue": 3,
    "necromancer": 4,
    "spiritborn": 5,
    "paladin": 6,
    "warlock": 7,
}

CLASS_RU = {
    "sorcerer": "Чародей",
    "druid": "Друид",
    "barbarian": "Варвар",
    "rogue": "Разбойник",
    "necromancer": "Некромант",
    "spiritborn": "Духорожденный",
    "paladin": "Паладин",
    "warlock": "Чернокнижник",
}


def fallback_class_registry() -> dict[str, dict[str, Any]]:
    return {
        slug: {
            "id": class_id,
            "slug": slug,
            "class_mask": 1 << class_id,
            "name": {"en": slug.title(), "ru": CLASS_RU.get(slug)},
            "discovery_source": "fallback",
        }
        for slug, class_id in CLASS_IDS.items()
    }


def fallback_class_by_id(class_id: int) -> dict[str, Any] | None:
    for class_info in fallback_class_registry().values():
        if class_info["id"] == class_id:
            return class_info
    return None


@dataclass(frozen=True)
class FetchConfig:
    timeout: float
    retries: int
    sleep: float
    backoff: float
    block_sleep: float
    max_sleep: float
    jitter: float


def make_fetch_config(args: argparse.Namespace) -> FetchConfig:
    return FetchConfig(
        timeout=args.timeout,
        retries=args.retries,
        sleep=args.sleep,
        backoff=args.retry_backoff,
        block_sleep=args.block_sleep,
        max_sleep=args.max_retry_sleep,
        jitter=args.retry_jitter,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "item"


def fetch_text(url: str, config: FetchConfig) -> str:
    last_error: Exception | None = None
    for attempt in range(config.retries + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=config.timeout) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, "replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt >= config.retries:
                break
            delay = retry_delay(exc, attempt, config)
            print(
                f"  fetch retry {attempt + 1}/{config.retries} after {delay:.1f}s: {url} ({exc})",
                file=sys.stderr,
            )
            time.sleep(delay)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}") from last_error


def retry_delay(exc: Exception, attempt: int, config: FetchConfig) -> float:
    retry_after = None
    if isinstance(exc, HTTPError):
        retry_after_header = exc.headers.get("Retry-After")
        if retry_after_header and retry_after_header.isdigit():
            retry_after = float(retry_after_header)

    delay = config.sleep * (config.backoff**attempt)
    if isinstance(exc, HTTPError) and exc.code in {403, 429}:
        delay = max(delay, config.block_sleep)
    if retry_after is not None:
        delay = max(delay, retry_after)
    delay = min(delay, config.max_sleep)
    if config.jitter > 0:
        delay += random.uniform(0, delay * config.jitter)
    return delay


def extract_json_script(html_text: str, script_id: str) -> Any:
    pattern = re.compile(
        rf"<script\b(?=[^>]*\bid=[\"']{re.escape(script_id)}[\"'])[^>]*>(.*?)</script>",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html_text)
    if not match:
        return None
    try:
        return json.loads(html.unescape(match.group(1)).strip())
    except json.JSONDecodeError:
        return None


def _extract_balanced_json(text: str, start: int) -> tuple[str, int]:
    if text[start] not in "[{":
        raise ValueError(f"Expected JSON object or array at offset {start}")
    depth = 0
    in_string = False
    escaped = False
    index = start
    while index < len(text):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
                if depth == 0:
                    index += 1
                    return text[start:index], index
        index += 1
    raise ValueError("Unclosed JSON value in Wowhead script")


def parse_wowhead_page_data_script(script_text: str) -> dict[str, Any]:
    """Parse WH.setPageData("key", <json>) calls from Wowhead data scripts."""
    result: dict[str, Any] = {}
    marker = "WH.setPageData("
    index = 0
    while True:
        call_start = script_text.find(marker, index)
        if call_start == -1:
            break
        cursor = call_start + len(marker)
        while cursor < len(script_text) and script_text[cursor].isspace():
            cursor += 1
        if cursor >= len(script_text) or script_text[cursor] != '"':
            index = cursor
            continue

        cursor += 1
        key_chars: list[str] = []
        escaped = False
        while cursor < len(script_text):
            char = script_text[cursor]
            if escaped:
                key_chars.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                break
            else:
                key_chars.append(char)
            cursor += 1
        key = "".join(key_chars)
        cursor += 1

        while cursor < len(script_text) and script_text[cursor] not in "[{":
            cursor += 1
        json_text, cursor = _extract_balanced_json(script_text, cursor)
        result[key] = json.loads(json_text)
        index = cursor
    return result


def extract_page_meta(html_text: str) -> dict[str, Any] | None:
    page_meta = extract_json_script(html_text, "data.pageMeta")
    if page_meta:
        return page_meta
    match = re.search(r"var\s+data\s*=\s*(\{.*?\});", html_text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return data.get("pageMeta") or data


def extract_data_url(calc_html: str) -> str:
    match = re.search(r"https://nether\.wowhead\.com/diablo-4/data/paragon-calc\?[^\"\\]+", calc_html)
    if match:
        return html.unescape(match.group(0))
    match = re.search(r"src=[\"']([^\"']*/diablo-4/data/paragon-calc\?[^\"']+)[\"']", calc_html)
    if match:
        return urljoin(WOWHEAD_NETHER_BASE, html.unescape(match.group(1)))
    raise RuntimeError("Could not find Wowhead paragon-calc data script URL")


def extract_simple_page_fields(html_text: str) -> dict[str, Any]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, re.IGNORECASE | re.DOTALL)
    canonical_match = re.search(
        r"<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"']([^\"']+)[\"']",
        html_text,
        re.IGNORECASE,
    )
    alternates = {
        lang: href
        for lang, href in re.findall(
            r"<link[^>]+rel=[\"']alternate[\"'][^>]+hreflang=[\"']([^\"']+)[\"'][^>]+href=[\"']([^\"']+)[\"']",
            html_text,
            re.IGNORECASE,
        )
    }
    tooltip_html = ""
    tooltip_start = re.search(
        r"<div[^>]+class=[\"'][^\"']*wowhead-tooltip[^\"']*[\"'][^>]*>",
        html_text,
        re.IGNORECASE,
    )
    if tooltip_start:
        start = tooltip_start.start()
        end_marker = html_text.find('id="tooltip-controls"', tooltip_start.end())
        if end_marker != -1:
            end = html_text.rfind("<div", start, end_marker)
            tooltip_html = html_text[start : end if end != -1 else end_marker]
        else:
            tooltip_html = html_text[start : tooltip_start.end() + 12000]
    else:
        tooltip_match = re.search(
            r"<table[^>]+class=[\"'][^\"']*wowhead-tooltip[^\"']*[\"'][^>]*>(.*?)</table>",
            html_text,
            re.IGNORECASE | re.DOTALL,
        )
        tooltip_html = tooltip_match.group(1) if tooltip_match else ""
    tooltip_text = strip_tags(tooltip_html) if tooltip_html else ""
    if not tooltip_text:
        body_match = re.search(r"<body[^>]*>(.*?)</body>", html_text, re.IGNORECASE | re.DOTALL)
        tooltip_text = strip_tags(body_match.group(1) if body_match else html_text)[:8000]

    return {
        "title": strip_tags(title_match.group(1)) if title_match else None,
        "h1": strip_tags(h1_match.group(1)) if h1_match else None,
        "canonical_url": canonical_match.group(1) if canonical_match else None,
        "alternate_urls": alternates,
        "tooltip_text": tooltip_text,
    }


def strip_tags(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</(div|p|tr|li|h\d)>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def fetch_list_page(kind: str, class_id: int, locale: str, config: FetchConfig) -> dict[str, Any]:
    locale_part = f"/{locale}" if locale != "en" else ""
    url = f"{WOWHEAD_BASE}{locale_part}/paragon-{kind}/class:{class_id}"
    html_text = fetch_text(url, config)
    return {
        "source_url": url,
        "html_sha256": sha256_text(html_text),
        "listviews": extract_json_script(html_text, "data.page.listPage.listviews"),
        "page_meta": extract_page_meta(html_text),
    }


def fetch_global_list_page(kind: str, locale: str, config: FetchConfig) -> dict[str, Any]:
    locale_part = f"/{locale}" if locale != "en" else ""
    url = f"{WOWHEAD_BASE}{locale_part}/paragon-{kind}"
    html_text = fetch_text(url, config)
    return {
        "source_url": url,
        "html_sha256": sha256_text(html_text),
        "listviews": extract_json_script(html_text, "data.page.listPage.listviews"),
        "page_meta": extract_page_meta(html_text),
    }


def read_existing_raw(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def split_class_names(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def infer_class_name_from_items(items: list[dict[str, Any]]) -> str | None:
    counts: dict[str, int] = {}
    for item in items:
        for class_name in split_class_names(item.get("playerClassNames")):
            counts[class_name] = counts.get(class_name, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda pair: (pair[1], pair[0]))[0]


def filter_list_page_by_class(list_page: dict[str, Any], class_name: str | None) -> dict[str, Any]:
    filtered = deepcopy(list_page)
    listviews = filtered.get("listviews") or []
    if not listviews:
        return filtered
    if not class_name:
        listviews[0]["data"] = []
        return filtered
    listviews[0]["data"] = [
        item
        for item in listviews[0].get("data") or []
        if class_name in split_class_names(item.get("playerClassNames"))
    ]
    filtered["source_url"] = f"{filtered.get('source_url')}#filtered:{class_name}"
    filtered["filtered_class_name"] = class_name
    return filtered


def find_existing_paragon_data_url(raw_root: Path) -> str | None:
    for raw_path in sorted(raw_root.glob("*/wowhead_raw.json")):
        raw = read_existing_raw(raw_path)
        data_url = (((raw or {}).get("sources") or {}).get("paragon_data"))
        if data_url:
            return data_url
    return None


def resolve_paragon_data_url(
    args: argparse.Namespace,
    config: FetchConfig,
    raw_root: Path,
    reuse_events: list[str],
) -> tuple[str, str]:
    if args.paragon_data_url:
        return args.paragon_data_url, ""

    calc_url = f"{WOWHEAD_BASE}/paragon-calc/paladin"
    try:
        calc_html = fetch_text(calc_url, config)
        return extract_data_url(calc_html), calc_html
    except Exception as exc:
        data_url = find_existing_paragon_data_url(raw_root)
        if not data_url:
            raise
        reuse_events.append(f"paragon calc page fetch failed; reused existing paragon data URL: {exc}")
        return data_url, ""


def discover_classes(args: argparse.Namespace, config: FetchConfig) -> dict[str, Any]:
    raw_root = Path(args.out)
    reuse_events: list[str] = []
    warnings: list[str] = []
    data_url, calc_html = resolve_paragon_data_url(args, config, raw_root, reuse_events)
    print(f"Discovering classes from paragon data script: {data_url}", file=sys.stderr)
    data_script = fetch_text(data_url, config)
    page_data = parse_wowhead_page_data_script(data_script)
    class_board_ids = sorted(int(class_id) for class_id in page_data["d4.paragonCalc.d4.classBoards"].keys())

    global_list_pages: dict[str, dict[str, Any]] = {"glyphs": {}, "nodes": {}}
    for locale in ["en", args.locale] if args.locale != "en" else ["en"]:
        for section, kind in (("glyphs", "glyphs"), ("nodes", "nodes")):
            try:
                print(f"Fetching {locale} global {section[:-1]} list", file=sys.stderr)
                global_list_pages[section][locale] = fetch_global_list_page(kind, locale, config)
            except Exception as exc:
                warnings.append(f"global {section} {locale} list fetch failed: {exc}")

    registry: dict[str, dict[str, Any]] = {}
    fallback_registry = fallback_class_registry()
    for class_id in class_board_ids:
        fallback = fallback_class_by_id(class_id)
        name_en = (fallback or {}).get("name", {}).get("en")
        name_ru = (fallback or {}).get("name", {}).get("ru")
        discovery_source = "fallback"

        # Unknown future class ids are inferred by class-filtered list pages.
        if not fallback:
            try:
                filtered = fetch_list_page("glyphs", class_id, "en", config)
                name_en = infer_class_name_from_items(listview_items(filtered))
                discovery_source = "filtered_glyphs"
            except Exception as exc:
                warnings.append(f"class {class_id} English name discovery failed: {exc}")
            if args.locale != "en":
                try:
                    filtered_ru = fetch_list_page("glyphs", class_id, args.locale, config)
                    name_ru = infer_class_name_from_items(listview_items(filtered_ru))
                except Exception as exc:
                    warnings.append(f"class {class_id} {args.locale} name discovery failed: {exc}")

        slug = (fallback or {}).get("slug") or slugify(name_en or f"class-{class_id}")
        registry[slug] = {
            "id": class_id,
            "slug": slug,
            "class_mask": 1 << class_id,
            "name": {"en": name_en or slug.replace("-", " ").title(), "ru": name_ru},
            "discovery_source": discovery_source,
        }

    return {
        "schema_version": 1,
        "checked_at": utc_now(),
        "data_url": data_url,
        "calc_html": calc_html,
        "page_data": page_data,
        "raw_script_sha256": sha256_text(data_script),
        "classes": registry,
        "global_list_pages": global_list_pages,
        "warnings": warnings,
        "reuse_events": reuse_events,
    }


def detail_cache_key(kind: str, item_id: int, locale: str) -> str:
    return f"{kind}:{item_id}:{locale}"


def load_detail_cache(path: Path | None, raw_root: Path | None = None) -> dict[str, Any]:
    cache = read_json_if_exists(path) if path else None
    if not cache:
        cache = {"schema_version": 1, "details": {}}
    cache.setdefault("details", {})

    if raw_root:
        for raw_path in sorted(raw_root.glob("*/wowhead_raw.json")):
            raw = read_existing_raw(raw_path)
            if not raw:
                continue
            for section, kind in (("glyphs", "glyph"), ("nodes", "node")):
                for item_id, locales in ((raw.get("details") or {}).get(section) or {}).items():
                    for locale, detail in (locales or {}).items():
                        if not detail or detail.get("fetch_error"):
                            continue
                        key = detail_cache_key(kind, int(item_id), locale)
                        cache["details"].setdefault(key, cacheable_detail(detail))
    return cache


def save_detail_cache(path: Path | None, cache: dict[str, Any]) -> None:
    if not path:
        return
    cache["updated_at"] = utc_now()
    write_json(path, cache)


def cacheable_detail(detail: dict[str, Any]) -> dict[str, Any]:
    cached = deepcopy(detail)
    cached.pop("reused_from_previous_raw", None)
    return cached


def item_detail_url(kind: str, item_id: int, name: str, locale: str) -> str:
    locale_part = f"/{locale}" if locale != "en" else ""
    item_kind = "paragon-glyph" if kind == "glyph" else "paragon-node"
    return f"{WOWHEAD_BASE}{locale_part}/{item_kind}/{slugify(name)}-{item_id}"


def fetch_detail_page(kind: str, item_id: int, name: str, locale: str, config: FetchConfig) -> dict[str, Any]:
    url = item_detail_url(kind, item_id, name, locale)
    html_text = fetch_text(url, config)
    fields = extract_simple_page_fields(html_text)
    fields.update(
        {
            "source_url": url,
            "html_sha256": sha256_text(html_text),
            "page_meta": extract_page_meta(html_text),
        }
    )
    return fields


def listview_items(list_page: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not list_page:
        return []
    listviews = list_page.get("listviews") or []
    if not listviews:
        return []
    return listviews[0].get("data") or []


def collect_detail_pages(
    kind: str,
    en_items: list[dict[str, Any]],
    locales: list[str],
    config: FetchConfig,
    max_details: int | None,
    existing_details: dict[str, Any] | None = None,
    prefer_existing_details: bool = False,
    detail_cache: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    details: dict[str, Any] = {}
    detail_errors: list[str] = []
    limited_items = en_items[:max_details] if max_details is not None else en_items
    for index, item in enumerate(limited_items, 1):
        item_id = int(item["id"])
        name = str(item.get("name") or item_id)
        details[str(item_id)] = {}
        for locale in locales:
            cache_key = detail_cache_key(kind, item_id, locale)
            cached_detail = ((detail_cache or {}).get("details") or {}).get(cache_key)
            if cached_detail and not cached_detail.get("fetch_error"):
                details[str(item_id)][locale] = deepcopy(cached_detail)
                details[str(item_id)][locale]["reused_from_detail_cache"] = True
                continue

            existing_detail = (((existing_details or {}).get(str(item_id)) or {}).get(locale) or {})
            if prefer_existing_details and existing_detail and not existing_detail.get("fetch_error"):
                details[str(item_id)][locale] = deepcopy(existing_detail)
                details[str(item_id)][locale]["reused_from_previous_raw"] = True
                if detail_cache is not None:
                    detail_cache.setdefault("details", {})[cache_key] = cacheable_detail(existing_detail)
                continue
            try:
                details[str(item_id)][locale] = fetch_detail_page(kind, item_id, name, locale, config)
                if detail_cache is not None:
                    detail_cache.setdefault("details", {})[cache_key] = cacheable_detail(details[str(item_id)][locale])
                if config.sleep:
                    time.sleep(config.sleep)
            except Exception as exc:  # noqa: BLE001 - crawler must preserve partial progress.
                if existing_detail and not existing_detail.get("fetch_error"):
                    details[str(item_id)][locale] = deepcopy(existing_detail)
                    details[str(item_id)][locale]["reused_from_previous_raw"] = True
                    if detail_cache is not None:
                        detail_cache.setdefault("details", {})[cache_key] = cacheable_detail(existing_detail)
                    continue
                else:
                    details[str(item_id)][locale] = {
                        "source_url": item_detail_url(kind, item_id, name, locale),
                        "fetch_error": str(exc),
                        "title": name,
                        "h1": name,
                        "canonical_url": None,
                        "alternate_urls": {},
                        "tooltip_text": None,
                        "page_meta": None,
                    }
                detail_errors.append(f"{kind} {item_id} {locale}: {exc}")
        print(f"  {kind} details {index}/{len(limited_items)}: {name}", file=sys.stderr)
    return details, detail_errors


def crawl(args: argparse.Namespace) -> Path:
    if args.class_slug.lower() == "all":
        return crawl_all(args)

    class_slug = args.class_slug.lower()
    registry = getattr(args, "class_registry", None) or fallback_class_registry()
    if class_slug not in registry:
        raise SystemExit(f"Unknown class '{class_slug}'. Known: {', '.join(sorted(registry))}")

    checked_at = utc_now()
    class_info = registry[class_slug]
    class_id = int(class_info["id"])
    fetch_config = make_fetch_config(args)
    locales = ["en"]
    if args.locale != "en":
        locales.append(args.locale)

    out_dir = Path(args.out) / class_slug
    out_path = out_dir / "wowhead_raw.json"
    existing_raw = read_existing_raw(out_path)
    detail_cache_path = Path(args.detail_cache) if getattr(args, "detail_cache", None) else None
    detail_cache = load_detail_cache(detail_cache_path, Path(args.out)) if detail_cache_path else None

    calc_url = f"{WOWHEAD_BASE}/paragon-calc/{class_slug}"
    calc_html = ""
    warnings: list[str] = []
    reuse_events: list[str] = []
    if args.paragon_data_url:
        data_url = args.paragon_data_url
    else:
        print(f"Fetching paragon calc page: {calc_url}", file=sys.stderr)
        try:
            calc_html = fetch_text(calc_url, fetch_config)
            data_url = extract_data_url(calc_html)
        except Exception as exc:
            data_url = (((existing_raw or {}).get("sources") or {}).get("paragon_data"))
            if not data_url:
                raise
            reuse_events.append(f"paragon calc page fetch failed; reused existing paragon data URL: {exc}")
    print(f"Fetching paragon data script: {data_url}", file=sys.stderr)
    data_script = fetch_text(data_url, fetch_config)
    page_data = parse_wowhead_page_data_script(data_script)

    list_pages: dict[str, Any] = {"glyphs": {}, "nodes": {}}
    global_list_pages = getattr(args, "global_list_pages", None) or {}
    for locale in locales:
        class_name = (class_info.get("name") or {}).get(locale)
        for section, kind in (("glyphs", "glyphs"), ("nodes", "nodes")):
            global_page = ((global_list_pages.get(section) or {}).get(locale))
            if global_page:
                list_pages[section][locale] = filter_list_page_by_class(global_page, class_name)
                continue

            print(f"Fetching {locale} {section[:-1]} list", file=sys.stderr)
            try:
                list_pages[section][locale] = fetch_list_page(kind, class_id, locale, fetch_config)
            except Exception as exc:
                existing_list_page = ((((existing_raw or {}).get("list_pages") or {}).get(section) or {}).get(locale))
                if not existing_list_page:
                    raise
                list_pages[section][locale] = deepcopy(existing_list_page)
                list_pages[section][locale]["reused_from_previous_raw"] = True
                reuse_events.append(f"{section} {locale} list fetch failed; reused existing raw list page: {exc}")

    detail_errors: list[str] = []
    details = {"glyphs": {}, "nodes": {}}
    if not args.skip_detail_pages:
        glyph_details, glyph_detail_errors = collect_detail_pages(
            "glyph",
            listview_items(list_pages["glyphs"].get("en")),
            locales,
            fetch_config,
            args.max_detail_pages,
            ((existing_raw or {}).get("details") or {}).get("glyphs"),
            args.prefer_existing_details,
            detail_cache,
        )
        node_details, node_detail_errors = collect_detail_pages(
            "node",
            listview_items(list_pages["nodes"].get("en")),
            locales,
            fetch_config,
            args.max_detail_pages,
            ((existing_raw or {}).get("details") or {}).get("nodes"),
            args.prefer_existing_details,
            detail_cache,
        )
        details["glyphs"] = glyph_details
        details["nodes"] = node_details
        detail_errors.extend(glyph_detail_errors)
        detail_errors.extend(node_detail_errors)
        if args.strict_detail_pages and detail_errors:
            warnings.extend(detail_errors)
    elif existing_raw and existing_raw.get("details"):
        details = deepcopy(existing_raw.get("details") or details)
        detail_errors = list(existing_raw.get("detail_errors") or [])
        reuse_events.append("detail pages skipped; reused existing detail section from previous raw")

    page_meta = extract_page_meta(calc_html) if calc_html else (existing_raw or {}).get("page_meta")
    output = {
        "schema_version": 1,
        "source": "wowhead",
        "class": class_slug,
        "class_id": class_id,
        "class_mask": int(class_info.get("class_mask") or (1 << class_id)),
        "class_name": class_info.get("name") or {"en": class_slug.title(), "ru": CLASS_RU.get(class_slug)},
        "class_discovery": {
            key: value
            for key, value in class_info.items()
            if key not in {"id", "slug", "class_mask", "name"}
        },
        "locale": args.locale,
        "checked_at": checked_at,
        "sources": {
            "paragon_calc": calc_url,
            "paragon_data": data_url,
            "glyphs": {locale: list_pages["glyphs"][locale]["source_url"] for locale in locales},
            "nodes": {locale: list_pages["nodes"][locale]["source_url"] for locale in locales},
        },
        "page_meta": page_meta,
        "paragon_calc": {
            "source_url": calc_url,
            "resolved_data_url": data_url,
            "page_data": page_data,
            "raw_script_sha256": sha256_text(data_script),
        },
        "list_pages": list_pages,
        "details": details,
        "detail_errors": detail_errors,
        "reuse_events": reuse_events,
        "warnings": warnings,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    save_detail_cache(detail_cache_path, detail_cache or {})
    print(f"Wrote {out_path}", file=sys.stderr)
    return out_path


def crawl_all(args: argparse.Namespace) -> Path:
    fetch_config = make_fetch_config(args)
    discovery = discover_classes(args, fetch_config)
    out_root = Path(args.out)
    class_registry = discovery["classes"]
    write_json(
        out_root / "_classes.json",
        {
            "schema_version": 1,
            "checked_at": discovery["checked_at"],
            "source": "wowhead",
            "paragon_data_url": discovery["data_url"],
            "classes": class_registry,
            "warnings": discovery["warnings"],
            "reuse_events": discovery["reuse_events"],
        },
    )

    detail_cache = Path(args.detail_cache) if args.detail_cache else out_root / "_detail_cache.json"
    results: list[dict[str, Any]] = []
    ordered_classes = sorted(class_registry.values(), key=lambda item: int(item["id"]))
    for index, class_info in enumerate(ordered_classes, 1):
        print(
            f"\n[{index}/{len(ordered_classes)}] Crawling {class_info['slug']} "
            f"({(class_info.get('name') or {}).get('en')})",
            file=sys.stderr,
        )
        child_args = argparse.Namespace(**vars(args))
        child_args.class_slug = class_info["slug"]
        child_args.paragon_data_url = args.paragon_data_url or discovery["data_url"]
        child_args.class_registry = class_registry
        child_args.global_list_pages = discovery["global_list_pages"]
        child_args.detail_cache = str(detail_cache)
        child_args.prefer_existing_details = True
        try:
            raw_path = crawl(child_args)
            raw = read_existing_raw(raw_path) or {}
            results.append(
                {
                    "class": class_info["slug"],
                    "raw_path": str(raw_path),
                    "glyphs": len(listview_items(((raw.get("list_pages") or {}).get("glyphs") or {}).get("en"))),
                    "nodes": len(listview_items(((raw.get("list_pages") or {}).get("nodes") or {}).get("en"))),
                    "detail_errors": len(raw.get("detail_errors") or []),
                    "warnings": raw.get("warnings") or [],
                }
            )
        except Exception as exc:  # noqa: BLE001 - keep all-class crawls resumable.
            results.append({"class": class_info["slug"], "error": str(exc)})
            print(f"  class crawl failed: {class_info['slug']}: {exc}", file=sys.stderr)

        if args.class_sleep and index < len(ordered_classes):
            print(f"  sleeping {args.class_sleep:.1f}s before next class", file=sys.stderr)
            time.sleep(args.class_sleep)

    summary = {
        "schema_version": 1,
        "checked_at": utc_now(),
        "classes": [item["slug"] for item in ordered_classes],
        "results": results,
        "detail_cache": str(detail_cache),
        "warnings": discovery["warnings"],
        "reuse_events": discovery["reuse_events"],
    }
    summary_path = out_root / "_crawl_all_summary.json"
    write_json(summary_path, summary)
    return summary_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Wowhead Diablo IV paragon crawler")
    subparsers = parser.add_subparsers(dest="command", required=True)
    crawl_parser = subparsers.add_parser("crawl", help="Download raw paragon data for one class")
    crawl_parser.add_argument("--class", dest="class_slug", required=True, help="Class slug, e.g. paladin, or all")
    crawl_parser.add_argument("--out", required=True, help="Output directory for raw data")
    crawl_parser.add_argument("--locale", default="ru", help="Secondary locale to fetch, default: ru")
    crawl_parser.add_argument(
        "--paragon-data-url",
        default=None,
        help="Direct Wowhead nether paragon-calc data URL. Useful when the public calc page is temporarily blocked.",
    )
    crawl_parser.add_argument("--timeout", type=float, default=30.0)
    crawl_parser.add_argument("--retries", type=int, default=4)
    crawl_parser.add_argument("--sleep", type=float, default=0.75, help="Base delay between requests and retries")
    crawl_parser.add_argument("--retry-backoff", type=float, default=2.0, help="Retry delay multiplier")
    crawl_parser.add_argument("--block-sleep", type=float, default=20.0, help="Minimum retry delay for 403/429")
    crawl_parser.add_argument("--max-retry-sleep", type=float, default=90.0, help="Maximum retry delay")
    crawl_parser.add_argument("--retry-jitter", type=float, default=0.25, help="Random jitter fraction for retry delays")
    crawl_parser.add_argument("--class-sleep", type=float, default=5.0, help="Delay between classes when --class all")
    crawl_parser.add_argument(
        "--detail-cache",
        default=None,
        help="Shared detail cache JSON. Defaults to <out>/_detail_cache.json for --class all.",
    )
    crawl_parser.add_argument("--skip-detail-pages", action="store_true", help="Only fetch list pages and data scripts")
    crawl_parser.add_argument(
        "--max-detail-pages",
        type=int,
        default=None,
        help="Debug limit for glyph/node detail pages. Omit for full class crawl.",
    )
    crawl_parser.add_argument(
        "--strict-detail-pages",
        action="store_true",
        help="Promote detail page fetch errors to top-level warnings.",
    )
    crawl_parser.add_argument(
        "--prefer-existing-details",
        action="store_true",
        help="Reuse existing successful detail pages before fetching them again.",
    )
    crawl_parser.set_defaults(func=crawl)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
