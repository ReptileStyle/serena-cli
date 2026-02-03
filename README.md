# serena-cli

CLI-обёртка для [Serena MCP server](https://github.com/oraios/serena), позволяющая использовать Serena из любого контекста — включая субагентов Claude Code, которые не имеют доступа к MCP-тулам.

## Проблема

Субагенты Claude Code (Task tool) не могут использовать MCP-тулы. Serena доступна только основному агенту. Это ограничивает возможности делегирования задач по анализу кода.

## Решение

Daemon-процесс держит Serena MCP server запущенным и проксирует tool calls через Unix-сокет. Bash-обёртка `serena-cli` предоставляет единый CLI-интерфейс для всех агентов.

```
Agent (любой) → Bash("serena-cli find_symbol ...") → Unix socket → Daemon → Serena MCP → Dart LSP
```

## Установка

### 1. Скопировать скрипты

```bash
cp serena-daemon.py ~/scripts/
cp serena-cli ~/scripts/
chmod +x ~/scripts/serena-cli ~/scripts/serena-daemon.py
```

### 2. Убедиться, что Serena установлена

```bash
uvx --from "git+https://github.com/oraios/serena" serena --help
```

### 3. Убедиться, что проект настроен для Serena

В корне проекта должна быть директория `.serena/` с файлом `project.yml`:

```yaml
languages:
- dart  # или python, typescript, и т.д.
encoding: "utf-8"
project_name: "my-project"
```

### 4. Настроить Claude Code

#### Добавить permission в `.claude/settings.local.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash(~/scripts/serena-cli:*)"
    ]
  }
}
```

#### (Опционально) Убрать Serena MCP из `~/.claude.json`:

Если хотите единый интерфейс без дублирования Dart LSP и системного промпта Serena в контексте — удалите секцию `mcpServers.serena` из проекта в `~/.claude.json`.

### 5. Добавить документацию в CLAUDE.md проекта

Скопируйте содержимое файла `CLAUDE_MD_TEMPLATE.md` в ваш `CLAUDE.md`. Это даст агентам (включая субагентов) полную информацию о доступных тулах и их параметрах.

## Использование

```bash
# Поиск символа
serena-cli --project /path/to/project find_symbol '{"name_path_pattern": "MyClass"}'

# Обзор символов файла
serena-cli --project /path/to/project get_symbols_overview '{"relative_path": "lib/src/foo.dart"}'

# Поиск паттерна
serena-cli --project /path/to/project search_for_pattern '{"substring_pattern": "TODO", "relative_path": "lib/"}'

# Поиск использований символа
serena-cli --project /path/to/project find_referencing_symbols '{"name_path": "MyClass", "relative_path": "lib/src/foo.dart"}'

# Поиск файлов
serena-cli --project /path/to/project find_file '{"file_mask": "*.dart", "relative_path": "lib/"}'

# Листинг директории
serena-cli --project /path/to/project list_dir '{"relative_path": "lib/src/", "recursive": false}'
```

Если `--project` не указан, используется `$SERENA_PROJECT` или текущая директория.

### Управление daemon

```bash
serena-cli --status    # статус daemon
serena-cli --stop      # остановить daemon
```

Daemon стартует автоматически при первом вызове.

## Как это работает

1. `serena-cli` проверяет, запущен ли daemon (PID-файл + Unix-сокет в `/tmp/`)
2. Если нет — запускает `serena-daemon.py` в фоне и ждёт готовности
3. Daemon стартует Serena MCP server (`uvx serena start-mcp-server`), проводит MCP-инициализацию
4. Daemon слушает Unix-сокет, принимает JSON-запросы `{"tool": "...", "args": {...}}`
5. Проксирует запрос к Serena через MCP JSON-RPC, возвращает результат
6. Lock обеспечивает последовательную обработку запросов (Dart LSP — однопоточный)

## Доступные тулы (только чтение)

| Тул | Описание |
|-----|----------|
| `find_symbol` | Поиск символов (классы, методы, функции) по name_path паттерну |
| `get_symbols_overview` | Обзор всех символов файла, сгруппированных по типу |
| `search_for_pattern` | Поиск regex-паттерна в файлах |
| `find_referencing_symbols` | Все использования символа в кодовой базе |
| `find_file` | Поиск файлов по маске |
| `list_dir` | Листинг директории |

## Требования

- Python 3.10+
- `uvx` (из `uv`)
- Serena (`uvx --from "git+https://github.com/oraios/serena" serena`)
- Language server для вашего языка (Dart LSP для Dart, и т.д.)
