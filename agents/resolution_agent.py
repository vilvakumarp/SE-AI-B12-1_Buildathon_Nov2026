def create_resolution(complaint: str, analysis: dict, knowledge: dict) -> dict:
    if not knowledge["sources"]:
        return {"response": "I’m sorry, but I could not find a relevant company policy for this request. A human support specialist will review it.", "confidence": "Low"}
    evidence = knowledge["summary"].strip()
    sources = ", ".join(dict.fromkeys(knowledge.get("sources", [])))
    # The starter response intentionally quotes retrieved evidence rather than
    # inventing policy details. AutoGen can later turn this evidence into a
    # polished customer-facing response while preserving the same contract.
    passages = [part.strip() for part in evidence.replace("\r", "").split("\n") if part.strip()]
    focused = " ".join(passages[:3])[:650]
    response = f"Based on the company knowledge base, here is the relevant guidance:\n\n{focused}\n\nSource: {sources}"
    return {"response": response, "confidence": "High"}
