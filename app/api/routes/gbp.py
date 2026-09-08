import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.auth import get_current_doctor
from app.core.limiter import limiter
from app.models.gbp import GBPAccount, GBPKeyword, GBPOAuthState, GBPPost, GBPReview, GBPReviewReply
from app.schemas.gbp import (
    GBPAccountCreate,
    GBPAccountOut,
    GBPKeywordCreate,
    GBPKeywordOut,
    GBPPostGenerateIn,
    GBPPostOut,
    GBPPostScheduleIn,
    GBPPostUpdateIn,
    GBPReviewOut,
    GBPReviewReplyOut,
    MessageOut,
    OAuthCallbackIn,
    OAuthUrlOut,
    ReviewReplyApproveIn,
    ReviewReplyGenerateIn,
    SEOSuggestion,
    SEOSuggestionsOut,
    SyncResultOut,
)
from app.services import gbp_ai, gbp_api, gbp_oauth

router = APIRouter(
    prefix="/gbp",
    tags=["Google Business Profile"],

    dependencies=[Depends(get_current_doctor)],
)

public_router = APIRouter(prefix="/gbp", tags=["Google Business Profile"])

OAUTH_STATE_TTL_MINUTES = 10

async def _get_account_or_404(account_id: uuid.UUID, db: AsyncSession) -> GBPAccount:
    account = await db.get(GBPAccount, account_id)
    if account is None or not account.is_active:
        raise HTTPException(status_code=404, detail="GBP account not found")
    return account

def _star_to_int(star: str | None) -> int | None:
    mapping = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
    return mapping.get(star, None) if star else None

@router.post(
    "/accounts",
    response_model=GBPAccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Pre-register a GBP account slot before the OAuth flow",
)
async def create_gbp_account(
    body: GBPAccountCreate,
    db: AsyncSession = Depends(get_db),
):
    account = GBPAccount(display_name=body.display_name)
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account

@router.get(
    "/auth/url",
    response_model=OAuthUrlOut,
    summary="Get Google OAuth2 consent URL for a GBP account",
)
async def get_oauth_url(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _get_account_or_404(account_id, db)
    auth_url, state = gbp_oauth.get_auth_url(str(account_id))

    await db.execute(
        GBPOAuthState.__table__.delete().where(
            GBPOAuthState.account_id == account_id,
            GBPOAuthState.consumed_at.is_(None),
        )
    )
    db.add(
        GBPOAuthState(
            state=state,
            account_id=account_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=OAUTH_STATE_TTL_MINUTES),
        )
    )
    await db.commit()

    return OAuthUrlOut(auth_url=auth_url, state=state)

@public_router.get(
    "/auth/callback",
    response_model=GBPAccountOut,
    summary="OAuth2 callback — exchange code and save tokens",
)
async def oauth_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    oauth_state = await db.get(GBPOAuthState, state)
    if oauth_state is None:
        raise HTTPException(status_code=400, detail="Invalid or unrecognized OAuth state parameter")
    if oauth_state.consumed_at is not None:
        raise HTTPException(status_code=400, detail="OAuth state has already been used")

    expires_at = oauth_state.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="OAuth state has expired; restart the connect flow")

    oauth_state.consumed_at = datetime.now(timezone.utc)
    await db.commit()

    account_id = oauth_state.account_id
    account = await _get_account_or_404(account_id, db)

    token_data = await gbp_oauth.exchange_code(code)
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)
    expires_at = datetime.now(timezone.utc).replace(
        second=0, microsecond=0
    )
    from datetime import timedelta
    expires_at = expires_at.replace() if False else (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    )

    gbp_accounts_data = await gbp_oauth.fetch_account_info(access_token)
    gbp_accounts_list = gbp_accounts_data.get("accounts", [])
    google_account_id = gbp_accounts_list[0]["name"] if gbp_accounts_list else None

    location_id = None
    location_name = None
    if google_account_id:
        locations_data = await gbp_oauth.fetch_locations(access_token, google_account_id)
        locations = locations_data.get("locations", [])
        if locations:
            location_id = locations[0].get("name")
            location_name = locations[0].get("title")

    account.access_token_enc = gbp_oauth.encrypt_token(access_token) if access_token else None
    account.refresh_token_enc = (
        gbp_oauth.encrypt_token(refresh_token) if refresh_token else None
    )
    account.token_expires_at = expires_at
    account.google_account_id = google_account_id
    account.location_id = location_id
    account.location_name = location_name

    await db.commit()
    await db.refresh(account)
    return account

@router.get(
    "/accounts",
    response_model=list[GBPAccountOut],
    summary="List all linked GBP accounts",
)
async def list_gbp_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(GBPAccount).where(GBPAccount.is_active.is_(True)))
    return list(result.scalars().all())

