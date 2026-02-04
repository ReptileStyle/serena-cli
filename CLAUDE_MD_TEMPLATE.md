# Serena CLI — шаблон секции для CLAUDE.md

Скопируйте секцию ниже в CLAUDE.md вашего проекта.

---

## Serena CLI (Навигация по коду — Приоритет!)

Serena — семантический анализатор кода на базе Dart Language Server. Даёт доступ к символьной навигации (классы, методы, поля), поиску использований и паттернов. Работает через daemon-процесс.

**Вызов одинаковый для всех агентов** (основной и субагенты) — через Bash:
```bash
serena-cli <tool_name> '<json_args>'
```

Daemon стартует автоматически при первом вызове. Все пути (`relative_path`) — относительно корня проекта.

### СТРОГИЕ ПРАВИЛА (обязательны для ВСЕХ агентов)

1. **ЗАПРЕЩЕНО использовать Grep и Glob для поиска по коду проекта** пока Serena доступна.
   - Используй `serena-cli` для ЛЮБОГО поиска и навигации по коду.
   - Grep/Glob **разрешены ТОЛЬКО** если `serena-cli` вернул ошибку (daemon упал, таймаут, etc.) или для поиска вне проекта.
   - **Read РАЗРЕШЁН всегда** — Serena ищет нужное, Read читает. Это нормальный workflow.

2. **ЗАПРЕЩЕНО читать целые .dart файлы вместо символьного доступа.**
   - Хочешь понять структуру файла → `get_symbols_overview`
   - Хочешь увидеть метод/класс → `find_symbol` с `include_body: true`
   - Если ты уже прочитал файл целиком — НЕ анализируй его повторно символьными тулами, у тебя уже есть информация.

3. **Приоритет тулов для поиска** (от высшего к низшему):
   - `find_symbol` / `get_symbols_overview` — если знаешь имя символа или файл
   - `find_referencing_symbols` — если ищешь использования
   - `search_for_pattern` — если не знаешь точное имя символа или ищешь в не-код файлах
   - `find_file` / `list_dir` — для навигации по файловой структуре
   - Grep/Glob — **ТОЛЬКО при ошибке Serena**
   - Read — разрешён всегда

4. **НЕ используй Serena для редактирования** — только навигация и поиск.

### Workflow навигации по коду

**Символы идентифицируются через name_path** — путь в дереве символов внутри файла.
Метод `bar` в классе `Foo` имеет name_path `Foo/bar`. При перегрузке добавляется индекс: `Foo/bar[0]`.

**Пошаговый подход — от общего к конкретному:**

1. **Не знаешь структуру файла** → `get_symbols_overview` для обзора
2. **Знаешь класс, хочешь список методов** → `find_symbol` с `depth: 1`
3. **Хочешь прочитать конкретный метод** → `find_symbol` с `include_body: true`
4. **Не знаешь имя символа** → `search_for_pattern` для поиска кандидатов, затем символьные тулы
5. **Ищешь все использования** → `find_referencing_symbols`

**Используй `relative_path`** для ограничения области поиска — это значительно ускоряет работу.

**Пример workflow (Dart):**
```bash
# 1. Что в файле?
serena-cli get_symbols_overview '{"relative_path": "lib/src/resources/repository/tasks.dart"}'

# 2. Вижу TasksRepository — какие у него методы?
serena-cli find_symbol '{"name_path_pattern": "TasksRepository", "depth": 1}'

# 3. Хочу прочитать реализацию fetch
serena-cli find_symbol '{"name_path_pattern": "TasksRepository/fetch", "include_body": true}'

# 4. Не знаю точное имя, ищу по паттерну
serena-cli search_for_pattern '{"substring_pattern": "registerSingleton.*Tasks", "relative_path": "lib/src/"}'

# 5. Где используется TasksRepository?
serena-cli find_referencing_symbols '{"name_path": "TasksRepository", "relative_path": "lib/src/resources/repository/tasks.dart"}'
```

### Когда НЕ использовать Serena

- `serena-cli` вернул ошибку → fallback на Grep/Glob
- Поиск вне проекта → Grep/Glob

### Тулы

#### `find_symbol` — Поиск символов по имени

Поиск классов, методов, функций, полей по паттерну name_path. Возвращает список символов с локациями.

Паттерн name_path может быть:
- Простое имя: `"Foo"` — найдёт любой символ с этим именем
- Относительный путь: `"Foo/bar"` — найдёт символ с этим суффиксом name_path
- Абсолютный путь: `"/Foo/bar"` — точное совпадение полного name_path
- С индексом перегрузки: `"Foo/bar[1]"` — конкретная перегрузка

```bash
# Найти класс
serena-cli find_symbol '{"name_path_pattern": "TasksRepository"}'
# Найти с ограничением по директории (быстрее)
serena-cli find_symbol '{"name_path_pattern": "TasksRepository", "relative_path": "lib/src/"}'
# Получить класс с его методами
serena-cli find_symbol '{"name_path_pattern": "TasksRepository", "depth": 1}'
# Прочитать исходный код метода
serena-cli find_symbol '{"name_path_pattern": "TasksRepository/fetch", "include_body": true}'
# Подстроковый поиск — найдёт getValue, getData и т.д.
serena-cli find_symbol '{"name_path_pattern": "Foo/get", "substring_matching": true}'
```

