import csv
from pathlib import Path

from agents.knowledge_agent import retrieve_knowledge
from agents.log_agent import log_ticket
from agents.query_agent import analyze_query
from agents.resolution_agent import create_resolution
from guardrails import validate_input


def test_query_agent_classifies_billing_escalation():
    result = analyze_query("You charged my card three times and I am contacting my bank.")
    assert result["category"] == "Billing"
    assert result["urgency"] == "High"
    assert result["sentiment"] == "Angry"


def test_knowledge_agent_returns_source_evidence():
    result = retrieve_knowledge("How can I check my refund status?", "Refund")
    assert result["sources"]
    assert result["summary"]


def test_resolution_does_not_answer_without_evidence():
    result = create_resolution("Do you offer corporate gift cards?", analyze_query("Do you offer corporate gift cards?"), {"sources": [], "summary": ""})
    assert result["confidence"] == "Low"
    assert "could not find" in result["response"].lower()


def test_log_escalates_high_risk(tmp_path, monkeypatch):
    import agents.log_agent as log_agent
    path = tmp_path / "tickets.csv"
    monkeypatch.setattr(log_agent, "LOG_PATH", path)
    analysis = {"category": "Billing", "urgency": "High", "sentiment": "Angry"}
    knowledge = {"sources": ["sop.docx"]}
    resolution = {"response": "Review required.", "confidence": "High"}
    result = log_ticket("I will contact my bank and lawyer.", "Test", "EVAL-1", analysis, knowledge, resolution)
    assert result["status"] == "Escalated"
    assert path.exists()
    with path.open(encoding="utf-8") as stream:
        assert len(list(csv.DictReader(stream))) == 1


def test_guardrail_rejects_oversized_input(monkeypatch):
    monkeypatch.setattr("guardrails.MAX_INPUT_CHARS", 10)
    try:
        validate_input("this complaint is too long")
        assert False, "Expected oversized input to be rejected"
    except ValueError as error:
        assert "too long" in str(error)


def test_query_agent_classifies_tanglish_payment_issue():
    result = analyze_query("Enakku UPI payment failed, amount debit aayiduchu")
    assert result["category"] == "Billing"


def test_guardrail_rejects_unsupported_script():
    from guardrails import validate_resolution
    assert not validate_resolution(
        {"response": "कृपया உங்கள் புகாரை பகிரவும்", "confidence": "High"},
        "எனக்கு ஒரு complaint உள்ளது",
        "",
    )
