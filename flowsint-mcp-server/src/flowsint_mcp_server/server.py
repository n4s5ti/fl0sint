"""
FlowSint MCP Server
===================
Wraps FlowSint REST API endpoints as MCP tools so mcporter can generate a CLI.

Usage:
    FLOWSINT_API_URL=http://localhost:5001 uv run flowsint-mcp

Each tool accepts an optional ``api_token`` parameter for JWT auth.
Use the ``login`` tool first to obtain one.
"""

import json
import shutil
import csv
import os
import sys
import importlib
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
try:
    import cookiecutter
except Exception:
    cookiecutter = None

API_URL = os.getenv("FLOWSINT_API_URL", "http://127.0.0.1:5001").rstrip("/")

mcp = FastMCP(
    "flowsint",
    instructions="FlowSint CLI — graph intelligence and OSINT investigation platform. "
    "Use the login tool first to authenticate, then explore investigations, "
    "sketches, enrichers, flows, and analyses.",
)


def _supports_http2() -> bool:
    try:
        import inspect

        request_signature = inspect.signature(httpx.request)
        if "http2" not in request_signature.parameters:
            return False
        import h2  # type: ignore
        return True
    except Exception:
        return False



def _req(method, path, *, api_token="", json_body=None, params=None):
    headers = {"Accept": "application/json"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    url = f"{API_URL}{path}"
    kwargs = {
        "headers": headers,
        "json": json_body,
        "params": params,
        "timeout": 30,
    }
    if _supports_http2():
        kwargs["http2"] = True
    resp = httpx.request(method, url, **kwargs)
    if resp.status_code >= 400:
        return {"error": f"HTTP {resp.status_code}", "detail": resp.text[:500]}
    if not resp.text.strip():
        return {"ok": True}
    ct = resp.headers.get("content-type", "")
    if "application/json" in ct:
        return resp.json()
    return {"raw": resp.text}


def _parse_csv_list(raw: str) -> list[str]:
    """Parse comma-separated values while preserving empty input handling."""
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_csv_nodes(raw_csv: str) -> list[str]:
    """Parse node IDs from a CSV file and return normalized values."""
    if not raw_csv:
        return []

    node_ids: list[str] = []
    try:
        with open(raw_csv, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise ValueError(f"Failed to read node IDs from '{raw_csv}': {exc}")

    if not rows:
        return []

    first_row = [value.strip() for value in rows[0]]
    start_index = 0
    has_more_rows = len(rows) > 1
    if (
        has_more_rows
        and any(first_row)
        and not first_row[0].startswith("#")
        and len([value for value in first_row if value]) <= 1
        and first_row[0].lower().replace("-", "").replace("_", "") in {
            "nodeid",
            "nodeids",
            "id",
            "ids",
            "nodeidentifier",
            "nodeidentifiervalue",
            "nodevalue",
        }
    ):
        start_index = 1

    for row_num, row in enumerate(rows[start_index:], start=start_index + 1):
        values = [value.strip() for value in row]
        if not any(values):
            continue
        extra_values = [value for value in values[1:] if value]
        if extra_values:
            raise ValueError(
                f"CSV row {row_num} has extra columns; expected one node ID per row"
            )
        node_id = values[0]
        if not node_id or node_id.lower().replace("-", "").replace("_", "") in {
            "nodeid",
            "nodeids",
            "id",
            "ids",
            "nodeidentifier",
            "nodeidentifiervalue",
            "nodevalue",
        }:
            continue
        node_ids.append(node_id)

    return node_ids


def _resolve_node_ids(node_ids: str, node_ids_csv: str) -> tuple[list[str], str | None]:
    ids = _parse_csv_list(node_ids)
    if not node_ids_csv:
        return ids, None

    try:
        csv_ids = _parse_csv_nodes(node_ids_csv)
    except ValueError as exc:
        return [], str(exc)

    return ids + csv_ids, None


def _as_bool(text: str, default: bool = False) -> bool:
    return text.strip().lower() in {"1", "true", "yes", "on"} if text else default


def _load_launch_params_json(raw: str) -> dict[str, Any]:
    if not raw:
        return {}

    try:
        params = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid launch params JSON: {exc}")

    if not isinstance(params, dict):
        raise ValueError("launch_params_json must decode to a JSON object")
    return params


def _coalesce_optional_bool(cli: bool | None, env_name: str, default: bool = False) -> bool:
    if cli is not None:
        return cli
    return _as_bool(os.getenv(env_name), default)


def _build_launch_params(
    enable_http2: bool | None,
    enable_quic: bool | None,
    enable_gpu: bool | None,
    gpu_provider: str,
    max_concurrency: int | None,
    request_timeout: int | None,
    launch_params_json: str = "",
) -> dict[str, Any]:
    params: dict[str, Any] = {}

    if launch_params_json:
        params.update(_load_launch_params_json(launch_params_json))

    http2_enabled = _coalesce_optional_bool(enable_http2, "FLOWSINT_MCP_ENABLE_HTTP2", False)
    quic_enabled = _coalesce_optional_bool(enable_quic, "FLOWSINT_MCP_ENABLE_QUIC", False)
    gpu_enabled = _coalesce_optional_bool(enable_gpu, "FLOWSINT_MCP_ENABLE_GPU", False)

    if http2_enabled:
        params["enable_http2"] = True
    if quic_enabled:
        params["enable_quic"] = True
    if gpu_enabled:
        params["enable_gpu"] = True

    if gpu_provider:
        params["gpu_provider"] = gpu_provider.strip().lower()

    if max_concurrency and max_concurrency > 0:
        params["max_concurrency"] = max_concurrency

    if request_timeout and request_timeout > 0:
        params["request_timeout"] = request_timeout

    return params


def _launch_single_enricher(name: str, ids: list[str], sketch_id: str, api_token: str = ""):
    return _launch_single_enricher_with_params(name, ids, sketch_id, api_token, {})


def _launch_single_enricher_with_params(
    enricher_name: str,
    ids: list[str],
    sketch_id: str,
    api_token: str,
    params: dict[str, Any],
):
    return _req(
        "POST",
        f"/api/enrichers/{enricher_name}/launch",
        api_token=api_token,
        json_body={"node_ids": ids, "sketch_id": sketch_id, **({"params": params} if params else {})},
    )


def _load_template_context(template_context: str) -> dict[str, Any]:
    if not template_context:
        return {}
    try:
        context = json.loads(template_context)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid template_context JSON: {exc}")
    if not isinstance(context, dict):
        raise ValueError("template_context must be a JSON object")
    return context


def _load_json_payload(raw: str) -> dict[str, Any]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Template content must be a JSON object")
    return payload


def _load_yaml_payload(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required to read .yaml/.yml enricher templates. "
            "Install with `uv pip install PyYAML`."
        ) from exc

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Template content must be a YAML object")
    return payload


def _find_rendered_template_file(root: Path) -> Path:
    candidates = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".json", ".yaml", ".yml"}
        and path.name.lower() != "cookiecutter.json"
    )
    if not candidates:
        raise FileNotFoundError(
            f"Cookiecutter template output contains no JSON/YAML file in {root}"
        )

    for name in ("template.json", "template.yaml", "template.yml"):
        matched = [path for path in candidates if path.name == name]
        if matched:
            return sorted(matched)[0]
    return candidates[0]


