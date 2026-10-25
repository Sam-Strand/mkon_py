# mkon (Python)

Python-реализация **mkon**, эквивалентная [mkon-js](../../graph-flow/frontend/git/mkon-js). Без зависимостей, Python ≥ 3.9.

Цель — чтобы цикл **JS → mkon → Python → mkon → JS** ничего не ломал: для одних и тех же данных обе
реализации пишут побайтово одинаковый текст, читают текст друг друга в одинаковые данные и на
битом тексте падают с одинаковой ошибкой. JS-версия — эталон; общие тесты лежат в mkon-js.

API — как у модуля `json` из стандартной библиотеки: `loads` / `load` / `dumps` / `dump`.

```python
import mkon

data = mkon.loads(r"{ html: ⊄, latex: \not\subset, aliases: [notsub, notsubset] }")
# {'html': '⊄', 'latex': '\\not\\subset', 'aliases': ['notsub', 'notsubset']}

with open("config.mkon", encoding="utf-8") as f:
    cfg = mkon.load(f, keep_comments=True)     # комментарии сохраняются…
cfg["port"] = 8080
with open("config.mkon", "w", encoding="utf-8") as f:
    mkon.dump(cfg, f)                          # …и пишутся обратно на свои места
```

## Установка

```bash
pip install -e .
```

## API

### `loads(s, *, keep_comments=False, object_hook=None, object_pairs_hook=None, parse_float=None, parse_int=None, parse_constant=None)`

Текст (`str`, `bytes` или `bytearray`; BOM у байтов допускается) → объекты Python. Хуки — как в `json.loads`:

```python
mkon.loads("{ price: 19.90 }", parse_float=Decimal)          # {'price': Decimal('19.90')}
mkon.loads("[9007199254740993]", parse_int=int)               # точный int за пределами 2**53
mkon.loads("{b: 1, a: 2}", object_pairs_hook=OrderedDict)
mkon.loads("[NaN, Infinity]", parse_constant=lambda s: None)  # [None, None]
```

`load(fp, **kw)` — то же из файлового объекта.

Ошибка — `MkonDecodeError(ValueError)` с атрибутами как у `json.JSONDecodeError`: `msg`, `doc`, `pos`,
`lineno`, `colno` (позиции — в символах Python) и `hint` — подсказка о вероятной причине
(например, незакрытая `{` в quoteless-значении «съела» конец строки).

### `dumps(obj, *, indent=2, sort_keys=False, default=None, check_circular=True, skipkeys=False, ...)`

Аргументы, общие с `json.dumps`: `indent` (число пробелов или строка, например `"\t"`), `sort_keys`,
`default(obj)` для неподдерживаемых типов, `check_circular` (цикл → `ValueError`), `skipkeys`.
Ключи `int` / `float` / `bool` / `None` превращаются в строки, как в `json`.

Аргументы mkon — те же возможности, что в mkon-js:

| Аргумент | По умолчанию | |
|---|---|---|
| `quotes` | `"min"` | `"min"` — кавычки только где нужны, `"keys"`, `"strings"`, `"all"` |
| `quote_char` | `'"'` | или `"'"` |
| `separator` | `False` | запятые между элементами (строки тогда в кавычках) |
| `braces_same_line` | `False` | `{` / `[` на строке с ключом |
| `root_braces` | `True` | фигурные скобки вокруг корневого объекта |
| `condense` | `0` | уместить объект/массив в одну строку, если он короче N символов |
| `multiline` | `"std"` | многострочные строки в тройных кавычках; `"no-tabs"` — с экранированием табов; `"off"` — без них |
| `eol` | `"\n"` | или `"\r\n"` |
| `keep_comments` | `True` | писать комментарии, прочитанные с `loads(..., keep_comments=True)` |
| `colors` | `False` | ANSI-раскраска для терминала |

Неверное значение опции — `ValueError` / `TypeError` (JS в таких случаях молча берёт значение по умолчанию).
`dump(obj, fp, **kw)` — то же в файловый объект.

```python
mkon.dumps(Point(1, 2), default=dataclasses.asdict)
mkon.dumps(cfg, indent=4, sort_keys=True, condense=60)
```

### Комментарии

`loads(..., keep_comments=True)` возвращает `MkonDict` / `MkonList` — обычные `dict` / `list`, которые
помнят комментарии. Значения можно менять, ключи — добавлять и удалять: `dumps` запишет комментарии на
своих местах. `get_comments(obj)` — доступ к ним (та же структура, что `__COMMENTS__` в mkon-js).

### Командная строка

```bash
python -m mkon config.mkon              # проверить и вывести отформатированным
python -m mkon -i config.mkon           # переформатировать файл на месте (комментарии сохраняются)
python -m mkon --check *.mkon           # только проверить; код возврата 1 при ошибке
python -m mkon --to-json config.mkon    # mkon → JSON
python -m mkon --from-json data.json    # JSON → mkon
echo '{a: 1, b: [x, y]}' | python -m mkon --indent tab
```

После `pip install` доступна и команда `mkon`.

### Соответствие с mkon-js

| Python | mkon-js |
|---|---|
| `loads(s)` / `loads(s, keep_comments=True)` | `parse(s)` / `rt.parse(s)` |
| `dumps(v, ...)` | `stringify(v, opts)` / `rt.stringify(v, opts)` |
| `indent`, `sort_keys`, `root_braces`, `braces_same_line`, `quote_char`, `keep_comments` | `space`, `sortProps`, `emitRootBraces`, `bracesSameLine`, `quoteChar`, `keepWsc` |
| `quotes`, `separator`, `condense`, `multiline`, `eol`, `colors` | так же |
| `get_comments(v)` | `getComment(v)` |
| `MkonDecodeError` | `Error` |

