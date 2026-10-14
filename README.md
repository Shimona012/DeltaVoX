# 🎙️ Speech Contrastive Analytics Engine
> *Multimodal AI Hackathon 2026 — Track C*

## 📁 Getting Started
Clone or extract the repository and navigate into the project directory:
```bash
git clone https://github.com/Shimona012/DeltaVoX.git
cd DELTAVOX
```

---

## 🏗️ Structure Overview

```
DELTAVOX/
├── backend/
│   ├── main.py              # FastAPI server orchestrating endpoints
│   ├── features.py          # Log-scale semitone pitch & RMS extractor
│   ├── grounding.py         # Dynamic Time Warping (DTW) & temporal flaw grounder
│   └── explainer.py         # Gemini 3.8 Flash causal feedback layer
├── frontend/
│   └── index.html           # Interactive UI (Waveform curves, Audio Docks, Rubric)
├── dataset/                 # Contrastive spectrum recordings (L0 to L4)
├── docs/                    # Technical Report draft
└── README.md
```

---

## How to Run the App Locally

### 1. Launch Backend Server
Open PowerShell / Terminal in the project directory:
```powershell
cd DELTAVOX
$env:GEMINI_API_KEY="your_api_key_here"  # optional for live Gemini feedback
python -m uvicorn backend.main:app --reload
```

Then open `http://127.0.0.1:8000`. The FastAPI server serves the frontend and API together; opening `frontend/index.html` directly will not enable sign-in or analysis.

### Account sign-in setup

Email verification and password reset need an SMTP account. Configure these environment variables in the same PowerShell window before starting the server:

```powershell
$env:SCE_PUBLIC_BASE_URL="http://127.0.0.1:8000"
$env:SCE_SMTP_HOST="smtp.example.com"
$env:SCE_SMTP_PORT="587"
$env:SCE_SMTP_USERNAME="your-smtp-user"
$env:SCE_SMTP_PASSWORD="your-smtp-password"
$env:SCE_SMTP_FROM="noreply@example.com"
$env:SCE_SMTP_STARTTLS="true"
```

### Developer Contact Us inbox (Firestore)

Contact Us submissions are stored in Firestore and are not emailed. Review them in Firebase Console → Firestore Database → `contact_requests`. The form offers anonymous submission or an email address for a reply. The service-account key is private server data; never place it in `frontend/` or commit it.

1. In Firebase Console, create or select a project and create a Firestore database.
2. Set its rules from the project’s `firestore.rules` file and deploy them. Client-side Firestore reads and writes are denied; DeltaVox accesses it through the server Admin SDK.
3. Create a Firebase Admin service-account JSON key and save it outside this project folder. Put its full path in `SCE_FIREBASE_SERVICE_ACCOUNT`. Treat the JSON file like a password.
4. Install the updated requirements and restart the backend. Contact Us documents are accessible through the Firebase Console to project members; client-side Firestore access remains denied by the included rules.

PowerShell setup for the current terminal (replace every placeholder):

```powershell
$env:SCE_FIREBASE_PROJECT_ID="your-firebase-project-id"
$env:SCE_FIREBASE_SERVICE_ACCOUNT="C:\secrets\firebase-service-account.json"
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload
```

Copy `.env.example` to `.env`, fill in the values, install the updated requirements, and restart DeltaVox. `.env` is ignored by Git. For a hosted deployment, add the same values through the hosting provider’s environment settings.

Only the Firebase project ID and server-side service-account path are needed for the Firestore contact inbox. See [Firebase Admin setup](https://firebase.google.com/docs/admin/setup).

Speech scripts include controls for target duration and pace. These values are passed to Gemini when `GEMINI_API_KEY` is configured. Without that key, the app provides a short English offline template; other languages and longer custom scripts require the key.

---

## 🗓️ 10-Day Mastery & Development Roadmap

| Day | Focus Area | What You Learn & Build |
|---|---|---|
| **Day 1–2** | **Scaffolding & Core Audio** | Understand Librosa, Hz → Log Cents math, test audio extraction |
| **Day 3–4** | **DTW Alignment & Grounding** | Learn Dynamic Time Warping, z-score normalization across voices |
| **Day 5–6** | **Dataset & Flaw Gradient** | Build L0 → L4 contrastive pairs (TED, MLK, Debate excerpts) |
| **Day 7–8** | **Gemini Causal Integration** | Prompt engineering for zero-hallucination mathematical coaching |
| **Day 9–10**| **UI Polish & Technical Report** | Master WaveSurfer visualization, finalize 6-page report & 3-min demo video |
