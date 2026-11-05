"""Offline evaluation for routing, retrieval, and escalation behavior."""
import asyncio
import csv
from pathlib import Path

from team import run_support_team

CASES = [
    ("Where is my order? It was supposed to arrive yesterday.", "Shipping", "Medium", "Frustrated", "Resolved"),
    ("I want to return the shoes I bought last week. How do I get a refund?", "Refund", "Low", "Neutral", "Resolved"),
    ("I forgot my password and can't log in.", "Account", "Medium", "Frustrated", "Resolved"),
    ("The blender I received is broken. Can I get a replacement?", "Product", "Medium", "Frustrated", "Resolved"),
    ("You charged my card three times! Fix this now or I am contacting my bank and a lawyer.", "Billing", "High", "Angry", "Escalated"),
    ("Do you sell gift cards for a company of 500 employees with custom branding?", "Product", "Low", "Neutral", "Escalated"),
]


async def evaluate(output_path="evaluation_results.csv"):
    rows = []
    for complaint, expected_category, expected_urgency, expected_sentiment, expected_status in CASES:
        result = await run_support_team(complaint, "Evaluation User", "EVAL-001")
        analysis = result.get("analysis", {})
        knowledge = result.get("knowledge", {}) or {}
        response = str(result.get("response", "")).strip()
        category_correct = analysis.get("category") == expected_category
        urgency_correct = analysis.get("urgency") == expected_urgency
        sentiment_correct = analysis.get("sentiment") == expected_sentiment
        status_correct = result.get("status") == expected_status
        grounding_present = bool(knowledge.get("sources"))
        confidence_valid = result.get("confidence") in {"High", "Medium", "Low"}
        response_present = bool(response)
        rows.append({
            "complaint": complaint,
            "expected_category": expected_category,
            "actual_category": analysis.get("category"),
            "category_correct": category_correct,
            "expected_urgency": expected_urgency,
            "actual_urgency": analysis.get("urgency"),
            "urgency_correct": urgency_correct,
            "expected_sentiment": expected_sentiment,
            "actual_sentiment": analysis.get("sentiment"),
            "sentiment_correct": sentiment_correct,
            "expected_status": expected_status,
            "actual_status": result.get("status"),
            "status_correct": status_correct,
            "sources_found": len(knowledge.get("sources", [])),
            "grounding_present": grounding_present,
            "response_present": response_present,
            "confidence": result.get("confidence"),
            "confidence_valid": confidence_valid,
            "overall_pass": all([category_correct, urgency_correct, sentiment_correct, status_correct, response_present, confidence_valid, grounding_present]),
        })
    fields = list(rows[0])
    with Path(output_path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    print(f"Category accuracy: {sum(r['category_correct'] for r in rows) / len(rows):.1%}")
    print(f"Urgency accuracy: {sum(r['urgency_correct'] for r in rows) / len(rows):.1%}")
    print(f"Sentiment accuracy: {sum(r['sentiment_correct'] for r in rows) / len(rows):.1%}")
    print(f"Status accuracy: {sum(r['status_correct'] for r in rows) / len(rows):.1%}")
    print(f"Grounding present: {sum(r['grounding_present'] for r in rows) / len(rows):.1%}")
    print(f"Overall smoke-test pass rate: {sum(r['overall_pass'] for r in rows) / len(rows):.1%}")
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    asyncio.run(evaluate())
