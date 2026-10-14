"""
backend/main.py
FastAPI server: serves the web app, the contrastive dataset, authentication and the analysis API.

Run from the project root:
    .venv\\Scripts\\python -m uvicorn backend.main:app --reload
then open http://127.0.0.1:8000
"""

import json
import logging
import os
import re
import sys
import smtplib
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from email.message import EmailMessage
from typing import Optional, Dict, Any, List

# Make `backend.*` importable no matter which directory uvicorn is launched from.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend import config
from backend.db import init_db, get_conn, purge_expired_sessions
from backend import auth
from backend import inbox
from backend.features import extract_acoustic_footprint
from backend.grounding import align_and_ground_flaws
from backend import explainer
from backend.explainer import generate_causal_feedback, compare_spoken_vs_script


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("sce")

_analysis_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_ANALYSES)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.validate_production_security()
    init_db()
    purge_expired_sessions()
    yield


app = FastAPI(
    title="Speech Contrastive Engine",
    version="2026.10-beta",
    lifespan=lifespan,
    docs_url=None if config.ENV == "production" else "/api/docs",
    redoc_url=None,
    openapi_url=None if config.ENV == "production" else "/api/openapi.json",
)

if config.ALLOWED_ORIGINS:
    # Only needed if the frontend is hosted on a different origin. Never combine "*" with credentials.
    app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS, allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["Content-Type", "Authorization", "X-SCE-Request"])

