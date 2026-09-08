import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import PrescriptionUploadStatus

class PrescriptionLineItem(BaseModel):

    raw_line: str | None = None
    drug_name: str | None = None
    dosage: str | None = None
    frequency: str | None = None

class PrescriptionUploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    consultation_id: uuid.UUID | None
    original_filename: str
    ocr_raw_text: str | None
    extracted_items: list[PrescriptionLineItem] | None
    corrected_items: list[PrescriptionLineItem] | None
    status: PrescriptionUploadStatus
    created_at: datetime
    ocr_completed_at: datetime | None
    reviewed_at: datetime | None

class PrescriptionReviewRequest(BaseModel):
    corrected_items: list[PrescriptionLineItem] = Field(
        min_length=1, description="Staff/doctor-corrected line items; becomes the record of truth"
    )
