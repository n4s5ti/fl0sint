"""Forensic scope fences around legacy service and enricher entrypoints."""

from __future__ import annotations

import pytest
from celery import Task


from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.forensics import (
    LegacyExecutionFenced,
    dispatch_legacy_task,
    forensic_case_scope,
)
from flowsint_core.core.services import investigation_service as investigation_module
from flowsint_core.core.services.investigation_service import InvestigationService
from flowsint_core.core.services.flow_service import FlowService
from flowsint_core.core.services.sketch_service import SketchService
from flowsint_core.tasks import flow as flow_tasks
from flowsint_core.tasks import enricher as enricher_tasks
from flowsint_core.tasks.enricher import run_enricher
from flowsint_core.tasks.flow import run_flow


class InstrumentedRepository:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_by_id(self, *_: object) -> object:
        self.calls.append("get_by_id")
        return object()

    def add(self, _: object) -> None:
        self.calls.append("add")


class InstrumentedGraphService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def flush(self) -> None:
        self.calls.append("flush")


class LegacyTestEnricher(Enricher):
    @classmethod
    def name(cls) -> str:
        return "legacy-test"

    @classmethod
    def category(cls) -> str:
        return "test"

    @classmethod
    def key(cls) -> str:
        return "value"

    async def scan(self, values: list[str]) -> list[dict[str, str]]:
        return []


def test_investigation_service_is_fenced_before_permission_repository_or_graph_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Database:
        def __init__(self) -> None:
            self.commits = 0

        def commit(self) -> None:
            self.commits += 1

    class InvestigationRepository:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_accessible_by_user(self, *_: object) -> list[str]:
            self.calls.append("get_accessible_by_user")
            return ["legacy"]

        def add(self, _: object) -> None:
            self.calls.append("add")

        def add_user_role(self, _: object) -> None:
            self.calls.append("add_user_role")

    db = Database()
    investigation_repo = InvestigationRepository()
    service = InvestigationService(
        db=db,  # type: ignore[arg-type]
        investigation_repo=investigation_repo,  # type: ignore[arg-type]
        sketch_repo=InstrumentedRepository(),  # type: ignore[arg-type]
        analysis_repo=InstrumentedRepository(),  # type: ignore[arg-type]
        profile_repo=InstrumentedRepository(),  # type: ignore[arg-type]
    )
    permission_checked = False
    graph_constructed = False

    def record_permission(*_: object, **__: object) -> None:
        nonlocal permission_checked
        permission_checked = True

    def record_graph_factory(*_: object, **__: object) -> object:
        nonlocal graph_constructed
        graph_constructed = True
        return object()

    monkeypatch.setattr(service, "_check_permission", record_permission)
    monkeypatch.setattr(
        investigation_module, "create_graph_service", record_graph_factory
    )

    with forensic_case_scope("case-investigation"):
        with pytest.raises(LegacyExecutionFenced) as read_error:
            service.get_accessible_investigations(object())  # type: ignore[arg-type]
        with pytest.raises(LegacyExecutionFenced) as create_error:
            service.create("name", None, object())  # type: ignore[arg-type]
        with pytest.raises(LegacyExecutionFenced) as delete_error:
            service.delete(object(), object())  # type: ignore[arg-type]

    assert read_error.value.decision.operation == (
        "investigation_service_get_accessible_investigations"
    )
    assert create_error.value.decision.operation == "investigation_service_create"
    assert delete_error.value.decision.operation == "investigation_service_delete"
    assert investigation_repo.calls == []
    assert permission_checked is False
    assert graph_constructed is False
    assert db.commits == 0
    assert service.get_accessible_investigations(object()) == ["legacy"]  # type: ignore[arg-type]


def test_flow_service_read_and_mutation_are_fenced_before_repository_calls() -> None:
    flow_repo = InstrumentedRepository()
    service = FlowService(
        db=None,  # type: ignore[arg-type]
        flow_repo=flow_repo,  # type: ignore[arg-type]
        custom_type_repo=InstrumentedRepository(),  # type: ignore[arg-type]
        sketch_repo=InstrumentedRepository(),  # type: ignore[arg-type]
        investigation_repo=InstrumentedRepository(),  # type: ignore[arg-type]
    )

    with forensic_case_scope("case-flow"):
        with pytest.raises(LegacyExecutionFenced) as read_error:
            service.get_by_id(object())  # type: ignore[arg-type]
        with pytest.raises(LegacyExecutionFenced) as write_error:
            service.create("name", None, [], {})

    assert read_error.value.decision.operation == "flow_service_get_by_id"
    assert write_error.value.decision.operation == "flow_service_create"
    assert flow_repo.calls == []


