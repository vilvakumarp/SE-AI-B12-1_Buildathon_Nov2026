"""AutoGen-backed support workflow with deterministic retrieval and logging."""
import json
import os
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient
from agents.knowledge_agent import retrieve_knowledge
from agents.log_agent import log_ticket
from agents.query_agent import analyze_query
from agents.resolution_agent import create_resolution
from guardrails import MAX_AGENT_CALLS, MAX_API_SECONDS, MAX_OUTPUT_TOKENS, validate_analysis, validate_input, validate_resolution
from time_utils import today_ist
from usage_tracker import record_usage

QUERY_AGENT_PROMPT = """You are Customer Query Agent for a customer-support desk.
Your only responsibility is to understand and classify the customer's current request.
Use the recent session context to resolve references such as 'this', 'that', 'still', and 'again', but treat the latest customer message as the active request.
Do not answer the customer, quote policy, retrieve documents, ask for evidence, or create a ticket.
Choose exactly one category: Shipping, Refund, Account, Product, or Billing.
Choose urgency: High for legal threats, fraud, repeated charges, security risk, or explicit urgent harm; Medium for delivery delay, failed payment, damaged product, blocked access, or a refund explicitly outside the documented timeline; routine return/refund questions and general product enquiries are Low.
Choose sentiment: Angry, Frustrated, Neutral, or Happy based on the customer's tone, not the topic.
Set needs_clarification true only when the actual problem cannot be understood from the current message plus context.
Return JSON only with: category, urgency, sentiment, summary, needs_clarification.
The summary must describe the customer's issue in one crisp sentence, not summarize the conversation."""

KNOWLEDGE_AGENT_PROMPT = """You are Knowledge Base Agent for a grounded customer-support workflow.
Your only responsibility is to select the smallest set of relevant evidence from the supplied BM25/ChromaDB retrieval.
Use the current request and context to distinguish the actual issue from greetings, follow-up wording, and unrelated messages.
Never invent a policy, infer an SLA, combine unrelated passages, or use general model knowledge.
Do not write a customer reply and do not make an escalation decision.
Reject weak or unrelated retrieval rather than presenting it as guidance.
When evidence contains a timeline, preserve the timeline and its conditions exactly. When evidence does not contain a timeline, mark it as insufficient; never infer one.
Preserve source filenames exactly as supplied.
Return JSON only with: summary, sources, evidence_found.
The summary must contain only the relevant facts needed by the Resolution Agent and should be concise."""

RESOLUTION_AGENT_PROMPT = """You are Resolution Agent for a customer-support desk.
Your responsibility is to answer the customer's actual question using only the selected knowledge-base evidence and recent session context.
First identify what the customer is trying to achieve; do not respond to the last sentence in isolation when it is a follow-up.
If the requirement is unclear, ask one focused clarification question instead of dumping retrieved text.
If the request is clear, provide a short, ordered, actionable response. Explain only the policy steps relevant to this issue.
Every date, hour, day, week, month, waiting period, refund window, or escalation timeline must be directly supported by the supplied SOP evidence.
For a potential complaint, request proportionate evidence: payment failure may need a screenshot or transaction proof; damaged/wrong product may need a product photo and order/bill; refund may need order/refund proof; delivery delay does not require a product photo.
Never request passwords, OTPs, full card numbers, or unnecessary personal data.
Do not promise an outcome or deadline unless explicitly supported by the evidence.
Use the customer's language: English input gets English only; Tamil input gets formal standard Tamil; mixed input may use natural Tanglish. Do not use local slang or another script.
Do not include raw retrieved passages or duplicate source citations in the response.
Return JSON only with: response and confidence, where confidence is High, Medium, or Low."""

SENIOR_EDITOR_PROMPT = """You are Senior Editor and final quality reviewer for customer support.
Review the Query/Knowledge/Resolution outputs against the customer's current request and recent session context before delivery.
Perform three internal review passes: verify the unresolved goal and context, verify every policy/timeline/evidence request against the SOP evidence, then proofread grammar, word choice, and language naturalness.
Remove irrelevant content, repeated information, raw RAG dumps, unsupported claims, invented policies, accidental topic changes, and awkward literal translations.
Ensure the answer directly addresses the customer's goal and uses the conversation context when the latest message is vague.
If the requirement is still unclear, preserve one focused clarification question; do not answer a different question.
Keep the response concise, polite, professional, and actionable.
Language rules: English input must produce English only; Tamil input must use formal standard Tamil; Tanglish may preserve natural English product/process terms. Never introduce Hindi or another unsupported script. Preserve valid product terms such as TV, order, payment, screenshot, UPI, account, refund, and support when clearer in English.
Do not add a source name or claim that a complaint was registered unless the workflow explicitly confirms it.
Never invent numeric timelines such as hours or working days. If the evidence does not state a timeline, do not promise one.
Return JSON only with: response and confidence, where confidence is High, Medium, or Low."""

