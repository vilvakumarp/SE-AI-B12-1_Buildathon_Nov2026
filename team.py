"""Pipeline wiring for the four support agents."""
import os
from agents.query_agent import analyze_query, clarification_response
from agents.knowledge_agent import retrieve_knowledge
from agents.resolution_agent import create_resolution
from agents.log_agent import log_ticket
from observability import traceable

def register_confirmed_complaint(complaint: str, customer_name: str = "", order_id: str = "", sentiment_override: str = "") -> dict:
    """Send an accepted complaint summary directly to the logging/escalation stage."""
    analysis = analyze_query(complaint)
    if sentiment_override in {"Angry", "Frustrated", "Neutral", "Happy"}:
        analysis["sentiment"] = sentiment_override
    knowledge = retrieve_knowledge(complaint, analysis["category"]) or {"summary": "No matching company policy was found.", "sources": [], "scores": [], "links": []}
    resolution = create_resolution(complaint, analysis, knowledge)
    acknowledgement = "Complaint has been registered under the applicable SOP. The submitted details have been recorded. Support will respond within the timeframe specified in the SOP and provide updates."
    return log_ticket(complaint, customer_name, order_id, analysis, knowledge, resolution, initial_status="Open", response_override=acknowledgement)


@traceable("customer_support_team")
async def run_support_team(complaint: str, customer_name: str = "", order_id: str = "", progress_callback=None, record_ticket: bool = True, session_context: str = "") -> dict:
    understanding_text = f"{session_context}\nCurrent customer message: {complaint}" if session_context else complaint
    early_analysis = analyze_query(understanding_text)
    if early_analysis.get("needs_clarification"):
        if progress_callback: progress_callback("Query Agent", "Requesting clarification before searching the knowledge base")
        return {"ticket_id": "", "status": "Needs clarification", "response": clarification_response(early_analysis, complaint), "category": early_analysis["category"], "analysis": early_analysis, "knowledge": {"sources": []}, "confidence": "Low"}
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required because this support workflow always uses AutoGen agents.")
    from autogen_team import run_autogen_support
    return await run_autogen_support(complaint, customer_name, order_id, progress_callback, record_ticket, session_context)
    if progress_callback: progress_callback("Query Agent", "Analyzing complaint")
    analysis = analyze_query(complaint)
    if progress_callback: progress_callback("Knowledge Base Agent", "Searching ChromaDB")
    knowledge = retrieve_knowledge(understanding_text, analysis["category"]) or {"summary": "No matching company policy was found.", "sources": [], "scores": [], "links": []}
    if progress_callback: progress_callback("Resolution Agent", "Drafting evidence-grounded response")
    resolution = create_resolution(complaint, analysis, knowledge)
    if not record_ticket:
        return {"ticket_id": "", "status": "Chat", "response": resolution["response"], "analysis": analysis, "knowledge": knowledge, "confidence": resolution["confidence"]}
    if progress_callback: progress_callback("Log Agent", "Recording confirmed complaint and applying escalation rules")
    return log_ticket(complaint, customer_name, order_id, analysis, knowledge, resolution)
