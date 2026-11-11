"""Small, testable UI error formatting helpers."""
from __future__ import annotations


def analysis_error_message(response) -> str:
    try:
        detail=response.json().get("detail",response.text)
    except (ValueError,AttributeError):
        detail="Backend returned an unreadable error response."
    return f"Analysis failed (HTTP {response.status_code}): {detail}"
