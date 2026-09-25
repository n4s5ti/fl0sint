from sqlalchemy.orm import Session

from ..models import Grievance
from .base import BaseRepository


class GrievanceRepository(BaseRepository[Grievance]):
    """Repository for grievance CRUD operations."""

    model = Grievance

    def __init__(self, db: Session):
        super().__init__(db)

    def create(self, install_id: str, tool_name: str, report: str) -> Grievance:
        entity = Grievance(
            install_id=install_id,
            tool_name=tool_name,
            report=report,
        )
        self.add(entity)
        return entity

    def get_by_install_id(self, install_id: str) -> list[Grievance]:
        return (
            self._db.query(Grievance)
            .filter(Grievance.install_id == install_id)
            .order_by(Grievance.created_at.desc())
            .all()
        )

    def list_all(self) -> list[Grievance]:
        return (
            self._db.query(Grievance)
            .order_by(Grievance.created_at.desc())
            .all()
        )
