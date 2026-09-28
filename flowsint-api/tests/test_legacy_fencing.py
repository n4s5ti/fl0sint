"""Legacy enricher launch is fenced before graph lookup or task dispatch."""

from __future__ import annotations

import pytest
from fastapi import BackgroundTasks


from app.api.routes.enrichers import launchEnricherPayload, launch_enricher
from app.api.sketch_utils import update_sketch_last_modified, update_sketch_timestamp
from flowsint_core.core.forensics import LegacyExecutionFenced, forensic_case_scope


@pytest.mark.asyncio
async def test_legacy_enricher_launch_is_fenced_before_task_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched = False

    def record_dispatch(*_: object, **__: object) -> object:
        nonlocal dispatched
        dispatched = True
        return object()

    monkeypatch.setattr("app.api.routes.enrichers.celery.send_task", record_dispatch)

    with forensic_case_scope("case-route"):
        with pytest.raises(LegacyExecutionFenced) as error:
            await launch_enricher(
                "legacy-test",
                launchEnricherPayload(node_ids=["node-1"], sketch_id="sketch-1"),
                current_user=object(),  # type: ignore[arg-type]
                db=object(),  # type: ignore[arg-type]
            )

    assert error.value.decision.operation == "legacy_enricher_launch"
    assert dispatched is False


def test_timestamp_wrapper_is_fenced_before_background_task_scheduling() -> None:
    executed = False

    @update_sketch_timestamp
    def legacy_route(**_: object) -> None:
        nonlocal executed
        executed = True

    background_tasks = BackgroundTasks()
    with forensic_case_scope("case-timestamp"):
        with pytest.raises(LegacyExecutionFenced) as error:
            legacy_route(
                sketch_id="sketch-1",
                background_tasks=background_tasks,
                db=object(),
            )

    assert error.value.decision.operation == "sketch_timestamp_update"
    assert background_tasks.tasks == []
    assert executed is False


def test_timestamp_task_body_is_fenced_before_database_access() -> None:
    class Database:
        def __init__(self) -> None:
            self.queries = 0
            self.commits = 0

        def query(self, *_: object) -> object:
            self.queries += 1
            raise AssertionError("timestamp task queried the database")

        def commit(self) -> None:
            self.commits += 1
            raise AssertionError("timestamp task committed")

    db = Database()
    with forensic_case_scope("case-timestamp-task"):
        with pytest.raises(LegacyExecutionFenced) as error:
            update_sketch_last_modified(db, "sketch-1")

    assert error.value.decision.operation == "sketch_timestamp_task"
    assert db.queries == 0
    assert db.commits == 0
