"""Settings, read from environment variables (see .env.example)."""
import os

DB_PATH = os.environ.get("PF_DB_PATH", "data/portafolios.db")
# Code your friends need to create an account. Empty = registration closed.
INVITE_CODE = os.environ.get("PF_INVITE_CODE", "")
# True when served over HTTPS (behind Caddy). Marks the session cookie Secure.
SECURE_COOKIES = os.environ.get("PF_SECURE_COOKIES", "1") == "1"
SESSION_DAYS = int(os.environ.get("PF_SESSION_DAYS", "30"))
# Seconds between price refreshes.
FAST_INTERVAL = int(os.environ.get("PF_FAST_INTERVAL", "20"))   # crypto
SLOW_INTERVAL = int(os.environ.get("PF_SLOW_INTERVAL", "60"))   # stocks, bonds, MEP
# Set to 1 only for local testing without internet: prices are simulated.
MOCK_PRICES = os.environ.get("PF_MOCK_PRICES", "0") == "1"
BENCHMARKS = [("SPY", "etf_us"), ("BTC", "cripto"), ("MEP", "mep")]