def test_sketch_graph_mutation_is_fenced_before_repository_or_graph_calls() -> None:
    sketch_repo = InstrumentedRepository()
    service = SketchService(
        db=None,  # type: ignore[arg-type]
        sketch_repo=sketch_repo,  # type: ignore[arg-type]
        investigation_repo=InstrumentedRepository(),  # type: ignore[arg-type]
    )

    with forensic_case_scope("case-sketch"):
        with pytest.raises(LegacyExecutionFenced) as error:
            service.add_node(object(), object(), object())  # type: ignore[arg-type]

    assert error.value.decision.operation == "sketch_service_add_node"
    assert sketch_repo.calls == []


@pytest.mark.asyncio
async def test_legacy_enricher_execute_is_fenced_before_graph_calls() -> None:
    graph_service = InstrumentedGraphService()
    enricher = LegacyTestEnricher(graph_service=graph_service)  # type: ignore[arg-type]

    with forensic_case_scope("case-enricher"):
        with pytest.raises(LegacyExecutionFenced) as error:
            await enricher.execute([])

    assert error.value.decision.operation == "legacy_enricher_execute"
    assert graph_service.calls == []


def test_legacy_enricher_task_is_fenced_before_task_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_opened = False

    def fail_if_session_opened() -> object:
        nonlocal session_opened
        session_opened = True
        raise AssertionError("task body started")

    monkeypatch.setattr(enricher_tasks, "SessionLocal", fail_if_session_opened)

    with forensic_case_scope("case-task"):
        with pytest.raises(LegacyExecutionFenced) as error:
            run_enricher.run(object(), "legacy-test", [], None)

    assert error.value.decision.operation == "legacy_run_enricher_task"
    assert session_opened is False


@pytest.mark.parametrize(
    "publish",
    [
        lambda: run_enricher.delay("legacy-test", [], None),
        lambda: run_enricher.apply_async(args=("legacy-test", [], None)),
    ],
)
def test_legacy_enricher_task_publish_is_fenced_before_broker(
    publish: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = False

    def record_publish(*_: object, **__: object) -> object:
        nonlocal published
        published = True
        return object()

    monkeypatch.setattr(Task, "apply_async", record_publish)

    with forensic_case_scope("case-task-publish"):
        with pytest.raises(LegacyExecutionFenced) as error:
            publish()  # type: ignore[operator]

    assert error.value.decision.operation == "legacy_task_publish"
    assert published is False


def test_legacy_enricher_task_publishes_outside_forensic_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = False

    def record_publish(*_: object, **__: object) -> str:
        nonlocal published
        published = True
        return "published"

    monkeypatch.setattr(Task, "apply_async", record_publish)
    assert run_enricher.apply_async(args=("legacy-test", [], None)) == "published"
    assert published is True


def test_sanctioned_legacy_dispatch_is_fenced_before_broker_publish() -> None:
    class Broker:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object, object]] = []

        def send_task(self, task_name: str, args: object, kwargs: object) -> str:
            self.calls.append((task_name, args, kwargs))
            return "published"

    broker = Broker()

    with forensic_case_scope("case-dispatch"):
        with pytest.raises(LegacyExecutionFenced):
            dispatch_legacy_task(broker, "run_enricher", args=["legacy-test"])

    assert broker.calls == []
    assert (
        dispatch_legacy_task(broker, "run_enricher", args=["legacy-test"])
        == "published"
    )
    assert broker.calls == [("run_enricher", ["legacy-test"], None)]


def test_legacy_flow_task_is_fenced_before_body_and_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_opened = False
    published = False

    def fail_if_session_opened() -> object:
        nonlocal session_opened
        session_opened = True
        raise AssertionError("flow task body started")

    def record_publish(*_: object, **__: object) -> object:
        nonlocal published
        published = True
        return object()

    monkeypatch.setattr(flow_tasks, "SessionLocal", fail_if_session_opened)
    monkeypatch.setattr(Task, "apply_async", record_publish)

    with forensic_case_scope("case-flow-task"):
        with pytest.raises(LegacyExecutionFenced) as run_error:
            run_flow.run(object(), [], [], None)
        with pytest.raises(LegacyExecutionFenced) as delay_error:
            run_flow.delay([], [], None)
        with pytest.raises(LegacyExecutionFenced) as async_error:
            run_flow.apply_async(args=([], [], None))

    assert run_error.value.decision.operation == "legacy_run_flow_task"
    assert delay_error.value.decision.operation == "legacy_task_publish"
    assert async_error.value.decision.operation == "legacy_task_publish"
    assert session_opened is False
    assert published is False


def test_legacy_flow_task_publishes_outside_forensic_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def record_publish(*_: object, **__: object) -> str:
        return "published"

    monkeypatch.setattr(Task, "apply_async", record_publish)
    assert run_flow.apply_async(args=([], [], None)) == "published"