def _polish_tamil_support(text):
    """Correct a few common literal Tamil support translations."""
    replacements = {
        "காச்செட்டுகள்": "cache",
        "காச்செட்டுகளை": "cache-ஐ",
        "குமுதிக் கையெழுத்துகள்": "cookies",
        "குமுதிக் கையெழுத்துகளை": "cookies-ஐ",
        "உரிய சுருக்கப்பட்ட பட்டிகளைப் பார்க்கவும்": "Settings பகுதிக்குச் செல்லவும்",
        "இந்த நடவடிக்கைகள் உங்களுக்குத் தேவையான இடத்தை உருவாக்க வேண்டும்.": "இந்த நடவடிக்கைகள் சிக்கலைத் தீர்க்க உதவும்.",
        "நீங்கள் எங்களை மேலும் உதவ முடியும்.": "உங்களுக்கு உதவ, இது எங்களுக்கு உதவியாக இருக்கும்.",
        "பிறகு, உங்கள் குறைப்பிற்கு நீங்கள் தேவையான பேரியலுக்கு நாங்கள் தயார்.": "அதன் பின்னர், உங்கள் புகாரைத் தீர்க்க தேவையான அடுத்தடுத்த நடவடிக்கைகளை மேற்கொள்ள நாங்கள் உதவுகிறோம்.",
        "உங்கள் குறைப்பு தொடர்பான": "உங்கள் புகார் தொடர்பான",
        "செயற்படுத்த அனுப்புங்கள்": "பதிவேற்றவும்",
        "எங்களுக்குச் செயற்படுத்த": "எங்களிடம் பதிவேற்ற",
        "தேவையான பேரியலுக்கு": "அடுத்தடுத்த நடவடிக்கைகளுக்கு",
        "புகைப்படத்தை எங்களிடம் பதிவேற்றவும்": "புகைப்படத்தை எங்களிடம் பதிவேற்றவும்",
    }
    for source, target in replacements.items(): text = text.replace(source, target)
    return text

def _json(text, fallback):
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start < 0 or end < start: return fallback
    try:
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, dict) else fallback
    except json.JSONDecodeError: return fallback

async def _run(agent, task):
    result = await agent.run(task=task)
    for message in reversed(result.messages):
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip(): return content, getattr(message, "models_usage", None)
    return "", None

async def summarize_complaint_history(history, customer_name=""):
    """Create a focused complaint summary from the whole current chat."""
    entries = [{"role": str(item.get("role", "")), "content": str(item.get("content", ""))} for item in (history or []) if str(item.get("content", "")).strip()]
    fallback = " ".join(item["content"] for item in entries if item["role"] == "customer")[-1200:]
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not entries:
        return fallback
    client = OpenAIChatCompletionClient(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), api_key=api_key, max_tokens=300, timeout=MAX_API_SECONDS, max_retries=1)
    try:
        editor = AssistantAgent(name="senior_editor_complaint_summary", model_client=client, system_message="""You are the Senior Editor responsible for preparing a complaint for registration.
Review the COMPLETE current conversation, not just the last customer message. Identify the customer's actual unresolved problem: what went wrong, which product/order/payment/account is affected, the relevant event or failed outcome, and what remedy or support the customer is requesting. Use the customer's earlier messages to resolve references such as 'this', 'it', 'same issue', or 'not resolved'. Treat a final request such as 'raise a complaint', 'what is my ticket number', or 'please help' as an instruction, not as the complaint itself.
Ignore greetings, casual messages, voice-test messages, agent answers, policy dumps, ticket-number questions, and repeated confirmations. Do not invent an order number, date, amount, deadline, policy, or cause. If the conversation contains no concrete problem, state that more details are required rather than manufacturing one.
Perform three internal passes before returning: (1) extract the actual problem and supporting facts, (2) remove unrelated chat and unsupported claims, (3) proofread for a crisp customer-facing complaint. Keep it to one or two sentences. Preserve technical terms such as order, payment, refund, UPI, screenshot, and support where clearer. Use the customer's language style: English for English input, formal standard Tamil for Tamil input, and natural Tanglish only when the customer used Tanglish. Never use literal, awkward, slang, or unrelated Tamil.
Return JSON only: {\"summary\": \"...\"}.""")
        text, _ = await _run(editor, json.dumps({"customer": customer_name, "conversation": entries}, ensure_ascii=False))
        parsed = _json(text, {"summary": fallback})
        return str(parsed.get("summary") or fallback).strip()[:1200]
    except Exception:
        return fallback
    finally:
        await client.close()

