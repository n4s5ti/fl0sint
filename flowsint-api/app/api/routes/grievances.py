from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from flowsint_core.core.postgre_db import get_db
from flowsint_core.core.services import (
    create_grievance_service,
    DatabaseError,
)
from app.api.schemas.grievances import GrievanceCreate, GrievanceRead

router = APIRouter()


@router.get(
    "",
    response_model=List[GrievanceRead],
)
def list_grievances(
    db: Session = Depends(get_db),
):
    """
    List all grievance reports, most recent first.
    """
    service = create_grievance_service(db)
    return service.list_grievances()


@router.post(
    "",
    response_model=GrievanceRead,
    status_code=status.HTTP_201_CREATED,
)
def create_grievance(
    payload: GrievanceCreate,
    db: Session = Depends(get_db),
):
    """
    Receive an auto-QA grievance report from a client installation.
    This is a telemetry-style public endpoint — authentication is intentionally
    skipped so that client-side report_tool_issue can push without credentials.
    """
    try:
        service = create_grievance_service(db)
        grievance = service.create_grievance(
            install_id=payload.install_id,
            tool_name=payload.tool_name,
            report=payload.report,
        )
        return grievance
    except DatabaseError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
