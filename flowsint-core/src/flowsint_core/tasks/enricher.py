import asyncio
import hashlib
import uuid
from typing import Any, Dict, List, Optional

from celery import states
from flowsint_enrichers import ENRICHER_REGISTRY, load_all_enrichers
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from flowsint_core.utils import to_json_serializable

from ..core.celery import celery
from ..core.config import destination_registry
from ..core.connector_egress import (
    ConnectorCredentialUnavailable,
    ConnectorPolicyError,
    ConnectorValidationError,
    EgressAuthorizer,
    connector_template_digest,
)
from ..core.enums import EventLevel
from ..core.logger import Logger
from ..core.models import Scan
from ..core.projection.contracts import ProjectionError
from ..core.projection.registry import projection_registry
from ..core.postgre_db import SessionLocal, get_db
from ..core.services import create_enricher_template_service, create_vault_service
from ..core.template_enricher import TemplateEnricher
from ..templates.types import Template
from .graph_projection import project_graph_evidence

# Auto-discover and register all enrichers
load_all_enrichers()

db: Session = next(get_db())

def _get_or_create_scan(session, scan_id, sketch_id):
    dialect = session.get_bind().dialect.name
    values = {
        "id": scan_id,
        "status": EventLevel.PENDING,
        "sketch_id": sketch_id,
    }
    if dialect == "sqlite":
        statement = sqlite_insert(Scan).values(**values)
    elif dialect == "postgresql":
        statement = postgres_insert(Scan).values(**values)
    else:
        raise RuntimeError(f"Unsupported Scan persistence dialect: {dialect}")

    session.execute(statement.on_conflict_do_nothing(index_elements=["id"]))
    session.flush()
    session.expire_all()
    current_scan = session.get(Scan, scan_id)
    if current_scan is None:
        raise RuntimeError("Canonical Scan could not be loaded after insert")
    return current_scan



@celery.task(name="run_enricher", bind=True)
def run_enricher(
    self,
    enricher_name: str,
    serialized_objects: List[dict],
    sketch_id: str | None,
    owner_id: Optional[str] = None,
    params: Dict[str, Any] | None = None,
):
    session = SessionLocal()

    try:
        scan_id = uuid.UUID(self.request.id)

        scan = Scan(
            id=scan_id,
            status=EventLevel.PENDING,
            sketch_id=uuid.UUID(sketch_id) if sketch_id else None,
        )
        session.add(scan)
        session.commit()

        # Create vault instance if owner_id is provided
        vault = None
        if owner_id:
            try:
                vault = create_vault_service(session).for_user(uuid.UUID(owner_id))
            except Exception as e:
                Logger.error(
                    sketch_id, {"message": f"Failed to create vault: {str(e)}"}
                )

        if not ENRICHER_REGISTRY.enricher_exists(enricher_name):
            raise ValueError(f"Enricher '{enricher_name}' not found in registry")

        run_params = params or {}
        run_params = ENRICHER_REGISTRY.filter_params(
            enricher_name, run_params
        )

        enricher = ENRICHER_REGISTRY.get_enricher(
            name=enricher_name,
            sketch_id=sketch_id,
            scan_id=scan_id,
            vault=vault,
            params=run_params,
        )

        # Deserialize objects back into Pydantic models
        # The preprocess method in Enricher will handle these already-parsed objects
        results = asyncio.run(enricher.execute(values=serialized_objects))

        scan.status = EventLevel.COMPLETED
        scan.details = to_json_serializable(results)
        session.commit()

        return {"result": scan.details}

    except Exception as ex:
        session.rollback()
        error_logs = f"An error occurred: {str(ex)}"
        print(f"Error in task: {error_logs}")

        scan = session.query(Scan).filter(Scan.id == uuid.UUID(self.request.id)).first()
        if scan:
            scan.status = EventLevel.FAILED
            scan.error = error_logs
            session.commit()

        self.update_state(state=states.FAILURE)
        raise ex

    finally:
        session.close()


