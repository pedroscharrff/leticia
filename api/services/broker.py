"""
Webhook broker — transform engine.

Translates arbitrary external payloads to a canonical internal representation
(and back), driven by data-only mappings stored in the database. No code change
required to onboard a new external system: just create a mapping row.

Source expression syntax (used in field_map values):

  $.path.to.value          read by JSONPath (dot + [idx])
  $.items[0].name          array index
  $.items[*].sku           array projection (returns a list)
  =literal text            literal string (= prefix)
  ={{$.first}} {{$.last}}  Jinja-lite template with $.expr placeholders

Canonical field keys use dot-paths to build nested output:
  {"customer.phone": "$.from", "customer.name": "$.profile.name"}
  →  {"customer": {"phone": "...", "name": "..."}}

Match rules: a dict of {jsonpath: expected_value}. The mapping is applied
only if every entry matches (literal equality, after coercing to str).
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import re
from typing import Any

_PATH_TOKEN = re.compile(r"\.|\[([^\]]+)\]")
_TEMPLATE_EXPR = re.compile(r"\{\{\s*(\$[^}]+?)\s*\}\}")


# ── JSONPath resolver ────────────────────────────────────────────────────────

def _parse_path(expr: str) -> list[str | int | tuple]:
    """
    Tokenize "$.items[0].sku" → ["items", 0, "sku"]
              "$.items[*].sku" → ["items", ("*",), "sku"]
    """
    if not expr.startswith("$"):
        raise ValueError(f"path must start with $: {expr}")
    body = expr[1:]
    if body.startswith("."):
        body = body[1:]
    if not body:
        return []
    tokens: list[str | int | tuple] = []
    # Split by . but keep [...] segments
    parts = re.split(r"\.(?![^\[]*\])", body)
    for part in parts:
        if not part:
            continue
        # part may be "items[0]" or "items[*]" or "items"
        m = re.match(r"^([^\[]+)?(.*)$", part)
        name, rest = m.group(1), m.group(2)
        if name:
            tokens.append(name)
        for bracket in re.findall(r"\[([^\]]+)\]", rest):
            if bracket == "*":
                tokens.append(("*",))
            else:
                try:
                    tokens.append(int(bracket))
                except ValueError:
                    tokens.append(bracket.strip("'\""))
    return tokens


def resolve_path(data: Any, expr: str) -> Any:
    """Resolve a JSONPath expression against `data`. Returns None if missing."""
    try:
        tokens = _parse_path(expr)
    except ValueError:
        return None
    current: Any = data
    for tok in tokens:
        if current is None:
            return None
        if isinstance(tok, tuple) and tok[0] == "*":
            if not isinstance(current, list):
                return None
            current = list(current)
            continue
        if isinstance(current, list):
            # If we still have a list (after projection) apply token to each
            current = [_step(item, tok) for item in current]
        else:
            current = _step(current, tok)
    return current


def _step(item: Any, tok: str | int) -> Any:
    if item is None:
        return None
    if isinstance(tok, int):
        if isinstance(item, list) and -len(item) <= tok < len(item):
            return item[tok]
        return None
    if isinstance(item, dict):
        return item.get(tok)
    return None


# ── Expression evaluator ─────────────────────────────────────────────────────

def evaluate(expr: Any, payload: Any) -> Any:
    """
    Evaluate one field_map value.
      - non-string → returned as-is (literal)
      - "$..."     → JSONPath read
      - "=tmpl"    → template (Jinja-lite) or literal
      - other str  → literal string
    """
    if not isinstance(expr, str):
        return expr
    if expr.startswith("$"):
        return resolve_path(payload, expr)
    if expr.startswith("="):
        body = expr[1:]
        if "{{" not in body:
            return body
        def _sub(m: re.Match) -> str:
            val = resolve_path(payload, m.group(1))
            return "" if val is None else str(val)
        return _TEMPLATE_EXPR.sub(_sub, body)
    return expr


# ── Dotted-key nesting ───────────────────────────────────────────────────────

def _assign_nested(out: dict, dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    cur = out
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


# ── Public API ───────────────────────────────────────────────────────────────

def apply_mapping(field_map: dict[str, Any], payload: Any) -> dict[str, Any]:
    """Build a canonical dict by applying every entry in field_map."""
    out: dict[str, Any] = {}
    for canonical_key, source_expr in (field_map or {}).items():
        value = evaluate(source_expr, payload)
        _assign_nested(out, canonical_key, value)
    return out


def matches(match_rules: dict[str, Any], payload: Any) -> bool:
    """All entries must equal their resolved value (string-compared)."""
    if not match_rules:
        return True
    for path, expected in match_rules.items():
        actual = resolve_path(payload, path)
        if str(actual) != str(expected):
            return False
    return True


def pick_mapping(
    mappings: list[dict[str, Any]],
    payload: Any,
) -> dict[str, Any] | None:
    """First mapping (in given order) whose match_rules satisfy payload."""
    for m in mappings:
        if not m.get("enabled", True):
            continue
        rules = m.get("match_rules") or {}
        if matches(rules, payload):
            return m
    return None


# ── HMAC verification ────────────────────────────────────────────────────────

def verify_hmac(
    secret: str,
    algorithm: str,
    expected_header: str | None,
    raw_body: bytes,
) -> bool:
    if not secret or not expected_header:
        return False
    algo = (algorithm or "sha256").lower()
    if algo not in {"sha1", "sha256", "sha512"}:
        return False
    digest = _hmac.new(secret.encode(), raw_body, getattr(hashlib, algo)).hexdigest()
    # Also accept base64 (Shopify-style)
    import base64
    b64 = base64.b64encode(
        _hmac.new(secret.encode(), raw_body, getattr(hashlib, algo)).digest()
    ).decode()
    return _hmac.compare_digest(digest, expected_header) or \
           _hmac.compare_digest(b64, expected_header)


def idempotency_hash(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


# ── Field discovery (for the visual mapper) ──────────────────────────────────

def discover_paths(payload: Any, prefix: str = "$", max_depth: int = 6) -> list[dict]:
    """
    Walk a sample payload and emit a flat list of {path, type, sample} entries
    for the visual mapper. Lists are reported as both [0] and [*] forms.
    """
    out: list[dict] = []

    def _walk(node: Any, path: str, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                child_path = f"{path}.{k}"
                out.append({
                    "path": child_path,
                    "type": _type_of(v),
                    "sample": _short(v),
                })
                _walk(v, child_path, depth + 1)
        elif isinstance(node, list) and node:
            sample = node[0]
            out.append({
                "path": f"{path}[0]",
                "type": _type_of(sample),
                "sample": _short(sample),
            })
            out.append({
                "path": f"{path}[*]",
                "type": f"list<{_type_of(sample)}>",
                "sample": f"{len(node)} items",
            })
            _walk(sample, f"{path}[0]", depth + 1)

    _walk(payload, prefix, 0)
    return out


def _type_of(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def _short(v: Any) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= 80 else s[:77] + "..."