@router.delete(
    "/accounts/{account_id}",
    response_model=MessageOut,
    summary="Unlink / deactivate a GBP account",
)
async def delete_gbp_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    account = await _get_account_or_404(account_id, db)
    account.is_active = False
    account.access_token_enc = None
    account.refresh_token_enc = None
    await db.commit()
    return MessageOut(message=f"GBP account '{account.display_name}' unlinked successfully")

@router.post(
    "/accounts/{account_id}/reviews/sync",
    response_model=SyncResultOut,
    summary="Sync latest reviews from Google into the database",
)
async def sync_reviews(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    account = await _get_account_or_404(account_id, db)
    raw_reviews = await gbp_api.list_reviews(account)

    db.add(account)

    synced = 0
    for raw in raw_reviews:
        google_review_id: str = raw.get("name", "")
        if not google_review_id:
            continue

        result = await db.execute(
            select(GBPReview).where(GBPReview.google_review_id == google_review_id)
        )
        existing = result.scalar_one_or_none()

        if existing is None:
            reviewer = raw.get("reviewer", {})
            star = raw.get("starRating")
            review_reply = raw.get("reviewReply")

            review = GBPReview(
                account_id=account_id,
                google_review_id=google_review_id,
                reviewer_name=reviewer.get("displayName"),
                reviewer_photo_url=reviewer.get("profilePhotoUrl"),
                rating=_star_to_int(star),
                review_text=raw.get("comment"),
                review_published_at=_parse_dt(raw.get("createTime")),
                has_existing_reply=bool(review_reply),
                existing_reply_text=(review_reply or {}).get("comment"),
            )
            db.add(review)
            synced += 1
        else:

            review_reply = raw.get("reviewReply")
            existing.has_existing_reply = bool(review_reply)
            existing.existing_reply_text = (review_reply or {}).get("comment")
            existing.synced_at = datetime.now(timezone.utc)

    await db.commit()
    return SyncResultOut(synced=synced, message=f"Synced {synced} new review(s) from Google")

@router.get(
    "/accounts/{account_id}/reviews",
    response_model=list[GBPReviewOut],
    summary="List all reviews for a GBP account",
)
async def list_reviews(
    account_id: uuid.UUID,
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    await _get_account_or_404(account_id, db)
    total = (
        await db.execute(
            select(sa_func.count()).select_from(GBPReview)
            .where(GBPReview.account_id == account_id)
        )
    ).scalar_one()
    result = await db.execute(
        select(GBPReview).where(GBPReview.account_id == account_id)
        .order_by(GBPReview.review_published_at.desc())
        .limit(limit).offset(offset)
    )
    response.headers["X-Total-Count"] = str(total)
    return list(result.scalars().all())

@router.post(
    "/reviews/{review_id}/generate-reply",
    response_model=GBPReviewReplyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ask AI to draft a reply to a review",
)
@limiter.limit("20/minute")
async def generate_review_reply(
    request: Request,
    review_id: uuid.UUID,
    body: ReviewReplyGenerateIn,
    db: AsyncSession = Depends(get_db),
):
    review = await db.get(GBPReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")

    draft = await gbp_ai.generate_review_reply(
        reviewer_name=review.reviewer_name,
        rating=review.rating,
        review_text=review.review_text,
        clinic_name=body.clinic_name,
        tone=body.tone,
    )

    reply = GBPReviewReply(review_id=review_id, ai_draft=draft)
    db.add(reply)
    await db.commit()
    await db.refresh(reply)
    return reply

@router.put(
    "/reviews/{review_id}/reply",
    response_model=GBPReviewReplyOut,
    summary="Approve and post a reply to Google",
)
async def approve_and_post_reply(
    review_id: uuid.UUID,
    body: ReviewReplyApproveIn,
    db: AsyncSession = Depends(get_db),
):

    result = await db.execute(
        select(GBPReviewReply)
        .where(GBPReviewReply.review_id == review_id)
        .order_by(GBPReviewReply.created_at.desc())
        .limit(1)
    )
    reply = result.scalar_one_or_none()
    if reply is None:
        raise HTTPException(status_code=404, detail="No AI draft found for this review. Generate one first.")

    reply.approved_text = body.approved_text
    reply.status = "APPROVED"
    reply.approved_at = datetime.now(timezone.utc)

    if body.post_immediately:

        review = await db.get(GBPReview, review_id)
        account = await db.get(GBPAccount, review.account_id)

        try:
            await gbp_api.post_reply(account, review.google_review_id, body.approved_text)
            reply.status = "POSTED"
            reply.posted_at = datetime.now(timezone.utc)

            review.has_existing_reply = True
            review.existing_reply_text = body.approved_text
            db.add(account)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Reply saved but could not be posted to Google: {exc}",
            )

    await db.commit()
    await db.refresh(reply)
    return reply

@router.post(
    "/accounts/{account_id}/posts/generate",
    response_model=GBPPostOut,
    status_code=status.HTTP_201_CREATED,
    summary="Generate an AI-written GBP post from a topic brief",
)
@limiter.limit("20/minute")
async def generate_post(
    request: Request,
    account_id: uuid.UUID,
    body: GBPPostGenerateIn,
    db: AsyncSession = Depends(get_db),
):
    await _get_account_or_404(account_id, db)

    ai_data = await gbp_ai.generate_gbp_post(
        topic_brief=body.topic_brief,
        post_type=body.post_type,
        clinic_name=body.clinic_name,
    )

    post = GBPPost(
        account_id=account_id,
        topic_brief=body.topic_brief,
        post_type=body.post_type,
        ai_title=ai_data.get("title"),
        ai_body=ai_data["body"],
        call_to_action=ai_data.get("call_to_action"),
        status="DRAFT",
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return post

@router.get(
    "/accounts/{account_id}/posts",
    response_model=list[GBPPostOut],
    summary="List all posts for a GBP account",
)
async def list_posts(
    account_id: uuid.UUID,
    response: Response,
    status_filter: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    await _get_account_or_404(account_id, db)
    filters = [GBPPost.account_id == account_id]
    if status_filter:
        filters.append(GBPPost.status == status_filter.upper())

    count_query = select(sa_func.count()).select_from(GBPPost)
    for f in filters:
        count_query = count_query.where(f)
    total = (await db.execute(count_query)).scalar_one()

    query = select(GBPPost)
    for f in filters:
        query = query.where(f)
    query = query.order_by(GBPPost.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    response.headers["X-Total-Count"] = str(total)
    return list(result.scalars().all())

@router.put(
    "/posts/{post_id}",
    response_model=GBPPostOut,
    summary="Edit a post draft before scheduling/publishing",
)
async def update_post(
    post_id: uuid.UUID,
    body: GBPPostUpdateIn,
    db: AsyncSession = Depends(get_db),
):
    post = await db.get(GBPPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.status not in ("DRAFT", "SCHEDULED"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot edit a post with status '{post.status}'",
        )
    if body.final_title is not None:
        post.final_title = body.final_title
    if body.final_body is not None:
        post.final_body = body.final_body
    if body.call_to_action is not None:
        post.call_to_action = body.call_to_action
    await db.commit()
    await db.refresh(post)
    return post

@router.post(
    "/posts/{post_id}/schedule",
    response_model=GBPPostOut,
    summary="Schedule a post for auto-publish at a future datetime",
)
async def schedule_post(
    post_id: uuid.UUID,
    body: GBPPostScheduleIn,
    db: AsyncSession = Depends(get_db),
):
    post = await db.get(GBPPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.status not in ("DRAFT",):
        raise HTTPException(
            status_code=400,
            detail=f"Only DRAFT posts can be scheduled (current status: {post.status})",
        )
    if body.scheduled_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="scheduled_at must be in the future")

    post.scheduled_at = body.scheduled_at
    post.status = "SCHEDULED"
    await db.commit()
    await db.refresh(post)
    return post

@router.post(
    "/posts/{post_id}/publish",
    response_model=GBPPostOut,
    summary="Immediately publish a post to Google Business Profile",
)
async def publish_post_now(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    post = await db.get(GBPPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.status == "PUBLISHED":
        raise HTTPException(status_code=400, detail="Post is already published")

    account = await db.get(GBPAccount, post.account_id)
    if account is None or not account.is_active:
        raise HTTPException(status_code=404, detail="Linked GBP account not found")

    title = post.final_title or post.ai_title
    body_text = post.final_body or post.ai_body
    payload = gbp_api.build_post_payload(post.post_type, title, body_text, post.call_to_action)

    try:
        result_data = await gbp_api.create_local_post(account, payload)
        post.status = "PUBLISHED"
        post.published_at = datetime.now(timezone.utc)
        post.google_post_id = result_data.get("name")
        post.last_error = None
        db.add(account)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to publish post: {exc}")

    await db.commit()
    await db.refresh(post)
    return post

@router.delete(
    "/posts/{post_id}/schedule",
    response_model=GBPPostOut,
    summary="Cancel a scheduled post (reverts to DRAFT)",
)
async def cancel_schedule(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    post = await db.get(GBPPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.status != "SCHEDULED":
        raise HTTPException(status_code=400, detail="Post is not in SCHEDULED status")

    post.status = "DRAFT"
    post.scheduled_at = None
    await db.commit()
    await db.refresh(post)
    return post

@router.post(
    "/accounts/{account_id}/keywords",
    response_model=GBPKeywordOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a keyword to track for this GBP location",
)
async def add_keyword(
    account_id: uuid.UUID,
    body: GBPKeywordCreate,
    db: AsyncSession = Depends(get_db),
):
    await _get_account_or_404(account_id, db)
    keyword = GBPKeyword(account_id=account_id, keyword=body.keyword.lower().strip())
    db.add(keyword)
    await db.commit()
    await db.refresh(keyword)
    return keyword

@router.get(
    "/accounts/{account_id}/keywords",
    response_model=list[GBPKeywordOut],
    summary="List all tracked keywords for a GBP account",
)
async def list_keywords(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _get_account_or_404(account_id, db)
    result = await db.execute(
        select(GBPKeyword)
        .where(GBPKeyword.account_id == account_id)
        .order_by(GBPKeyword.created_at.desc())
    )
    return list(result.scalars().all())

@router.post(
    "/accounts/{account_id}/keywords/sync",
    response_model=SyncResultOut,
    summary="Refresh keyword performance metrics from Google",
)
async def sync_keywords(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    account = await _get_account_or_404(account_id, db)
    result = await db.execute(
        select(GBPKeyword).where(GBPKeyword.account_id == account_id)
    )
    keywords: list[GBPKeyword] = list(result.scalars().all())

    if not keywords:
        return SyncResultOut(synced=0, message="No keywords to sync — add keywords first")

    keyword_strings = [kw.keyword for kw in keywords]
    metrics = await gbp_api.get_search_keyword_counts(account, keyword_strings)
    db.add(account)

    metric_map = {m["keyword"]: m for m in metrics}
    synced = 0
    for kw in keywords:
        data = metric_map.get(kw.keyword, {})
        prev_impressions = kw.impressions

        kw.impressions = data.get("impressions")
        kw.clicks = data.get("clicks")
        kw.average_position = data.get("average_position")
        kw.last_synced_at = datetime.now(timezone.utc)

        if prev_impressions is not None and kw.impressions is not None:
            if kw.impressions > prev_impressions:
                kw.trend = "UP"
            elif kw.impressions < prev_impressions:
                kw.trend = "DOWN"
            else:
                kw.trend = "STABLE"
        else:
            kw.trend = "UNKNOWN"

        synced += 1

    await db.commit()
    return SyncResultOut(synced=synced, message=f"Synced metrics for {synced} keyword(s)")

@router.delete(
    "/keywords/{keyword_id}",
    response_model=MessageOut,
    summary="Remove a tracked keyword",
)
async def delete_keyword(
    keyword_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    kw = await db.get(GBPKeyword, keyword_id)
    if kw is None:
        raise HTTPException(status_code=404, detail="Keyword not found")
    keyword_text = kw.keyword
    await db.delete(kw)
    await db.commit()
    return MessageOut(message=f"Keyword '{keyword_text}' removed")

@router.get(
    "/accounts/{account_id}/seo-suggestions",
    response_model=SEOSuggestionsOut,
    summary="Get AI-powered SEO improvement tips for the GBP location",
)
@limiter.limit("10/minute")
async def get_seo_suggestions(
    request: Request,
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    account = await _get_account_or_404(account_id, db)

    profile_data = {
        "location_name": account.location_name,
        "location_id": account.location_id,
        "is_active": account.is_active,
    }

    kw_result = await db.execute(
        select(GBPKeyword).where(GBPKeyword.account_id == account_id)
    )
    keywords = kw_result.scalars().all()
    keyword_data = [
        {
            "keyword": kw.keyword,
            "impressions": kw.impressions,
            "clicks": kw.clicks,
            "average_position": kw.average_position,
            "trend": kw.trend,
            "last_synced_at": kw.last_synced_at.isoformat() if kw.last_synced_at else None,
        }
        for kw in keywords
    ]

    rev_result = await db.execute(
        select(GBPReview).where(GBPReview.account_id == account_id)
    )
    reviews = rev_result.scalars().all()
    total_reviews = len(reviews)
    ratings = [r.rating for r in reviews if r.rating]
    avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else None
    unanswered = sum(1 for r in reviews if not r.has_existing_reply)

    profile_data.update({
        "total_reviews": total_reviews,
        "average_rating": avg_rating,
        "unanswered_reviews": unanswered,
        "total_keywords_tracked": len(keywords),
    })

    raw_suggestions = await gbp_ai.generate_seo_suggestions(profile_data, keyword_data)

    suggestions = [
        SEOSuggestion(
            priority=s.get("priority", "MEDIUM"),
            category=s.get("category", "General"),
            suggestion=s.get("suggestion", ""),
            rationale=s.get("rationale", ""),
        )
        for s in raw_suggestions
    ]

    return SEOSuggestionsOut(
        account_id=account_id,
        generated_at=datetime.now(timezone.utc),
        suggestions=suggestions,
    )

def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
