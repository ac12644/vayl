"""Generate openapi/vayl-server.yaml and the API Reference tool pages from the live tool registry.

base.yaml holds the hand-written parts: /mcp, the probes, /metrics and the shared components. This
script adds GET /v1/tools and one POST /v1/tools/{name} operation per tool, built from the tool's MCP
input schema, the capability its `_guard` call requires, its MCP annotations, and the real example
in examples.json (captured by capture_examples.py). Run it after changing a tool:

    python openapi/generate.py            # rewrite the spec and docs/api-reference/tools/*.md
    python openapi/generate.py --check    # exit 1 if either is stale (CI runs this via the tests)
"""
import asyncio
import copy
import inspect
import json
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_OUT = os.path.join(ROOT, "openapi", "vayl-server.yaml")
PAGES_DIR = os.path.join(ROOT, "docs", "api-reference", "tools")
SPEC_URL = "https://raw.githubusercontent.com/ac12644/vayl/main/openapi/vayl-server.yaml"
TOOLS_DOCS = "https://vayl.gitbook.io/vayl-docs/documentation/reference/mcp-tools"

# The same groups as the Documentation space's MCP tools pages, in the same order.
GROUPS = {
    "Memory": ("memory", "Store, query, correct and retract facts, and read their history.",
               ["remember", "recall", "recall_related", "forget", "history", "list_memories",
                "get_memory", "update_memory"]),
    "Safety and approval": ("safety-and-approval",
                            "Check a fact before acting on it, and approve or reject gated changes.",
                            ["check_before_act", "safe_recall", "pending_changes", "confirm_change",
                             "reject_change", "set_reconcile_policy", "get_reconcile_policy"]),
    "Accountability": ("accountability",
                       "Signed decisions, attestations and receipts, and the tamper-evident audit log.",
                       ["record_decision", "explain_decision", "attest", "verify_receipt", "audit_log",
                        "verify_audit", "export_public_key"]),
    "Compliance": ("compliance", "Erasure, export and retention for GDPR, with signed receipts.",
                   ["delete", "delete_all", "export_memory", "purge_expired"]),
    "Administration": ("administration", "API keys and principals, the license, metrics and health checks.",
                       ["create_principal", "list_principals", "revoke_principal", "license_status",
                        "stats", "health"]),
}
CAPABILITY = re.compile(r'_guard\("(\w+)", \w+, cap=C\.([A-Z]+)')
ERRORS = {
    "401": "Unauthorized", "403": "Forbidden", "413": "TooLarge", "422": "InvalidArguments",
    "429": "RateLimited",
}


def _tools():
    os.environ.setdefault("VAYL_DB", ":memory:")
    from vayl.api import mcp_server
    return mcp_server, sorted(asyncio.run(mcp_server.mcp.list_tools()), key=lambda t: t.name)


def _capability(tool):
    m = CAPABILITY.search(inspect.getsource(tool.fn))
    if not m or m.group(1) != tool.name:
        raise SystemExit(f"can't find the _guard capability for {tool.name}")
    return m.group(2).lower()


def _hints(tool):
    a = tool.annotations
    out = []
    if a and a.read_only_hint:
        out.append("read-only")
    if a and a.destructive_hint:
        out.append("destructive: erases data")
    if a and a.idempotent_hint:
        out.append("idempotent")
    return out


def _operation(tool, group, example):
    cap = _capability(tool)
    hints = _hints(tool)
    title = tool.annotations.title if tool.annotations and tool.annotations.title else tool.name
    lines = [inspect.cleandoc(tool.description or ""), "",
             f"**Requires capability:** `{cap}`." + (f" **Behaviour:** {', '.join(hints)}." if hints else ""),
             f"The same tool over MCP is `tools/call` with `\"name\": \"{tool.name}\"`."]
    schema = copy.deepcopy(tool.parameters)
    schema.pop("title", None)
    op = {
        "tags": [group], "operationId": tool.name, "summary": title, "description": "\n".join(lines),
        "security": [{"apiKey": []}, {"oidc": []}],
        "parameters": [{"$ref": "#/components/parameters/RequestId"}],
        "requestBody": {"required": True, "content": {"application/json": {"schema": schema}}},
        "responses": {
            "200": {"description": "The tool ran. `result` is its text reply.",
                    "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ToolResult"}}}},
            **{code: {"$ref": f"#/components/responses/{name}"} for code, name in ERRORS.items()},
        },
    }
    if example:
        op["requestBody"]["content"]["application/json"]["example"] = example["request"]
        op["responses"]["200"]["content"]["application/json"]["example"] = example["response"]
    return op


