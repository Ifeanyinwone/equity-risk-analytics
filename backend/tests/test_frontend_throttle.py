import sys
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from frontend.ui_errors import analysis_error_message


def test_frontend_formats_rate_limit_as_clear_user_message():
    response=SimpleNamespace(
        status_code=429,
        text="",
        json=lambda:{"detail":"Analysis limit reached. Please wait 30 seconds before trying again."},
    )
    message=analysis_error_message(response)
    assert "HTTP 429" in message
    assert "Analysis limit reached" in message
    assert "wait 30 seconds" in message
