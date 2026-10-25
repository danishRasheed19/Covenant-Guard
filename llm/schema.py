"""Turn a Pydantic model / JSON Schema into the smaller schema dialect Gemini accepts.

Gemini's structured output understands only part of JSON Schema. This converter keeps what it
understands (types, properties, required, items, enum, nullable, description) and drops the rest
(titles, defaults, formats, numeric limits ...). That is safe because the schema only *guides* the
model; the answer is validated again with Pydantic, which stays the authority.
"""

_KEEP_TEXT = ("description",)


def to_gemini_schema(schema) -> dict:
    """`schema` is a Pydantic model class or a JSON-Schema dict."""
    if hasattr(schema, "model_json_schema"):
        schema = schema.model_json_schema()
    defs = {**schema.get("definitions", {}), **schema.get("$defs", {})}

    def convert(node: dict, seen: tuple) -> dict:
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            if name in seen:
                raise ValueError(f"recursive schema '{name}' is not supported")
            if name not in defs:
                raise ValueError(f"unknown schema reference '{name}'")
            out = convert(defs[name], seen + (name,))
            for key in _KEEP_TEXT:
                if key in node:
                    out[key] = node[key]
            return out
        if "allOf" in node and len(node["allOf"]) == 1:
            out = convert(node["allOf"][0], seen)
            for key in _KEEP_TEXT:
                if key in node:
                    out[key] = node[key]
            return out
        if "anyOf" in node:
            options = node["anyOf"]
            non_null = [o for o in options if o.get("type") != "null"]
            if len(non_null) != 1:
                raise ValueError("unions of several types are not supported; use one type or Optional[...]")
            out = convert(non_null[0], seen)
            if len(non_null) < len(options):
                out["nullable"] = True
            for key in _KEEP_TEXT:
                if key in node:
                    out[key] = node[key]
            return out

        out = {}
        kind = node.get("type")
        if isinstance(kind, list):                       # ["string", "null"]
            non_null = [k for k in kind if k != "null"]
            if len(non_null) != 1:
                raise ValueError("unions of several types are not supported")
            if len(non_null) < len(kind):
                out["nullable"] = True
            kind = non_null[0]
        if kind:
            out["type"] = kind.upper()
        if "enum" in node:
            out["enum"] = list(node["enum"])
        elif "const" in node:
            out["enum"] = [node["const"]]
        for key in _KEEP_TEXT:
            if key in node:
                out[key] = node[key]
        if "properties" in node:
            out["properties"] = {name: convert(sub, seen) for name, sub in node["properties"].items()}
            if node.get("required"):
                out["required"] = list(node["required"])
        if "items" in node:
            out["items"] = convert(node["items"], seen)
        return out

    result = convert(schema, ())
    if result.get("type") != "OBJECT":
        raise ValueError("the top-level schema must be an object")
    return result
