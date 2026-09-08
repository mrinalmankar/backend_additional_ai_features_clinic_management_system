import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy import func as sa_func
from sqlalchemy import select

from app.api.deps import CurrentDoctor, DbSession
from app.core.auth import require_owner_or_privileged
from app.core.config import settings
from app.core.limiter import limiter
from app.models.enums import PrescriptionUploadStatus
from app.models.prescription import PrescriptionUpload
from app.schemas.prescription import PrescriptionReviewRequest, PrescriptionUploadOut
from app.services.ocr_service import OCRServiceError, extract_raw_text, parse_candidate_line_items

router = APIRouter(prefix="/prescriptions", tags=["prescriptions"])

_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}

async def _get_or_404(db: DbSession, upload_id: uuid.UUID) -> PrescriptionUpload:
    upload = await db.get(PrescriptionUpload, upload_id)
    if upload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prescription upload not found")
    return upload

@router.post("/upload", response_model=PrescriptionUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_prescription(
    db: DbSession,
    auth: CurrentDoctor,
    patient_id: uuid.UUID = Form(...),
    consultation_id: uuid.UUID | None = Form(default=None),
    file: UploadFile = File(...),
) -> PrescriptionUpload:

    if file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type '{file.content_type}'. Allowed: {sorted(_ALLOWED_CONTENT_TYPES)}",
        )

    contents = await file.read()
    if len(contents) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds max size of {settings.max_upload_size_bytes} bytes",
        )
    if len(contents) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    upload_id = uuid.uuid4()
    storage_dir = Path(settings.prescription_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    extension = Path(file.filename or "").suffix or ".bin"
    image_path = storage_dir / f"{upload_id}{extension}"
    image_path.write_bytes(contents)

    upload = PrescriptionUpload(
        id=upload_id,
        patient_id=patient_id,
        doctor_id=auth.doctor_id,
        consultation_id=consultation_id,
        image_path=str(image_path),
        original_filename=file.filename or "unknown",
        status=PrescriptionUploadStatus.UPLOADED,
    )
    db.add(upload)
    await db.commit()
    await db.refresh(upload)
    return upload

@router.post("/{upload_id}/extract", response_model=PrescriptionUploadOut)
@limiter.limit("20/minute")
async def extract_prescription(
    request: Request, upload_id: uuid.UUID, db: DbSession, auth: CurrentDoctor
) -> PrescriptionUpload:

    upload = await _get_or_404(db, upload_id)
    require_owner_or_privileged(auth, upload.doctor_id)

    if upload.status == PrescriptionUploadStatus.REVIEWED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Prescription already reviewed; corrected_items is the record of truth, re-extraction is not allowed",
        )

    upload.status = PrescriptionUploadStatus.OCR_PROCESSING
    await db.commit()

    try:
        raw_text = extract_raw_text(upload.image_path)
    except OCRServiceError as exc:
        upload.status = PrescriptionUploadStatus.UPLOADED
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"OCR failed, image preserved: {exc}",
        ) from exc

    candidate_items = parse_candidate_line_items(raw_text)

    upload.ocr_raw_text = raw_text
    upload.extracted_items = candidate_items
    upload.status = PrescriptionUploadStatus.OCR_DONE
    upload.ocr_completed_at = sa_func.now()
    await db.commit()
    await db.refresh(upload)
    return upload

@router.post("/{upload_id}/review", response_model=PrescriptionUploadOut)
async def review_prescription(
    upload_id: uuid.UUID, payload: PrescriptionReviewRequest, db: DbSession, auth: CurrentDoctor
) -> PrescriptionUpload:

    upload = await _get_or_404(db, upload_id)
    require_owner_or_privileged(auth, upload.doctor_id)

    if upload.status not in (PrescriptionUploadStatus.OCR_DONE, PrescriptionUploadStatus.REVIEWED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Prescription must have OCR results before review (current status: {upload.status.value})",
        )

    upload.corrected_items = [item.model_dump() for item in payload.corrected_items]
    upload.status = PrescriptionUploadStatus.REVIEWED
    upload.reviewed_at = sa_func.now()
    await db.commit()
    await db.refresh(upload)
    return upload

@router.get("/{upload_id}", response_model=PrescriptionUploadOut)
async def get_prescription(upload_id: uuid.UUID, db: DbSession, auth: CurrentDoctor) -> PrescriptionUpload:
    upload = await _get_or_404(db, upload_id)
    require_owner_or_privileged(auth, upload.doctor_id)
    return upload

@router.get("/", response_model=list[PrescriptionUploadOut])
async def list_prescriptions(
    db: DbSession,
    response: Response,
    auth: CurrentDoctor,
    patient_id: uuid.UUID | None = None,
    doctor_id: uuid.UUID | None = None,
    status_filter: PrescriptionUploadStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[PrescriptionUpload]:

    if auth.is_privileged:
        effective_doctor_id = doctor_id
    else:
        effective_doctor_id = auth.doctor_id

    filters = []
    if patient_id is not None:
        filters.append(PrescriptionUpload.patient_id == patient_id)
    if effective_doctor_id is not None:
        filters.append(PrescriptionUpload.doctor_id == effective_doctor_id)
    if status_filter is not None:
        filters.append(PrescriptionUpload.status == status_filter)

    count_query = select(sa_func.count()).select_from(PrescriptionUpload)
    for f in filters:
        count_query = count_query.where(f)
    total = (await db.execute(count_query)).scalar_one()

    query = select(PrescriptionUpload)
    for f in filters:
        query = query.where(f)
    query = query.order_by(PrescriptionUpload.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(query)
    response.headers["X-Total-Count"] = str(total)
    return list(result.scalars().all())
