<img width="1984" height="1250" alt="Снимок экрана 2026-05-27 183913" src="https://github.com/user-attachments/assets/502e9121-525c-46fc-a12f-fc2c9ec7891d" />

# Paragon Optimizer Data Pipeline

Оптимизатор досок парагона. Предназначен в первую очередь для ИИ(билд-врайтера), как вспомогательный инструмент, но можно использовать вручную.
- 100% написан **CODEX**-ом(и немного Grok-ом).

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

`optimize_fast.bat` использует новые повышенные дефолты из исходников (30 000 маршрутов / 1000 кандидатов) для хорошего качества за разумное время. `optimize_long.bat` снимает лимит маршрутов (но оставляет разумный отбор кандидатов), а `optimize_long_full.bat` полностью отключает оба лимита (--max-routes 0 --candidate-targets 0) для максимально тщательного поиска.

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

В `schema` поле `available_glyphs` выводится объектами, а не только id: для каждого глифа доступны `id`, `name`, `max_level`, `radius.starting`, `radius.upgrade_levels` и структурированный `node_bonus` или `null`. В `profile_schema_example` есть пример блока `glyph_levels` с уровнем `51`, чтобы было видно, где задается прокачка глифов.

Запуск эвристического оптимизатора:

```bash
paragon_optimizer/bin/paragon_optimize.exe optimize --profile paragon_optimizer/profiles/paladin_juggernaut_shield_bash.json
```

Профиль запуска хранит конкретного персонажа и параметры расчета:

```json
{
  "class": "paladin",
  "points": 252,
  "weights": "../weights/paladin_juggernaut_shield_bash.json",
  "glyph_levels": {
    "sentinel": 51,
    "exploit": 51,
    "turf": 51,
    "spirit": 51,
    "honed": 51
  },
  "starting_stats": {
    "strength": 2124.0,
    "intelligence": 254.0,
    "willpower": 254.0,
    "dexterity": 255.0
  }
}
```

Файл в `weights/*.json` отвечает только за модель оценки: веса статов, приоритеты досок, глифов и optional `minimums`. Стартовые характеристики, класс, количество свободных очков, уровни глифов и ссылка на веса живут в `profiles/*.json`.
Относительный путь в поле `weights` считается от директории самого профиля.

`glyph_levels` задаёт реальные уровни прокачанных глифов по id из `data/normalized/glyphs/<class>/*.json`. Допустимый диапазон — `1..max_level`; неизвестный id или уровень вне диапазона считается ошибкой профиля. Если глиф не указан, CLI считает его уровень равным `1`. Для текущих билдов в примерах используется `51`: на текущем датасете это уже открывает оба апгрейда радиуса из `radius.upgrade_levels` и даёт радиус `5`.

### Как работают веса (`weights/*.json`)

Файл весов — это модель оценки. Чем выше скор — тем лучше маршрут.

- **`weights`** — веса статов (линейная модель полезности). Вклад = значение_стата × вес; все вклады суммируются.
  - Значения статов из данных нод — «как в тексте»: `5.0` для +5% или +5 к силе (нет деления/умножения на 100).
  - Пример: нода даёт `{"max_life": 5.0}` (+5% макс. здоровья), вес `"max_life": 2` → вклад +10.0 в stat-часть скора этой ноды.
  - Для одной ноды: сумма (стат × вес) по всем её статам + бонус типа ноды.
  - `glyph_bonus` — специальный ключ: добавляется один раз в `glyph_score` за каждый активированный глиф (когда набран порог threshold).
  - `glyph_socket` — бонус только для эвристики маршрутизации (чтобы приоритетно добираться до сокетов).
- **`glyphs`** — приоритет конкретных глифов (по id из схемы). Неактивированный глиф получает только **25%** веса. Уровень глифа задаётся не здесь, а в `profiles/*.json` через `glyph_levels`.
- **`scheme`**:
  - объект — сильно поднимает ценность легендарных узлов на этих досках;
  - массив — желаемый порядок досок.
- **`minimums`** (опц.) — штрафы, если стат ниже порога.

**Скор маршрута** = (статы от выбранных нод × веса) + (бонусы типов нод: normal 0.1 / magic 0.2 / rare 0.3 / legendary 0.4 + overrides из scheme) + (взвешенные бонус-статы активированных редких/легендарных) + (скор глифов: активация порога + scaling + предпочтения) − (штрафы из `minimums` и за перерасход очков).