CSP = ("default-src 'self'; script-src 'self'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
       "media-src 'self' blob:; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    rid = uuid.uuid4().hex[:12]
    request.state.request_id = rid
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > 2 * config.MAX_UPLOAD_BYTES + 1_000_000:
        return JSONResponse({"detail": "Upload too large."}, status_code=413)
    started = time.time()
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=(), payment=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["X-Request-ID"] = rid
    if request.url.scheme == "https" or config.FORCE_SECURE_COOKIES:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.endswith((".css", ".js", ".html")) or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    # Log method/path/status/latency only -- never bodies, cookies or audio.
    log.info("%s %s %s %.0fms rid=%s", request.method, request.url.path, response.status_code,
             (time.time() - started) * 1000, rid)
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    fields = sorted({".".join(str(p) for p in e.get("loc", [])[1:]) or "body" for e in exc.errors()})
    return JSONResponse({"detail": f"Invalid or missing field(s): {', '.join(fields)}"}, status_code=422)


@app.exception_handler(StarletteHTTPException)
async def http_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path == "/favicon.ico":
        return Response(status_code=204)
    if exc.status_code == 404 and not request.url.path.startswith(("/api/", "/dataset/")):
        # Unknown page: let the single-page app render its own 404 view.
        return FileResponse(os.path.join(config.FRONTEND_DIR, "index.html"), status_code=404)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", "-")
    log.exception("Unhandled error rid=%s", rid)
    return JSONResponse({"detail": f"Something went wrong on our side. Reference: {rid}"}, status_code=500)


# ------------------------------------------------------------------------------------
# Dataset (read-only, allow-listed file types)
# ------------------------------------------------------------------------------------
SAFE_SEG = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
SAFE_FILE = re.compile(r"^[A-Za-z0-9_\-]{1,64}\.(wav|json)$")


def _inside(base: str, target: str) -> bool:
    base, target = os.path.realpath(base), os.path.realpath(target)
    try:
        return os.path.commonpath([base, target]) == base
    except ValueError:  # different drives on Windows
        return False


def resolve_preset(rel: str) -> str:
    """Accept only `dataset/<folder>/<file>.wav` and confirm it really lives inside dataset/."""
    parts = (rel or "").strip().split("/")
    if (len(parts) != 3 or parts[0] != "dataset" or not SAFE_SEG.match(parts[1])
            or not SAFE_FILE.match(parts[2]) or not parts[2].endswith(".wav")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unknown dataset sample.")
    target = os.path.join(config.DATASET_DIR, parts[1], parts[2])
    if not _inside(config.DATASET_DIR, target) or not os.path.isfile(target):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unknown dataset sample.")
    return target


@app.get("/dataset/{folder}/{filename}", include_in_schema=False)
def dataset_file(folder: str, filename: str):
    if not SAFE_SEG.match(folder) or not SAFE_FILE.match(filename):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found.")
    target = os.path.join(config.DATASET_DIR, folder, filename)
    if not _inside(config.DATASET_DIR, target) or not os.path.isfile(target):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found.")
    media = "audio/wav" if filename.endswith(".wav") else "application/json"
    return FileResponse(target, media_type=media, headers={"Cache-Control": "public, max-age=3600"})


LEVEL_LABELS = ["L0 Gold", "L1 Subtle", "L2 Minor", "L3 Moderate", "L4 Egregious"]


@app.get("/api/health")
def health():
    return {"status": "ok", "version": app.version, "llm": bool(os.environ.get("GEMINI_API_KEY"))}


@app.get("/api/presets")
def list_presets():
    datasets = []
    if os.path.isdir(config.DATASET_DIR):
        for folder in sorted(os.listdir(config.DATASET_DIR)):
            fdir = os.path.join(config.DATASET_DIR, folder)
            if not SAFE_SEG.match(folder) or not os.path.isdir(fdir):
                continue
            manifest: Dict[str, Any] = {}
            mpath = os.path.join(fdir, "manifest.json")
            if os.path.isfile(mpath):
                try:
                    with open(mpath, "r", encoding="utf-8") as fh:
                        manifest = json.load(fh)
                except (OSError, ValueError):
                    manifest = {}
            files = manifest.get("levels") or sorted(f for f in os.listdir(fdir) if f.endswith(".wav"))
            levels = []
            for i, f in enumerate(files):
                if SAFE_FILE.match(f) and os.path.isfile(os.path.join(fdir, f)):
                    levels.append({"level": i, "label": LEVEL_LABELS[i] if i < len(LEVEL_LABELS) else f,
                                   "path": f"dataset/{folder}/{f}"})
            if levels:
                datasets.append({
                    "id": folder,
                    "title": str(manifest.get("title", folder.replace("_", " ").title()))[:120],
                    "category": str(manifest.get("category", ""))[:80],
                    "synthetic": bool(manifest.get("synthetic", False)),
                    "source_note": str(manifest.get("source_note", ""))[:300],
                    "levels": levels,
                })
    # Flat list kept for backwards compatibility with older clients.
    flat = [{"path": lv["path"], "name": lv["label"], "category": d["id"]} for d in datasets for lv in d["levels"]]
    return {"datasets": datasets, "presets": flat}


# ------------------------------------------------------------------------------------
# Authentication
# ------------------------------------------------------------------------------------
class SignupBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)
    display_name: str = Field(max_length=40)
    accept_terms: bool


class LoginBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class EmailBody(BaseModel):
    email: str = Field(max_length=254)


class ContactBody(BaseModel):
    name: str = Field(default="", max_length=60)
    email: str = Field(default="", max_length=254)
    category: str = Field(max_length=32)
    message: str = Field(min_length=1, max_length=1000)


class TokenBody(BaseModel):
    token: str = Field(min_length=20, max_length=128)


class ResetPasswordBody(TokenBody):
    new_password: str = Field(max_length=128)


class GenerateScriptBody(BaseModel):
    topic: str = Field(max_length=200)
    intent: str = Field(default="persuasive_oratory", max_length=40)
    language: str = Field(default="English", max_length=20)
    duration_seconds: int = Field(default=60, ge=15, le=300)
    pace_wpm: int = Field(default=130, ge=80, le=220)


def _public_user(u: Dict[str, Any]) -> Dict[str, Any]:
    return {"display_name": u["display_name"], "email": u["email"]}


def _email_delivery_configured() -> bool:
    return bool(config.SMTP_HOST and config.SMTP_FROM and config.SMTP_USERNAME and config.SMTP_PASSWORD)


def _send_auth_email(email: str, purpose: str, token: str) -> None:
    if not _email_delivery_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Email delivery is not configured. Ask the site administrator to configure SMTP.")
    if purpose == "verify_email":
        url = f"{config.PUBLIC_BASE_URL}/#verify_email={token}"
        subject = "Verify your DELTAVOX email address"
        action = "verify your email address"
        expiry = "24 hours"
    else:
        url = f"{config.PUBLIC_BASE_URL}/#reset_password={token}"
        subject = "Reset your DELTAVOX password"
        action = "reset your password"
        expiry = "30 minutes"
    message = EmailMessage()
    message["From"] = config.SMTP_FROM
    message["To"] = email
    message["Subject"] = subject
    message.set_content(f"Use this link to {action}:\n\n{url}\n\nThe link expires in {expiry}. If you did not request this, you can ignore this message.")
    timeout = 15
    if int(config.SMTP_PORT) == 465 or (not config.SMTP_STARTTLS and int(config.SMTP_PORT) != 587):
        server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=timeout)
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=timeout)
    with server:
        server.ehlo()
        if config.SMTP_STARTTLS and int(config.SMTP_PORT) != 465:
            server.starttls()
            server.ehlo()
        if config.SMTP_USERNAME and config.SMTP_PASSWORD:
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
        server.send_message(message)


