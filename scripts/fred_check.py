"""Live FRED DGS3MO check used by run_rad.ps1; never prints credentials."""
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)
sys.path.insert(0, str(ROOT / "backend"))

from app.services.market_data import fetch_us_tbill  # noqa: E402

try:
    result = fetch_us_tbill()
except Exception:
    print("FRED retrieval failed. Credentials were not displayed.")
    raise SystemExit(1)

print(f"Yield: {result['annual_yield']:.4%}")
print(f"Observation date: {result['observation_date']}")
print(f"Source: {result['source']}")
