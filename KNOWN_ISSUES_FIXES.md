# Known Issues & Fixes

- Важно: исправлять надо баги в отдельных ветках(ветвится от мастера). После исправления, в разделе конкретной баги укажи название ветки, куда залил фикс.
Иначе если править в мастере первую багу, можно поломать воспроизведение других.

## Баг #1: Жадный алгоритм построения маршрута (run_greedy_route + consider_targets) не "видит" ценность инвестиций в 1–N низкозначимых коннекторных нод для достижения высокой ценности rare/legendary ноды с gated bonus (или высокой intrinsic ценностью)

### Краткое описание

Команда:
```
bin\paragon_optimize.exe optimize --profile "profiles\spiritborn.json" ...
```
(дефолтные параметры, или с `--no-html` для отладки через JSON payload без генерации HTML).

На доске `prodigy_s_tempo` в итоговом лучшем маршруте алгоритм выбирает, например, `prodigy_s_tempo_5_6` и `prodigy_s_tempo_7_6` (обычные нормальные ноды, +5 dexterity каждая), вместо того чтобы взять:
- `prodigy_s_tempo_3_6` (magic, +2 max_resource, без требований)
- `prodigy_s_tempo_3_7` (rare "Eager Prey" / "Легкая добыча", requirements: strength 210, stats: +10 dexterity +2 max_resource, bonus_stats: +2 max_resource)

В профиле spiritborn.json starting_stats содержит strength: 368, поэтому требование редкой ноды выполняется сразу при её взятии (через effective_totals в compute_effective_stats).

Пользователь специально поставил вес ресурса очень низким (`max_resource: 0.01`, есть также `max_resource_backup: 2.1`). Даже в этом случае "очевидный" вклад редкой ноды (главным образом 10 dexterity по весу 1.45) + позитив от ресурса делает комбинацию "синяя + жёлтая" (magic + rare) лучше или равнозначной по dex за те же ~2 очка + даёт дополнительный ресурс.

Финальный scorer (`route_score_value` + `compute_effective_stats`) считает всё правильно (гating бонусов, glyph interactions и т.д.). Проблема исключительно в том, какие маршруты вообще генерирует поиск.

### Root Cause Analysis (код в native/optimizer_cli.cpp)

Основной поиск маршрутов — это **жадный инкрементальный алгоритм** (не A*, не полноценный поиск путей с lookahead):

1. **Построение эвристических оценок нод (только для поиска, не для финального скора)**:
   - `route_node_score` (строки ~977–999):
     ```cpp
     double score = node_base_score(node, weights);  // type priority + weighted stats
     if (!node.bonus_stats.empty()) {
         score += weighted_stats_score(node.bonus_stats, weights) * RARE_BONUS_ROUTE_HINT_FACTOR; // 0.6
     }
     ...
     ```
   - `RARE_BONUS_ROUTE_HINT_FACTOR = 0.6` (строка 56). Хинт применяется **только к самой rare/legendary ноде**.
   - `node_base_score` для коннекторов (normal/magic с низким весом статы, например ресурс) даёт очень маленькое значение (в основном type priority: normal=0.1, magic=0.2 + крошечный weighted ресурс).
   - В `build_route_input` (~2302) эти оценки попадают в `input.scores`, по которым потом считается gain пути.

2. **Выбор следующего шага в жадном построении** (`run_greedy_route` + `consider_targets`, ~2211–2276, ~2145–2199):
   - На каждом шаге от текущего selected set строится shortest_path_tree.
   - Для каждого оставшегося target'а считается путь: суммируется `gain += input.scores[node]` по всем **новым** нодам на пути (включая target).
   - Плюс cluster, минус `detour_cost` от low-value нод.
   - `candidate.ratio = adjusted_gain / cost`.
   - Выбирается лучший по `better_candidate` (в первую очередь ratio, потом adjusted_gain).
   - **Критично**: если `adjusted_gain <= 0.0` и target не glyph_socket/legendary — кандидат полностью отбрасывается (~2181).

3. **Low-value detour penalty** (в consider_targets, ~2166–2172):
   ```cpp
   if (node != target && ... && score < input.low_value_threshold) {
       ... detour_pressure += weak_fraction * cost;
   }
   detour_cost = detour_pressure * input.detour_penalty;  // detour_penalty = score_reference * glyph_route.detour (0.25 по умолчанию)
   ```
   `low_value_threshold = route_score_reference(...) * 0.35` (~2307).
   Ноды с низкой эвристикой (ресурсные magic'и при весе 0.01, многие normal'ы) сильно штрафуются, когда они — "просто по пути" к ценному target'у. Это делает длинные/средние пути к side-редким с посредственными коннекторами непривлекательными по ratio/adjusted_gain.

4. **Отбор кандидатов и seeding** (`candidate_targets` ~2012, ~4863):
   - Rares/legendary/glyph_socket всегда попадают в "priority" bucket и имеют преимущество.
   - Seeds включают все targets + "-1".
   - Для seed'а редкой в `ordered_targets` она ставится первой в списке, но `consider_targets` всё равно перебирает все и выбирает по метрике ratio. Если путь к редкой имеет худший ratio, чем путь к ближайшему +5 dex normal'у — редкая не будет выбрана на этом шаге (и, возможно, никогда).

5. **Local improvement не всегда спасает** (`improve_route_locally` ~3671, `adjacent_add_nodes`, `removable_route_nodes`, `rare_feeder_unlock_bonus` ~1010):
   - Рост только через **adjacent** к текущему selected (добавляются только прямые соседи).
   - `rare_feeder_unlock_bonus` (0.85) бустит только прямых соседей unselected rare'ов — и только на базе их **gated bonus_stats**, не intrinsic stats самой rare.
   - Есть специальная "hunger" логика (~3732+) для инъекции свопов "one cheap feeder away", но она ограничена.
   - Перед полным `route_score_value` есть быстрый prefilter (~3724):
     ```cpp
     double add_sc = ... + rare_feeder_unlock_bonus(...);
     double quick_delta = add_sc - node_base_score(remove...);
     if (quick_delta <= 1e-9) continue;
     ```
   - Если чтобы "подобраться" к редкой нужно добавить 1–2 mediocre коннектора (их base score низкий), quick_delta против удаления хорошего dex normal'а будет отрицательным — предложение даже не дойдёт до полного скора.
   - Чтобы сделать саму редкую adjacent, сначала нужно "потратить" своп(ы) на feeder'ы. При низком весе gated бонуса (ресурс) unlock сигнал крошечный. Даже если intrinsic ценность редкой высокая (+10 dex), feeder не получает достаточно буста на этапе предложения.
   - removable_route_nodes требует, чтобы удаление не ломало связность selected subgraph.
   - Бюджеты ограничены (LOCAL_IMPROVEMENT_REMOVE_CANDIDATES=36, ADD=64).

