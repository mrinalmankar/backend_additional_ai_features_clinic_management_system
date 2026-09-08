from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx

from app.core.config import settings
from app.services.gbp_oauth import decrypt_token, encrypt_token, refresh_access_token

if TYPE_CHECKING:
    from app.models.gbp import GBPAccount

BUFFER_SECONDS = 120

async def _get_valid_token(account: "GBPAccount") -> str:
    now = datetime.now(timezone.utc)
    if (
        account.token_expires_at is None
        or account.token_expires_at <= now + timedelta(seconds=BUFFER_SECONDS)
    ):
        new_token, new_expires = await refresh_access_token(account.refresh_token_enc)
        account.access_token_enc = encrypt_token(new_token)
        account.token_expires_at = new_expires
        return new_token

    return decrypt_token(account.access_token_enc)

async def list_reviews(account: "GBPAccount") -> list[dict[str, Any]]:
    if settings.gbp_mock_mode:
        return _mock_reviews()

    token = await _get_valid_token(account)
    location = account.location_id
    url = f"https://mybusiness.googleapis.com/v4/{location}/reviews"

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        data = resp.json()
        return data.get("reviews", [])

async def post_reply(account: "GBPAccount", review_name: str, reply_text: str) -> dict[str, Any]:
    if settings.gbp_mock_mode:
        return {"comment": reply_text, "updateTime": datetime.utcnow().isoformat() + "Z"}

    token = await _get_valid_token(account)
    url = f"https://mybusiness.googleapis.com/v4/{review_name}/reply"

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.put(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"comment": reply_text},
        )
        resp.raise_for_status()
        return resp.json()

async def create_local_post(account: "GBPAccount", post_data: dict[str, Any]) -> dict[str, Any]:
    if settings.gbp_mock_mode:
        return {
            "name": f"{account.location_id}/localPosts/mock_{secrets.token_hex(6)}",
            "state": "LIVE",
            "topicType": post_data.get("topicType", "STANDARD"),
            "summary": post_data.get("summary", ""),
        }

    token = await _get_valid_token(account)
    url = f"https://mybusiness.googleapis.com/v4/{account.location_id}/localPosts"

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=post_data,
        )
        resp.raise_for_status()
        return resp.json()

def build_post_payload(post_type: str, title: str | None, body: str, cta: str | None) -> dict[str, Any]:

    topic_map = {
        "WHATS_NEW": "STANDARD",
        "OFFER": "OFFER",
        "EVENT": "EVENT",
        "PRODUCT": "PRODUCT",
    }
    payload: dict[str, Any] = {
        "topicType": topic_map.get(post_type, "STANDARD"),
        "summary": body,
    }
    if title:
        payload["event"] = {"title": title, "schedule": {}}
    if cta:
        payload["callToAction"] = {"actionType": "LEARN_MORE", "url": cta}
    return payload

async def get_search_keyword_counts(
    account: "GBPAccount", keywords: list[str]
) -> list[dict[str, Any]]:
    if settings.gbp_mock_mode:
        return _mock_keyword_insights(keywords)

    token = await _get_valid_token(account)
    location = account.location_id
    url = (
        "https://businessprofileperformance.googleapis.com/v1/"
        f"{location}:fetchMultiDailyMetricsTimeSeries"
    )

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=28)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={
                "dailyMetrics": [
                    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
                    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
                    "WEBSITE_CLICKS",
                ],
                "dailyRange.startDate.year": start.year,
                "dailyRange.startDate.month": start.month,
                "dailyRange.startDate.day": start.day,
                "dailyRange.endDate.year": end.year,
                "dailyRange.endDate.month": end.month,
                "dailyRange.endDate.day": end.day,
            },
        )
        resp.raise_for_status()
        raw = resp.json()

    return _aggregate_keyword_metrics(keywords, raw)

def _mock_reviews() -> list[dict[str, Any]]:
    import random
    names = ["Priya Sharma", "Rahul Mehta", "Ananya Iyer", "Deepak Kulkarni", "Sneha Patil"]
    texts = [
        "Excellent clinic! The doctor was very thorough and explained everything clearly.",
        "Very professional staff. The waiting time was a bit long but worth it.",
        "Great experience overall. Highly recommend for dental issues.",
        "The doctor is knowledgeable but the clinic could improve its appointment system.",
        "Amazing service! My teeth feel great after the treatment.",
    ]
    reviews = []
    for i, (name, text) in enumerate(zip(names, texts)):
        reviews.append({
            "name": f"accounts/123/locations/456/reviews/review_{i + 1:03d}",
            "reviewer": {
                "displayName": name,
                "profilePhotoUrl": f"https://i.pravatar.cc/80?img={i + 10}",
            },
            "starRating": random.choice(["THREE", "FOUR", "FIVE"]),
            "comment": text,
            "createTime": f"2026-08-{20 + i:02d}T10:00:00Z",
            "reviewReply": None,
        })
    return reviews

def _mock_keyword_insights(keywords: list[str]) -> list[dict[str, Any]]:
    import random
    results = []
    for kw in keywords:
        results.append({
            "keyword": kw,
            "impressions": random.randint(50, 1200),
            "clicks": random.randint(5, 150),
            "average_position": round(random.uniform(1.5, 8.0), 1),
        })
    return results

def _aggregate_keyword_metrics(
    keywords: list[str], raw: dict[str, Any]
) -> list[dict[str, Any]]:

    multi_series = raw.get("multiDailyMetricTimeSeries", [])
    total_impressions = 0
    total_clicks = 0
    for series_entry in multi_series:
        for dms in series_entry.get("dailyMetricTimeSeries", []):
            metric = dms.get("dailyMetric", "")
            for pt in dms.get("timeSeries", {}).get("datedValues", []):
                val = int(pt.get("value", 0))
                if "IMPRESSIONS" in metric:
                    total_impressions += val
                elif "CLICKS" in metric:
                    total_clicks += val

    n = max(len(keywords), 1)
    return [
        {
            "keyword": kw,
            "impressions": total_impressions // n,
            "clicks": total_clicks // n,
            "average_position": None,
        }
        for kw in keywords
    ]
