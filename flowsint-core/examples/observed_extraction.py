"""Fetch a controlled URL or replay an authorized saved source proof."""
from __future__ import annotations
import argparse, asyncio, dataclasses, json

async def _live(url: str, runtime_config: str | None) -> dict:
    from flowsint_execution.extraction_runtime import execute_live_observed_extraction
    result = await execute_live_observed_extraction(url, config_path=runtime_config)
    return dataclasses.asdict(result)

async def _saved(args) -> dict:
    from flowsint_execution.extraction_runtime import resolve_and_extract_observations
    state, result = await resolve_and_extract_observations(
        args.proof, caller_id=args.caller, scope=args.scope, source_family=args.source_family,
        operation_id=args.operation, occurrence_id=args.occurrence, config_path=args.runtime_config,
    )
    payload = None
    if result is not None:
        from flowsint_execution.observed_extraction import serialize_observed_extraction_metadata
        payload = serialize_observed_extraction_metadata(result).model_dump(mode="json")
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
    output = asyncio.run(_live(args.url, args.runtime_config) if args.url else _saved(args))
    print(json.dumps(output, indent=2, default=lambda value: value.model_dump(mode="json") if hasattr(value, "model_dump") else value.value if hasattr(value, "value") else str(value)))

if __name__ == "__main__":
    main()
