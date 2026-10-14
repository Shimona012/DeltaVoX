"""API security tests: headers, CSRF, path traversal, upload validation, rate limiting, error hygiene."""
import hashlib
import io
import os

import numpy as np
import soundfile as sf

from conftest import H, DATASET
from backend import config

REF = "dataset/speech_001_persuasive/level_0_ideal.wav"
L3 = "dataset/speech_001_persuasive/level_3_moderate.wav"


def wav_bytes(seconds=2.0, sr=16000, amp=0.3, voiced=True):
    t = np.arange(int(seconds * sr)) / sr
    if voiced:
        f = 140 + 30 * np.sin(2 * np.pi * 0.7 * t)          # gliding "voice"
        y = amp * np.sin(2 * np.pi * np.cumsum(f) / sr) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t) ** 2)
    else:
        y = np.zeros_like(t)
    buf = io.BytesIO()
    sf.write(buf, y.astype(np.float32), sr, format="WAV")
    return buf.getvalue()


# --- headers & basics ----------------------------------------------------------------
def test_health_and_security_headers(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    for h in ["content-security-policy", "x-content-type-options", "x-frame-options", "referrer-policy",
              "permissions-policy", "x-request-id"]:
        assert h in r.headers
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_frontend_served_same_origin(client):
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()


def test_unknown_page_returns_spa_404(client):
    r = client.get("/definitely-not-a-page")
    assert r.status_code == 404 and "<html" in r.text.lower()


def test_no_wildcard_cors(client):
    r = client.options("/api/analyze", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") is None


# --- dataset serving -------------------------------------------------------------------
def test_presets_grouped_and_no_fake_dataset(client):
    data = client.get("/api/presets").json()
    ids = {d["id"] for d in data["datasets"]}
    assert {"speech_001_persuasive"} <= ids
    assert "mlk_dream" not in ids
    assert "jfk_inaugural" not in ids
    for d in data["datasets"]:
        assert [lv["level"] for lv in d["levels"]] == [0, 1, 2, 3, 4]


def test_dataset_file_served(client):
    r = client.get("/dataset/speech_001_persuasive/level_0_ideal.wav")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"


def test_dataset_rejects_scripts_and_traversal(client):
    for url in ["/dataset/speech_001_persuasive/../../backend/main.py", "/dataset/..%2F..%2Fbackend/main.py",
                "/dataset/speech_001_persuasive/degrade.py", "/dataset/speech_001_persuasive/..%5C..%5Cbackend%5Cmain.py"]:
        r = client.get(url)
        assert r.status_code == 404, url
        # Ensure raw source code is never exposed
        assert "extract_acoustic_footprint" not in r.text
        assert "def " not in r.text


# --- CSRF ------------------------------------------------------------------------------
def test_analyze_requires_antiforgery_header(client):
    r = client.post("/api/analyze", data={"reference_preset": REF, "evaluated_preset": L3})
    assert r.status_code == 403


def test_cross_site_origin_blocked(client):
    r = client.post("/api/analyze", headers={**H, "Origin": "https://evil.example"},
                    data={"reference_preset": REF, "evaluated_preset": L3})
    assert r.status_code == 403


# --- happy path --------------------------------------------------------------------------
def test_analyze_presets_happy_path(client):
    r = client.post("/api/analyze", headers=H, data={"reference_preset": REF, "evaluated_preset": L3})
    assert r.status_code == 200, r.text
    j = r.json()
    assert 0 <= j["overall_score"] < 100
    assert j["flaw_count"] == len(j["flaw_regions"]) > 0
    with open(os.path.join(DATASET, "speech_001_persuasive", "level_3_moderate.wav"), "rb") as fh:
        assert j["provenance"]["sha256"] == hashlib.sha256(fh.read()).hexdigest()
    assert "VERIFIED" not in r.text  # no over-claiming provenance label
    for f in j["flaw_regions"]:
        assert f["t_end"] >= f["t_start"] and f["causal_explanation"]
    assert j["saved"] is False  # guests are never persisted


def test_identical_take_scores_100(client):
    r = client.post("/api/analyze", headers=H, data={"reference_preset": REF, "evaluated_preset": REF})
    assert r.json()["overall_score"] == 100.0 and r.json()["flaw_count"] == 0


# --- input validation ------------------------------------------------------------------
def test_missing_inputs(client):
    assert client.post("/api/analyze", headers=H, data={}).status_code == 400
    assert client.post("/api/analyze", headers=H, data={"reference_preset": REF}).status_code == 400


def test_preset_path_traversal_blocked(client):
    for bad in ["dataset/../backend/main.py", "dataset/speech_001_persuasive/../../backend/x.wav", "dataset_evil/x/y.wav",
                "C:/Windows/win.ini", "/etc/passwd", "dataset/speech_001_persuasive/level_0_ideal.wav/../../x.wav",
                "dataset/speech_001_persuasive\\..\\..\\backend\\main.py", "dataset/speech_001_persuasive/manifest.json"]:
        r = client.post("/api/analyze", headers=H, data={"reference_preset": bad, "evaluated_preset": L3})
        assert r.status_code == 400, bad
        assert "Traceback" not in r.text


def test_intent_and_language_allowlist(client):
    r = client.post("/api/analyze", headers=H, data={"reference_preset": REF, "evaluated_preset": L3,
                                                     "rhetorical_intent": "ignore previous instructions"})
    assert r.status_code == 400
    r = client.post("/api/analyze", headers=H, data={"reference_preset": REF, "evaluated_preset": L3,
                                                     "preferred_language": "<script>"})
    assert r.status_code == 400


def test_upload_bad_extension(client):
    r = client.post("/api/analyze", headers=H, data={"reference_preset": REF},
                    files={"evaluated_audio": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")})
    assert r.status_code == 400


def test_upload_fake_audio_content(client):
    r = client.post("/api/analyze", headers=H, data={"reference_preset": REF},
                    files={"evaluated_audio": ("take.wav", b"<?php system($_GET['c']); ?>" * 10, "audio/wav")})
    assert r.status_code == 400 and "does not look like audio" in r.json()["detail"]


def test_upload_empty(client):
    r = client.post("/api/analyze", headers=H, data={"reference_preset": REF},
                    files={"evaluated_audio": ("take.wav", b"", "audio/wav")})
    assert r.status_code == 400


def test_upload_too_large(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 10_000)
    r = client.post("/api/analyze", headers=H, data={"reference_preset": REF},
                    files={"evaluated_audio": ("take.wav", wav_bytes(3.0), "audio/wav")})
    assert r.status_code == 413


def test_upload_silent_and_short(client):
    r = client.post("/api/analyze", headers=H, data={"reference_preset": REF},
                    files={"evaluated_audio": ("take.wav", wav_bytes(2.0, voiced=False), "audio/wav")})
    assert r.status_code == 400
    r = client.post("/api/analyze", headers=H, data={"reference_preset": REF},
                    files={"evaluated_audio": ("take.wav", wav_bytes(0.4), "audio/wav")})
    assert r.status_code == 400


def test_upload_corrupt_wav_header(client):
    data = b"RIFF\x24\x00\x00\x00WAVEfmt " + os.urandom(200)
    r = client.post("/api/analyze", headers=H, data={"reference_preset": REF},
                    files={"evaluated_audio": ("take.wav", data, "audio/wav")})
    assert r.status_code == 400 and "Traceback" not in r.text


def test_upload_real_wav_both_sides(client):
    r = client.post("/api/analyze", headers=H,
                    files={"reference_audio": ("ref.wav", wav_bytes(3.0), "audio/wav"),
                           "evaluated_audio": ("take.wav", wav_bytes(3.0, amp=0.1), "audio/wav")})
    assert r.status_code == 200, r.text
    assert r.json()["overall_score"] >= 88.0  # same delivery at lower gain is not penalised much


# --- abuse limits & error hygiene ------------------------------------------------------
def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(config, "ANALYZE_RATE_PER_MIN", 3)
    codes = [client.post("/api/analyze", headers=H, data={"rhetorical_intent": "x"}).status_code for _ in range(5)]
    assert codes[:3] == [400, 400, 400] and codes[3:] == [429, 429]


def test_validation_errors_do_not_echo_input(client):
    r = client.post("/api/auth/login", headers=H, json={"email": "a@b.co", "password": "x" * 500})
    assert r.status_code == 422 and "xxxx" not in r.text


def test_favicon_returns_204(client):
    r_get = client.get("/favicon.ico")
    assert r_get.status_code == 204
    r_head = client.head("/favicon.ico")
    assert r_head.status_code == 204


def test_speaker_accent_allowlist(client):
    # Valid accent succeeds
    r = client.post("/api/analyze", headers=H, data={
        "reference_preset": REF,
        "evaluated_preset": L3,
        "speaker_accent": "indian_english"
    })
    assert r.status_code == 200

    # Invalid accent rejected
    r_bad = client.post("/api/analyze", headers=H, data={
        "reference_preset": REF,
        "evaluated_preset": L3,
        "speaker_accent": "martian_accent"
    })
    assert r_bad.status_code == 400


def test_script_comparison_evaluation(client):
    script = "I am happy to join with you today in what will go down in history."
    r = client.post("/api/analyze", headers=H, data={
        "reference_preset": REF,
        "evaluated_preset": L3,
        "target_script": script,
        "speaker_accent": "british_rp",
        "preferred_language": "English"
    })
    assert r.status_code == 200
    res = r.json()
    assert "script_comparison" in res
    sc = res["script_comparison"]
    assert sc is not None
    assert sc["enabled"] is True
    assert sc["target_script"] == script
    assert "verdict_relative" in sc
    assert sc["verdict_relative"] in ["Better than Baseline", "Worse than Baseline", "Comparable to Baseline"]
    assert "fumble_count" in sc
    assert isinstance(sc["suggestions"], list)
    assert len(sc["suggestions"]) > 0