Скор — это произвольная utility-метрика (не проценты и не «эффективность» в игровых единицах). Веса задают относительную ценность разных статов именно для вашей модели оценки.

### Что реально учитывается от глифов

У глифов есть два разных слоя оценки, и их важно не смешивать:

- **Обычный вес ноды** считается всегда: если нода даёт `{"dexterity": 5.0}`, она получает `5.0 × weights.dexterity` независимо от того, стоит она в радиусе глифа или нет.
- **Дополнительная ценность в радиусе глифа** считается отдельно. Если глиф говорит `For every 5 Dexterity purchased within range...`, то ноды с `dexterity` внутри радиуса дополнительно помогают `glyph_score` и маршрутной эвристике `glyph_route`.
- **Усиление normal/magic/rare нод глифом** учитывается отдельно: если глиф даёт `+30% bonus to all Magic nodes within range`, то подходящие выбранные ноды в радиусе получают дополнительный вклад в финальный `glyph_node_bonus_score`, а маршрутная эвристика может заранее повышать их ценность через `glyph_route.node_bonus`.
- **`glyphs`** задаёт приоритет самого глифа по id. Это не множитель к каждой ноде в радиусе и не замена весам статов.

Для финальной оценки назначенного глифа CLI считает примерно так:

```text
stat_in_radius = сумма threshold-стата выбранных нод в радиусе
increments = floor(stat_in_radius / 5)
glyph_score =
  increments × scaling_value_per_5 × weights[bonus_stat]
  + glyph_bonus, если threshold выполнен
  + glyphs[glyph_id], если для глифа задан приоритет
  + node_bonus_score от усиленных normal/magic/rare нод в радиусе
```

Пример: глиф `spirit` у друида содержит текст `For every 5 Dexterity purchased within range, you deal +2.0% increased Critical Strike Damage` и требует `+25 Dexterity`. Если в радиусе набрано `59 Dexterity`, то `increments = floor(59 / 5) = 11`, а scaling-часть глифа будет `11 × 2.0 × weights[bonus_stat]`.

Откуда берутся поля:

- `threshold_stat` берётся из нормализованного `threshold_attributes[].stat_key` глифа, например `dexterity`.
- `requirement` и `scaling_value_per_5` сейчас парсятся из `bonus_text.en` глифа.
- `bonus_stat` сейчас **не хранится явно** в нормализованных данных. CLI выводит его эвристически по `skill_tags`: например `Damage` → `damage`, `Critical Strikes` → `critical_strike_damage`, `Vulnerable` → `vulnerable_damage`. Если у глифа есть несколько тегов, выбирается тот stat key, у которого выше вес в `weights`.

Из-за этого для некоторых глифов возможна грубая аппроксимация. Например текст может говорить про `Critical Strike Damage`, но при наличии тега `Damage` и более высокого веса `damage` CLI может оценить scaling через `weights.damage`. Это не меняет фактические данные глифа, но влияет на модель скора.

`glyph_route` не добавляет постоянный стат в результат. Это эвристика маршрутизации: она временно повышает ценность нод в радиусе, которые помогают добрать threshold, следующие шаги `For every 5 ...` или получают normal/magic/rare bonus от возможного глифа. Финальный вклад всё равно попадает в `glyph_score` / `glyph_node_bonus_score`, а не в `stats` выбранной ноды.

**Важно про ограничения глифов:** поле `glyphs` в файле весов является приоритетом, а не строгим whitelist. Нулевой вес у глифа означает отсутствие дополнительного предпочтения, но не абсолютный запрет. Если нужен строгий запрет всех негайдовых глифов, это должно поддерживаться отдельной логикой CLI.

### Что реально учитывается от легендарных нод

Обычные числовые статы нод находятся в `stats`, а условные дополнительные строки редких нод вида `Bonus: Another ... if requirements met` находятся в `bonus_stats`. Такие `bonus_stats` учитываются только если требования редкой/легендарной ноды выполнены с учётом глубины доски.

У многих легендарных нод главный эффект написан текстом: например `After spending 75 Spirit, you deal 40%[x] increased damage for 5 seconds`. Такие множители часто **не превращаются в `stats`** при нормализации: у легендарной ноды может быть `"stats": {}` и `"bonus_stats": {}`. В этом случае оптимизатор не знает реальную силу эффекта из данных ноды.

