"""Literal-preserving comparison keys for SQLite schema SQL.

This is a conservative lexical comparator, not a SQL grammar validator or a
rewriter. It never changes persisted SQL/hashes or executes supplied SQL. ASCII
keyword/unquoted-identifier case and token-separating comments/whitespace may
vary; quoted spelling, numeric spelling and token boundaries may not.
"""
from __future__ import annotations

from collections import OrderedDict
import re
import sys
from threading import RLock

_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_SPACE = frozenset(" \t\n\r\v\f")
_NUMBER = re.compile(r"""
    0[xX][0-9a-fA-F](?:_?[0-9a-fA-F])* |
    (?: [0-9](?:_?[0-9])*(?:\.(?:[0-9](?:_?[0-9])*)?)? | \.[0-9](?:_?[0-9])* )
    (?: [eE][+-]?[0-9](?:_?[0-9])* )?
""", re.VERBOSE)
_MULTI_SYMBOLS = ("->>", "||", "<<", ">>", "<=", ">=", "==", "!=", "<>", "->")
_SINGLE_SYMBOLS = frozenset("(),.;+-*/%~&|<>=")
_QUOTE_KINDS = {"'": "string", '"': "quoted-identifier", "`": "backtick-identifier", "[": "bracket-identifier"}
CACHE_BYTE_LIMIT = 2 << 20
CACHE_ENTRY_LIMIT = 512
CACHE_SQL_BYTE_LIMIT = 32 << 10
_CACHE = OrderedDict()
_CACHE_BYTES = 0
_CACHE_LOCK = RLock()


class SQLSchemaKeyError(ValueError):
    pass


def clear_canonical_sql_cache():
    global _CACHE_BYTES
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE_BYTES = 0


def canonical_sql_cache_info():
    with _CACHE_LOCK:
        return {"entries": len(_CACHE), "tracked_bytes": _CACHE_BYTES,
                "byte_limit": CACHE_BYTE_LIMIT, "entry_limit": CACHE_ENTRY_LIMIT}


def _retained_size(key, value):
    seen = set()
    stack = [key, value]
    size = 512  # OrderedDict node, stored tuple and allocator overhead.
    while stack:
        item = stack.pop()
        identity = id(item)
        if identity in seen:
            continue
        seen.add(identity)
        size += sys.getsizeof(item)
        if isinstance(item, tuple):
            stack.extend(item)
    return size


def _identifier_start(char):
    return ("A" <= char <= "Z" or "a" <= char <= "z" or char == "_"
            or ord(char) >= 128)


def _identifier_part(char):
    return _identifier_start(char) or "0" <= char <= "9" or char == "$"


def _quoted_end(sql, start):
    opener = sql[start]
    closer = "]" if opener == "[" else opener
    index = start + 1
    while index < len(sql):
        if sql[index] == closer:
            # SQLite doubles quote/backtick escapes. Bracket identifiers end
            # at the first ]; SQL Server's ]] escape is not SQLite syntax.
            if opener != "[" and index + 1 < len(sql) and sql[index + 1] == closer:
                index += 2
                continue
            return index + 1
        index += 1
    raise SQLSchemaKeyError("unterminated quoted SQL schema token")


