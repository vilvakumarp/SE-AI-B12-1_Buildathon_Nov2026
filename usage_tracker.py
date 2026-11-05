import csv, os
from pathlib import Path
from time_utils import now_ist

USAGE_PATH = Path("logs/api_usage.csv")
FIELDS = ["timestamp", "ticket_id", "model", "requests", "prompt_tokens", "completion_tokens", "estimated_cost_usd"]

def record_usage(ticket_id, model, requests, prompt_tokens, completion_tokens):
    cost = prompt_tokens / 1000 * float(os.getenv("OPENAI_INPUT_COST_PER_1K", "0")) + completion_tokens / 1000 * float(os.getenv("OPENAI_OUTPUT_COST_PER_1K", "0"))
    USAGE_PATH.parent.mkdir(exist_ok=True); exists = USAGE_PATH.exists()
    row = {"timestamp": now_ist().strftime("%Y-%m-%d %H:%M:%S IST"), "ticket_id": ticket_id, "model": model, "requests": requests, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "estimated_cost_usd": round(cost, 6)}
    with USAGE_PATH.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        if not exists: writer.writeheader()
        writer.writerow(row)
    return row
