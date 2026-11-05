import re

def sentiment_from_text(complaint: str) -> str:
    text = str(complaint or "").lower()
    angry_markers = (
        "fix this now", "ridiculous", "terrible", "worst", "unacceptable",
        "lawyer", "fraud", "fraudulent", "scam", "cheated", "cheating",
        "consumer court", "consumer forum", "file a case", "filing a case",
        "take legal action", "legal notice", "police complaint", "three times",
        "again and again", "you are a thief", "you people are cheating",
        "useless", "idiot", "stupid", "damn", "bloody nonsense",
        "மோசடி", "ஏமாற்றம்", "கோபம்"
    )
    frustrated_markers = ("not working", "still waiting", "not received", "not delivered", "late", "broken", "failed", "can't", "cannot", "unable", "why", "வரவில்லை", "வேலை செய்யவில்லை", "தாமதம்", "பிரச்சினை", "புகார்")
    positive_markers = ("thank", "thanks", "happy", "excellent", "great", "helpful", "resolved")
    # Evaluate the meaning of the message, not a single isolated token. A
    # threat, accusation, or abusive phrase is treated as angry when it is
    # directed at the support interaction; ordinary positive language does not
    # cancel a clearly heated complaint.
    escalation_context = any(phrase in text for phrase in ("consumer court", "consumer forum", "file a case", "filing a case", "legal action", "legal notice", "police complaint"))
    accusation_context = any(phrase in text for phrase in ("you cheated", "you are cheating", "cheated me", "fraudulent charge", "this is fraud", "this is a scam", "you are a thief"))
    repeated_failure = any(phrase in text for phrase in ("still not", "again and again", "multiple times", "third time", "no one has helped"))
    negative_context = any(marker in text for marker in frustrated_markers) or repeated_failure
    direct_heat = any(marker in text for marker in ("fix this now", "ridiculous", "terrible", "worst", "unacceptable", "you are a thief", "useless", "idiot", "stupid", "damn", "bloody nonsense", "மோசடி", "ஏமாற்றம்", "கோபம்"))
    if (escalation_context and (negative_context or accusation_context or "complaint" in text or "problem" in text)) or accusation_context or direct_heat or text.count("!") >= 2:
        return "Angry"
    if any(marker in text for marker in frustrated_markers):
        return "Frustrated"
    if any(marker in text for marker in positive_markers):
        return "Happy"
    return "Neutral"

def sentiment_from_conversation(messages) -> str:
    text = " ".join(str(item.get("content", "")) for item in (messages or []) if item.get("role", "customer") == "customer")
    return sentiment_from_text(text)


def analyze_query(complaint: str) -> dict:
    text = complaint.lower()
    if any(k in text for k in ("refund", "return", "money back")):
        category = "Refund"
    elif any(k in text for k in ("password", "login", "log in", "account")):
        category = "Account"
    elif any(k in text for k in ("charged", "billing", "payment", "upi", "transaction")):
        category = "Billing"
    elif any(k in text for k in ("broken", "damaged", "replacement", "blender")):
        category = "Product"
    else:
        category = "Shipping"
    high = any(k in text for k in ("lawyer", "fraud", "bank", "three times", "urgent", "10 days late"))
    angry = any(k in text for k in ("terrible", "怒", "fix this now", "ridiculous", "lawyer", "bank", "three times")) or text.count("!") >= 2
    urgency = "High" if high else ("Medium" if any(k in text for k in ("late", "broken", "can't", "cannot")) else "Low")
    sentiment = sentiment_from_text(complaint)
    cleaned = re.sub(r"\s+", " ", complaint).strip()
    vague_markers = ("complaint", "issue", "problem", "help", "support", "something unusual", "not working", "what happened", "can you fix", "same issue", "this issue", "that issue")
    known_subjects = ("order", "payment", "charged", "refund", "return", "delivery", "late", "broken", "damaged", "password", "login", "account", "upi", "transaction", "product", "item", "website", "app")
    vague_request = (len(cleaned) < 90 and any(k in text for k in vague_markers) and not any(k in text for k in known_subjects)) or (len(cleaned.split()) <= 5 and any(k in text for k in ("help", "issue", "problem", "not working", "unusual")) and not any(k in text for k in known_subjects))
    return {"category": category, "urgency": urgency, "sentiment": sentiment, "summary": cleaned[:160], "needs_clarification": vague_request}

def clarification_response(analysis: dict, complaint: str = "") -> str:
    if re.search(r"[\u0B80-\u0BFF]", complaint):
        return "இந்தப் புகாரை பதிவு செய்ய நான் உதவுகிறேன். நிகழ்ந்த பிரச்சினையைச் சுருக்கமாகவும், அது விநியோகம், கட்டணம், பணத்தைத் திரும்பப் பெறுதல், தயாரிப்பு அல்லது கணக்கு தொடர்பானதா என்பதையும் தெரிவிக்கவும். கடவுச்சொல் அல்லது OTP-ஐ பகிர வேண்டாம்."
    return "I can help you raise this complaint. Could you briefly tell me what happened and whether it relates to delivery, payment, refund, product, or account? Please do not share passwords or OTPs."
