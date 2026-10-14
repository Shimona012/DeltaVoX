# 🎙️ DELTAVOX: Speech Contrastive Analytics Engine
> ***The sound of change.***  
> **Hear exactly where your delivery fell apart, down to the second.**  
> *Multimodal AI Hackathon 2026 — Track C*

---

## What is DeltaVox?

Most speech analysis tools tell you **what** you said: transcripts, grammar, filler words, or an average words-per-minute pace. But when it comes to delivery, feedback is usually ambiguous: *"try to sound more energetic"* or *"be more engaging."* You could have all the enthusiasm in the world and still struggle to get it across effectively.

**DeltaVox** replaces subjective guesswork with **contrastive speech analytics**. Instead of an abstract score, it measures the exact mathematical **delta** between your practice take and an ideal reference version of the same speech—hence the name:

> *"At 0:14–0:19 your pitch flattened to 40% of the reference, and at 0:31 your projection dropped 1.8 standard deviations."*

### Key Features
- **Voice-Agnostic Pitch:** Raw Hz frequencies are translated to musical semitone cents on a log scale.
$$\text{Cents} = 1200 \cdot \log_2\left(\frac{f_0}{55\text{ Hz}}\right).$$
A 5-semitone inflection measures the same regardless of whether you have a deep bass or high soprano voice.
- **Gain-Invariant Energy:** Root-Mean-Square (RMS) loudness is z-scored per take. Microphone hardware, distance, and recording levels do not count as delivery flaws—only the expressive contour matters.
- **Dynamic Time Warping (DTW):** Real deliveries never match frame-for-frame. Sakoe–Chiba constrained DTW aligns variations in cadence and speaking speed so the comparison is always word-for-word.
- **Deterministic Flaw Grounding:** Flags specific delivery breakdowns—monotone pitch collapse, projection drops, and intent-sensitive dead pauses—with start and end timestamps.
- **Script vs. Live Speech Alignment:** Evaluates live audio against a prepared script or a reference take, accounting for rhetorical intent (persuasive oratory, storytelling, debate rebuttal, or lecture).
- **Contextual Speech Generation:** Integrated with Gemini to draft custom speeches tailored to specific speaking time limits and target rhetorical intents.
- **Math-Grounded Coaching:** Gemini translates verified acoustic measurements and flaw timestamps into readable, practical coaching tips without making up arbitrary diagnoses.
- **Synthetic 5-Stage Benchmark:** Includes a built-in synthetic benchmark spanning five clear degradation tiers (L0 Ideal $\rightarrow$ L4 Egregious) for instant testing and one-click demos.

---

## Project Structure

```
DELTAVOX/
├── backend/
│   ├── main.py              # FastAPI application, routing, and preset endpoints
│   ├── features.py          # Resilient audio loading, pYIN pitch, and RMS extraction
│   ├── grounding.py         # DTW alignment, temporal flaw grounding, and rubric scoring
│   ├── explainer.py         # Gemini causal explanation layer with offline fallback
│   ├── auth.py              # Session management, password hashing, CSRF tokens
│   └── database.py          # Local SQLite storage and state persistence
├── frontend/
│   ├── index.html           # Single-page UI with audio recorders and docks
│   ├── app.js               # WaveSurfer visualization, interactive playback, and API calls
│   └── style.css            # Responsive layout with custom themes
├── dataset/                 # Contrastive benchmark recordings (speech_001_persuasive)
├── tests/                   # Pytest suite (engine, auth, and security)
└── README.md
```

---

## Getting Started

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.10–3.14)
- `ffmpeg` installed on your system PATH (recommended for decoding browser WebM/Opus audio)

### 2. Clone and Install
```bash
git clone https://github.com/Shimona012/DeltaVoX.git
cd DELTAVOX

# Create and activate virtual environment
python -m venv .venv

# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Run Locally
```bash
# Optional: supply Gemini API key for live AI coaching notes
$env:GEMINI_API_KEY="your_api_key_here"  # Windows PowerShell
# export GEMINI_API_KEY="your_api_key_here"  # macOS/Linux

python -m uvicorn backend.main:app --reload
```

Open **`http://127.0.0.1:8000`** in your browser.  
*(Note: FastAPI serves the frontend and backend together on the same port so cookies, CSRF tokens, and audio endpoints work out of the box).*

---

## Configuration & Optional Services

DeltaVox works completely offline using deterministic feedback templates and local SQLite storage. For advanced capabilities, configure environment variables in a `.env` file (see `.env.example`):

### Live Gemini Coaching & Speech Generation
Set `GEMINI_API_KEY` to enable Gemini 3.8 Flash causal coaching summaries and speech generation tailored to speech length and rhetorical intent. Without an API key, DeltaVox uses deterministic offline coaching templates.

### Account Verification & Password Reset (SMTP)
```ini
SCE_PUBLIC_BASE_URL="http://127.0.0.1:8000"
SCE_SMTP_HOST="smtp.example.com"
SCE_SMTP_PORT=587
SCE_SMTP_USERNAME="your-smtp-user"
SCE_SMTP_PASSWORD="your-smtp-password"
SCE_SMTP_FROM="noreply@example.com"
SCE_SMTP_STARTTLS="true"
```

### Developer Contact Inbox (Firebase Firestore)
Contact form submissions can be forwarded to a private Firestore collection without exposing client-side credentials:
```ini
SCE_FIREBASE_PROJECT_ID="your-firebase-project-id"
SCE_FIREBASE_SERVICE_ACCOUNT="/path/to/service-account.json"
```

---

## Running the Test Suite

DeltaVox includes an automated test suite verifying audio engine math, monotonic benchmark degradation, rate limiting, path traversal defenses, and authentication:

```bash
pytest
```

---

## Development Milestones

| Stage | Focus Area | Implementation |
|---|---|---|
| **Phase 1** | **Acoustic Extraction** | Librosa feature extraction, pYIN pitch tracking, Hz $\rightarrow$ Log Cents conversion |
| **Phase 2** | **Alignment & Grounding** | Sakoe–Chiba Dynamic Time Warping, z-score energy normalization across voices |
| **Phase 3** | **Contrastive Benchmark** | 5-level synthetic degradation spectrum (L0 Ideal $\rightarrow$ L4 Egregious) |
| **Phase 4** | **Causal Explanations** | Grounded Gemini integration using metric deltas with strict offline fallback |
| **Phase 5** | **Hardening & UI** | CSRF protection, rate limiting, responsive dual-waveform player, multi-theme UI |

---

##  AI Assistance & Tooling Disclosure
In compliance with hackathon guidelines, the following AI tools and coding assistants were utilized during development:
- **Google Antigravity (Pair Programming Assistant):** Used for architectural brainstorming, signal processing pipeline scaffolding, authoring the 50-test automated test suite, security audits (CSRF/rate limiting/path traversal), and documentation drafting.
- **OpenAI Codex:** Used for debugging edge-case audio alignment issues during evaluation take processing.
- **ChatGPT:** Used for conceptual code explanations, parameter tuning ideas, and sanity-checking signal processing math.
- **Gemini 3.8 Flash (Runtime API):** Integrated directly into DeltaVox's backend (`backend/explainer.py`) as the runtime causal coaching engine to translate verified metric deltas into plain-English feedback.
*Note: All architecture decisions, signal processing math (semitone cents conversion, z-score energy normalization, DTW constraints), and repository files were reviewed, verified, and tested.*