**Почему даже seeding редкой первым таргетом не помогает**:
- На первом шаге от gate'ов считается ratio полного пути (стоимость в очках может быть 3–5+, gain = сумма низких scores коннекторов + hinted score редкой).
- Ratio такого пути часто сильно хуже, чем ratio короткого пути к ближайшему высокому normal'у (+5 dex ~7.25 по весу 1.45 / 1 cost).
- После нескольких высокорацио шагов "по главной линии" фронтир уходит дальше, и side-trip становится ещё хуже.

**Итог**: поиск (и локальный ремонт) систематически недо-исследует области графа, где ценность сконцентрирована в редких/легендарных, а "проездной билет" — это одна или несколько нод с низкой собственной эвристической оценкой. Финальный scorer никогда не видит такие комбинации, потому что greedy их не генерирует.

Это проявляется особенно ярко, когда:
- Веса на gated stats низкие (пользовательский кейс).
- Коннекторы — "дешёвые" normal/magic с mediocre статами.
- Редкая лежит в стороне от естественных высокоплотных путей (высокий dex/int/will в данном профиле).

### Обобщение проблемы (почему не "гавнокодить под один кейс")

- Проблема может проявляться через **цепочку 2+ коннекторных нод**.
- Редкая нода может иметь **очень высокий вес именно в bonus_stats** ("жёлтая с большим весом") — gated ценность огромная, но если коннекторы низкие по базовой эвристике, и/или путь от текущего selected длинный, та же комбинация факторов (слабый pull на access nodes + detour penalty + adjacent-only local + quick prefilter) будет препятствовать.
- Нужно решение, работающее для **любых prize-нод** (rare/legendary с высокой суммарной ценностью — stats + gated bonus) и произвольных access path'ов в графе доски.
- Нельзя просто отключать low_value_threshold/detour глобально — это важный анти-филлер механизм.
- Нельзя в основном greedy делать дорогой lookahead или полную переоценку (алгоритм вызывается для десятков тысяч маршрутов/вариантов раскладок досок).

### Предложения по исправлению (рациональные, с сохранением архитектуры)

**1. Unlock / Prize Potential Field в эвристике основного поиска (основной рекомендуемый фикс)**

   Перед/внутри `build_route_input` (или сразу после `candidate_targets`) для всех rare/legendary нод с ненулевым бонусом (или высоким node_base_score / приоритетной ценностью) вычисляем "prize_value".

   Делаем лёгкий reverse multi-source propagation (BFS с decay по графовой дистанции или по point-cost, радиус ограничен, например 4–6):
   - contribution[node] += prize_value * (RARE_ACCESS_PULL_FACTOR) * (DECAY ** distance)
   - RARE_ACCESS_PULL_FACTOR можно начать с 0.35–0.5 (меньше, чем 0.6 на саму ноду).
   - Decay ~0.75–0.85.

   Полученный potential добавляем:
   - Либо напрямую в `input.scores` (влияет на gain, ratio, low_value_threshold check).
   - Либо отдельным полем `input.unlock_potentials`, и в `consider_targets` при суммировании gain для путей к prize-таргетам (или вообще) добавляем потенциал коннекторов.

   Эффект:
   - Коннекторы (в т.ч. 3_6) на пути к ценной редкой получают небольшой, но заметный буст в эвристике.
   - Пути к редкой получают лучший summed gain / ratio, меньше detour_pressure (если буст применяется и к low_value проверке).
   - Работает для цепочек любой разумной длины.
   - Для high-weight yellow — prize_value большой → сильный pull.
   - Остаётся чистой эвристикой: финальный `route_score_value` / `compute_effective_stats` ничего не меняется и всегда честно проверяет gating + все взаимодействия.

**2. Context-aware отношение к detour / low-value для prize targets**

   В `consider_targets`:
   - Если target имеет высокий unlock_potential (или это rare/legendary с положительным бонусом/высокой ценностью), то либо:
     - не применять (или применять с пониженным коэффициентом) low_value detour_pressure к нодам на пути, которые имеют положительный potential к этому target'у;
     - либо считать "intrinsic gain" пути отдельно, а unlock/adjusted часть добавлять сверх без полного налога.
   - Можно ослабить фильтр `adjusted_gain <= 0` конкретно для rare/legendary (или заменить на более мягкий порог).

   Это не отменяет защиту от филлеров в общем случае, но позволяет "инвестиционные" пути к призам проходить в рассмотрение.

**3. Усиление local improvement (вспомогательно)**

   - В `adjacent_add_nodes` / приоритетах и в quick prefilter'ах (`quick_delta`) при расчёте add_sc для candidate'а, соседнего с unselected rare, учитывать **не только gated bonus** редкой, но и её intrinsic `node_base_score` (или полную hinted ценность). Редкая может быть ценной сама по себе даже при нулевом бонусе.
   - Распространить rare_feeder_unlock_bonus / potential и на distance-2 boundary (ограниченно), или добавить dedicated "rare rescue" фазу после обычных passes: специально искать unselected призы и предлагать свопы, которые приближают к ним (жертвуя самыми низкоценными removable).
   - При предложении свопа, который делает редкую adjacent (или добавляет саму редкую), игнорировать или сильно снижать порог quick_delta.
   - Опционально: увеличить бюджеты кандидатов, когда на доске есть unselected призы с высоким remaining potential.

**4. Диагностика и тесты (обязательно при фиксе)**

   - В JSON payload (особенно при `--include-route-steps` или отдельном debug-режиме) добавлять:
     - Для каждого рассмотренного target'а в жадном построении — breakdown (gain, detour_pressure, adjusted, ratio, был ли отброшен по <=0).
     - Список "prize nodes" и применённый к ним/их access unlock potential (для отладки).
   - Добавить синтетические тесты (в стиле существующих в `tests/test_native_glyph_scoring.py` или новые) на графе, моделирующем ситуацию: два лёгких +5 dex normal'а vs редкая за 1–2 low-value коннекторами (с tunable весами на gated и intrinsic). Проверять, что в сгенерированном маршруте (или после local improve) редкая + коннектор предпочтительнее.
   - Регресс-тесты на spiritborn профиле (проверять присутствие 3_6 + 3_7 в selected_nodes на prodigy_s_tempo в JSON-выводе).

