from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

class GBPAccountCreate(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=255,
                              examples=["Main Branch – Koregaon Park"])

class GBPAccountOut(BaseModel):
    id: uuid.UUID
    display_name: str
    google_account_id: Optional[str]
    location_id: Optional[str]
    location_name: Optional[str]
    is_active: bool
    token_expires_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

class OAuthUrlOut(BaseModel):
    auth_url: str
    state: str

class OAuthCallbackIn(BaseModel):
    code: str
    state: str
    account_id: uuid.UUID

class GBPReviewOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    google_review_id: str
    reviewer_name: Optional[str]
    rating: Optional[int]
    review_text: Optional[str]
    review_published_at: Optional[datetime]
    has_existing_reply: bool
    existing_reply_text: Optional[str]
    synced_at: datetime

    model_config = {"from_attributes": True}

class ReviewReplyGenerateIn(BaseModel):
    clinic_name: str = Field(default="our clinic",
                             examples=["Apex Dental Clinic"])
    tone: str = Field(default="professional",
                      examples=["professional", "warm", "concise"])

class ReviewReplyApproveIn(BaseModel):
    approved_text: str = Field(..., min_length=5,
                               description="Final reply text to be posted.")
    post_immediately: bool = Field(
        default=True,
        description="If True the reply is posted to Google right away; "
                    "if False it stays APPROVED but is not posted yet.",
    )

class GBPReviewReplyOut(BaseModel):
    id: uuid.UUID
    review_id: uuid.UUID
    ai_draft: str
    approved_text: Optional[str]
    status: str
    created_at: datetime
    approved_at: Optional[datetime]
    posted_at: Optional[datetime]

    model_config = {"from_attributes": True}

class GBPPostGenerateIn(BaseModel):
    topic_brief: str = Field(..., min_length=5,
                             examples=["Summer teeth whitening offer, 20% off till Sept 30"])
    post_type: str = Field(
        default="WHATS_NEW",
        examples=["WHATS_NEW", "OFFER", "EVENT", "PRODUCT"],
    )
    clinic_name: str = Field(default="our clinic", examples=["Apex Dental Clinic"])

class GBPPostUpdateIn(BaseModel):
    final_title: Optional[str] = None
    final_body: Optional[str] = None
    call_to_action: Optional[str] = None

class GBPPostScheduleIn(BaseModel):
    scheduled_at: datetime = Field(...,
                                   description="UTC datetime when the post should be published.")

class GBPPostOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    topic_brief: str
    post_type: str
    ai_title: Optional[str]
    ai_body: str
    call_to_action: Optional[str]
    final_title: Optional[str]
    final_body: Optional[str]
    status: str
    scheduled_at: Optional[datetime]
    published_at: Optional[datetime]
    google_post_id: Optional[str]
    retry_count: int
    last_error: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

class GBPKeywordCreate(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=255,
                         examples=["dental clinic pune", "teeth whitening koregaon park"])

class GBPKeywordOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    keyword: str
    impressions: Optional[int]
    clicks: Optional[int]
    average_position: Optional[float]
    trend: str
    last_synced_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}

class SEOSuggestion(BaseModel):
    priority: str
    category: str
    suggestion: str
    rationale: str

class SEOSuggestionsOut(BaseModel):
    account_id: uuid.UUID
    generated_at: datetime
    suggestions: list[SEOSuggestion]

class MessageOut(BaseModel):
    message: str

class SyncResultOut(BaseModel):
    synced: int
    message: str