def _load_template_payload_file(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        return _load_json_payload(path.read_text(encoding="utf-8"))
    return _load_yaml_payload(path)


def _render_template(template_path: str, template_context: str) -> dict[str, Any]:
    path = Path(template_path)
    if not path.exists():
        raise FileNotFoundError(f"template_path does not exist: {template_path}")
    if not path.is_dir():
        raise ValueError(
            "template_path must be a cookiecutter template directory. "
            "Pass the template root that contains cookiecutter.json."
        )

    context = _load_template_context(template_context)

    if cookiecutter is None:
        raise RuntimeError(
            "cookiecutter is required for template mode. "
            "Install with `uv pip install cookiecutter`."
        )

    cookiecutter_fn = None
    if cookiecutter is not None:
        cookiecutter_fn = getattr(cookiecutter, "cookiecutter", None)
    if cookiecutter_fn is None:
        cookiecutter_main = sys.modules.get("cookiecutter.main")
        if cookiecutter_main is not None:
            cookiecutter_fn = getattr(cookiecutter_main, "cookiecutter", None)

    if cookiecutter_fn is None:
        try:
            cookiecutter_main = importlib.import_module("cookiecutter.main")
            cookiecutter_fn = getattr(cookiecutter_main, "cookiecutter", None)
        except Exception as exc:
            raise RuntimeError(
                "cookiecutter is required for template mode. "
                "Install with `uv pip install cookiecutter`."
            ) from exc

    if cookiecutter_fn is None:
        raise RuntimeError(
            "cookiecutter is required for template mode. "
            "Install with `uv pip install cookiecutter`."
        )

    output_dir = Path(template_path) / "_render_output"
    try:
        rendered_root = Path(
            cookiecutter_fn(
                str(path),
                no_input=True,
                extra_context=context,
                output_dir=str(output_dir),
            )
        )
        payload_path = _find_rendered_template_file(rendered_root)
        return _load_template_payload_file(payload_path)
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def _build_template_create_body(
    name: str,
    category: str,
    description: str,
    version: float,
    is_public: bool,
    payload_content: dict[str, Any],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "category": category,
        "version": version,
        "content": payload_content,
        "is_public": is_public,
    }
    if description:
        body["description"] = description
    return body


@mcp.tool(description="Authenticate with the FlowSint API and receive a JWT access token.")
def login(email: str, password: str) -> str:
    data = {"username": email, "password": password}
    resp = httpx.post(f"{API_URL}/api/auth/token", data=data, timeout=15)
    if resp.status_code >= 400:
        return f"Login failed: {resp.status_code} — {resp.text[:500]}"
    payload = resp.json()
    token = payload.get("access_token", "")
    if not token:
        return f"Unexpected response: {json.dumps(payload)}"
    return f"Login successful. Token:\n{token}"


@mcp.tool(description="Check whether the FlowSint API is reachable.")
def health() -> str:
    try:
        resp = httpx.get(f"{API_URL}/health", timeout=10)
        return f"API reachable (HTTP {resp.status_code}): {resp.json()}"
    except httpx.RequestError as exc:
        return f"API unreachable: {exc}"


@mcp.tool(description="List all available enrichers, optionally filtered by category.")
def list_enrichers(category: str = "", api_token: str = "") -> str:
    params = {"category": category} if category else None
    return json.dumps(_req("GET", "/api/enrichers", api_token=api_token, params=params), indent=2)


@mcp.tool(description="Create a new enricher template (composite enricher). "
    "Provide `content` as a JSON string, or use `template_path` + `template_context` "
    "to render from a cookiecutter template directory.")
def create_enricher_template(
    name: str,
    category: str,
    content: str = "",
    api_token: str = "",
    description: str = "",
    version: float = 1.0,
    is_public: bool = False,
    template_path: str = "",
    template_context: str = "",
) -> str:
    try:
        if template_path:
            if content:
                return (
                    "Provide either `content` (legacy JSON mode) or "
                    "`template_path` (cookiecutter mode), not both."
                )
            payload_content = _render_template(template_path, template_context)
        else:
            if not content:
                return "content is required unless template_path is provided"
            payload_content = _load_json_payload(content)
        body = _build_template_create_body(
            name=name,
            category=category,
            description=description,
            version=version,
            is_public=is_public,
            payload_content=payload_content,
        )
    except json.JSONDecodeError as exc:
        return f"Invalid content JSON: {exc}"
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        return str(exc)

    return json.dumps(
        _req("POST", "/api/enrichers/templates", api_token=api_token, json_body=body),
        indent=2,
    )


@mcp.tool(description="Launch an enricher against specified nodes in a sketch.")
def launch_enricher(
    enricher_name: str,
    node_ids: str = "",
    sketch_id: str = "",
    node_ids_csv: str = "",
    enable_http2: bool | None = None,
    enable_quic: bool | None = None,
    enable_gpu: bool | None = None,
    gpu_provider: str = "",
    max_concurrency: int | None = None,
    request_timeout: int | None = None,
    launch_params_json: str = "",
    api_token: str = "",
) -> str:
    if not sketch_id:
        return json.dumps({"error": "sketch_id is required"}, indent=2)
    ids, error = _resolve_node_ids(node_ids, node_ids_csv)
    if error:
        return json.dumps({"error": error}, indent=2)
    try:
        launch_params = _build_launch_params(
            enable_http2=enable_http2,
            enable_quic=enable_quic,
            enable_gpu=enable_gpu,
            gpu_provider=gpu_provider,
            max_concurrency=max_concurrency,
            request_timeout=request_timeout,
            launch_params_json=launch_params_json,
        )
    except ValueError as exc:
        return str(exc)

    return json.dumps(
        _launch_single_enricher_with_params(
            enricher_name=enricher_name,
            ids=ids,
            sketch_id=sketch_id,
            api_token=api_token,
            params=launch_params,
        ),
        indent=2,
    )


@mcp.tool(description="Launch one or more enrichers (comma-separated) against specified nodes in a sketch.")
def launch_enrichers(
    enricher_names: str,
    node_ids: str = "",
    sketch_id: str = "",
    node_ids_csv: str = "",
    enable_http2: bool | None = None,
    enable_quic: bool | None = None,
    enable_gpu: bool | None = None,
    gpu_provider: str = "",
    max_concurrency: int | None = None,
    request_timeout: int | None = None,
    launch_params_json: str = "",
    api_token: str = "",
) -> str:
    names = _parse_csv_list(enricher_names)
    if not names:
        return json.dumps({"error": "enricher_names is required"}, indent=2)
    if not sketch_id:
        return json.dumps({"error": "sketch_id is required"}, indent=2)

    ids, error = _resolve_node_ids(node_ids, node_ids_csv)
    if error:
        return json.dumps({"error": error}, indent=2)

    try:
        launch_params = _build_launch_params(
            enable_http2=enable_http2,
            enable_quic=enable_quic,
            enable_gpu=enable_gpu,
            gpu_provider=gpu_provider,
            max_concurrency=max_concurrency,
            request_timeout=request_timeout,
            launch_params_json=launch_params_json,
        )
    except ValueError as exc:
        return str(exc)

    results = []
    for name in names:
        result = _launch_single_enricher_with_params(
            enricher_name=name,
            ids=ids,
            sketch_id=sketch_id,
            api_token=api_token,
            params=launch_params,
        )
        results.append({"enricher_name": name, "result": result})

    return json.dumps({"results": results}, indent=2)


@mcp.tool(description="List all flows accessible to your account.")
def list_flows(category: str = "", api_token: str = "") -> str:
    params = {"category": category} if category else None
    return json.dumps(_req("GET", "/api/flows", api_token=api_token, params=params), indent=2)


@mcp.tool(description="Get a single flow by its ID.")
def get_flow(flow_id: str, api_token: str = "") -> str:
    return json.dumps(_req("GET", f"/api/flows/{flow_id}", api_token=api_token), indent=2)


@mcp.tool(description="Create a new flow from a JSON definition.")
def create_flow(flow_json: str, api_token: str = "") -> str:
    try:
        body = json.loads(flow_json)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"
    return json.dumps(_req("POST", "/api/flows/create", api_token=api_token, json_body=body), indent=2)


@mcp.tool(description="Launch a flow against specified nodes in a sketch.")
def launch_flow(flow_id: str, node_ids: str, sketch_id: str, api_token: str = "") -> str:
    ids = [n.strip() for n in node_ids.split(",") if n.strip()]
    return json.dumps(_req("POST", f"/api/flows/{flow_id}/launch",
        api_token=api_token, json_body={"node_ids": ids, "sketch_id": sketch_id}), indent=2)


@mcp.tool(description="List all investigations you have access to.")
def list_investigations(api_token: str = "") -> str:
    return json.dumps(_req("GET", "/api/investigations", api_token=api_token), indent=2)


@mcp.tool(description="Get a single investigation by ID.")
def get_investigation(investigation_id: str, api_token: str = "") -> str:
    return json.dumps(_req("GET", f"/api/investigations/{investigation_id}", api_token=api_token), indent=2)


@mcp.tool(description="Create a new investigation.")
def create_investigation(title: str, description: str = "", api_token: str = "") -> str:
    # API contract (InvestigationCreate) requires `name` and `description`.
    body = {"name": title, "description": description or title}
    return json.dumps(_req("POST", "/api/investigations/create", api_token=api_token, json_body=body), indent=2)


@mcp.tool(description="List sketches for an investigation.")
def list_sketches(investigation_id: str, api_token: str = "") -> str:
    return json.dumps(_req("GET", f"/api/investigations/{investigation_id}/sketches", api_token=api_token), indent=2)


@mcp.tool(description="Get a sketch by ID.")
def get_sketch(sketch_id: str, api_token: str = "") -> str:
    return json.dumps(_req("GET", f"/api/sketches/{sketch_id}", api_token=api_token), indent=2)


@mcp.tool(description="Get the full graph (nodes + edges) for a sketch.")
def get_sketch_graph(sketch_id: str, api_token: str = "") -> str:
    return json.dumps(_req("GET", f"/api/sketches/{sketch_id}/graph", api_token=api_token), indent=2)


@mcp.tool(description="Create a new sketch within an investigation.")
def create_sketch(investigation_id: str, title: str, description: str = "", api_token: str = "") -> str:
    # API contract (SketchCreate) requires title, description, investigation_id.
    body = {"title": title, "investigation_id": investigation_id, "description": description or title}
    return json.dumps(_req("POST", "/api/sketches/create", api_token=api_token, json_body=body), indent=2)


@mcp.tool(description="List analyses accessible to you.")
def list_analyses(api_token: str = "") -> str:
    return json.dumps(_req("GET", "/api/analyses", api_token=api_token), indent=2)


@mcp.tool(description="Get a single analysis by ID.")
def get_analysis(analysis_id: str, api_token: str = "") -> str:
    return json.dumps(_req("GET", f"/api/analyses/{analysis_id}", api_token=api_token), indent=2)


@mcp.tool(description="List analyses for a specific investigation.")
def list_investigation_analyses(investigation_id: str, api_token: str = "") -> str:
    return json.dumps(_req("GET", f"/api/analyses/investigation/{investigation_id}", api_token=api_token), indent=2)


@mcp.tool(description="List all available entity types in the system.")
def list_types(api_token: str = "") -> str:
    return json.dumps(_req("GET", "/api/types", api_token=api_token), indent=2)


@mcp.tool(description="List custom types you have created.")
def list_custom_types(api_token: str = "") -> str:
    return json.dumps(_req("GET", "/api/custom-types", api_token=api_token), indent=2)


@mcp.tool(description="List enricher templates (user-defined composite enrichers).")
def list_enricher_templates(api_token: str = "") -> str:
    return json.dumps(_req("GET", "/api/enrichers/templates", api_token=api_token), indent=2)


def main():
    try:
        httpx.get(f"{API_URL}/health", timeout=5)
    except httpx.RequestError as exc:
        print(f"WARNING: Cannot reach FlowSint API at {API_URL}: {exc}", file=sys.stderr)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()



