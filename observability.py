"""API configuration checks and optional LangSmith tracing."""
import os
from functools import wraps


def api_status() -> dict:
    openai_key = bool(os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    langsmith_key = bool(os.getenv("LANGSMITH_API_KEY"))
    tracing = os.getenv("LANGSMITH_TRACING", "false").lower() == "true"
    return {
        "openai_configured": openai_key and bool(model),
        "openai_model": model,
        "serper_configured": bool(os.getenv("SERPER_API_KEY")),
        "langsmith_configured": langsmith_key and tracing,
        "langsmith_project": os.getenv("LANGSMITH_PROJECT", "customer-support-desk"),
    }


def require_openai() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not configured. Add it to .env before enabling AutoGen/OpenAI calls.")
    if not os.getenv("OPENAI_MODEL"):
        raise RuntimeError("OPENAI_MODEL is not configured. Add a model name to .env.")


def traceable(name):
    """Use LangSmith when configured; otherwise leave the function unchanged."""
    try:
        from langsmith import traceable as ls_traceable
    except ImportError:
        ls_traceable = None

    def decorator(function):
        if ls_traceable and os.getenv("LANGSMITH_TRACING", "false").lower() == "true" and os.getenv("LANGSMITH_API_KEY"):
            return ls_traceable(name=name, run_type="chain")(function)
        return function
    return decorator