| Параметр | Тип | Обяз. | Default | Описание |
|----------|-----|-------|---------|----------|
| `name_path_pattern` | string | да | — | Паттерн name_path (см. выше) |
| `relative_path` | string | нет | `""` | Ограничить поиск файлом/директорией. Значительно ускоряет поиск |
| `depth` | int | нет | `0` | Глубина дочерних символов. 1 = методы класса |
| `include_body` | bool | нет | `false` | Включить исходный код символа. Использовать только когда нужно прочитать реализацию |
| `include_info` | bool | нет | `false` | Включить docstring/сигнатуру (игнорируется если include_body=true) |
| `substring_matching` | bool | нет | `false` | Подстроковый поиск последнего элемента паттерна |
| `include_kinds` | int[] | нет | `[]` | LSP symbol kind фильтр (пустой = все) |
| `exclude_kinds` | int[] | нет | `[]` | Исключить LSP symbol kinds |

#### `get_symbols_overview` — Обзор символов файла

**Первый тул при знакомстве с файлом.** Возвращает символы, сгруппированные по типу (Class, Method, Field и т.д.)

```bash
serena-cli get_symbols_overview '{"relative_path": "lib/src/resources/repository/tasks.dart"}'
serena-cli get_symbols_overview '{"relative_path": "lib/src/resources/repository/tasks.dart", "depth": 1}'
```

| Параметр | Тип | Обяз. | Default | Описание |
|----------|-----|-------|---------|----------|
| `relative_path` | string | да | — | Путь к файлу |
| `depth` | int | нет | `0` | 0 = только top-level, 1 = с дочерними (методы классов) |

#### `search_for_pattern` — Поиск regex-паттерна

Гибкий поиск произвольных паттернов. Regex компилируется с DOTALL (точка матчит newlines). Не использовать `.*` в начале/конце — бессмысленно. Предпочитать не-жадные квантификаторы `.*?`.

Область поиска гибко настраивается: `relative_path` ограничивает директорию, glob-паттерны фильтруют файлы поверх этого. Globs матчатся от корня проекта.

```bash
# Поиск в конкретной директории
serena-cli search_for_pattern '{"substring_pattern": "BehaviorSubject", "relative_path": "lib/src/resources/"}'
# С контекстом вокруг совпадений
serena-cli search_for_pattern '{"substring_pattern": "registerSingleton", "context_lines_before": 2, "context_lines_after": 2}'
# Только dart-файлы
serena-cli search_for_pattern '{"substring_pattern": "TODO", "paths_include_glob": "**/*.dart"}'
# Исключить тесты
serena-cli search_for_pattern '{"substring_pattern": "TasksRepository", "paths_exclude_glob": "*test*"}'
# Только код (не yaml/html)
serena-cli search_for_pattern '{"substring_pattern": "class.*Repository", "restrict_search_to_code_files": true}'
```

| Параметр | Тип | Обяз. | Default | Описание |
|----------|-----|-------|---------|----------|
| `substring_pattern` | string | да | — | Regex-паттерн (DOTALL) |
| `relative_path` | string | нет | `""` | Ограничить файлом/директорией |
| `restrict_search_to_code_files` | bool | нет | `false` | Только код-файлы (не yaml/html) |
| `context_lines_before` | int | нет | `0` | Строки контекста до совпадения |
| `context_lines_after` | int | нет | `0` | Строки контекста после совпадения |
| `paths_include_glob` | string | нет | `""` | Glob-фильтр включения (`"*.dart"`, `"src/**/*.ts"`) |
| `paths_exclude_glob` | string | нет | `""` | Glob-фильтр исключения (приоритет над include) |

#### `find_referencing_symbols` — Поиск использований символа

Находит все места в кодовой базе, где используется указанный символ. Возвращает метаданные ссылающихся символов и сниппеты кода.

```bash
serena-cli find_referencing_symbols '{"name_path": "TasksRepository", "relative_path": "lib/src/resources/repository/tasks.dart"}'
serena-cli find_referencing_symbols '{"name_path": "TasksRepository/fetch", "relative_path": "lib/src/resources/repository/tasks.dart"}'
```

| Параметр | Тип | Обяз. | Default | Описание |
|----------|-----|-------|---------|----------|
| `name_path` | string | да | — | Name_path символа (логика как в find_symbol) |
| `relative_path` | string | да | — | **Файл** (не директория!), содержащий символ |
| `include_info` | bool | нет | `false` | Доп. инфо о ссылающихся символах |
| `include_kinds` | int[] | нет | `[]` | Фильтр по типам ссылающихся символов |
| `exclude_kinds` | int[] | нет | `[]` | Исключить типы ссылающихся символов |

#### `find_file` — Поиск файлов по маске

```bash
serena-cli find_file '{"file_mask": "*.dart", "relative_path": "lib/src/blocs/"}'
serena-cli find_file '{"file_mask": "tasks*", "relative_path": "."}'
```

| Параметр | Тип | Обяз. | Default | Описание |
|----------|-----|-------|---------|----------|
| `file_mask` | string | да | — | Имя файла или маска (`*`, `?`) |
| `relative_path` | string | да | — | Директория для поиска. `"."` = корень проекта |

#### `list_dir` — Листинг директории

```bash
serena-cli list_dir '{"relative_path": "lib/src/resources/", "recursive": false}'
serena-cli list_dir '{"relative_path": ".", "recursive": true, "skip_ignored_files": true}'
```

| Параметр | Тип | Обяз. | Default | Описание |
|----------|-----|-------|---------|----------|
| `relative_path` | string | да | — | Директория. `"."` = корень проекта |
| `recursive` | bool | да | — | Рекурсивный обход поддиректорий |
| `skip_ignored_files` | bool | нет | `false` | Пропускать gitignored файлы |

### Управление daemon

```bash
serena-cli --status   # проверить статус
serena-cli --stop     # остановить daemon
```
