from __future__ import annotations

import json

import httpx

from app.core.config import settings

def _strip_json_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```json"):
        text = text.removeprefix("```json")
    elif text.startswith("```"):
        text = text.removeprefix("```")
    return text.removesuffix("```").strip()

class GBPAIServiceError(Exception):
    pass

async def _call_groq(system_prompt: str, user_content: str, temperature: float = 0.4) -> str:
    if not settings.groq_api_key:
        raise GBPAIServiceError("groq_API_KEY is not configured")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={
                "model": settings.groq_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": temperature,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

async def _call_ollama(system_prompt: str, user_content: str, temperature: float = 0.4) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "stream": False,
                "options": {"temperature": temperature},
            },
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()

async def _llm(system_prompt: str, user_content: str, temperature: float = 0.4) -> str:
    primary, fallback = (
        (_call_groq, _call_ollama)
        if settings.llm_provider == "groq"
        else (_call_ollama, _call_groq)
    )
    try:
        return await primary(system_prompt, user_content, temperature)
    except Exception as pe:
        try:
            return await fallback(system_prompt, user_content, temperature)
        except Exception as fe:
            raise GBPAIServiceError(
                f"Both LLM providers failed. primary={pe!r} fallback={fe!r}"
            ) from fe

_REVIEW_REPLY_SYSTEM = """\
You are a professional clinic reputation manager. Your job is to write warm, \
empathetic, and professional replies to Google Business Profile reviews on \
behalf of a dental/medical clinic.

Rules:
- Thank the reviewer by name if a name is available.
- Address specific points raised in the review.
- For negative reviews: apologise sincerely, invite them to reach out directly, \
  never argue or make excuses.
- Keep replies concise: 2-4 sentences maximum.
- End with a warm closing line that includes the clinic name.
- Never invent medical claims or discount offers.
- Reply ONLY with the reply text, no labels or explanation.
"""

async def generate_review_reply(
    reviewer_name: str | None,
    rating: int | None,
    review_text: str | None,
    clinic_name: str = "our clinic",
    tone: str = "professional",
) -> str:
    user_content = (
        f"Clinic name: {clinic_name}\n"
        f"Preferred tone: {tone}\n"
        f"Reviewer name: {reviewer_name or 'Anonymous'}\n"
        f"Star rating: {rating or 'not given'} / 5\n"
        f"Review text: {review_text or '(no written review)'}\n\n"
        "Write a reply to this review."
    )
    return await _llm(_REVIEW_REPLY_SYSTEM, user_content, temperature=0.5)

_POST_GEN_SYSTEM = """\
You are a social media content expert specialising in Google Business Profile \
posts for dental / medical clinics in India.

Given a topic brief, generate a Google Business Profile post.

Return your response as valid JSON with exactly these keys:
{
  "title": "Short punchy headline (max 58 chars, or null if not applicable)",
  "body": "Post body text (100-200 chars, engaging, emoji welcome)",
  "call_to_action": "Optional CTA text (e.g. 'Book now at apexdental.in') or null"
}

Rules:
- Write in a warm, trustworthy, professional tone.
- Highlight the patient benefit, not technical jargon.
- Optimise for local SEO: include clinic name and city naturally.
- Do NOT use markdown in the output JSON values.
- Return ONLY the JSON object, no surrounding explanation.
"""

async def generate_gbp_post(
    topic_brief: str,
    post_type: str,
    clinic_name: str = "our clinic",
) -> dict[str, str | None]:
    user_content = (
        f"Clinic name: {clinic_name}\n"
        f"Post type: {post_type}\n"
        f"Topic / offer brief: {topic_brief}"
    )
    raw = await _llm(_POST_GEN_SYSTEM, user_content, temperature=0.65)

    try:
        clean = _strip_json_fences(raw)
        data = json.loads(clean)
        return {
            "title": data.get("title"),
            "body": data.get("body", raw),
            "call_to_action": data.get("call_to_action"),
        }
    except (json.JSONDecodeError, AttributeError):

        return {"title": None, "body": raw, "call_to_action": None}

_SEO_SYSTEM = """\
You are an expert in Google Business Profile (GBP) local SEO for medical and \
dental clinics in India.

Analyse the provided profile data and keyword performance metrics and return \
exactly 6 actionable SEO improvement tips as a JSON array.

Each tip must be a JSON object with:
{
  "priority": "HIGH" | "MEDIUM" | "LOW",
  "category": one of ["Profile Completeness", "Reviews", "Posts", "Keywords", \
               "Photos", "Q&A", "Business Hours", "Local Citations"],
  "suggestion": "One-sentence actionable tip",
  "rationale": "One-sentence explanation of why this helps ranking"
}

Return ONLY a JSON array of 6 objects, no markdown, no explanation.
"""

async def generate_seo_suggestions(
    profile_data: dict,
    keyword_data: list[dict],
) -> list[dict]:
    user_content = (
        "=== PROFILE DATA ===\n"
        f"{json.dumps(profile_data, indent=2)}\n\n"
        "=== KEYWORD PERFORMANCE (last 28 days) ===\n"
        f"{json.dumps(keyword_data, indent=2)}\n\n"
        "Generate 6 SEO improvement suggestions."
    )
    raw = await _llm(_SEO_SYSTEM, user_content, temperature=0.3)

    try:
        clean = _strip_json_fences(raw)
        suggestions = json.loads(clean)
        if isinstance(suggestions, list):
            return suggestions
        return [suggestions]
    except (json.JSONDecodeError, AttributeError):

        return [
            {
                "priority": "HIGH",
                "category": "Profile Completeness",
                "suggestion": raw[:250],
                "rationale": "AI analysis generated unstructured output; review manually.",
            }
        ]
