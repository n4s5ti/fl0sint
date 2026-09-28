import asyncio
import hashlib
import os
import time

import httpx

os.environ.setdefault("AUTH_SECRET", "repro")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379")

from flowsint_execution.acquisition import AcquisitionRequest, InputOccurrence, Resources
from flowsint_execution.fetch import (
    FetchOutcome,
    FetchParameters,
    FetchResult,
    FetchStatus,
    TrustedFetchPolicy,
    admit_fetch,
    execute_fetch,
)
from flowsint_execution.models import canonical_input_hash
import flowsint_enrichers.website.to_text as website_module
from flowsint_enrichers.website.to_text import WebsiteToText
from flowsint_types.website import Website


DIGEST = "a" * 64
POLICY_DIGEST = "b" * 64


def operation(*, timeout=0.03):
    url = "https://example.test/a"
    request = AcquisitionRequest(
        operation_id="repro-op",
        caller_id="repro",
        scope="local",
        capability_digest=DIGEST,
        endpoint_policy_digest=POLICY_DIGEST,
        inputs=(InputOccurrence(occurrence_id="input-0", input_ref=canonical_input_hash(url), type_tag="http_url", value=url),),
        allocation=Resources(requests=1, bytes=100, elapsed_seconds=timeout, concurrency=1),
    )
    policy = TrustedFetchPolicy(
        caller_id="repro", scope="local", capability_digest=DIGEST,
        endpoint_policy_digest=POLICY_DIGEST,
        allowed_origins=("https://example.test:443",),
    )
    return admit_fetch(request, policy, FetchParameters())


async def cancellation_cleanup_is_unbounded():
    class CancellationResistantStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            if False:
                yield b""
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # A buggy/custom transport can delay or ignore cancellation.
                await asyncio.sleep(0.25)
                return

    async def handler(_request):
        return httpx.Response(200, stream=CancellationResistantStream())

    started = time.monotonic()
    result = await execute_fetch(operation(), transport=httpx.MockTransport(handler))
    elapsed = time.monotonic() - started
    print("deadline_seconds=0.03 elapsed_seconds=%.3f status=%s" % (elapsed, result.outcomes[0].status.value))
    assert elapsed > 0.20
    assert result.outcomes[0].status is FetchStatus.SUCCESS


async def admitted_operation_is_replayable():
    hits = 0

    async def handler(_request):
        nonlocal hits
        hits += 1
        return httpx.Response(200, content=b"ok")

    admitted = operation(timeout=1)
    transport = httpx.MockTransport(handler)
    first = await execute_fetch(admitted, transport=transport)
    second = await execute_fetch(admitted, transport=transport)
    print("replay_hits=%d first_requests=%d second_requests=%d" % (
        hits, first.actual_resources.requests, second.actual_resources.requests
    ))
    assert hits == 2


async def integrated_consumer_accepts_substituted_input_ref():
    async def substituted(admitted):
        item = admitted.inputs[0]
        outcome = FetchOutcome(
            occurrence_id=item.occurrence_id,
            input_ref="c" * 64,
            status=FetchStatus.SUCCESS,
            requested_origin="https://example.test:443",
            final_origin="https://example.test:443",
            text="substituted content",
            body=b"substituted content",
            actual_resources=Resources(requests=1, bytes=19, elapsed_seconds=0.01),
        )
        return FetchResult(operation_id="wrong-operation", outcomes=(outcome,), actual_resources=outcome.actual_resources)

    original = website_module.execute_fetch
    website_module.execute_fetch = substituted
    try:
        enricher = WebsiteToText(params_schema=[], params={}, graph_service=object())
        occurrence = (await enricher._scan_occurrences([Website(url="https://example.test/a")]))[0]
    finally:
        website_module.execute_fetch = original
    print("substituted_input_ref_status=%s text=%s" % (occurrence.status.value, occurrence.outputs[0].text))
    assert occurrence.outputs[0].text == "substituted content"


async def main():
    await cancellation_cleanup_is_unbounded()
    await admitted_operation_is_replayable()
    await integrated_consumer_accepts_substituted_input_ref()


asyncio.run(main())
