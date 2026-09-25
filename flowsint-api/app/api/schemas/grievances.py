from .base import ORMBase
from pydantic import BaseModel
from pydantic import UUID4
from datetime import datetime


class GrievanceCreate(BaseModel):
    """Payload from OMP report_tool_issue auto-QA push."""
    install_id: str
    tool_name: str
    report: str


class GrievanceRead(ORMBase):
    id: UUID4
    created_at: datetime
    install_id: str
    tool_name: str
    report: str
