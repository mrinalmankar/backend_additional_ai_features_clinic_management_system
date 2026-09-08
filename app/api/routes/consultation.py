import uuid

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import func as sa_func
from sqlalchemy import select

from app.api.deps import CurrentDoctor, DbSession
from app.core.auth import require_owner_or_privileged
from app.core.limiter import limiter
from app.models.consultation import Consultation
from app.models.enums import ConsultationStatus
from app.schemas.consultation import ConsultationCreate, ConsultationOut, SummaryApproveRequest
from app.services.llm_service import LLMServiceError, generate_consultation_summary

router = APIRouter(prefix="/consultations", tags=["consultations"])

async def _get_or_404(db: DbSession, consultation_id: uuid.UUID) -> Consultation:
    consultation = await db.get(Consultation, consultation_id)
    if consultation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")
    return consultation

@router.post("/", response_model=ConsultationOut, status_code=status.HTTP_201_CREATED)
async def create_consultation(payload: ConsultationCreate, db: DbSession, auth: CurrentDoctor) -> Consultation:
    consultation = Consultation(
        patient_id=payload.patient_id,
        doctor_id=auth.doctor_id,
        transcript_text=payload.transcript_text,
        status=ConsultationStatus.DRAFT,
    )
    db.add(consultation)
    await db.commit()
    await db.refresh(consultation)
    return consultation

@router.post("/{consultation_id}/generate-summary", response_model=ConsultationOut)
@limiter.limit("10/minute")
async def generate_summary(
    request: Request, consultation_id: uuid.UUID, db: DbSession, auth: CurrentDoctor
) -> Consultation:
    consultation = await _get_or_404(db, consultation_id)
    require_owner_or_privileged(auth, consultation.doctor_id)

    if consultation.status == ConsultationStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Consultation is already approved; cannot regenerate summary",
        )

    consultation.status = ConsultationStatus.SUMMARY_PENDING
    await db.commit()

    try:
        summary_text = await generate_consultation_summary(consultation.transcript_text)
    except LLMServiceError as exc:
        consultation.status = ConsultationStatus.DRAFT
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Summary generation failed, transcript preserved: {exc}",
        ) from exc

    consultation.ai_summary = summary_text
    consultation.status = ConsultationStatus.PENDING_REVIEW
    consultation.summary_generated_at = sa_func.now()
    await db.commit()
    await db.refresh(consultation)
    return consultation

@router.post("/{consultation_id}/approve", response_model=ConsultationOut)
async def approve_summary(
    consultation_id: uuid.UUID, payload: SummaryApproveRequest, db: DbSession, auth: CurrentDoctor
) -> Consultation:
    consultation = await _get_or_404(db, consultation_id)
    if auth.role != "admin" and auth.doctor_id != consultation.doctor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the treating doctor or an admin can approve this consultation",
        )

    if consultation.status != ConsultationStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Consultation must be in pending_review status to approve "
            f"(current status: {consultation.status.value})",
        )

    consultation.approved_summary = payload.final_summary or consultation.ai_summary
    consultation.status = ConsultationStatus.APPROVED
    consultation.approved_at = sa_func.now()
    await db.commit()
    await db.refresh(consultation)
    return consultation

@router.get("/{consultation_id}", response_model=ConsultationOut)
async def get_consultation(consultation_id: uuid.UUID, db: DbSession, auth: CurrentDoctor) -> Consultation:
    consultation = await _get_or_404(db, consultation_id)
    require_owner_or_privileged(auth, consultation.doctor_id)
    return consultation

@router.get("/", response_model=list[ConsultationOut])
async def list_consultations(
    db: DbSession,
    response: Response,
    auth: CurrentDoctor,
    doctor_id: uuid.UUID | None = None,
    patient_id: uuid.UUID | None = None,
    status_filter: ConsultationStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Consultation]:

    if auth.is_privileged:
        effective_doctor_id = doctor_id
    else:
        effective_doctor_id = auth.doctor_id

    filters = []
    if effective_doctor_id is not None:
        filters.append(Consultation.doctor_id == effective_doctor_id)
    if patient_id is not None:
        filters.append(Consultation.patient_id == patient_id)
    if status_filter is not None:
        filters.append(Consultation.status == status_filter)

    count_query = select(sa_func.count()).select_from(Consultation)
    for f in filters:
        count_query = count_query.where(f)
    total = (await db.execute(count_query)).scalar_one()

    query = select(Consultation)
    for f in filters:
        query = query.where(f)
    query = query.order_by(Consultation.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(query)

    response.headers["X-Total-Count"] = str(total)
    return list(result.scalars().all())