def _safe_connector_scan_details(result) -> dict[str, Any]:
    """Expose connector outcomes to Scan without output values or transport material."""
    return {
        "outcomes": [
            {
                "status": outcome.status.value,
                "diagnostic": (
                    outcome.diagnostic.model_dump(mode="json")
                    if outcome.diagnostic is not None
                    else None
                ),
                "evidence": [
                    {
                        "destination_id": evidence.destination_id,
                        "endpoint_id": evidence.endpoint_id,
                        "capability": evidence.capability,
                        "policy_version": evidence.policy_version,
                        "artifact_sha256": evidence.artifact_sha256,
                        "artifact_reference": evidence.artifact_reference,
                    }
                    for evidence in outcome.evidence
                ],
            }
            for outcome in result.outcomes
        ]
    }


def _connector_failure_diagnostic(error):
    from ..core.execution import RedactedDiagnostic

    if isinstance(error, ProjectionError):
        return RedactedDiagnostic(
            code=error.code,
            safe_message=error.safe_message,
            retryable=False,
        )
    if isinstance(error, (ConnectorPolicyError, ConnectorValidationError)):
        return RedactedDiagnostic(
            code="connector_policy_denied",
            safe_message="Connector policy denied the request.",
            retryable=False,
        )
    if isinstance(error, ConnectorCredentialUnavailable):
        return RedactedDiagnostic(
            code="connector_credential_unavailable",
            safe_message="Connector credentials are unavailable.",
            retryable=False,
        )
    return RedactedDiagnostic(
        code="connector_execution_failed",
        safe_message="Connector execution could not be completed.",
        retryable=False,
    )


