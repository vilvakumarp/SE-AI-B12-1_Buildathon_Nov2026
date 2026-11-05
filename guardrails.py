"""Hard limits and validation rules for model-backed support calls."""
import os
import re

MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "4000"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "400"))
MAX_API_SECONDS = float(os.getenv("MAX_API_SECONDS", "30"))
MAX_AGENT_CALLS = 4


def validate_input(text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise ValueError("Complaint cannot be empty.")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(f"Complaint is too long. Limit is {MAX_INPUT_CHARS} characters.")
    return text


def validate_analysis(value: dict) -> bool:
    return value.get("category") in {"Shipping", "Refund", "Account", "Product", "Billing"} and value.get("urgency") in {"Low", "Medium", "High"} and value.get("sentiment") in {"Neutral", "Frustrated", "Angry", "Happy"}


def validate_resolution(value: dict, complaint: str = "", evidence: str = "") -> bool:
    response = value.get("response", "")
    if not (isinstance(response, str) and response.strip() and len(response) <= 3000 and value.get("confidence") in {"High", "Medium", "Low"}):
        return False
    # Supported customer languages are Tamil and English/Tanglish only.
    # English product and process terms remain permitted in Tamil responses.
    unsupported_scripts = r"[\u0900-\u097F\u0600-\u06FF\u0400-\u04FF\u4E00-\u9FFF\u0980-\u09FF\u0C00-\u0C7F\u0D00-\u0D7F\u0C80-\u0CFF]"
    if re.search(unsupported_scripts, response):
        return False
    # English input must receive English only; do not force Tamil into an
    # English conversation. Tamil/Tanglish input may use Tamil script.
    if not re.search(r"[\u0B80-\u0BFF]", complaint) and re.search(r"[\u0B80-\u0BFF]", response):
        return False
    timeline_pattern = r"\b\d+\s*[-–]\s*\d+\s*(?:working\s+)?days?\b|\bwithin\s+\d+\s+(?:days?|hours?|weeks?|months?)\b|\b\d+\s+(?:days?|hours?|weeks?|months?)\b|\b(?:tomorrow|today|next week|after \d+)\b"
    if re.search(timeline_pattern, response, flags=re.IGNORECASE) and not re.search(timeline_pattern, evidence, flags=re.IGNORECASE):
        return False
    # Reject common off-topic Tamil insertions unless the same subject exists
    # in the customer's request or retrieved evidence.
    unrelated_markers = ("நகை", "நகைகளை", "வரம்பு", "வரம்புகளை")
    source_text = f"{complaint} {evidence}"
    if any(marker in response and marker not in source_text for marker in unrelated_markers):
        return False
    # For English/code-switched text, require at least one meaningful subject
    # word from the request/evidence in the answer.
    source_words = set(re.findall(r"[a-zA-Z]{4,}", source_text.lower()))
    answer_words = set(re.findall(r"[a-zA-Z]{4,}", response.lower()))
    stop = {"this", "that", "with", "your", "from", "have", "will", "please", "about", "customer", "support", "should", "could"}
    if source_words and answer_words and not ((source_words - stop) & (answer_words - stop)):
        return False
    return True