@app.post("/api/auth/signup", status_code=201)
def signup(body: SignupBody, request: Request, response: Response):
    auth.verify_csrf(request)
    auth.rate_limit(request, "auth", config.AUTH_RATE_PER_15MIN, 900)
    if not body.accept_terms:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Please accept the Terms and Privacy notice to create an account.")
    if not _email_delivery_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Email verification is unavailable until the administrator configures SMTP.")
    user = auth.create_user(body.email, body.password, body.display_name)
    email = user["email"]
    token = auth.create_email_token(email, "verify_email", config.EMAIL_VERIFY_TTL_SEC)
    try:
        if token:
            _send_auth_email(email, "verify_email", token)
    except Exception:
        auth.delete_unverified_user(user["id"])
        log.exception("Verification email delivery failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="We could not send the verification email. Please try again.")
    return {"ok": True, "email_verification_required": True,
            "detail": "Check your inbox for a verification link before signing in."}


@app.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response):
    auth.verify_csrf(request)
    auth.rate_limit(request, "auth", config.AUTH_RATE_PER_15MIN, 900)
    user = auth.authenticate(body.email, body.password)
    auth.start_session(request, response, user["id"])
    return {"user": _public_user(user)}


@app.post("/api/auth/reset-password")
def reset_password_route(body: ResetPasswordBody, request: Request, response: Response):
    auth.verify_csrf(request)
    auth.rate_limit(request, "auth", config.AUTH_RATE_PER_15MIN, 900)
    if not auth.reset_password_with_token(body.token, body.new_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This password reset link is invalid or expired. Request a new one.")
    return {"ok": True, "detail": "Password updated. Sign in with your new password."}


@app.post("/api/auth/password-reset/request")
def request_password_reset(body: EmailBody, request: Request):
    auth.verify_csrf(request)
    auth.rate_limit(request, "auth", config.AUTH_RATE_PER_15MIN, 900)
    if not _email_delivery_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Password reset email is not configured. Ask the site administrator for help.")
    email = auth.normalise_email(body.email)
    token = auth.create_email_token(email, "reset_password", config.PASSWORD_RESET_TTL_SEC)
    if token:
        try:
            _send_auth_email(email, "reset_password", token)
        except Exception:
            log.exception("Password reset email delivery failed")
    return {"ok": True, "detail": "If an account exists for that email, a password reset link has been sent."}


@app.post("/api/auth/verification/resend")
def resend_verification(body: EmailBody, request: Request):
    auth.verify_csrf(request)
    auth.rate_limit(request, "auth", config.AUTH_RATE_PER_15MIN, 900)
    if not _email_delivery_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Verification email is not configured. Ask the site administrator for help.")
    email = auth.normalise_email(body.email)
    token = auth.create_email_token(email, "verify_email", config.EMAIL_VERIFY_TTL_SEC)
    if token:
        try:
            _send_auth_email(email, "verify_email", token)
        except Exception:
            log.exception("Verification email resend failed")
    return {"ok": True, "detail": "If verification is needed, a link has been sent to that email."}


@app.post("/api/auth/verification/confirm")
def confirm_verification(body: TokenBody, request: Request):
    auth.verify_csrf(request)
    if not auth.consume_email_verification(body.token):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This verification link is invalid or expired. Request a new one.")
    return {"ok": True, "detail": "Email verified. You can now sign in."}


@app.get("/api/config/public")
def public_config():
    return {"email_delivery_enabled": _email_delivery_configured(),
            "contact_enabled": inbox.firestore_configured()}


@app.post("/api/contact")
def contact_team(body: ContactBody, request: Request):
    auth.verify_csrf(request)
    auth.rate_limit(request, "contact", 5, 900)
    if body.category not in {"hackathon", "speech_engine", "feature", "general"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unknown contact category.")
    email = body.email.strip().lower()
    if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Enter a valid email address or leave it blank.")
    try:
        request_id = inbox.save_request(name=body.name, email=email, category=body.category, message=body.message)
    except inbox.InboxUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    detail = (f"Thank you. We’ll get back to you at {email}." if email
              else "Thank you. Your message was saved anonymously.")
    return {"ok": True, "id": request_id, "detail": detail}


@app.post("/api/script/generate")
def generate_speech_script(body: GenerateScriptBody, request: Request):
    auth.verify_csrf(request)
    auth.rate_limit(request, "script", 6, 60)
    if body.intent not in config.ALLOWED_INTENTS or body.language not in config.ALLOWED_LANGUAGES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Choose a supported speaking style and language.")
    topic_clean = re.sub(r"[<>\"'`\\\x00-\x1f]", " ", body.topic.strip())[:150]
    if not topic_clean:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Please enter a speech topic or theme.")

    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key and explainer.GENAI_AVAILABLE:
        try:
            client = explainer.genai.Client(api_key=api_key,
                                            http_options=explainer.genai_types.HttpOptions(timeout=int(config.SCRIPT_TIMEOUT_SEC * 1000)))
            target_words = round(body.duration_seconds * body.pace_wpm / 60)
            prompt = (f"Write an original, natural-sounding spoken {body.intent.replace('_', ' ')} script in {body.language} about {topic_clean!r}. "
                      f"Aim for {target_words} words (about {body.duration_seconds} seconds at {body.pace_wpm} words per minute). "
                      "Make the content unmistakably specific to the topic: use concrete details, accurate examples, and a clear idea the audience can remember. "
                      "Avoid generic openings such as 'When we reflect on', 'undeniable opportunity', 'transformative change', or 'every voice'; avoid hollow motivational filler and repeated abstractions. "
                      "Use conversational, speakable sentences, varied rhythm, and paragraphs with breathing room. "
                      "For storytelling, create a vivid, coherent mini-story with a concrete protagonist or scene, a meaningful turn, and an earned ending; format it as three readable paragraphs. "
                      "Return only the script, with no title, preface, quotation marks, or word-count note.")
            resp = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt
            )
            text = (resp.text or "").strip().strip('"')
            if len(text) > 20:
                return {"topic": topic_clean, "script": text, "source": "gemini",
                        "target_words": target_words, "estimated_seconds": round(len(text.split()) / body.pace_wpm * 60)}
        except Exception as exc:
            log.warning("Script generation failed (%s)", type(exc).__name__)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                                detail="AI script generation failed. Check the Gemini API key, model, and free-tier quota, then try again.") from exc

    # Topic-specific offline scripts prevent the old generic filler when the API key is absent.
    topic_lower = topic_clean.casefold()
    if any(word in topic_lower for word in ("dinosaur", "prehistoric", "dinos")) and body.intent != "storytelling":
        script = ("Dinosaurs were not monsters from a legend. For more than 160 million years, they shaped life on Earth—from enormous plant-eaters moving in herds to quick hunters built for pursuit. Fossils let us reconstruct how they grew, moved, and lived.\n\n"
                  "Then, about 66 million years ago, a giant asteroid struck near what is now Mexico. Dust and debris darkened the skies, disrupted food chains, and ended the reign of most dinosaurs.\n\n"
                  "But their story did not end completely: birds are living dinosaurs, carrying that ancient branch into our world. Their history shows how life can adapt across immense changes.")
    elif "soap" in topic_lower:
        script = ("Soap looks simple, but each molecule is built to solve a problem. One end mixes with water; the other grips oil and grease. When we wash, those molecules surround dirt and microbes so running water can carry them away.\n\n"
                  "That small piece of chemistry has had an enormous effect on everyday health. Handwashing helps interrupt the spread of infection at home, in schools, and in hospitals. It works because the chemistry is practical, repeatable, and available to ordinary people.\n\n"
                  "So the next time you reach for soap, remember what is happening: a tiny bridge between oil and water is helping protect a whole community.")
    else:
        script = None
    fallback_30s = {
        "persuasive_oratory": (
            f"When we reflect on {topic_clean}, we recognize an undeniable opportunity for transformative change. "
            "Every voice that steps forward with conviction brings us one step closer to our collective vision. "
            "Let us act with purpose today, ensuring that our efforts pave the way for a more resilient future."
        ),
        "informative_lecture": (
            f"To thoroughly understand {topic_clean}, we must examine the core principles that define it. "
            "Clear observation and disciplined analysis reveal patterns that often escape casual inspection. "
            "By synthesizing these insights, we gain a comprehensive framework for sound, forward-looking decisions."
        ),
        "storytelling": (f"Maya nearly walked past the clue. After rain, a small shape had appeared in the trail beside her town, and she noticed that it looked unlike any stone she had seen before. The label on her sample bag read: {topic_clean}.\n\n"
                         "At the local museum, an expert helped her investigate. The first guess was wrong; a closer look, a few careful questions, and one overlooked detail revealed what the object could tell them. Maya returned to the trail with a notebook and a better way to observe.\n\n"
                         "By the end of the week, she had not solved every mystery, but she knew how to begin: look closely, test an idea, and stay curious when the answer changes. Her discovery became the start of a story she could share.")
    }
    if "soap" in topic_lower and body.intent == "storytelling":
        script = ("At the school science fair, Arjun set out a bowl of water, a little oil, and a bar of soap. His classmates expected a trick. Instead, he dropped oil into the bowl and watched it float in a shiny layer.\n\n"
                  "Then he added soap and stirred. The oil broke into tiny droplets. Arjun explained that one end of a soap molecule clings to water while the other attaches to oil, letting the rinse carry the mess away.\n\n"
                  "His project won no grand prize, but the youngest visitor left repeating the explanation to her family. Arjun realized the best science display was the one that made an everyday habit suddenly make sense.")
    script = script or fallback_30s.get(body.intent, fallback_30s["persuasive_oratory"])
    if body.language != "English":
        # Offline templates are intentionally English; never mislabel them as translated.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Custom-language script generation needs GEMINI_API_KEY. The offline template is English.")
    return {"topic": topic_clean, "script": script, "source": "template",
            "target_words": round(body.duration_seconds * body.pace_wpm / 60),
            "estimated_seconds": round(len(script.split()) / body.pace_wpm * 60),
            "note": f"Offline topic template · about {round(len(script.split()) / body.pace_wpm * 60)} seconds. Add GEMINI_API_KEY to generate scripts at the selected length and pace."}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    auth.verify_csrf(request)
    auth.end_session(request, response)
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    user = auth.current_user(request)
    return {"user": _public_user(user) if user else None}