**5. Чего избегать**

   - Хардкод специфичных правил под "resource", "vigor", конкретную доску или конкретный стат.
   - Глобальное отключение low_value_threshold / detour_penalty.
   - Тяжёлые вычисления (полный effective stats, glyph evaluation) внутри основного greedy цикла consider_targets.
   - Увеличение числа маршрутов как "решение" — это маскирует проблему, а не исправляет эвристику.

### Как воспроизводить / отлаживать

1. `bin\paragon_optimize.exe optimize --profile "profiles\spiritborn.json" --no-html --include-route-steps` (или без include, чтобы посмотреть финальный payload).
2. В результатах найти запись по доске `prodigy_s_tempo` (в `boards` / `selected_nodes` / `route`).
3. Посмотреть, присутствуют ли `prodigy_s_tempo_3_6` и `prodigy_s_tempo_3_7` среди selected.
4. Для глубокого анализа — временно добавить логирование/JSON полей вокруг `consider_targets` (какие targets рассматривались, их adjusted_gain и т.д.).

### Статус

Ветка фикса: `fix/bug-1-prize-potential-field`.

Open. Требует реализации, тестов и проверки на нескольких профилях/досках (включая кейсы с высоковесовыми bonus_stats на редких и ситуации с длинными access chain'ами).

---

Дополнительные замечания:
- Существующий код уже содержит комментарии и специальную логику "hunger for yellow nodes" именно для этой проблемы (см. редкие комментарии около rare_feeder_unlock_bonus, adjacent_add_nodes, improve_route_locally). Баг показывает, что текущих мер (0.6/0.85 + adjacent + limited candidates) недостаточно для всех реалистичных случаев.
- Архитектурно правильно, что эвристика поиска может быть "оптимистичной" (в т.ч. с hint'ами), а истина всегда в `route_score_value`. Фикс должен оставаться в этом парадигме.

---

## Баг #2: Эвристика построения маршрута (node_base_score, route_node_score, glyph_route_node_bonuses) систематически предпочитает normal-ноды ("серые" +primary stat) magic-нодам ("синим"), даже когда последние попадают под glyph node_bonus (усиление всех магических нод от глифов), из-за чего теряется мультипликативный бонус от глифа и иногда тратятся лишние очки на неоптимальные пути/коннекторы

### Краткое описание

Команда:
```
bin\paragon_optimize.exe optimize --profile "profiles\spiritborn.json" ...
```
(дефолтные параметры, или с `--no-html --include-route-steps`).

На доске `revealing` (и часто на других досках при тех же дефолтах) в итоговом маршруте алгоритм выбирает, например:
- `revealing_6_16` (normal, +5 strength — "серая сила")
- `revealing_7_16` (normal +5 dex)
- `revealing_7_15` (normal +5 dex)

вместо более выгодных вариантов, где за сопоставимые или меньшие вложения можно взять другие normal'ы (например `revealing_7_13` + `revealing_8_13`) **и** при этом включить одну или несколько magic (синих) нод.

Пользователь специально указывает: "зачем он взял ... revealing_6_16 для revealing_7_16 и revealing_7_15, хотя можно без проблем было взять revealing_7_13 и revealing_8_13 ? не тратясь на лишние ноды? а взять например синюю которая может не приоритетный стат, но перемножена усилением глифы (синие же улучшает)".

Особенно критично, когда в раскладке присутствует глиф с node_bonus на magic (в spiritborn это menagerist ~+25%→272%+ к magic в радиусе на lvl 51 по сэмплам, hubris ~+30%→..., outmatch и др.). В профиле weights glyph_route.node_bonus = 1.0, а сами такие глифы имеют высокие веса (menagerist: 160). Если глиф "стоит на усиление магических нод", а в selected мало или не те magic — огромный мультипликативный бонус (stats magic * (1 + multiplier)) по сути теряется.

Часто наблюдается паттерн:
- Жадный проход в основном "серыми" normal'ами (высокий надёжный gain от +5 primary stat, особенно dex 1.45).
- Потом через локальные замены (improve_route_locally) "прилетает" ещё бюджет/слоты — и он снова заполняется нормалами.
- Либо маршрут прокладывается через низкоценные коннекторы (типа +str normal при весе strength 0.2), лишь бы соединить.

Финальный scorer (`route_score_value` + `compute_effective_stats` + `build_node_bonus_multipliers`) считает всё честно: применяет реальные `node_bonus_multipliers` только к тем selected magic, что покрыты назначенными assigned_glyphs. Проблема исключительно в генерации selected set'а.

### Root Cause Analysis (код в native/optimizer_cli.cpp)

1. **Базовая эвристика нод (node_base_score / priority_for_type / weighted_stats_score, ~908–966)**:
   - normal: priority 0.1 + weighted (обычно 5 * 1.45 dex = 7.25) → ~7.35
   - magic: priority 0.2 + weighted_stats_score(её stats) — у большинства board magic это маленькие величины (% урона, жизни, resistance и т.п.), часто 0.1–2.0 в сумме по весам пользователя.
   - Даже если у magic хороший стат по весам, его base score почти всегда сильно ниже нормала.

2. **Учёт glyph node_bonus в поиске маршрута (частичный, ~1836–1926)**:
   - В `glyph_route_node_bonuses` (вызывается в build_route_input) через `build_glyph_value_matrices` для нод в радиусе glyph_sockets, у которых glyph.node_bonus.node_type == "magic" и info.node_bonus_multiplier != 0, считается:
     ```cpp
     double raw_score = weighted_stats_score(node.stats, weights);
     double direct = raw_score * info.node_bonus_multiplier * weights.glyph_route.node_bonus;
     ```
     (для rare дополнительно бонус на bonus_stats).
   - Этот вклад попадает в accumulators → candidate_bonus (с future/synergy) → бонус в `route_bonuses`, который добавляется в `input.scores` и используется в `route_node_score`.
   - Ограничения:
     - raw_score у magic маленький → даже при multiplier ~1.5–2+ (уровень 51) и node_bonus=1.0 абсолютный hint часто << 7 от normal'а.
     - Применяется только proximity к socket'ам (до выбора реального assigned glyph и до финального selected).
     - Дальше capping: `route_hint_limit = max(node_base_score(node), ...) * max_bonus_multiplier (1.6)`.
     - Hint не "propagate'ится" как potential field на коннекторы к ценной magic.

3. **Жадный инкрементальный выбор (run_greedy_route + consider_targets, ~2145–2276)**:
   - На каждом шаге от текущего selected считается shortest_path_tree, gain = сумма `input.scores[new nodes]` (включая target).
   - Применяется detour_pressure для нод со score < low_value_threshold (reference * 0.35).
   - Нормалы дают высокий, стабильный, немедленный gain/cost → выигрывают по ratio и adjusted_gain.
   - Magic с низким base (даже + hint) часто имеет низкий score → легко штрафуется как low-value, или путь к ней/через неё имеет худший ratio, чем к ближайшему +5 dex.
   - Если "хорошая" magic лежит чуть в стороне или требует 1–2 mediocre коннектора — она проигрывает.

4. **Local improvement не тянет magic'и (improve_route_locally ~3671, adjacent_add_nodes, quick prefilter ~3720)**:
   - Добавления только adjacent к текущему selected.
   - quick_delta = (route_node_score(add) + rare_feeder...) - node_base_score(remove)
   - Пример: удалить нормал (+7+) и добавить magic (base ~1.0 + hint ~1.0 = 2.0) → quick_delta << 0, предложение отбрасывается до полного скора.
   - Нет dedicated "magic under node-bonus glyph" hunger-логики (в отличие от редких с rare_feeder_unlock_bonus 0.85).
   - removable требует сохранения связности.
   - Бюджеты кандидатов ограничены.

5. **Разделение фаз route → glyphs → scoring**:
   - Основной поиск и local passes работают с route_node_score (static + glyph_route hints).
   - Реальные `node_bonus_multipliers` и `base_node_bonus_totals` / `bonus_node_bonus_totals` применяются только позже в `compute_effective_stats` (~2368–2378) после того, как `optimize_glyphs_for_route` назначил конкретные глифы на socket'ы.
   - Если route builder не включил достаточно magic в "правильных" местах — даже идеальное размещение menagerist/hubris даст маленький node_bonus_score.
   - Веса на glyph_bonus / glyph_socket высокие, но они влияют на ценность самих socket'ов, а не достаточно сильно "тянут" за собой magic-ноды в радиусе.

6. **Почему "лишние ноды" и странные коннекторы (типа revealing_6_16)**:
   - После построения плотного backbone'а из высокорацио нормалов остаётся бюджет (points_limit + scheme board weights).
   - Local passes заполняют adjacent low-cost normals.
   - Низкоценный коннектор (revealing_6_16 — strength normal, base ~ 0.1 + 5*0.2 = 1.1) может быть выбран на каком-то шаге как cheapest способ соединить два ценных нормала, либо попасть в selected через adjacent fill.
   - Альтернативный путь (7_13–8_13), который открывает доступ к magic в хорошем месте под будущий глиф, на этапе greedy имеет сопоставимый или худший immediate gain — поэтому не выбирается.

**Итог**: поиск (и локальный ремонт) недооценивает **будущую amplified ценность** magic нод. Финальный scorer никогда не видит маршруты, где 1–2 хорошие magic заменяют 2–3 normal'а с учётом (1 + node_bonus_multiplier), потому что такие маршруты редко генерируются.

Это проявляется особенно ярко, когда:
- В профиле есть глифы с node_bonus на "magic" + glyph_route.node_bonus > 0.
- У пользователя высокие веса на статах, которые хорошо "масштабируются" (damage, vulnerable и т.д.), и magic ноды дают именно такие %.
- На доске есть socket'ы + скопления magic нод в разумной досягаемости.
- Бюджет позволяет варианты с разной плотностью.

### Обобщение проблемы (почему не "гавнокодить под один кейс")

- Похожа на Баг #1: оба случая — когда высокая ценность сконцентрирована в "призе" (gated bonus rare или amplified magic), а "билет" — это ноды с низкой собственной эвристикой.
- Здесь "приз" — не единичная нода, а целый класс нод (все magic под конкретным node-bonus глифом), и реализация приза происходит в отдельной фазе glyph assignment.
- Нужно решение, которое работает для любых профилей с/без node-bonus глифов и не ломает защиту от филлеров (low_value_threshold, detour).
- Нельзя просто поднимать приоритет magic глобально — на билдах без таких глифов или со слабыми magic это навредит.
- Нельзя прогонять полный glyph optimization + effective stats на каждом шаге greedy (вызывается десятки тысяч раз).

### Предложения по исправлению (рациональные, с сохранением архитектуры)

**1. Усиленный Potential / Uplift для node-bonus magic (основной рекомендуемый, в духе предложения #1 для rares)**

   В `glyph_route_node_bonuses` (или параллельно в build_route_input / candidate_targets):
   - Для всех magic нод, для которых существует подходящий glyph с node_bonus в текущем наборе глифов, вычислять более заметный "amp_potential".
   - Делать ограниченный propagation (BFS decay) от glyph_sockets или от уже "хороших" magic — коннекторы на пути к зоне с высоким потенциалом amp получают небольшой буст.
   - Добавлять uplift не только в scores самих magic, но и учитывать в low_value_threshold check / detour_pressure для таких "investment" путей.
   - Можно ввести отдельный коэффициент `weights.glyph_route.magic_amp` (или усилить существующий node_bonus), и давать magic нодам базовый бонус в node_base_score / route_node_score, когда такие глифы присутствуют в профиле.

**2. Context-aware отношение к magic в local improvement и quick filter**

   - В `adjacent_add_nodes`, при формировании приоритетов и в quick_delta (~3722–3728):
     - Для candidate'ов типа "magic" и наличия node-bonus глифов считать add_sc с дополнительным "expected_amp" (использовать тот же механизм, что в glyph evaluation node_bonus_score).
     - Сильно снижать или игнорировать порог quick_delta при свопе "normal → magic under potential coverage".
   - После обычных passes local improvement добавить dedicated "magic rescue / fill" фазу: специально искать unselected magic в радиусах от уже выбранных socket'ов (или потенциальных) и предлагать их вместо самых низкоценных removable normals (с полным скорингом после чернового glyph assign только node-bonus глифов).
   - Распространить rare_feeder-подобную логику на magic: "magic_feeder_bonus" для adjacent к unselected magic в зонах с node-bonus потенциалом.

**3. Улучшение кандидатов и seeding под amplified nodes**

   - В `candidate_targets` (~2012+) и ordering отдавать приоритет не только rare/legendary/glyph_socket, но и magic нодам с высоким node-bonus hint (или кластерам с высокой плотностью magic + socket).
   - В `glyph_cluster_route_bonuses` / cluster логике учитывать наличие magic под node-bonus как фактор "valuable density".

**4. Диагностика и тесты (обязательно при фиксе)**

   - В JSON payload (особенно `--include-route-steps`) добавлять:
     - Breakdown по route_bonuses: какие ноды получили node_bonus uplift, от каких глифов, с каким значением.
     - В выводе по доске: кол-во selected magic, суммарный node_bonus_score (как в glyph evaluations), coverage (сколько из них реально получили multiplier > 0 от assigned glyphs).
     - При рассмотрении targets — был ли magic кандидат, его adjusted_gain с/без amp hint.
   - Синтетические тесты: на графе доски (или подмножестве revealing) смоделировать ситуацию "2–3 normal dex vs 1–2 magic + socket в радиусе", с tunable node_bonus_multiplier. Проверить, что при наличии node-bonus глифа в профиле алгоритм предпочитает вариант с magic (или хотя бы не хуже по финальному score).
   - Регресс на spiritborn: для revealing (и других досок со scheme weight) проверять присутствие разумного количества magic в зонах покрытия menagerist/hubris-like в JSON selected_nodes, и что node_bonus_score не нулевой/минимальный.

**5. Чего избегать**

   - Хардкод под конкретные глифы (menagerist), доски (revealing) или статы.
   - Глобальное завышение приоритета всех magic (сломает кейсы без node-bonus глифов).
   - Отключение low_value / detour глобально.
   - Тяжёлый полный glyph optimization внутри основного цикла построения маршрутов (можно позволить лёгкую черновую оценку только node-bonus части в limited local passes).
   - Простое увеличение MAX_ROUTES / CANDIDATE_TARGETS как "решение" — маскирует, а не исправляет эвристику.

### Как воспроизводить / отлаживать

1. `bin\paragon_optimize.exe optimize --profile "profiles\spiritborn.json" --no-html --include-route-steps`
2. В результатах найти запись по доске `revealing` (в `boards`).
3. Посмотреть `selected_nodes`, `route`, наличие revealing_6_16 / 7_16 / 7_15 и отсутствие "хороших" magic в окрестностях (например вокруг y=13–16, x=6–9 и т.п.).
4. Посмотреть в glyph evaluations для revealing — какие node-bonus глифы (menagerist и др.) были назначены, на какие socket'ы, и какие affected_nodes они реально усилили.
5. Для глубокого анализа — временно добавить вывод route_bonuses по magic нодам или логирование в consider_targets / adjacent_add_nodes.
6. Сравнить score при принудительном добавлении/замене magic (для отладки можно использовать модифицированный запуск или пост-обработку JSON).

### Статус

Ветка фикса: `fix/bug-2-magic-node-bonus-uplift`.

Fixed in branch. Реализован route-hint `magic_amp` для magic-нод под glyph `node_bonus` и короткий access-potential на ближайшие коннекторы. Требует проверки на spiritborn (и других классах, где есть node-bonus glyphs). Связан с Багом #1 — общая корневая причина в том, что эвристика поиска и локального ремонта недостаточно "видит" будущую/условную ценность за пределами немедленного node_base_score.

---

Дополнительные замечания (к Багу #2):
- В текущем коде уже есть специальная ветка обработки node_bonus именно для route hints (см. комментарии и if в ~1836), и glyph_route tuning имеет node_bonus / future / synergy. Баг показывает, что текущей величины uplift'а и механизма (raw * mult * 1.0, proximity-only, без propagation на коннекторы, слабое влияние на local quick_delta) недостаточно, когда base weighted у magic на 1–2 порядка ниже, чем у нормала.
- Как и в #1: правильно, что истина всегда в `route_score_value` / `compute_effective_stats`. Фикс должен оставаться в парадигме "улучшаем hints и local repair", а не менять финальный scorer.
- Полезно будет посмотреть реальные выводы из out/*.html или JSON для spiritborn, чтобы уточнить точные magic ноды, которые "могли бы быть" на revealing.

---

## Баг #3: Избыточное инвестирование в threshold-статы глифов (glyph threshold stat feeders) — перевыполнение требования +40 Dexterity для "Дрессировщик" (menagerist) в сокете revealing_5_15 на 5 ловкости (1 лишняя normal-нода), при тех же дефолтных прогонах spiritborn.json

### Краткое описание

Команда:
```
bin\paragon_optimize.exe optimize --profile "profiles\spiritborn.json" ...
```
(дефолтные параметры, или с `--no-html --include-route-steps`).

На доске `revealing` алгоритм назначает в сокет `revealing_5_15` глиф `menagerist` / "Дрессировщик" (редкий глиф с node_bonus +25%→272%+ на magic ноды в радиусе, activation req +40 Dexterity от купленных нод в радиусе для дополнительного бонуса, legendary bonus на Incarnate).

Для "анлока" (получения активации) требуется 40 ловкости от нод в радиусе сокета.

В итоговом selected наборе для этого сокета система "взяла":
- `revealing_2_15` (rare "Artifice"/"Изобретательность", даёт +10 dexterity + vulnerable_damage, req int 210)
- `revealing_5_12`, `revealing_7_12`, `revealing_3_14`, `revealing_5_16`, `revealing_7_16`, `revealing_7_15`, `revealing_3_17` — 7 normal-нод по +5 dexterity каждая.

Итого: 10 + 7×5 = 45 ловкости.

Требование 40 перевыполнено на 5. Одна нормальная нода (+5 dex, ~1 point) могла быть не взята (или заменена на более ценную ноду/путь), с сохранением requirement_met = true и полного бонуса глифа. Похоже на Баг #1 по симптомам ("жадный" набор под давлением даёт субоптимальный набор "фидеров").

Финальный scorer (`assign_glyphs` + `evaluate_glyph` + `route_score_value`) честно считает stat_in_radius и даёт полный activation + glyph weights при >=40. Проблема в генерации selected set'а и в локальных улучшениях, которые не отсекают избыток.

### Root Cause Analysis (код в native/optimizer_cli.cpp)

1. **Glyph threshold pressure в эвристике маршрута (build_glyph_value_matrices + glyph_route_stat_pressures ~1735, ~1630)**:
   - Для каждого glyph_socket + route-eligible glyph с threshold (для menagerist: threshold_stat="dexterity", requirement=40 из парсинга bonus_text).
   - Собирается supply = сумма stat_value всех radius_nodes (кроме самого socket'а) в радиусе глифа (radius=3 для starting menagerist).
   - `expected_fill = min(available_stat, max(req, req * weights.glyph_route.fill_target))` (fill_target=1.2 по умолчанию → цель 48).
   - Threshold nodes сортируются (сначала по manhattan distance к socket'у, потом base_score), затем для каждого считается contribution:
     - selected_stat = min(candidate.stat_value, remaining_fill)
     - activation_value (прогресс к requirement, с pool от glyph_bonus + glyph weight)
     - future / scaling (для menagerist scaling_value_per_5=null, так что в основном activation)
     - direct = (activation*... + ...) * GLYPH_ROUTE_BONUS_FACTOR * scarcity_multiplier
   - Эти бонусы аккумулируются в route_bonuses → используются в `route_node_score` и `consider_targets` (gain путей). +5 dex normal получает ~7.25 от base + заметный uplift от threshold contribution (особенно на ранних шагах, когда accumulated низкий). Это делает их конкурентоспособными или предпочтительными по ratio.

2. **Glyph relocation passes активно "докармливают" и защищают feeders (improve_route_glyph_relocations ~3596, glyph_relocation_add_priorities ~3409, glyph_relocation_remove_nodes ~3551)**:
   - После каждого assign_glyphs считается `selected_glyph_stat_pressure` (сумма threshold stat от уже selected нод в радиусе назначенных глифов).
   - В `glyph_relocation_remove_nodes`: кандидаты (только normal/magic) сортируются по `node_base_score + glyph_stat_pressure * 3.0`. Ноды, дающие dex под активным menagerist, получают сильный буст в score → удаляются в последнюю очередь (сортировка ascending, низкий score = первый на удаление).
   - В `glyph_relocation_add_priorities` (для assigned с unmet или под desired):
     ```cpp
     double desired_fill = std::max(info.requirement, info.requirement * weights.glyph_route.fill_target);
     ...
     if (before < desired_fill) {
         score += std::min(...) * dex_weight * 0.10;
     } else {
         score += stat_value * dex_weight * 0.03;   // residual даже после перевыполнения
     }
     score += node_base_score(...) * 0.05 + rare_feeder...;
     ```
     0.03 * 1.45 * 5 ≈ 0.22 + base*0.05 ≈ 0.37 — маленький, но положительный, и в комбинации с другими факторами (connectivity, свопы низкоценных) позволяет/поощряет добавление лишнего +5.
   - Проходит несколько GLYPH_RELOCATION_MAX_PASSES; assign_glyphs фиксирует menagerist на 5_15 → pressure применяются → лишние dex-ноды прилипают.

3. **Дискретность нод + buffer fill_target + отсутствие excess awareness**:
   - Шаги статов — 5 (normal) / 10 (редкие как 2_15). Легко перепрыгнуть 40 на +5.
   - fill_target=1.2 создаёт deliberate buffer, который в эвристике "тянет" дополнительные ноды.
   - В accumulation contribution и в relocation нет жёсткого "хватит, дальше 0 или near-zero для excess".
   - В финальной evaluate_glyph stat_in_radius считается честно (с учётом возможного node_bonus amplification, но для normal dex он обычно = raw), requirement_met = (stat >= req), бонус даётся полностью. Opportunity cost от лишней ноды не виден.

4. **Связь с greedy core и багами #1/#2**:
   - Как в #1: ценность "приза" (полный glyph activation + 160 вес menagerist + glyph_bonus 115 + scheme 600 на revealing) сконцентрирована, а "билет" — серия normal +5 dex с приличным base score. Greedy охотно их берёт.
   - Как в #2: pressure uplift применяется к threshold nodes (часто normal), делая их "липкими".
   - Local improvement ограничен adjacent, quick filters, бюджетами кандидатов — маргинальный dex-feeder, дающий pressure, редко проигрывает своп на "что-то более полезное" (особенно если то "полезное" не adjacent или имеет ниже immediate score).

**Итог**: система целенаправленно собирает threshold feeders для высоковесовых глифов, но из-за buffer'а, residual incentives, сильной защиты от удаления и отсутствия "post-requirement prune" логики в selected наборе оказывается 1 (или больше) лишних +primary normal'ов. На профилях с высоким весом dex и большим количеством +5 dex normal'ов в радиусах популярных сокетов (revealing и др.) это проявляется регулярно.

### Обобщение проблемы (почему не "гавнокодить под один кейс")

- Проявляется для **любых** threshold-глифов (не только menagerist/dex), особенно тех, у кого высокий glyph weight, нет/слабый scaling_value_per_5 (активация — разовый триггер на req), и в радиусе много дешёвых primary-stat normal'ов.
- fill_target, scarcity, future/synergy — полезные механизмы для "достаточно накормить" глиф; их нельзя просто отключать.
- Проблема усиливается при высоком glyph_bonus / scheme weights и когда primary stat (dex) имеет хороший вес (1.45) — +5 normal сам по себе выглядит привлекательно.
- Discrete nature графа + несколько фаз (greedy route → local → glyph assign → glyph relocation) позволяют избытку "просочиться" и закрепиться.
- Нужно решение, работающее для activation-only и activation+scaling глифов, сохраняющее желание "дотянуть до req", но не поощряющее/защищающее сильный excess.

### Предложения по исправлению (рациональные, с сохранением архитектуры)

**1. Строгий capping + zero/low excess incentive в threshold contributions (основной рекомендуемый)**
   - В `build_glyph_value_matrices` (цикл accumulated_stat по threshold_nodes, ~1788):
     - selected_stat и начисление activation/scaling/future — cap не только expected_fill, но и `requirement + max_reasonable_overshoot` (напр. 5 или 10, или размер самой большой threshold-ноды в радиусе).
     - После accumulated >= requirement (для глифов со scaling_value_per_5 <=0) — multiplier на contribution.direct = 0 или очень маленький (glyph_route.excess_factor = 0.05 или tunable).
   - В `glyph_relocation_add_priorities` (~3462):
     - В else (past desired_fill) drastically снизить коэффициент (0.01–0.02 вместо 0.03) или сделать 0, если before >= requirement && !has_useful_scaling.
     - Добавить явный `if (before >= info.requirement) { score += stat * w * very_small; }`.
   - Это уменьшит pull на маргинальные +5 в эвристике и в relocation add.

**2. Excess-aware protection от удаления и dedicated prune pass**
   - В `selected_glyph_stat_pressure` (~3526) или перед использованием в remove:
     - Для каждого assigned glyph посчитать current total stat_in_radius (или переиспользовать из assign).
     - При начислении pressure на ноду: если общий stat > requirement + buffer (напр. +5), то pressure_contrib для этой ноды = stat_value * clamp( (requirement + buffer - (total - stat_value)) / stat_value , 0, 1) или просто снижать boost для excess-нод.
   - В `glyph_relocation_remove_nodes` sort: ноды, чей вклад является excess (можно вычислить "would_be_after_remove"), получают меньший pressure* коэффициент.
   - Добавить после обычных relocation passes dedicated "threshold_excess_prune" (или внутри последней итерации): для assigned с requirement_met, пробовать кандидатов-remove среди normal threshold contributors (сортировать по base_score ascending, игнорируя/снижая pressure), если после remove stat_in_radius всё ещё >= req (с небольшим запасом), и своп/удаление даёт non-negative delta по полному route_score_value — делать. Ограничить бюджет (GLYPH_EXCESS_PRUNE_CANDIDATES).

**3. Улучшение качества выбора threshold providers (а не только количества)**
   - В `glyph_threshold_candidates` и сортировке в build_glyph_value_matrices: при прочих равных предпочитать ноды с высоким node_base_score или с дополнительными ценными статами (vuln, damage и т.д. на редких), а не чистые +5 primary. Редкие threshold-ноды (типа 2_15) должны иметь преимущество.
   - В relocation add/priorities: к score threshold-ноды добавлять не только stat pressure, но и "quality" (другие weighted stats ноды) * небольшой фактор. Это поможет предпочесть 10-dex rare + 3–4 хороших вместо 1 rare + 7 normals.
   - Опционально: в glyph_route_node_bonuses / contributions для threshold — давать чуть больший uplift редким threshold-нодам (аналог RARE_BONUS...).

**4. Tuning / конфигурация (малые изменения)**
   - Рассмотреть понижение дефолтного `fill_target` до 1.05–1.10 (или 1.0 + "одна дискретная нода").
   - Добавить в GlyphRouteTuning (и weights glyph_route) ключ `excess` / `threshold_buffer` (максимальный overshoot в статах, после которого uplift падает до нуля). Использовать в expected_fill / desired_fill.
   - Для глифов, у которых `scaling_value_per_5 <= 0` (активация только), использовать более жёсткий target = requirement (или requirement + 1–2).
   - Не трогать scarcity/synergy/future глобально — они помогают в других сценариях.

**5. Диагностика и тесты (обязательно при фиксе)**
   - В JSON payload (assigned_glyphs / glyph evaluations в выводе по доске):
     - `stat_in_radius`, `requirement`, `requirement_met`, `excess: stat_in_radius - requirement`
     - По желанию: `threshold_nodes_count`, `threshold_from_normals`, `threshold_from_rares`, или даже массив id нод, давших threshold stat (для глубокого анализа).
   - При `--include-route-steps` или debug: breakdown по threshold contributions в build_glyph_value_matrices (какие ноды получили сколько direct от activation для конкретного socket/glyph).
   - Синтетические/регресс-тесты: на revealing (или подмножестве) смоделировать ситуацию с menagerist-подобным req=40, кучей +5 dex normal + одной +10 rare в радиусе. Проверить, что после оптимизации stat_in_radius находится в [40, 40 + 5] (или configurable), и что при искусственном удалении одной marginal +5 dex ноды (если stat остаётся >=40) финальный score не падает (или падает незначительно).
   - Регресс на spiritborn: после прогона проверять для всех assigned с threshold (особенно menagerist на revealing_5_15 и аналогичных), что excess мал (не 5+ без веской причины от scaling или connectivity), и что в selected_nodes нет очевидных "лишних" threshold normal'ов, которые можно было бы исключить без потери req.
   - Добавить в STATS_TABLE.md или комментарии примеры "избыточного" vs "достаточного" для threshold.

**6. Чего избегать**
   - Полное отключение glyph_route.* давлений или fill_target.
   - Hardcode под конкретный глиф (menagerist), стат (dexterity), доску (revealing) или значение 40/5.
   - Тяжёлый полный re-assign_glyphs + route_score_value внутри основного greedy consider_targets (можно позволить лёгкую оценку только threshold части в limited passes).
   - Простое увеличение числа маршрутов / локальных итераций как "решение" — маскирует, а не исправляет.
   - Игнорирование residual 0.03/0.05 коэффициентов — именно они позволяют "прилипанию" excess после достижения req.

### Как воспроизводить / отлаживать

1. `bin\paragon_optimize.exe optimize --profile "profiles\spiritborn.json" --no-html --include-route-steps`
2. В JSON-выводе найти boards → "revealing".
3. Посмотреть assigned_glyphs (или evaluations) для socket "revealing_5_15" — должен быть menagerist (или его id), stat_in_radius ≈45, requirement=40, requirement_met=true, excess≈5.
4. Посмотреть selected_nodes (или route) на доске. Отобрать ноды в радиусе ~3 от (x=5,y=15) сокета (revealing_2_15, 3_14, 3_17, 5_12, 5_16, 7_12, 7_15, 7_16 и т.п.), просуммировать их dexterity stats → 45.
5. Проверить в glyph evaluations node_bonus_score (для magic под menagerist) — высокий, но это не отменяет opportunity cost от лишнего dex node'а.
6. Для глубокого анализа: временно добавить вывод/логи в `glyph_relocation_add_priorities` (какие threshold adds и их score), в `selected_glyph_stat_pressure`, и/или в цикл contributions threshold в build_glyph_value_matrices. Сравнить с принудительным "убрать одну +5 dex" и пересчётом score.
7. Смотреть out/*.html (если генерировался) — визуально на revealing board вокруг сокета 5_15.

### Статус

Open. Требует реализации, тестов и проверки на spiritborn (и других профилях/классах с threshold-глифами). Сильно связан с Багами #1 и #2 — общая корневая причина в том, что эвристика (и локальный ремонт) недостаточно точно балансирует "стоимость доступа/фидеров" против реальной маргинальной ценности после достижения ключевых порогов (gated rare, amplified magic, glyph activation).

---

Дополнительные замечания (к Багу #3):
- Текущий код уже имеет осознанную логику "давить на threshold stat" именно для того, чтобы activation бонус и glyph weights реализовались (см. pressures, desired_fill, activation_value_between, 0.10/0.03 в relocation). Баг показывает, что buffer (fill_target) + residual бонусы + сильная защита pressure*3 позволяют/поощряют overshoot на величину одной нормальной ноды.
- Как и в предыдущих: истина всегда в `route_score_value` / `assign_glyphs` / `evaluate_glyph`. Фикс должен улучшать hints, add/remove приоритеты и pruning excess, не меняя финальный scorer.
- Полезно будет после фикса прогнать несколько профилей (spiritborn, другие классы) и убедиться, что threshold-глифы всё ещё получают requirement_met, а excess не растёт в других местах.



## Баг #4:
найди багу. я использовал optimize_fast_paladin.bat , в начале max routes 10000 кандидаты 250, получил 10_06_2026_23_59_51.html 8700+ score. Затем поменял выборку без параметров(дефолт),т.е. 30000  роутов, 1000 кандидатов. Отчёт : 11_06_2026_00_05_04.html всего лишь 8530.76 score, вчём может быть проблема? ну во первых, мб выбрать идеальные пропорции по дефолту.

И посмотри аналитику от GROK(она ок или не ок?):
```
Как это работает (код в native/optimizer_cli.cpp)

1. Для каждой раскладки досок (generate_layouts) считается список "интересных" целей:

std::vector<int> targets = candidate_targets(graph, weights, options.candidate_targets, ...);
// ...
seeds.push_back(-1);
seeds.insert(seeds.end(), targets.begin(), targets.end());
   (см. строки ~4863, 2012–2072)

2. Для каждого seed'а запускается run_greedy_route (жадный набор по ratio/adjusted_gain из consider_targets).

3. Каждый такой жадный маршрут полностью скорится (score_route → assign_glyphs + route_score_value) — это pre-local score.

4. Среди всех таких pre-local результатов выбирается один лучший:

if (better_scored_route(item, best)) {
    best = std::move(item);
    best_graph = graph;
    best_sequence = sequence;
    best_scoring_context = scoring_context;
    best_route_bonuses = route_bonuses;
    have_best_context = true;
}
   (строки ~4918–4928)

5. Только этот один потом получает мощный ремонт:

RouteOutput improved_route = improve_route_locally(best_graph, ... best.route ...);
// + improve_route_glyph_relocations (ещё 12 проходов)
   (строки ~4940–4974)

6. Финальный результат — это post-local версия именно этого pre-local-победителя.

Почему увеличение кандидатов/роутов может дать худший финал

• candidate_targets с лимитом 250 vs 1000 возвращает разный по составу список целей.
• Внутри run_greedy_route / consider_targets на каждом шаге из этого списка выбирается лучший по эвристике (ratio + adjusted_gain с detour и т.д.).
• Больший список = больше "медиокрных" нормалов в меню → жадный алгоритм на каких-то шагах может выбрать другой узел (даже если в итоге pre-local score у такого маршрута чуть выше или равен).
• В итоге другой selected набор выигрывает pre-local конкурс → именно его отдадут в improve_route_locally.
• Этот набор может оказаться в худшем "бассейне" для локальных свопов и докормки threshold'ов глифов (особенно с учётом glyph_relocation_add_priorities с остаточными коэффициентами 0.03 и т.д.).

Плюс взаимодействие с max_routes:
• При CT=250 на одну раскладку уходит ~251 роут.
• При CT=1000 — ~1001 роут.
• Глобальный бюджет max_routes тратится по-разному → под лимитом ты можешь вообще не дойти до тех же последовательностей досок / ротаций / аттачментов, или дойти с другим количеством seeds.

Поэтому "больше поиска" легко меняет, какой именно base получит дорогой ремонт, и финальный score падает.

Это полностью согласуется с тем, что у тебя в KNOWN_ISSUES_FIXES.md уже написано (в нескольких местах):

│ Простое увеличение MAX_ROUTES / CANDIDATE_TARGETS как "решение" — маскирует, а не исправляет │ эвристику.

Дополнительные нюансы (не главная причина, но усугубляют)

• В glyph_relocation_add_priorities даже после достижения requirement всё ещё даётся 0.03 * stat * weight (строка ~3466). Это помогает "прилипать" лишним +5 нормалам под threshold'ы.
• Локальный ремонт ограничен adjacent + бюджетами (LOCAL_IMPROVEMENT_*_CANDIDATES, GLYPH_RELOCATION_*).
• quick prefilter (quick_delta <= 1e-9) в improve_route_locally может отбрасывать предложения до полного скора.

Что можно сделать (диагноз, а не сразу фикс)

Самый прямой способ сделать результат монотонным/стабильнее — не выбирать "одного чемпиона по pre-local", а:

• Либо держать top-K pre-local баз (с их graph/context/route_bonuses) и локально улучшать несколько, потом брать лучший post-local.
• Либо после всего поиска брать текущий best и ещё пару сильных pre-local из последних раскладок и тоже их чинить (дешево, если K маленький).
• Либо менять эвристику так, чтобы pre-local лучше коррелировал с post-local потенциалом (сложнее).

Пока этого нет — поведение "увеличил лимиты → стало хуже" является ожидаемым свойством текущей архитектуры.

Если хочешь, я могу предложить минимальный патч (например, улучшать 2–3 лучших pre-local базы и брать максимум), или сначала посмотреть на реальные JSON из твоих двух .html (там есть local_score_before, search.limits, selected_nodes и т.д.), чтобы подтвердить на конкретных данных.
```

От меня: если для каждого маршрута делать локальный ремонт?Дорого выйдет? ну то есть не `Среди всех таких pre-local результатов выбирается один лучший`, а берем все pre-local, и понеслась для каждого делать local ремонт?
