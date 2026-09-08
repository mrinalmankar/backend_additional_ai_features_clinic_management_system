import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ConsultationStatus

class ConsultationCreate(BaseModel):
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    transcript_text: str = Field(min_length=1, description="Raw consultation transcript text")

class ConsultationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    transcript_text: str
    ai_summary: str | None
    approved_summary: str | None
    status: ConsultationStatus
    created_at: datetime
    summary_generated_at: datetime | None
    approved_at: datetime | None

class SummaryApproveRequest(BaseModel):
    final_summary: str | None = Field(default=None, min_length=1)
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ConsultationStatus

class ConsultationCreate(BaseModel):
    patient_id: uuid.UUID
    transcript_text: str = Field(min_length=1, description="Raw consultation transcript text")

class ConsultationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    transcript_text: str
    ai_summary: str | None
    approved_summary: str | None
    status: ConsultationStatus
    created_at: datetime
    summary_generated_at: datetime | None
    approved_at: datetime | None

class SummaryApproveRequest(BaseModel):

    final_summary: str | None = Field(default=None, min_length=1)
