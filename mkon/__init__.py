"""mkon — формат конфигов для людей. Python-реализация, эквивалентная mkon-js.

API повторяет модуль `json` из стандартной библиотеки:

    >>> import mkon
    >>> mkon.loads("{ html: ⊄, aliases: [notsub, notsubset] }")
    {'html': '⊄', 'aliases': ['notsub', 'notsubset']}
    >>> print(mkon.dumps({'a': 1, 'b': [1, 2]}), end='')
    {
      a: 1
      b:
      [
        1
        2
      ]
    }

Соответствие типов (mkon → Python):

=================================  ===========================================
``{...}``                          ``dict``
``[...]``                          ``list`` (``dumps`` принимает и ``tuple``)
строка                             ``str``
``1``, ``-5``                      ``int`` (в пределах ±(2**53 - 1))
``1.5``, ``1e3``, ``NaN``, ``-0``  ``float``
``2024-01-15``, ``…T10:30Z``       ``datetime`` (UTC); ``dumps`` принимает и ``date``
``true`` / ``false`` / ``null``    ``bool`` / ``None``
=================================  ===========================================

Комментарии: ``loads(text, keep_comments=True)`` возвращает ``MkonDict`` / ``MkonList``,
которые помнят комментарии, и ``dumps`` пишет их обратно (``get_comments`` — доступ к ним).
"""

from ._common import MkonDatetime, MkonDict, MkonList
from ._common import get_comment as get_comments
from ._parse import MkonDecodeError, load, loads
from ._stringify import dump, dumps

__version__ = "0.2.0"

__all__ = [
    "load",
    "loads",
    "dump",
    "dumps",
    "MkonDecodeError",
    "get_comments",
    "MkonDict",
    "MkonList",
    "MkonDatetime",
]