Поэтому для легендарных нод главный инструмент оценки — `scheme`, если он задан объектом:

```json
"scheme": {
  "ancestral_guidance": 520.0
}
```

Такой вес добавляется к legendary-ноду на соответствующей доске как ручная utility-оценка. Иными словами, если легендарная нода важна для билда, но её `[x]`-множитель не попал в `stats`, нужно явно поднять вес доски в `scheme`. Иначе оптимизатор будет видеть в ней только базовый бонус типа `legendary 0.4` и может не взять даже сильную игровую ноду.

### Квалифицированные статы (Qualified Stats)

В ходе доработки парсера (`crawler/normalize.py`) была введена **строгая политика**:

- Ключ `damage` используется **только** для безусловного урона (`damage magic node +N%` без префиксов и аффиксов).
- Любой урон с аффиксом (элемент, форма, условие, школа навыка и т.д.) получает отдельный ключ (например `werebear_damage`, `earth_damage` и т.п.).
- При попытке присвоить `damage` ноде с аффиксом — **критическая ошибка** при нормализации.

Полная таблица **всех** статов (включая qualified damage и другие специальные ключи) вынесена в отдельный файл:

**→ [STATS_TABLE.md](STATS_TABLE.md)**

В ней содержится полный отсортированный по алфавиту список всех ключей из `available_stats` со взятыми напрямую из данных описаниями.

Пример блока `glyph_route` с параметрами эвристики:

```json
{
  "glyph_route": {
    "activation": 1.0,
    "scaling": 1.0,
    "node_bonus": 1.0,
    "future": 0.35,
    "synergy": 0.25,
    "scarcity": 0.30,
    "cluster": 0.35,
    "detour": 0.25,
    "path_efficiency": 0.50,
    "fill_target": 1.20,
    "max_bonus_multiplier": 1.60
  }
}
```

`activation` усиливает добор порога глифа, `scaling` - ценность статов сверх порога, `node_bonus` - маршрутный hint для normal/magic/rare нод, которые усиливаются возможным глифом, `future` и `synergy` - ноды, полезные нескольким возможным глифам, `scarcity` - дефицитные общие threshold-статы, `cluster` добавляет ценность плотных групп полезных нод, `detour` штрафует проход через слабые промежуточные ноды, `path_efficiency` управляет тем, насколько кластерная ценность влияет на выбор следующего шага, `fill_target` задаёт желаемое заполнение радиуса относительно требования, `max_bonus_multiplier` ограничивает максимальный маршрутный hint. Внутри эвристики строится матрица marginal-value для нод в радиусе глифа: она учитывает частичный прогресс к threshold, шаги scaling по 5 статов, небольшой кредит за неполное заполнение следующего scaling-шага и дополнительный weighted-score от усиливаемых нод. Если `node_bonus = 0`, этот маршрутный hint отключается; финальный скор назначенного глифа всё равно учитывает усиление выбранных нод.

Явные аргументы CLI переопределяют профиль:

```bash
paragon_optimizer/bin/paragon_optimize.exe optimize --profile paragon_optimizer/profiles/paladin_juggernaut_shield_bash.json --points 220
```

Практичный профиль запуска по умолчанию:

* `--max-routes 30000`
* `--candidate-targets 1000`
* `--workers 0`

С этими значениями по умолчанию `optimize_fast.bat` даёт заметно лучшее качество, чем старые 3000/320, и при этом укладывается примерно в 1–2 минуты на типичном билде. `candidate-targets` ниже 800–1000 начинает заметно терять качество на сложных профилях.

Для быстрых пробных или отладочных запусков лимиты можно уменьшить (вернуться к старым значениям):

```bash
paragon_optimizer/bin/paragon_optimize.exe optimize \
  --profile paragon_optimizer/profiles/paladin_juggernaut_shield_bash.json \
  --max-routes 3000 \
  --candidate-targets 320
```

Расчет маршрутов и скоринга запускается в несколько native-потоков: `--workers 0` использует половину логических ядер системы. `--max-routes 0` отключает лимит маршрутов, `--candidate-targets 0` отключает отсечение целевых узлов; это заметно тяжелее и обычно нужно только для проверки качества.

С новыми дефолтами (30 000 маршрутов / 1 000 кандидатов) `optimize_fast.bat` даёт существенно лучшее качество, чем старые 3000/320, при приемлемом времени (обычно 1–2 минуты).

