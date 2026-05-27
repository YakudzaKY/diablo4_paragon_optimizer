<img width="1984" height="1250" alt="Снимок экрана 2026-05-27 183913" src="https://github.com/user-attachments/assets/502e9121-525c-46fc-a12f-fc2c9ec7891d" />

# Paragon Optimizer Data Pipeline

Первый этап оптимизатора: локальный сбор и нормализация справочников парагона Diablo IV.

## Быстрый порядок работы

Если локальных данных еще нет или они устарели, сначала запустите краулер и нормализацию. Готовый батник в корне проекта полностью пересобирает `data/raw` и `data/normalized` для всех классов:

```bat
update_data.bat
```

После этого получите схему нужного класса: она показывает доступные статы, доски и глифы, которые можно использовать в профиле и файле весов.

```bat
get_schema.bat
```

По умолчанию пример смотрит `paladin`; для другого класса поменяйте `--class` внутри батника или запустите CLI напрямую:

```bat
bin\paragon_optimize.exe schema --class paladin
```

Дальше создайте или поправьте профиль в `profiles/*.json` и файл весов в `weights/*.json`, затем запускайте оптимизацию. В корне уже есть готовые примеры:

```bat
optimize_fast.bat
optimize_long.bat
optimize_long_full.bat
```

`optimize_fast.bat` использует обычные лимиты для быстрого прогона, `optimize_long.bat` снимает лимит маршрутов, а `optimize_long_full.bat` дополнительно отключает отсечение candidate targets.

## Crawler

```bash
python -m paragon_optimizer.crawler.wowhead_crawler crawl --class paladin --out paragon_optimizer/data/raw
```

Все классы можно скачать одним запуском:

```bash
python -m paragon_optimizer.crawler.wowhead_crawler crawl --class all --out paragon_optimizer/data/raw
```

Краулер скачивает:

- Wowhead `paragon-calc` data script с досками, координатами узлов, узлами и глифами;
- страницы списков глифов и узлов для класса;
- детальные страницы глифов и узлов на английском и русском языках;
- `source_url`, `checked_at`, хэши HTML/скриптов и предупреждения по частично нескачанным страницам.

Сырые данные сохраняются отдельно:

```text
paragon_optimizer/data/raw/<class>/wowhead_raw.json
```

Для быстрой отладки можно ограничить детальные страницы:

```bash
python -m paragon_optimizer.crawler.wowhead_crawler crawl --class paladin --out paragon_optimizer/data/raw --max-detail-pages 3
```

Если Wowhead временно отдает 403 на публичные страницы, но известен актуальный `nether` data-script, его можно передать напрямую:

```bash
python -m paragon_optimizer.crawler.wowhead_crawler crawl --class paladin --out paragon_optimizer/data/raw --paragon-data-url "https://nether.wowhead.com/diablo-4/data/paragon-calc?dv=17&db=1778694731"
```

Для повторного запуска поверх уже скачанного raw можно не дергать успешные detail-страницы заново:

```bash
python -m paragon_optimizer.crawler.wowhead_crawler crawl --class paladin --out paragon_optimizer/data/raw --prefer-existing-details
```

Для длинных прогонов используются задержки и backoff на `403/429`:

```bash
python -m paragon_optimizer.crawler.wowhead_crawler crawl --class all --out paragon_optimizer/data/raw --sleep 1.5 --block-sleep 45 --retries 5 --prefer-existing-details
```

## Normalize

```bash
python -m paragon_optimizer.crawler.normalize normalize --in paragon_optimizer/data/raw --out paragon_optimizer/data/normalized --class paladin
```

Нормализация всех скачанных классов:

```bash
python -m paragon_optimizer.crawler.normalize normalize --in paragon_optimizer/data/raw --out paragon_optimizer/data/normalized --class all
```

Нормализатор создает:

```text
paragon_optimizer/data/normalized/classes/<class>.json
paragon_optimizer/data/normalized/boards/<class>/*.json
paragon_optimizer/data/normalized/glyphs/<class>/*.json
paragon_optimizer/data/normalized/manifest/<class>.json
```

Связи между узлами выводятся из соседства координат на сетке и помечаются как `edge_source: "inferred_grid_adjacency"`. Исходные идентификаторы Wowhead, `searchText`, теги, требования и описания сохраняются в JSON, чтобы будущий оптимизатор мог работать офлайн, а парсер статов можно было уточнять без повторного краулинга.

Для полуавтоматической правки можно передать файл или директорию overrides:

```bash
python -m paragon_optimizer.crawler.normalize normalize --in paragon_optimizer/data/raw --out paragon_optimizer/data/normalized --class paladin --manual-overrides paragon_optimizer/crawler/manual_overrides
```

Ожидаемый файл для класса: `paragon_optimizer/crawler/manual_overrides/paladin.json`.

## Tests

```bash
build_native.bat
python -m unittest paragon_optimizer.tests.test_crawler_parsing
```

## Optimizer

Оптимизация, скоринг, перебор маршрутов и HTML-визуализация находятся в standalone C++ executable:

```bash
paragon_optimizer/build_native.bat
```

После сборки батник `paragon_optimizer/optimize.bat` запускает `paragon_optimizer/bin/paragon_optimize.exe` напрямую. Native CLI сам загружает normalized JSON, перебирает раскладки, считает маршруты, скоринг и пишет HTML-файл в `paragon_optimizer/out`. Python для оптимизации не используется.

**Важно про редкие и легендарные ноды:** в нормализованных данных безусловные статы ноды находятся в `stats`, а дополнительная строка `Bonus: Another ... if requirements met` находится в `bonus_stats`. Это соответствует игровой механике: базовые статы работают всегда, а дополнительные бонусы применяются только после выполнения требований с учётом масштабирования по глубине доски.