Перевести настройки из JS: `mkon._options.from_js_options({"bracesSameLine": True, "space": 4})`.

## Типы

| mkon | JS | Python |
|---|---|---|
| `{...}` | object | `dict` (с комментариями — `MkonDict`) |
| `[...]` | array | `list` (с комментариями — `MkonList`); `dumps` принимает и `tuple` |
| строка | string | `str` |
| `1`, `-5` | number | `int` (если в пределах ±(2⁵³−1)) |
| `1.5`, `1e3`, `NaN`, `±Infinity`, `-0` | number | `float` (`dumps` принимает и `Decimal`) |
| `2024-01-15`, `2024-01-15T10:30Z` | `Date` (UTC) | `datetime` с `tzinfo=UTC`; `dumps` принимает и `date` |
| `true` / `false` / `null` | boolean / null | `bool` / `None` |

## Решения, принятые ради совпадения с JS

Всё ниже — сознательно, чтобы один и тот же файл давал одни и те же данные в обоих языках.
Если что-то из этого не нравится — это вопрос к стандарту, а не к одной реализации.

- **Числа — IEEE-754 double.** Целый литерал за пределами ±(2⁵³−1) читается как `float`, а большой
  `int` пишется так, как JS записал бы этот double (`2**60` → `1152921504606847000`).
  Иначе `12345678901234567890` был бы одним числом в Python и другим в JS. Нужна точность —
  `loads(..., parse_int=int)` / `parse_float=Decimal` (как в `json`), но такой файл JS прочитает с округлением.
- **Формат чисел — как `Number.prototype.toString`:** `1e+21`, `1e-7`, `100`, `0.000001`.
  `float` с целым значением пишется без `.0` и читается обратно как `int` (`5.0 → 5`): в JS это один тип.
- **Даты — момент времени в UTC с точностью до миллисекунд.** Сдвиг пояса при чтении применяется и
  забывается, микросекунды обрезаются. `datetime` без `tzinfo` при `dumps` — ошибка
  (непонятно, какой это момент).
- **Длины в UTF-16.** Порог `condense`, отступы многострочных блоков и сортировка `sort_keys` считаются
  в UTF-16 code units, как в JS (эмодзи = 2). Парсер внутри работает поверх UTF-16.
- **Порядок ключей** обычного объекта в `dumps` — как у `for...in` в JS: ключи-индексы
  (`"0"`, `"1"`, `"10"`) по возрастанию, затем остальные в порядке вставки.
- **Пробелы** — набор JS `\s` (`﻿` — пробел, `\x1c`–`\x1f` — нет), а не `str.isspace()`.

## Известные расхождения

| Что | JS | Python |
|---|---|---|
| Год `0000` (`0000-01-01`) | `Date` | ошибка: `datetime.MINYEAR = 1` |

Можно закрыть на уровне стандарта, разрешив годы только 0001–9999.

## Тесты

```bash
python -m unittest discover -s tests
```

- `test_fixtures.py` — фикстуры из `mkon-js/test/assets` (все 77 проходят без изменений);
- `test_conformance.py` — общий `cases.json` (тот же файл прогоняет `mkon-js/test/conformance.test.js`);
- `test_cross_js.py` — **кросс-проверка с живой JS-реализацией**:
  - весь корпус текстов при 12 наборах опций (обычный режим и с комментариями);
  - 300 случайных значений «с подвохами» (`,` `}` `#` `//` кавычки, тройные кавычки, эмодзи, `NaN`,
    `-0`, даты, ключи-числа) — одинаковый текст в обоих языках, и каждый читает текст другого без потерь;
  - 3000 случайных, в основном невалидных текстов — одинаковые ошибки (с тем же сообщением) и данные.

  Нужны `node` и mkon-js: путь берётся из `$MKON_JS` или ищется рядом, иначе тест пропускается.
  Больше прогонов: `MKON_CROSS_COUNT=5000 MKON_CROSS_SEED=7`.
- `test_python_api.py` — Python API (хуки, `default`, циклы, `Decimal`, ошибки, комментарии, CLI).

### Общий набор тестов

Эталон — `mkon-js/test/assets` (фикстуры) и `mkon-js/test/conformance/cases.json`. Тесты читают их
**прямо из mkon-js** — путь из `$MKON_JS`, иначе ищется рядом (`../mkon-js` или
`~/Desktop/graph-flow/frontend/git/mkon-js` относительно этого репозитория). Копии нет, расходиться нечему.

Для CI без mkon-js набор можно положить локально (`tests/conformance/`; тесты возьмут его, если mkon-js
не найден, кросс-проверка тогда пропускается):

```bash
python scripts/sync_conformance.py [/path/to/mkon-js]
```

Кейс в `cases.json` — вход (`mkon`: текст или `input`: значение) и ожидания (`value`, `text` или
`error`/`message`). Значения — в «tagged JSON»: `{"$number": "NaN"}`, `{"$number": "-0"}`,
`{"$date": "2024-01-15T00:00:00.000Z"}`. Ожидания пересчитываются по JS:
`npm run conformance:update` в mkon-js, после чего `git diff` показывает, как изменился
стандарт. Поле `"py": "skip: причина"` — известное расхождение.
