import enum

class ConsultationStatus(str, enum.Enum):

    DRAFT = "draft"
    SUMMARY_PENDING = "summary_pending"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"

class PrescriptionUploadStatus(str, enum.Enum):

    UPLOADED = "uploaded"
    OCR_PROCESSING = "ocr_processing"
    OCR_DONE = "ocr_done"
    REVIEWED = "reviewed"
