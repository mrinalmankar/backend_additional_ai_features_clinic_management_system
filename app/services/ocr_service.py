import re

import pytesseract
from PIL import Image
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
_FREQUENCY_PATTERNS = [
    r"\b\d\s*-\s*\d\s*-\s*\d\b",
    r"\bOD\b", r"\bBD\b", r"\bTDS\b", r"\bQID\b", r"\bHS\b", r"\bSOS\b",
    r"\bonce\s+daily\b", r"\btwice\s+daily\b", r"\bthrice\s+daily\b",
    r"\bevery\s+\d+\s+hours?\b",
]
_FREQUENCY_RE = re.compile("|".join(_FREQUENCY_PATTERNS), re.IGNORECASE)

_DOSAGE_RE = re.compile(r"\b\d+(\.\d+)?\s*(mg|mcg|ml|g)\b", re.IGNORECASE)

class OCRServiceError(Exception):
    pass

def extract_raw_text(image_path: str) -> str:
    try:
        image = Image.open(image_path)
        return pytesseract.image_to_string(image)
    except Exception as exc:
        raise OCRServiceError(f"OCR extraction failed: {exc}") from exc

def parse_candidate_line_items(raw_text: str) -> list[dict]:
    items = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue

        dosage_match = _DOSAGE_RE.search(line)
        frequency_match = _FREQUENCY_RE.search(line)

        drug_name = line
        if dosage_match:
            drug_name = drug_name.replace(dosage_match.group(0), "")
        if frequency_match:
            drug_name = drug_name.replace(frequency_match.group(0), "")
        drug_name = re.sub(r"\s{2,}", " ", drug_name).strip(" -,.")

        items.append(
            {
                "raw_line": line,
                "drug_name": drug_name or None,
                "dosage": dosage_match.group(0) if dosage_match else None,
                "frequency": frequency_match.group(0) if frequency_match else None,
            }
        )
    return items
