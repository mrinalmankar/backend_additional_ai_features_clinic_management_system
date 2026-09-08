import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SQLEnum, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import ConsultationStatus

class Consultation(Base):
    __tablename__ = "consultations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False, index=True
    )
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False, index=True
    )

    transcript_text: Mapped[str] = mapped_column(Text, nullable=False)

    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    approved_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[ConsultationStatus] = mapped_column(
                SQLEnum(
                        ConsultationStatus,
                        native_enum=False,
                        length=32,
                        values_callable=lambda enum_type: [member.value for member in enum_type],
                ),
        nullable=False, default=ConsultationStatus.DRAFT, index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    summary_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