Справка по доступным статам, доскам и глифам класса:

```bash
paragon_optimizer/bin/paragon_optimize.exe schema --class paladin
```

Запуск эвристического оптимизатора:

```bash
paragon_optimizer/bin/paragon_optimize.exe optimize --profile paragon_optimizer/profiles/paladin_juggernaut_shield_bash.json
```

Профиль запуска хранит конкретного персонажа и параметры расчета:

```json
{
  "class": "paladin",
  "points": 252,
  "legendary_glyphs": true,
  "weights": "../weights/paladin_juggernaut_shield_bash.json",
  "starting_stats": {
    "strength": 2124.0,
    "intelligence": 254.0,
    "willpower": 254.0,
    "dexterity": 255.0
  }
}
```

Файл в `weights/*.json` отвечает только за модель оценки: веса статов, приоритеты досок, глифов и optional `minimums`. Стартовые характеристики, класс, количество свободных очков и ссылка на веса живут в `profiles/*.json`.
Относительный путь в поле `weights` считается от директории самого профиля.

Для настройки агрессивности поиска вокруг глифов в файле весов можно добавить блок `glyph_route`. Он влияет только на эвристику построения маршрута, финальный скор по-прежнему пересчитывается полной моделью:

```json
{
  "glyph_route": {
    "activation": 1.0,
    "scaling": 1.0,
    "future": 0.35,
    "synergy": 0.25,
    "scarcity": 0.30,
    "fill_target": 1.20,
    "max_bonus_multiplier": 1.60
  }
}
```

`activation` усиливает добор порога глифа, `scaling` - ценность статов сверх порога, `future` и `synergy` - ноды, полезные нескольким возможным глифам, `scarcity` - дефицитные общие threshold-статы, `fill_target` задаёт желаемое заполнение радиуса относительно требования, `max_bonus_multiplier` ограничивает максимальный маршрутный hint.

Явные аргументы CLI переопределяют профиль:

```bash
paragon_optimizer/bin/paragon_optimize.exe optimize --profile paragon_optimizer/profiles/paladin_juggernaut_shield_bash.json --points 220
```

Практичный профиль запуска по умолчанию:

* `--max-routes 3000`
* `--candidate-targets 320`
* `--workers 0`

На `paladin_juggernaut_shield_bash.json` этот профиль дал тот же лучший результат, что `6000` и `10000` маршрутов, но укладывается примерно в несколько секунд. `candidate-targets` ниже `320` уже начинал терять качество на этом тесте.

Для быстрых пробных запусков лимиты можно уменьшить:

```bash
paragon_optimizer/bin/paragon_optimize.exe optimize \
  --profile paragon_optimizer/profiles/paladin_juggernaut_shield_bash.json \
  --max-routes 3000 \
  --candidate-targets 320
```

Расчет маршрутов и скоринга запускается в несколько native-потоков: `--workers 0` использует половину логических ядер системы. `--max-routes 0` отключает лимит маршрутов, `--candidate-targets 0` отключает отсечение целевых узлов; это заметно тяжелее и обычно нужно только для проверки качества.

По умолчанию JSON не содержит полные `route_steps`, чтобы вывод оставался компактнее. Для отладки маршрута:

```bash
paragon_optimizer/bin/paragon_optimize.exe optimize --profile paragon_optimizer/profiles/paladin_juggernaut_shield_bash.json --include-route-steps
```

CLI всегда пишет в stdout чистый JSON. HTML создаётся по умолчанию, а путь попадает в поле `html_file`; если нужен только JSON без HTML-файла, используется `--no-html`.

### Общие параметры native CLI

* `--data`: Путь к директории с нормализованными данными парагона. По умолчанию используется `data/normalized` рядом с директорией `bin`.

### Параметры команды `optimize`

* `--profile`: Путь к JSON профилю запуска из `profiles/*.json`. Профиль может задавать `class`, `points`, `legendary_glyphs`, `weights`, `starting_stats`, `max_routes`, `candidate_targets`, `workers`, `scheme`, `include_route_steps`, `no_html` и `data`.
* `--class`: Класс персонажа (например, `paladin`, `barbarian`, `sorcerer`). Обязателен, если не задан в профиле.
* `--points`: Доступное количество очков парагона для распределения. Обязательно, если не задано в профиле.
* `--weights`: Путь к JSON файлу с весами характеристик, узлов и глифов, определяющими приоритеты для оптимизации (например, `paragon_optimizer/weights/paladin_balanced.json`). Обязателен, если не задан в профиле. Файл весов может содержать опциональные поля `scheme` для перебора досок и `glyph_route` для настройки агрессивности глиф-ориентированной маршрутной эвристики.
* `--scheme`: Опциональный список досок, которые алгоритм должен использовать, исключая другие доски. Например: `--scheme castle shield_bearer fervent divinity`. Если передано, алгоритм перебирает только стартовую доску и указанные в `scheme`.
* `--legendary-glyphs`: Использовать ли легендарные глифы (допустимые значения: `true`, `false`. По умолчанию `true`).
* `--max-routes`: Лимит на максимальное количество полных маршрутов, которые будут детально оценены после предварительного отбора. По умолчанию `3000`; `0` отключает лимит.
* `--candidate-targets`: Количество лучших потенциальных целевых узлов на доске для построения путей, отбираемых эвристикой. По умолчанию `320`; `0` отключает отсечение.
* `--workers`: Количество потоков для расчета маршрутов. `0` использует половину логических ядер системы и используется по умолчанию; `1` включает однопоточный режим.
* `--include-route-steps`: Включить детальный пошаговый список выбранных узлов (`route_steps`) в итоговый JSON. Увеличивает размер вывода, но полезно для отладки.
* `--no-html`: Не создавать HTML-визуализацию результата.
