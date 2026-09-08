import httpx

from app.core.config import settings

SUMMARY_SYSTEM_PROMPT = (
    "You are a clinical documentation assistant. You will be given a raw "
    "doctor-patient consultation transcript. Produce a short, structured "
    "summary with exactly these sections:\n"
    "1. Chief complaint\n"
    "2. Key findings / diagnosis\n"
    "3. Advice given to patient\n\n"
    "Rules:\n"
    "- Only summarize what is present in the transcript. Do not infer or "
    "add medical information that was not stated.\n"
    "- If a section has no relevant content in the transcript, write "
    "'Not discussed' for that section.\n"
    "- This summary is a draft for the doctor to review and edit. It is "
    "not a final clinical record.\n"
    "- Keep it concise: a few sentences per section, not paragraphs."
)

class LLMServiceError(Exception):
    pass

async def _call_groq(transcript_text: str) -> str:
    if not settings.groq_api_key:
        raise LLMServiceError("groq_API_KEY is not configured")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={
                "model": settings.groq_model,
                "messages": [
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": transcript_text},
                ],
                "temperature": 0.2,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

async def _call_ollama(transcript_text: str) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": [
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": transcript_text},
                ],
                "stream": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["message"]["content"].strip()

async def generate_consultation_summary(transcript_text: str) -> str:
    if not settings.allow_third_party_llm_for_phi:
        try:
            return await _call_ollama(transcript_text)
        except Exception as exc:
            raise LLMServiceError(
                "Local LLM provider (Ollama) failed, and falling back to a "
                "third-party provider is disabled because transcripts contain "
                "patient PHI (set ALLOW_THIRD_PARTY_LLM_FOR_PHI=true to permit "
                f"groq as a fallback): {exc!r}"
            ) from exc

    primary, fallback = (
        (_call_groq, _call_ollama) if settings.llm_provider == "groq" else (_call_ollama, _call_groq)
    )

    try:
        return await primary(transcript_text)
    except Exception as primary_error:
        try:
            return await fallback(transcript_text)
        except Exception as fallback_error:
            raise LLMServiceError(
                f"Both LLM providers failed. primary={primary_error!r} fallback={fallback_error!r}"
            ) from fallback_error
