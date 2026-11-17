"""Which request keys does a Flask handler read? (S01 review test support)

Static, over the handler source: ``request.args.get('k')``, ``data.get('k')``,
``data['k']``, ``'k' in data`` and ``getlist``. A name counts as a request body
when it is ``request`` itself or one of the usual local names for the parsed
body. A call that passes such a name on to a function is followed (the callee's
parameters count as request bodies), two calls deep. This is a heuristic, so
the tests built on it also send a fixed list of keys that no handler needs to
mention.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
import textwrap
from typing import Any

BODY_NAMES = frozenset({'data', 'payload', 'body', 'args', 'form', 'values', '_data', 'json_data', 'filters'})
_READERS = frozenset({'get', 'getlist', 'pop'})
_MAX_DEPTH = 2


def _is_request_like(node: ast.AST, extra: frozenset[str]) -> bool:
    text = ast.unparse(node)
    root = re.match(r'[A-Za-z_][A-Za-z0-9_]*', text.lstrip('( '))
    return bool(re.search(r'\brequest\b', text)) or bool(root and (root.group(0) in BODY_NAMES or root.group(0) in extra))


def _key(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _callee(call: ast.Call, scope: dict[str, Any], imports: dict[str, str]) -> Any:
    name = call.func.id if isinstance(call.func, ast.Name) else None
    if name is None:
        return None
    if name in scope:
        return scope[name]
    if name in imports:
        try:
            return getattr(importlib.import_module(imports[name]), name)
        except (ImportError, AttributeError):
            return None
    return None


def _scan(func: Any, extra: frozenset[str], depth: int, seen: set[int]) -> set[str]:
    func = inspect.unwrap(func)
    if id(func) in seen or not callable(func):
        return set()
    seen.add(id(func))
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    except (OSError, TypeError, SyntaxError):
        return set()
    imports = {a.asname or a.name: n.module for n in ast.walk(tree)
               if isinstance(n, ast.ImportFrom) and n.module for a in n.names}
    scope = getattr(func, '__globals__', {})
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _READERS \
                and node.args and _is_request_like(node.func.value, extra):
            if (k := _key(node.args[0])) is not None:
                keys.add(k)
        elif isinstance(node, ast.Subscript) and _is_request_like(node.value, extra):
            if (k := _key(node.slice)) is not None:
                keys.add(k)
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.In) \
                and _is_request_like(node.comparators[0], extra):
            if (k := _key(node.left)) is not None:
                keys.add(k)
        elif isinstance(node, ast.Call) and depth < _MAX_DEPTH \
                and any(_is_request_like(a, extra) for a in node.args):
            target = _callee(node, scope, imports)
            if target is not None and inspect.isfunction(inspect.unwrap(target)):
                params = frozenset(inspect.signature(inspect.unwrap(target)).parameters)
                keys |= _scan(target, params, depth + 1, seen)
    return keys


def request_keys(view_function: Any) -> set[str]:
    """Keys the view function (and the helpers it hands the request to) reads."""
    return _scan(view_function, frozenset(), 0, set())
