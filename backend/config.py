"""
backend/config.py
Central configuration. Every value can be overridden with an environment variable,
so no secret ever needs to live in source code. See .env.example.
"""

import os
import sys

# Ensure virtual environment Scripts directory (with bundled ffmpeg.exe & ffprobe.exe) is on PATH
_venv_scripts = os.path.dirname(sys.executable)
if _venv_scripts and _venv_scripts not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{_venv_scripts}{os.pathsep}{os.environ.get('PATH', '')}"

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)
except ImportError:
    # Environment variables can still be supplied directly by the host.
    pass

DATASET_DIR = os.path.join(BASE_DIR, "dataset")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


ENV = os.environ.get("SCE_ENV", "development")          # "development" | "production"
DB_PATH = os.environ.get("SCE_DB_PATH", os.path.join(BASE_DIR, "data", "sce.sqlite3"))

# Comma-separated list of extra origins allowed to call the API cross-site.
# Leave empty when the frontend is served by this same server (recommended).
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("SCE_ALLOWED_ORIGINS", "").split(",") if o.strip()]

# Cookies are always HttpOnly + SameSite=Strict. "Secure" is added automatically on HTTPS,
# and can be forced (recommended in production behind a TLS-terminating proxy).
FORCE_SECURE_COOKIES = _bool("SCE_FORCE_SECURE_COOKIES", ENV == "production")
SESSION_COOKIE = "sce_session"
SESSION_TTL_SEC = int(os.environ.get("SCE_SESSION_TTL_SEC", str(7 * 24 * 3600)))

# Abuse limits
ANALYZE_RATE_PER_MIN = int(os.environ.get("SCE_ANALYZE_RATE_PER_MIN", "12"))
AUTH_RATE_PER_15MIN = int(os.environ.get("SCE_AUTH_RATE_PER_15MIN", "20"))
MAX_FAILED_LOGINS = 5
LOCKOUT_SEC = 15 * 60
MAX_CONCURRENT_ANALYSES = int(os.environ.get("SCE_MAX_CONCURRENT_ANALYSES", "2"))

# Upload limits
MAX_UPLOAD_BYTES = int(os.environ.get("SCE_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
MIN_AUDIO_SEC = 1.0
MAX_AUDIO_SEC = float(os.environ.get("SCE_MAX_AUDIO_SEC", "180"))
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".m4a", ".webm", ".flac"}

# Gemini (optional). Key is read server-side only and never sent to the browser.
GEMINI_MODEL = os.environ.get("SCE_GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_TIMEOUT_SEC = float(os.environ.get("SCE_GEMINI_TIMEOUT_SEC", "5.0"))
SCRIPT_TIMEOUT_SEC = float(os.environ.get("SCE_SCRIPT_TIMEOUT_SEC", "25.0"))

# Account email delivery. Configure these with environment variables; never
# commit SMTP passwords.
PUBLIC_BASE_URL = os.environ.get("SCE_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SMTP_HOST = os.environ.get("SCE_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SCE_SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SCE_SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SCE_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SCE_SMTP_FROM", SMTP_USERNAME)
SMTP_STARTTLS = _bool("SCE_SMTP_STARTTLS", True)
EMAIL_VERIFY_TTL_SEC = int(os.environ.get("SCE_EMAIL_VERIFY_TTL_SEC", str(24 * 3600)))
PASSWORD_RESET_TTL_SEC = int(os.environ.get("SCE_PASSWORD_RESET_TTL_SEC", str(30 * 60)))

# Firebase-backed contact storage. The service-account path stays server-side.
FIREBASE_PROJECT_ID = os.environ.get("SCE_FIREBASE_PROJECT_ID", "").strip()
FIREBASE_SERVICE_ACCOUNT = os.environ.get("SCE_FIREBASE_SERVICE_ACCOUNT", "").strip()

ALLOWED_INTENTS = {"persuasive_oratory", "informative_lecture", "storytelling",
                   "dramatic_declamation", "debate_rebuttal"}
ALLOWED_ACCENTS = {
    "general_american", "british_rp", "indian_english", "australian_nz",
    "global_esl", "singaporean_sea", "regional_accent"
}
ALLOWED_LANGUAGES = {
    "English", "Hindi", "Gujarati", "Spanish", "French", "German",
    "Japanese", "Mandarin", "Arabic", "Portuguese", "Italian", "Dutch",
    "Bengali", "Tamil", "Telugu", "Russian", "Korean"
}
MAX_SCRIPT_CHARS = 10000


def validate_production_security() -> None:
    """Fail closed when deployment settings would expose account credentials."""
    if ENV != "production":
        return
    if not PUBLIC_BASE_URL.startswith("https://"):
        raise RuntimeError("SCE_PUBLIC_BASE_URL must use https:// in production.")
    if SMTP_HOST and not SMTP_STARTTLS:
        raise RuntimeError("SMTP must use STARTTLS in production.")
    if not FORCE_SECURE_COOKIES:
        raise RuntimeError("Secure session cookies must be enabled in production.")
    if SESSION_TTL_SEC < 300 or SESSION_TTL_SEC > 30 * 24 * 3600:
        raise RuntimeError("SCE_SESSION_TTL_SEC must be between 5 minutes and 30 days.")
    if MAX_UPLOAD_BYTES < 1 or MAX_UPLOAD_BYTES > 50 * 1024 * 1024:
        raise RuntimeError("SCE_MAX_UPLOAD_BYTES must be between 1 byte and 50 MB.")
