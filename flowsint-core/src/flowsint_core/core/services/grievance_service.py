from sqlalchemy.orm import Session

from ..models import Grievance
from ..repositories import GrievanceRepository
from .base import BaseService


class GrievanceService(BaseService):
    """Service for grievance creation and lookup."""

    def __init__(self, db: Session, grievance_repo: GrievanceRepository, **kwargs):
        super().__init__(db, **kwargs)
        self._grievance_repo = grievance_repo

    def create_grievance(
        self, install_id: str, tool_name: str, report: str
    ) -> Grievance:
        """Record a new grievance (auto-QA report). Public endpoint — no auth checks."""
        grievance = self._grievance_repo.create(
            install_id=install_id,
            tool_name=tool_name,
            report=report,
        )
        self._commit()
        self._refresh(grievance)
        return grievance

    def get_by_install_id(self, install_id: str) -> list[Grievance]:
        return self._grievance_repo.get_by_install_id(install_id)

    def list_grievances(self) -> list[Grievance]:
        """List all grievances, most recent first."""
        return self._grievance_repo.list_all()


def create_grievance_service(db: Session) -> GrievanceService:
    """Factory for GrievanceService."""
    return GrievanceService(
        db=db,
        grievance_repo=GrievanceRepository(db),
    )