def _tokens(sql):
    result = []
    index = depth = 0
    while index < len(sql):
        char = sql[index]
        if char in _SPACE:
            index += 1
            continue
        if sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            index = len(sql) if end < 0 else end + 1
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0:
                raise SQLSchemaKeyError("unterminated SQL schema comment")
            index = end + 2
            continue
        if sql.startswith("*/", index):
            raise SQLSchemaKeyError("unmatched SQL schema comment terminator")
        # Adjacency matters: x'AB' is a BLOB, x 'AB' is two tokens.
        if char in "xX" and index + 1 < len(sql) and sql[index + 1] == "'":
            end = _quoted_end(sql, index + 1)
            raw = sql[index + 2:end - 1]
            if len(raw) % 2 or any(value not in "0123456789abcdefABCDEF" for value in raw):
                raise SQLSchemaKeyError("invalid SQLite BLOB literal")
            result.append(("blob", "x" + sql[index + 1:end]))
            index = end
            continue
        if char in _QUOTE_KINDS:
            end = _quoted_end(sql, index)
            result.append((_QUOTE_KINDS[char], sql[index:end]))
            index = end
            continue
        if _identifier_start(char):
            end = index + 1
            while end < len(sql) and _identifier_part(sql[end]):
                end += 1
            result.append(("word", sql[index:end].translate(_ASCII_FOLD)))
            index = end
            continue
        if "0" <= char <= "9" or (char == "." and index + 1 < len(sql) and "0" <= sql[index + 1] <= "9"):
            match = _NUMBER.match(sql, index)
            if match is None:
                raise SQLSchemaKeyError("invalid SQL numeric token")
            end = match.end()
            if end < len(sql) and (_identifier_part(sql[end]) or sql[end] == "."):
                raise SQLSchemaKeyError("malformed adjacent SQL numeric token")
            result.append(("number", sql[index:end]))
            index = end
            continue
        symbol = next((value for value in _MULTI_SYMBOLS if sql.startswith(value, index)), None)
        if symbol is None:
            if char not in _SINGLE_SYMBOLS:
                raise SQLSchemaKeyError("unknown or malformed SQL schema token")
            symbol = char
        if symbol == "(":
            depth += 1
        elif symbol == ")":
            depth -= 1
            if depth < 0:
                raise SQLSchemaKeyError("unmatched SQL schema closing parenthesis")
        result.append(("symbol", symbol))
        index += len(symbol)
    if depth:
        raise SQLSchemaKeyError("unterminated SQL schema parenthesis")
    if not result:
        raise SQLSchemaKeyError("empty SQL schema definition")
    return result


def _create_object_index(tokens):
    if tokens[0] != ("word", "create") or len(tokens) < 2:
        return None
    index = 1
    if tokens[index] in (("word", "temp"), ("word", "temporary")):
        index += 1
        allowed = {"table", "view", "trigger"}
    elif tokens[index] == ("word", "unique"):
        index += 1
        allowed = {"index"}
    elif tokens[index] == ("word", "virtual"):
        index += 1
        allowed = {"table"}
    else:
        allowed = {"table", "view", "trigger", "index"}
    if index < len(tokens) and tokens[index][0] == "word" and tokens[index][1] in allowed:
        return index
    return None


def canonical_sql_key(sql, ignore_if_not_exists=True):
    """Return immutable typed tokens; preserve all quoted content exactly.

    Malformed lexical input, unsupported ASCII characters, bind parameters and
    unbalanced quotes/comments/parentheses fail closed. This does not assert
    that arbitrary token sequences form executable SQL; schema admission still
    requires comparison with the reviewed SQLite-produced object definition.
    """
    if type(sql) is not str or not sql or "\x00" in sql:
        raise SQLSchemaKeyError("schema SQL must be nonempty NUL-free text")
    if type(ignore_if_not_exists) is not bool:
        raise TypeError("ignore_if_not_exists must be a boolean")
    # Cache only the exact caller input. A changed quoted literal or different
    # ignore flag must never inherit another definition's comparison key.
    cacheable = len(sql) <= CACHE_SQL_BYTE_LIMIT and len(sql.encode("utf-8")) <= CACHE_SQL_BYTE_LIMIT
    key = (sql, ignore_if_not_exists)
    if cacheable:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None:
                _CACHE.move_to_end(key)
                return cached[0]
    tokens = _tokens(sql)
    object_index = _create_object_index(tokens)
    if ignore_if_not_exists and object_index is not None:
        start = object_index + 1
        if tokens[start:start + 3] == [("word", "if"), ("word", "not"), ("word", "exists")]:
            if start + 3 == len(tokens) or tokens[start + 3][0] not in {"word", *_QUOTE_KINDS.values()}:
                raise SQLSchemaKeyError("CREATE IF NOT EXISTS requires an object name")
            del tokens[start:start + 3]
    value = tuple(tokens)
    if cacheable:
        size = _retained_size(key, value)
        if size <= CACHE_BYTE_LIMIT:
            global _CACHE_BYTES
            with _CACHE_LOCK:
                # Another thread may have completed the same exact input while
                # tokenization ran outside the lock.
                cached = _CACHE.get(key)
                if cached is not None:
                    _CACHE.move_to_end(key)
                    return cached[0]
                while _CACHE and (_CACHE_BYTES + size > CACHE_BYTE_LIMIT
                                  or len(_CACHE) >= CACHE_ENTRY_LIMIT):
                    _, (_, discarded) = _CACHE.popitem(last=False)
                    _CACHE_BYTES -= discarded
                if CACHE_ENTRY_LIMIT > 0:
                    _CACHE[key] = value, size
                    _CACHE_BYTES += size
    return value