async def run_autogen_support(complaint, customer_name="", order_id="", progress_callback=None, record_ticket=True, session_context=""):
    complaint = validate_input(complaint)
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key: raise RuntimeError("OPENAI_API_KEY is missing; cannot run AutoGen workflow.")
    client = OpenAIChatCompletionClient(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), api_key=api_key, max_tokens=MAX_OUTPUT_TOKENS, timeout=MAX_API_SECONDS, max_retries=1)
    calls = 0
    try:
        query_agent = AssistantAgent(name="customer_query_agent", model_client=client, system_message=QUERY_AGENT_PROMPT)
        if progress_callback: progress_callback("Query Agent", "Analyzing complaint with AutoGen")
        contextual_query = f"{session_context}\nCurrent customer message: {complaint}" if session_context else complaint
        fallback = analyze_query(contextual_query)
        calls += 1
        query_text, query_usage = await _run(query_agent, f"Current local date in India is {today_ist()} (IST). Do not use model training dates.\n\nRecent session context:\n{session_context}\n\nCurrent customer message:\n{complaint}")
        analysis = {**fallback, **_json(query_text, fallback)}
        if not validate_analysis(analysis): analysis = fallback
        # Keep routine return/refund enquiries Low even if the model overweights
        # a relative date such as “last week”.
        if fallback.get("category") == "Refund" and fallback.get("urgency") == "Low" and analysis.get("urgency") == "Medium":
            analysis["urgency"] = "Low"
        retrieved = retrieve_knowledge(contextual_query, analysis["category"]) or {"summary": "No matching company policy was found.", "sources": [], "scores": [], "links": []}
        if progress_callback: progress_callback("Knowledge Base Agent", "Searching ChromaDB and selecting evidence")
        knowledge_agent = AssistantAgent(name="knowledge_base_agent", model_client=client, system_message=KNOWLEDGE_AGENT_PROMPT)
        knowledge_fallback = {**retrieved, "evidence_found": bool(retrieved["sources"])}
        calls += 1
        knowledge_text, knowledge_usage = await _run(knowledge_agent, json.dumps({"question": complaint, "retrieved_evidence": retrieved["summary"], "sources": retrieved["sources"]}, ensure_ascii=False))
        knowledge = {**knowledge_fallback, **_json(knowledge_text, knowledge_fallback)}
        knowledge["sources"] = retrieved["sources"]
        if not knowledge.get("summary") or not knowledge.get("evidence_found"):
            knowledge = knowledge_fallback
        resolution_agent = AssistantAgent(name="resolution_agent", model_client=client, system_message=RESOLUTION_AGENT_PROMPT)
        if progress_callback: progress_callback("Resolution Agent", "Generating grounded response with AutoGen")
        fallback = create_resolution(contextual_query, analysis, knowledge)
        calls += 1
        resolution_text, resolution_usage = await _run(resolution_agent, json.dumps({"current_date_ist": today_ist(), "recent_session_context": session_context, "complaint": complaint, "evidence": knowledge["summary"], "sources": knowledge["sources"]}, ensure_ascii=False))
        resolution = {**fallback, **_json(resolution_text, fallback)}
        if not validate_resolution(resolution, contextual_query, knowledge["summary"]): resolution = fallback
        editor_agent = AssistantAgent(name="senior_editor_agent", model_client=client, system_message=SENIOR_EDITOR_PROMPT)
        if progress_callback: progress_callback("Senior Editor Agent", "Reviewing and refining the response before delivery")
        if calls >= MAX_AGENT_CALLS: raise RuntimeError("Maximum agent-call budget reached before senior editorial review.")
        calls += 1
        editor_text, editor_usage = await _run(editor_agent, json.dumps({"recent_session_context": session_context, "complaint": complaint, "draft_response": resolution["response"], "customer_language_style": "Preserve the customer's language/script and use formal Tamil when responding in Tamil.", "evidence": knowledge["summary"], "sources": knowledge["sources"]}, ensure_ascii=False))
        edited = {**resolution, **_json(editor_text, resolution)}
        if validate_resolution(edited, contextual_query, knowledge["summary"]): resolution = edited
        resolution["response"] = _polish_tamil_support(resolution["response"])
        if not record_ticket:
            return {"ticket_id": "", "status": "Chat", "response": resolution["response"], "analysis": analysis, "knowledge": knowledge, "confidence": resolution["confidence"]}
        if progress_callback: progress_callback("Log Agent", "Applying escalation decision and logging ticket")
        # Escalation is already enforced deterministically by log_ticket().
        # Avoid a fourth sequential model call: it adds latency and can
        # contradict the guarded escalation rules.
        result = log_ticket(complaint, customer_name, order_id, analysis, knowledge, resolution)
        usages = [query_usage, knowledge_usage, resolution_usage, editor_usage]
        result["usage"] = record_usage(result["ticket_id"], os.getenv("OPENAI_MODEL", "gpt-4o-mini"), len(usages), sum(getattr(u, "prompt_tokens", 0) or 0 for u in usages if u), sum(getattr(u, "completion_tokens", 0) or 0 for u in usages if u))
        return result
    finally:
        await client.close()