def build():
    mcp_server, tools = _tools()
    by_name = {t.name: t for t in tools}
    grouped = [n for _g, (_s, _d, names) in GROUPS.items() for n in names]
    if sorted(grouped) != sorted(by_name):
        raise SystemExit(f"GROUPS doesn't match the tools: missing {set(by_name) - set(grouped)}, "
                         f"unknown {set(grouped) - set(by_name)}")
    with open(os.path.join(ROOT, "openapi", "base.yaml")) as f:
        spec = yaml.safe_load(f)
    with open(os.path.join(ROOT, "openapi", "examples.json")) as f:
        examples = json.load(f)

    import vayl
    spec["info"]["version"] = vayl.__version__
    spec["tags"] += [{"name": g, "description": d} for g, (_s, d, _n) in GROUPS.items()]
    spec["paths"]["/v1/tools"] = {"get": {
        "tags": ["Tools"], "operationId": "listTools", "summary": "List the tools",
        "description": "Every tool with its title, description and JSON Schema for its arguments: the same "
                       "list as MCP `tools/list`, sorted by name.",
        "security": [{"apiKey": []}, {"oidc": []}],
        "parameters": [{"$ref": "#/components/parameters/RequestId"}],
        "responses": {"200": {"description": "The tools.", "content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/ToolList"},
            "example": {"tools": [{"name": "remember", "title": by_name["remember"].annotations.title,
                                   "description": "Store fact(s) from a statement. …",
                                   "input_schema": {"type": "object", "required": ["text"],
                                                    "properties": {"text": {"type": "string"}}}}]}}}},
            "401": {"$ref": "#/components/responses/Unauthorized"}},
    }}
    for group, (_slug, _desc, names) in GROUPS.items():
        for name in names:
            spec["paths"][f"/v1/tools/{name}"] = {"post": _operation(by_name[name], group, examples.get(name))}
    return spec, _pages()


def _pages():
    pages = {}
    for group, (slug, desc, names) in GROUPS.items():
        blocks = "\n\n".join(
            f'{{% openapi-operation spec="vayl-server" path="/v1/tools/{n}" method="post" %}}\n'
            f"[OpenAPI vayl-server]({SPEC_URL})\n{{% endopenapi-operation %}}" for n in names)
        pages[f"{slug}.md"] = (
            f"---\ndescription: {desc}\nicon: code\n---\n\n"
            f"<!-- Generated by openapi/generate.py from the tool registry. Edit the tool, then rerun it. -->\n\n"
            f"# {group}\n\n{desc} Each tool is `POST /v1/tools/<name>` with its arguments as a JSON object. "
            f"For when to use each one, see [MCP tools]({TOOLS_DOCS}).\n\n{blocks}\n")
    return pages


def render(spec):
    head = ("# Generated by openapi/generate.py from openapi/base.yaml, the live tool registry and\n"
            "# openapi/examples.json. Don't edit by hand: change those, then rerun the script.\n")
    return head + yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=110)


def main():
    spec, pages = build()
    outputs = {SPEC_OUT: render(spec), **{os.path.join(PAGES_DIR, k): v for k, v in pages.items()}}
    stale = [p for p, text in outputs.items()
             if not os.path.exists(p) or open(p, encoding="utf-8").read() != text]
    if "--check" in sys.argv:
        if stale:
            sys.exit("stale, rerun `python openapi/generate.py`: " + ", ".join(os.path.relpath(p, ROOT) for p in stale))
        return
    os.makedirs(PAGES_DIR, exist_ok=True)
    for p in stale:
        with open(p, "w", encoding="utf-8") as f:
            f.write(outputs[p])
    print(f"wrote {len(stale)} file(s)")


if __name__ == "__main__":
    main()