@celery.task(name="run_connector_template", bind=True)
def run_connector_template(
    self,
    template_id: str,
    template_digest: str,
    serialized_objects: List[dict],
    sketch_id: str | None,
    owner_id: str,
    idempotency_key: Optional[str] = None,
):
    """Run a registry-backed template with Stage 3 replay and lease fencing intact."""
    from ..core.execution import RedactedDiagnostic, canonical_input_hash
    from ..core.services.execution_service import (
        FINAL_RUN_STATUSES,
        RunLeaseLost,
        create_execution_service,
    )

    session = SessionLocal()
    scan = None
    flow_run = None
    lease = None
    execution_service = None
    safe_diagnostic = RedactedDiagnostic(
        code="connector_execution_failed",
        safe_message="Connector execution could not be completed.",
        retryable=False,
    )

    try:
        scan_id = uuid.UUID(self.request.id)
        owner_uuid = uuid.UUID(owner_id)
        template_uuid = uuid.UUID(template_id)
        idempotency_key = idempotency_key or str(scan_id)
        sketch_uuid = uuid.UUID(sketch_id) if sketch_id else None
        input_digest = canonical_input_hash(serialized_objects)
        operation_digest = hashlib.sha256(
            f"{template_uuid}:{template_digest}:{sketch_uuid or ''}".encode("utf-8")
        ).hexdigest()
        step_key = f"connector-template:{template_uuid}:{template_digest}"
        execution_service = create_execution_service(session)

        def canonical_scan(current_run):
            return _get_or_create_scan(session, current_run.id, sketch_uuid)

        def replay_or_attach():
            session.expire_all()
            current_run, _ = execution_service.create_or_reuse_run(
                owner_id=owner_uuid,
                idempotency_key=idempotency_key,
                input_digest=input_digest,
                input_count=len(serialized_objects),
                operation_digest=operation_digest,
                sketch_id=sketch_uuid,
                run_id=scan_id,
            )
            replayed_step = execution_service.get_step(current_run, step_key)
            if current_run.status in FINAL_RUN_STATUSES:
                current_scan = canonical_scan(current_run)
                if (
                    replayed_step is not None
                    and replayed_step.status in FINAL_RUN_STATUSES
                ):
                    current_scan.status = EventLevel.COMPLETED
                    current_scan.error = None
                    current_scan.details = _safe_connector_scan_details(
                        execution_service.reconstruct_structured_result(replayed_step)
                    )
                else:
                    diagnostic = current_run.safe_error_diagnostic or {}
                    current_scan.status = EventLevel.FAILED
                    current_scan.error = diagnostic.get(
                        "safe_message", safe_diagnostic.safe_message
                    )
                    current_scan.details = {
                        "status": "failed",
                        "flow_run_id": str(current_run.id),
                        "flow_run_reference": f"flow_run:{current_run.id}",
                        "error_code": diagnostic.get("code", safe_diagnostic.code),
                    }
                session.commit()
                return {"result": current_scan.details}
            return {
                "result": {
                    "status": "in_progress",
                    "flow_run_id": str(current_run.id),
                    "flow_run_reference": f"flow_run:{current_run.id}",
                }
            }

        flow_run, _ = execution_service.create_or_reuse_run(
            owner_id=owner_uuid,
            idempotency_key=idempotency_key,
            input_digest=input_digest,
            input_count=len(serialized_objects),
            operation_digest=operation_digest,
            sketch_id=sketch_uuid,
            run_id=scan_id,
        )
        lease = execution_service.claim_run(flow_run, str(scan_id))
        if lease is None:
            return replay_or_attach()
        scan = canonical_scan(flow_run)

        template_service = create_enricher_template_service(session)
        db_template = template_service.get_template(template_uuid, owner_uuid)
        template = Template.model_validate(db_template.content)
        projection_binding = projection_registry.resolve(template.projection)
        if connector_template_digest(template) != template_digest:
            raise ConnectorPolicyError("connector template digest denied")
        enricher = TemplateEnricher(
            template=template,
            registry=destination_registry,
            runtime_authorizer=EgressAuthorizer.enrichment(),
            sketch_id=sketch_id,
            scan_id=str(flow_run.id),
        )
        step_run = execution_service.begin_or_resume_step(
            flow_run, lease, step_key, len(serialized_objects)
        )
        execution_service.update_step_checkpoint(
            flow_run,
            lease,
            step_run,
            {
                **step_run.checkpoint,
                "destination_id": enricher.endpoint.destination_id,
                "endpoint_id": enricher.endpoint.endpoint_id,
                "capability": enricher.endpoint.capability,
                "policy_version": enricher.endpoint.policy_version,
            },
        )
        vault = create_vault_service(session).for_user(owner_uuid)
        enricher.vault = vault
        structured_result = asyncio.run(
            enricher.execute_structured(values=serialized_objects)
        )
        persisted = execution_service.persist_structured_result(
            flow_run,
            lease,
            step_run,
            structured_result,
            projection_binding=projection_binding,
        )
        for projection_job_id in persisted.projection_job_ids:
            try:
                project_graph_evidence.delay(str(projection_job_id))
            except Exception:
                # The committed durable job is recovered by the periodic sweep.
                pass

        scan.status = EventLevel.COMPLETED
        scan.error = None
        scan.details = _safe_connector_scan_details(structured_result)
        session.commit()
        return {"result": scan.details}

    except RunLeaseLost:
        session.rollback()
        return replay_or_attach()
    except Exception as error:
        session.rollback()
        safe_diagnostic = _connector_failure_diagnostic(error)
        if flow_run is not None and lease is not None:
            try:
                execution_service.fail_run(flow_run, lease, safe_diagnostic)
            except RunLeaseLost:
                session.rollback()
                return replay_or_attach()

            scan = canonical_scan(flow_run)
            scan.status = EventLevel.FAILED
            scan.error = safe_diagnostic.safe_message
            scan.details = {
                "status": "failed",
                "flow_run_id": str(flow_run.id),
                "flow_run_reference": f"flow_run:{flow_run.id}",
                "error_code": safe_diagnostic.code,
            }
            session.commit()

        raise RuntimeError(safe_diagnostic.safe_message) from None

    finally:
        session.close()
