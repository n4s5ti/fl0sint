"""Disposable independent runtime attack for the immutable DEF-42 archive."""

import asyncio
import json

from flowsint_enrichers.website import to_text as module
from flowsint_enrichers.website.to_text import WebsiteTextOccurrence, WebsiteToText
from flowsint_execution.models import OutcomeStatus, canonical_input_hash
from flowsint_types.phrase import Phrase
from flowsint_types.website import Website


class SilentLogger:
    info = staticmethod(lambda *_a, **_k: None)
    error = staticmethod(lambda *_a, **_k: None)
    completed = staticmethod(lambda *_a, **_k: None)


class RecordingGraph:
    def __init__(self):
        self.nodes = []
        self.relationships = []
        self.flushes = 0

    def create_node_from_flowsint_type(self, *, node_obj):
        self.nodes.append(node_obj)

    def create_relationship(self, *, from_obj, to_obj, rel_label):
        self.relationships.append((from_obj, to_obj, rel_label))

    def log_graph_message(self, _message):
        pass

    def flush(self):
        self.flushes += 1


class OneToMany(WebsiteToText):
    def _outputs_from_text(self, text):
        return (Phrase(text=f"{text}:1"), Phrase(text=f"{text}:2"))


def make(cls=WebsiteToText, **params):
    graph = RecordingGraph()
    value = cls(
        sketch_id="def42-independent",
        params_schema=[],
        params=params,
        graph_service=graph,
    )
    return value, graph


async def claim1_and_2():
    enricher, graph = make()

    async def fetch(url, **_kwargs):
        if "slow-a" in url:
            await asyncio.sleep(0.03)
            return "A"
        if "failed-a" in url:
            await asyncio.sleep(0.02)
            return None
        return "B"

    enricher._fetch_text_async = fetch
    slow, fast = Website(url="https://slow-a.invalid"), Website(url="https://fast-b.invalid")
    outputs = await enricher.execute([slow, fast])
    assert [x.text for x in outputs] == ["A", "B"]
    assert [(str(a.url), b.text) for a, b, _ in graph.relationships] == [
        (str(slow.url), "A"), (str(fast.url), "B")
    ]

    enricher2, graph2 = make()
    enricher2._fetch_text_async = fetch
    failed, good = Website(url="https://failed-a.invalid"), Website(url="https://fast-b.invalid")
    occurrences = await enricher2.scan([failed, good])
    assert all(isinstance(x, WebsiteTextOccurrence) for x in occurrences)
    assert [x.status for x in occurrences] == [OutcomeStatus.FAILURE, OutcomeStatus.SUCCESS]
    assert [x.text for x in enricher2.postprocess(occurrences)] == ["B"]
    assert [(str(a.url), b.text) for a, b, _ in graph2.relationships] == [(str(good.url), "B")]
    print("PASS claims-1-2 public execute and scan/postprocess")


async def claim3():
    enricher, graph = make(OneToMany)
    attempts = {}

    async def fetch(url, **_kwargs):
        attempts[url] = attempts.get(url, 0) + 1
        if "middle" in url:
            return None
        if "retry" in url and attempts[url] == 1:
            return None
        if "slow" in url:
            await asyncio.sleep(0.02)
        return "same"

    enricher._fetch_text_async = fetch
    values = [
        "https://duplicate.invalid", "https://middle.invalid",
        "https://duplicate.invalid", "https://slow.invalid",
    ]
    result = await enricher.execute_structured(values)
    assert [x.status for x in result.outcomes] == [
        OutcomeStatus.SUCCESS, OutcomeStatus.FAILURE,
        OutcomeStatus.SUCCESS, OutcomeStatus.SUCCESS,
    ]
    assert [x.input_ref for x in result.outcomes] == [canonical_input_hash(x) for x in values]
    assert [[o.text for o in x.outputs] for x in result.outcomes] == [
        ["same:1", "same:2"], [], ["same:1", "same:2"], ["same:1", "same:2"]
    ]
    assert len(graph.relationships) == 6

    first = await enricher.execute_structured(["https://retry.invalid"])
    second = await enricher.execute_structured(["https://retry.invalid"])
    assert first.outcomes[0].status is OutcomeStatus.FAILURE
    assert second.outcomes[0].status is OutcomeStatus.SUCCESS
    assert first.outcomes[0].input_ref == second.outcomes[0].input_ref
    print("PASS claim-3 duplicates/middle-failure/retry/one-to-many")


async def claim4():
    enricher, _graph = make(max_concurrency=1)
    entered = asyncio.Event()
    blocked = asyncio.Event()

    async def fetch(_url, **_kwargs):
        entered.set()
        await blocked.wait()

    enricher._fetch_text_async = fetch
    task = asyncio.create_task(enricher.execute_structured(["https://a.invalid", "https://b.invalid"]))
    await entered.wait()
    task.cancel()
    result = await task
    assert [x.status for x in result.outcomes] == [OutcomeStatus.HOLD, OutcomeStatus.HOLD]
    assert [x.diagnostic.code for x in result.outcomes] == ["cancelled", "cancelled"]
    print("PASS claim-4 parent cancellation retains HOLD outcomes")


async def claim5():
    enricher, graph = make()

    async def fetch(url, **_kwargs):
        return None if "bad" in url else "serialized"

    enricher._fetch_text_async = fetch
    result = await enricher.execute_structured(["https://good.invalid", "https://bad.invalid"])
    decoded = json.loads(result.model_dump_json())
    assert decoded["outcomes"][0]["outputs"][0]["text"] == "serialized"
    assert decoded["outcomes"][1]["diagnostic"]["code"] == "transport_failed"
    assert [(str(a.url), b.text) for a, b, _ in graph.relationships] == [
        ("https://good.invalid/", "serialized")
    ]
    print("PASS claim-5 graph capture and JSON serialization")


async def main():
    module.Logger = SilentLogger
    import flowsint_core.core.enricher_base as base
    base.Logger = SilentLogger
    await claim1_and_2()
    await claim3()
    await claim4()
    await claim5()
    print("ALL INDEPENDENT ATTACKS PASSED")


asyncio.run(main())
