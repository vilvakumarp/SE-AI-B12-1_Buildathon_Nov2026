import csv, json
from datetime import datetime
from pathlib import Path
from time_utils import now_ist

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "logs" / "tickets.csv"
FIELDS = ["ticket_id", "timestamp", "customer_name", "order_id", "complaint", "category", "urgency", "sentiment", "kb_sources", "response", "status", "admin_status", "admin_comment", "admin_updates", "escalation_reason"]


def log_ticket(complaint, customer_name, order_id, analysis, knowledge, resolution, initial_status=None, response_override=None):
    LOG_PATH.parent.mkdir(exist_ok=True)
    if LOG_PATH.exists():
        count = sum(1 for _ in LOG_PATH.open(encoding="utf-8"))
    else:
        count = 1
    ticket_id = f"TKT-{count:04d}"
    reasons = []
    if analysis["urgency"] == "High": reasons.append("high urgency")
    if analysis["sentiment"] == "Angry": reasons.append("angry sentiment")
    if not knowledge["sources"]: reasons.append("no matching knowledge-base document")
    if any(k in complaint.lower() for k in ("lawyer", "fraud", "bank")): reasons.append("legal, fraud, or payment dispute")
    if resolution["confidence"] == "Low": reasons.append("low resolution confidence")
    if any(k in complaint.lower() for k in ("gift card", "corporate branding", "custom branding")): reasons.append("unsupported product or service request")
    status = initial_status or ("Escalated" if reasons else "Resolved")
    sources = ", ".join(dict.fromkeys(knowledge.get("sources", [])))
    row = {"ticket_id": ticket_id, "timestamp": now_ist().strftime("%Y-%m-%d %H:%M:%S IST"), "customer_name": customer_name, "order_id": order_id, "complaint": complaint, "category": analysis["category"], "urgency": analysis["urgency"], "sentiment": analysis["sentiment"], "kb_sources": sources, "response": response_override or resolution["response"], "status": status, "admin_status": "Open", "admin_comment": "", "admin_updates": "[]", "escalation_reason": "; ".join(reasons)}
    with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if count == 1: writer.writeheader()
        writer.writerow(row)
    return {**row, "analysis": analysis, "knowledge": knowledge, "confidence": resolution["confidence"]}


def update_ticket(ticket_id, response=None, status=None, admin_status=None, admin_comment=None):
    if not LOG_PATH.exists():
        return False
    rows = []
    changed = False
    with LOG_PATH.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["ticket_id"] == ticket_id:
                if response is not None: row["response"] = response
                if status is not None: row["status"] = status
                row["admin_status"] = admin_status or row.get("admin_status") or ("Closed" if status == "Resolved" else "Open")
                row["admin_comment"] = admin_comment if admin_comment is not None else row.get("admin_comment", "")
                if admin_comment is not None or admin_status is not None:
                    try: updates = json.loads(row.get("admin_updates") or "[]")
                    except json.JSONDecodeError: updates = []
                    updates.insert(0, {"timestamp": now_ist().strftime("%Y-%m-%d %H:%M:%S IST"), "status": row["admin_status"], "comment": admin_comment or ""})
                    row["admin_updates"] = json.dumps(updates, ensure_ascii=False)
                if status == "Resolved": row["escalation_reason"] = "Human support approved response"
                changed = True
            rows.append(row)
    if changed:
        with LOG_PATH.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader(); writer.writerows(rows)
    return changed