По умолчанию JSON не содержит полные `route_steps`, чтобы вывод оставался компактнее. Для отладки маршрута:

```bash
paragon_optimizer/bin/paragon_optimize.exe optimize --profile paragon_optimizer/profiles/paladin_juggernaut_shield_bash.json --include-route-steps
```

Если маршрут не был переписан локальными заменами, в `route_steps` для каждого greedy-шага выводятся `gain_estimate`, `adjusted_gain_estimate`, `cluster_gain_estimate`, `detour_cost_estimate` и `path_efficiency`; это помогает увидеть, где эвристика делает крюк ради плотного кластера.

### Улучшения локальных замен (local improvement)

В фазе локальных замен (`improve_route_locally`) добавлена специальная логика для более рационального поиска высокобонусных редких нод (жёлтых), которые находятся в 1 шаге за дешёвой "синей" (magic) нодой:

- При формировании кандидатов на добавление (`adjacent_add_nodes`) и в быстром префильтре (`quick_delta`) ноды, ведущие к редкой с `bonus_stats`, получают дополнительный бонус (`LOCAL_RARE_FEEDER_BONUS_FACTOR = 0.85`).
- В каждом проходе локалки работает явный "охотник" на редкие: он находит unselected редкие с бонусом, у которых есть фидер на границе текущего selected set, и явно инжектит предложения "убрать одну из худших нод → добавить этот фидер".
- Увеличено количество проходов локалки (до 10) и размер пула add-кандидатов (до 64), чтобы было больше шансов "дотянуться" до таких спуров.

Это решает случаи, когда оптимизатор игнорировал ценную редкую ноду (например Sprightful с 2.6% AS при весе 3.55), потому что она была на distance-2 от основного пути, хотя стоила всего одной дешёвой magic-ноды. Полный скоринг (с правильной активацией gated бонусов) остаётся арбитром — мы только гарантируем, что такие варианты вообще рассматриваются.

CLI всегда пишет в stdout чистый JSON. HTML создаётся по умолчанию, а путь попадает в поле `html_file`; если нужен только JSON без HTML-файла, используется `--no-html`.

### Общие параметры native CLI

* `--data`: Путь к директории с нормализованными данными парагона. По умолчанию используется `data/normalized` рядом с директорией `bin`.

### Параметры команды `optimize`

* `--profile`: Путь к JSON профилю запуска из `profiles/*.json`. Профиль может задавать `class`, `points`, `weights`, `glyph_levels`, `starting_stats`, `max_routes`, `candidate_targets`, `workers`, `scheme`, `include_route_steps`, `no_html` и `data`.
* `--class`: Класс персонажа (например, `paladin`, `barbarian`, `sorcerer`). Обязателен, если не задан в профиле.
* `--points`: Доступное количество очков парагона для распределения. Обязательно, если не задано в профиле.
* `--weights`: Путь к JSON файлу с весами характеристик, узлов и глифов, определяющими приоритеты для оптимизации (например, `paragon_optimizer/weights/paladin_balanced.json`). Обязателен, если не задан в профиле. Файл весов может содержать опциональные поля `scheme` для перебора досок и `glyph_route` для настройки агрессивности глиф-ориентированной маршрутной эвристики.
* `--scheme`: Опциональный список досок, которые алгоритм должен использовать, исключая другие доски. Например: `--scheme castle shield_bearer fervent divinity`. Если передано, алгоритм перебирает только стартовую доску и указанные в `scheme`.
* `--legendary-glyphs`: Устаревший параметр переходного периода. Принимается для совместимости, но игнорируется; используйте `glyph_levels` в профиле.
* `--max-routes`: Лимит на максимальное количество полных маршрутов, которые будут детально оценены после предварительного отбора. По умолчанию `30000`; `0` отключает лимит.
* `--candidate-targets`: Количество лучших потенциальных целевых узлов на доске для построения путей, отбираемых эвристикой. По умолчанию `1000`; `0` отключает отсечение.
* `--workers`: Количество потоков для расчета маршрутов. `0` использует половину логических ядер системы и используется по умолчанию; `1` включает однопоточный режим.
* `--include-route-steps`: Включить детальный пошаговый список выбранных узлов (`route_steps`) в итоговый JSON. Увеличивает размер вывода, но полезно для отладки.
* `--no-html`: Не создавать HTML-визуализацию результата.
