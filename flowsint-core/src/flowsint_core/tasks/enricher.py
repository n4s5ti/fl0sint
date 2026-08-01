import asyncio
import uuid
from typing import Any, Dict, List, Optional

from celery import states
from flowsint_enrichers import ENRICHER_REGISTRY, load_all_enrichers
from sqlalchemy.orm import Session

from flowsint_core.utils import to_json_serializable

from ..core.celery import celery
from ..core.enums import EventLevel
from ..core.logger import Logger
from ..core.models import Scan
from ..core.postgre_db import SessionLocal, get_db
from ..core.services import create_enricher_template_service, create_vault_service
from ..core.template_enricher import TemplateEnricher
from ..templates.types import Template

# Auto-discover and register all enrichers
load_all_enrichers()

db: Session = next(get_db())


@celery.task(name="run_enricher", bind=True)
def run_enricher(
    self,
    enricher_name: str,
    serialized_objects: List[dict],
    sketch_id: str | None,
    owner_id: Optional[str] = None,
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

        enricher = ENRICHER_REGISTRY.get_enricher(
            name=enricher_name,
            sketch_id=sketch_id,
            scan_id=scan_id,
            vault=vault,
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


@celery.task(name="run_template_enricher", bind=True)
def run_template_enricher(
    self,
    template_name: str,
    serialized_objects: List[dict],
    sketch_id: str | None,
    owner_id: str,
    params: Dict[str, Any] | None = None,
    idempotency_key: Optional[str] = None,
):
    """Run a database template and persist its structured, redacted outcomes."""
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
        code="template_execution_failed",
        safe_message="Template execution could not be completed",
        retryable=False,
    )

    try:
        scan_id = uuid.UUID(self.request.id)
        owner_uuid = uuid.UUID(owner_id)
        idempotency_key = idempotency_key or str(scan_id)
        sketch_uuid = uuid.UUID(sketch_id) if sketch_id else None
        execution_service = create_execution_service(session)
        input_digest = canonical_input_hash(serialized_objects)

        def canonical_scan(current_run):
            current_scan = session.get(Scan, current_run.id)
            if current_scan is None:
                current_scan = Scan(
                    id=current_run.id,
                    status=EventLevel.PENDING,
                    sketch_id=sketch_uuid,
                )
                session.add(current_scan)
            return current_scan

        def replay_or_attach():
            session.expire_all()
            current_run, _ = execution_service.create_or_reuse_run(
                owner_id=owner_uuid,
                idempotency_key=idempotency_key,
                input_digest=input_digest,
                input_count=len(serialized_objects),
                sketch_id=sketch_uuid,
                run_id=scan_id,
            )
            replayed_step = execution_service.get_step(current_run, template_name)
            if current_run.status in FINAL_RUN_STATUSES:
                current_scan = canonical_scan(current_run)
                if (
                    replayed_step is not None
                    and replayed_step.status in FINAL_RUN_STATUSES
                ):
                    structured_result = execution_service.reconstruct_structured_result(
                        replayed_step
                    )
                    current_scan.status = EventLevel.COMPLETED
                    current_scan.error = None
                    current_scan.details = structured_result.model_dump(mode="json")
                else:
                    diagnostic = current_run.safe_error_diagnostic or {}
                    safe_message = diagnostic.get(
                        "safe_message", safe_diagnostic.safe_message
                    )
                    current_scan.status = EventLevel.FAILED
                    current_scan.error = safe_message
                    current_scan.details = {
                        "status": "failed",
                        "flow_run_id": str(current_run.id),
                        "flow_run_reference": f"flow_run:{current_run.id}",
                        "error": safe_message,
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
            sketch_id=sketch_uuid,
            run_id=scan_id,
        )
        lease = execution_service.claim_run(flow_run, str(scan_id))
        if lease is None:
            return replay_or_attach()
        scan = canonical_scan(flow_run)
        session.commit()

        step_run = execution_service.begin_or_resume_step(
            flow_run, lease, template_name, len(serialized_objects)
        )

        vault = None
        try:
            vault = create_vault_service(session).for_user(owner_uuid)
        except Exception:
            Logger.error(
                sketch_id, {"message": "Template vault could not be initialized"}
            )

        template_service = create_enricher_template_service(session)
        db_template = template_service.find_by_name(template_name, owner_uuid)
        if not db_template:
            raise ValueError("Template was not found")

        template = Template(**db_template.content)
        template_params = params or {}
        template_schema = TemplateEnricher.get_params_schema_for_template(template)
        allowed_params = {item["name"] for item in template_schema}
        template_params = {
            key: value for key, value in template_params.items() if key in allowed_params
        }

        enricher = TemplateEnricher(
            template=template,
            sketch_id=sketch_id,
            scan_id=str(flow_run.id),
            vault=vault,
            params=template_params,
        )
        structured_result = asyncio.run(
            enricher.execute_structured(values=serialized_objects)
        )
        execution_service.persist_structured_result(
            flow_run, lease, step_run, structured_result
        )

        scan.status = EventLevel.COMPLETED
        scan.error = None
        scan.details = structured_result.model_dump(mode="json")
        session.commit()
        return {"result": scan.details}

    except RunLeaseLost:
        session.rollback()
        return replay_or_attach()
    except Exception:
        session.rollback()
        if flow_run is not None and lease is not None:
            try:
                execution_service.fail_run(flow_run, lease, safe_diagnostic)
            except RunLeaseLost:
                session.rollback()
                return replay_or_attach()

            scan = canonical_scan(flow_run)
            scan.status = EventLevel.FAILED
            scan.error = safe_diagnostic.safe_message
            session.commit()

        self.update_state(state=states.FAILURE)
        raise RuntimeError(safe_diagnostic.safe_message) from None

    finally:
        session.close()