@app.delete("/api/account")
def delete_account(request: Request, response: Response):
    auth.verify_csrf(request)
    user = auth.require_user(request)
    auth.delete_user(user["id"])
    response.delete_cookie(config.SESSION_COOKIE, path="/", httponly=True,
                           secure=auth._cookie_secure(request), samesite="strict")
    return {"ok": True, "detail": "Your account, sessions and saved history were deleted."}


# ------------------------------------------------------------------------------------
# History (owner-scoped: every query filters by the session's user id)
# ------------------------------------------------------------------------------------
def _history_for(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    with get_conn() as c:
        rows = c.execute("SELECT id, created_at, overall, flaw_count, intent, ref_label, eval_label, rubric_json "
                         "FROM analyses WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit)).fetchall()
    return [{"id": r["id"], "created_at": r["created_at"], "overall": r["overall"], "flaw_count": r["flaw_count"],
             "intent": r["intent"], "reference": r["ref_label"], "evaluated": r["eval_label"],
             "rubric": json.loads(r["rubric_json"])} for r in rows]


@app.get("/api/history")
def history(request: Request):
    user = auth.require_user(request)
    return {"items": _history_for(user["id"])}


@app.delete("/api/history/{item_id}")
def delete_history_item(item_id: str, request: Request):
    auth.verify_csrf(request)
    user = auth.require_user(request)
    try:
        uuid.UUID(item_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found.")
    with get_conn() as c:
        cur = c.execute("DELETE FROM analyses WHERE id = ? AND user_id = ?", (item_id, user["id"]))
    if cur.rowcount == 0:
        # Same response whether the item does not exist or belongs to someone else (no IDOR oracle).
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found.")
    return {"ok": True}


# ------------------------------------------------------------------------------------
# Analysis
# ------------------------------------------------------------------------------------
MAGIC = [
    (0, b"RIFF", 8, b"WAVE"), (0, b"fLaC", None, None), (0, b"OggS", None, None),
    (0, b"ID3", None, None), (4, b"ftyp", None, None), (0, b"\x1a\x45\xdf\xa3", None, None),
]


def _looks_like_audio(head: bytes) -> bool:
    for off, sig, off2, sig2 in MAGIC:
        if head[off:off + len(sig)] == sig and (off2 is None or head[off2:off2 + len(sig2)] == sig2):
            return True
    return len(head) > 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0  # raw MPEG audio frame sync


async def _read_upload(upload: UploadFile, label: str) -> bytes:
    name = upload.filename or ""
    ext = os.path.splitext(name)[1].lower()
    if ext not in config.ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"{label}: unsupported file type. Use WAV, MP3, OGG, M4A, FLAC or WEBM.")
    chunks, total = [], 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > config.MAX_UPLOAD_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                                detail=f"{label}: file is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{label}: the file is empty.")
    if not _looks_like_audio(data[:16]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{label}: this file does not look like audio.")
    return data


def _safe_label(name: Optional[str]) -> str:
    base = os.path.basename(name or "upload")
    return re.sub(r"[^A-Za-z0-9 ._\-]", "_", base)[:80] or "upload"


def _extract_checked(path: str, label: str) -> Dict[str, Any]:
    try:
        feats = extract_acoustic_footprint(path)
    except Exception as e:
        log.exception(f"Audio extraction failure for {label}: {e}")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{label}: we could not decode this audio file.")
    dur = feats["duration_sec"]
    if dur < config.MIN_AUDIO_SEC:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{label}: audio must be at least {config.MIN_AUDIO_SEC:.0f} second long.")
    if dur > config.MAX_AUDIO_SEC:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{label}: audio must be at most {config.MAX_AUDIO_SEC:.0f} seconds.")
    if feats["speaker_baseline"]["rms_mean"] < 1e-5 or not any(v is not None for v in feats["f0_cents"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{label}: we could not hear any voiced speech.")
    return feats


def _run_pipeline(ref_path: str, eval_path: str, intent: str, language: str,
                  accent: str = "general_american", target_script: Optional[str] = None) -> Dict[str, Any]:
    if not _analysis_slots.acquire(timeout=30):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="The analyser is busy. Please try again shortly.")
    try:
        ref_feats = _extract_checked(ref_path, "Reference")
        eval_feats = _extract_checked(eval_path, "Your take")
        grounding = align_and_ground_flaws(ref_feats, eval_feats, intent)
        grounding["flaw_regions"] = generate_causal_feedback(grounding["flaw_regions"], intent, language, accent)
        script_comp = None
        if target_script:
            script_comp = compare_spoken_vs_script(eval_path, target_script, grounding["overall_score"], grounding["flaw_regions"], language, accent)
        return {"ref": ref_feats, "eval": eval_feats, "grounding": grounding, "script_comparison": script_comp}
    finally:
        _analysis_slots.release()


def _downsample(xs: List[Any], step: int) -> List[Any]:
    return xs[::step] if step > 1 else xs


@app.post("/api/analyze")
async def analyze(
    request: Request,
    reference_audio: Optional[UploadFile] = File(None),
    evaluated_audio: Optional[UploadFile] = File(None),
    reference_preset: Optional[str] = Form(None, max_length=200),
    evaluated_preset: Optional[str] = Form(None, max_length=200),
    rhetorical_intent: str = Form("persuasive_oratory", max_length=40),
    speaker_accent: str = Form("general_american", max_length=40),
    preferred_language: str = Form("English", max_length=20),
    target_script: Optional[str] = Form(None, max_length=config.MAX_SCRIPT_CHARS),
    save: bool = Form(True),
):
    auth.verify_csrf(request)
    auth.rate_limit(request, "analyze", config.ANALYZE_RATE_PER_MIN, 60)
    if rhetorical_intent not in config.ALLOWED_INTENTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unknown speech intent.")
    if speaker_accent not in config.ALLOWED_ACCENTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unsupported speaker accent.")
    if preferred_language not in config.ALLOWED_LANGUAGES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unsupported feedback language.")
    user = auth.current_user(request)

    temp_files: List[str] = []
    try:
        def stage(data: bytes, original: str) -> str:
            ext = os.path.splitext(original)[1].lower() or ".wav"
            fd, path = tempfile.mkstemp(suffix=ext, prefix="sce_")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            temp_files.append(path)
            return path

        if reference_audio is not None and reference_audio.filename:
            ref_bytes = await _read_upload(reference_audio, "Reference")
            ref_path, ref_label = stage(ref_bytes, reference_audio.filename), _safe_label(reference_audio.filename)
        elif reference_preset:
            ref_path, ref_label = resolve_preset(reference_preset), reference_preset
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Choose a reference: a dataset sample or an uploaded file.")

        if evaluated_audio is not None and evaluated_audio.filename:
            eval_bytes = await _read_upload(evaluated_audio, "Your take")
            eval_path, eval_label = stage(eval_bytes, evaluated_audio.filename), _safe_label(evaluated_audio.filename)
        elif evaluated_preset:
            eval_path, eval_label = resolve_preset(evaluated_preset), evaluated_preset
            with open(eval_path, "rb") as fh:
                eval_bytes = fh.read()
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Add your take: record, upload, or pick a dataset level.")

        eval_sha = auth.sha256_hex(eval_bytes)
        out = await run_in_threadpool(_run_pipeline, ref_path, eval_path, rhetorical_intent, preferred_language, speaker_accent, target_script)
    finally:
        for p in temp_files:
            try:
                os.remove(p)
            except OSError:
                pass

    g, ref_feats, eval_feats = out["grounding"], out["ref"], out["eval"]
    flaws = g["flaw_regions"]
    top = sorted(flaws, key=lambda f: f.get("point_recovery_potential", 0), reverse=True)[:3]

    analysis_id = None
    if user and save:
        analysis_id = str(uuid.uuid4())
        with get_conn() as c:
            c.execute("INSERT INTO analyses (id, user_id, created_at, overall, flaw_count, intent, ref_label, eval_label, "
                      "eval_sha256, rubric_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (analysis_id, user["id"], time.time(), g["overall_score"], g["total_flaws_count"],
                       rhetorical_intent, ref_label, eval_label, eval_sha, json.dumps(g["rubric_scores"])))

    step = max(1, len(eval_feats["frame_times"]) // 1500)  # cap payload size for long takes
    rstep = max(1, len(ref_feats["frame_times"]) // 1500)
    return {
        "analysis_id": analysis_id,
        "saved": analysis_id is not None,
        "provenance": {
            "sha256": eval_sha,
            "meaning": "SHA-256 of the exact bytes the server analysed. Re-hash your file to confirm it matches.",
        },
        "overall_score": g["overall_score"],
        "rubric_scores": g["rubric_scores"],
        "diagnostics": g["diagnostics"],
        "flaw_count": g["total_flaws_count"],
        "flaw_regions": flaws,
        "script_comparison": out.get("script_comparison"),
        "top_fixes": [{"flaw_name": f["flaw_name"], "t_start": f["t_start"], "t_end": f["t_end"],
                       "recovery": f.get("point_recovery_potential", 0), "advice": f.get("causal_explanation", "")}
                      for f in top],
        "durations": {"reference": ref_feats["duration_sec"], "evaluated": eval_feats["duration_sec"]},
        "time_series": {
            "eval_times": _downsample(eval_feats["frame_times"], step),
            "eval_f0_cents": _downsample(eval_feats["f0_cents"], step),
            "eval_rms": _downsample(eval_feats["rms"], step),
            "ref_times": _downsample(ref_feats["frame_times"], rstep),
            "ref_f0_cents": _downsample(ref_feats["f0_cents"], rstep),
            "ref_rms": _downsample(ref_feats["rms"], rstep),
        },
    }


# ------------------------------------------------------------------------------------
# Frontend (same origin => no CORS needed, cookies stay first-party)
# ------------------------------------------------------------------------------------
@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
def favicon():
    return Response(status_code=204)


# ------------------------------------------------------------------------------------
# Frontend (same origin => no CORS needed, cookies stay first-party)
# ------------------------------------------------------------------------------------
@app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
def robots():
    return FileResponse(os.path.join(config.FRONTEND_DIR, "robots.txt"), media_type="text/plain")


if os.path.isdir(config.FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
