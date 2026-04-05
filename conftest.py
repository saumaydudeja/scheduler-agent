"""
Root conftest — sets dummy env vars before any module is imported during testing.
Tests mock all external API calls so real credentials are never needed.
"""
import os

os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")
os.environ.setdefault("ESCALATION_EMAIL", "test@example.com")
