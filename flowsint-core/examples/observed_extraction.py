"""Fetch a controlled URL or replay an authorized saved source proof."""
from __future__ import annotations
import argparse, asyncio, json

class _NoGraph:
    def create_node_from_flowsint_type(self, **_kwargs): pass
    def create_relationship(self, **_kwargs): pass
    def log_graph_message(self, _message): pass
    def flush(self): pass

async def _live(url: str) -> dict:
    from flowsint_enrichers.website.to_text import WebsiteToText
    from flowsint_types.website import Website
    enricher = WebsiteToText(sketch_id="observed-extraction-example", params_schema=[], params={}, graph_service=_NoGraph())
    result = await enricher.execute_structured([Website(url=url)])
    return result.model_dump(mode="json")

async def _saved(args) -> dict:
    from flowsint_execution.extraction_runtime import resolve_and_extract_observations
    state, result = await resolve_and_extract_observations(
        args.proof, caller_id=args.caller, scope=args.scope, source_family=args.source_family,
        operation_id=args.operation, occurrence_id=args.occurrence, config_path=args.runtime_config,
    )
    payload = None
    if result is not None:
        from dataclasses import asdict
        payload = {"format_version": "observed-extraction/1.0", **asdict(result)}
    return {"state": state.value, "result": payload}

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--url", help="admitted HTTP(S) URL to fetch and retain")
    modes.add_argument("--proof", help="source-proof/1.0 value to replay")
    parser.add_argument("--runtime-config", help="reviewed artifact runtime JSON")
    parser.add_argument("--caller", default="website-to-text")
    parser.add_argument("--scope", default="local-web-fetch")
    parser.add_argument("--source-family", default="http")
    parser.add_argument("--operation")
    parser.add_argument("--occurrence")
    args = parser.parse_args()
    if args.proof and not all((args.runtime_config, args.operation, args.occurrence)):
        parser.error("saved replay requires --runtime-config, --operation, and --occurrence")
    output = asyncio.run(_live(args.url) if args.url else _saved(args))
    print(json.dumps(output, indent=2, default=lambda value: value.model_dump(mode="json")))

if __name__ == "__main__":
    main()
