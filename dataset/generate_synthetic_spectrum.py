"""
generate_synthetic_spectrum.py - Programmatically synthesizes the L0->L4 contrastive spectrum.
Generates:
- L0: Baseline Gold Audio (clean sine-modulated prosody)
- L1: Subtle Pacing Flaw (1 slight hesitation)
- L2: Minor Flaw (mild monotone + slight pacing surge)
- L3: Moderate Flaw (noticeable pitch compression + energy drop)
- L4: Egregious Flaw (complete monotone robotic flatline + 3.0s dead pause)
"""

import os
from pathlib import Path
import json
import numpy as np
import soundfile as sf

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = str(BASE_DIR / "speech_001_persuasive")
os.makedirs(DATASET_DIR, exist_ok=True)

SR = 22050
DURATION = 8.0  # 8-second speech benchmark

def create_synthetic_voice(
    f0_base: float = 140.0,
    f0_variance: float = 35.0,
    dead_pause_sec: float = 0.0,
    energy_drop: bool = False
) -> np.ndarray:
    t = np.linspace(0, DURATION, int(SR * DURATION), endpoint=False)

    # 1. Pitch contour modulation
    f0_contour = f0_base + f0_variance * np.sin(2 * np.pi * 0.8 * t) + (f0_variance * 0.4) * np.sin(2 * np.pi * 2.1 * t)
    phase = 2 * np.pi * np.cumsum(f0_contour) / SR

    # Harmonics (rich vocal timbre)
    y = 0.5 * np.sin(phase) + 0.25 * np.sin(2 * phase) + 0.12 * np.sin(3 * phase)

    # Syllabic amplitude envelope (pacing peaks)
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 3.2 * t) ** 2

    if energy_drop:
        # Fade out energy severely in second half
        fade_mask = np.ones_like(t)
        fade_mask[t > 4.0] = np.linspace(1.0, 0.1, len(fade_mask[t > 4.0]))
        env *= fade_mask

    y = y * env

    # Insert dead pause if requested
    if dead_pause_sec > 0:
        pause_samples = int(SR * dead_pause_sec)
        pause_start = int(SR * 2.5)
        y[pause_start:pause_start + pause_samples] = 0.0

    # Add slight realistic studio room noise
    noise = np.random.normal(0, 0.005, len(y))
    y = y + noise

    # Normalize peak to -1 dB
    max_val = np.max(np.abs(y)) + 1e-6
    return (y / max_val * 0.9).astype(np.float32)

def generate_spectrum():
    levels = {
        "level_0_ideal": {"f0_var": 40.0, "pause": 0.0, "drop": False, "desc": "Ideal gold standard with dynamic melodic prosody"},
        "level_1_subtle": {"f0_var": 30.0, "pause": 0.5, "drop": False, "desc": "Near perfect; minor cadence hesitation"},
        "level_2_minor": {"f0_var": 20.0, "pause": 1.2, "drop": False, "desc": "Noticeable pacing break and reduced pitch variance"},
        "level_3_moderate": {"f0_var": 10.0, "pause": 1.8, "drop": True, "desc": "Monotone compression and late phrase volume fade"},
        "level_4_egregious": {"f0_var": 1.5, "pause": 3.0, "drop": True, "desc": "Robotic pitch flatline + 3-second mid-sentence silence"}
    }

    manifest = {
        "speech_id": "speech_001_persuasive",
        "title": "Synthetic Oratory Benchmark - The Power of Civic Action",
        "sample_rate": SR,
        "duration_sec": DURATION,
        "levels": {}
    }

    for lvl_name, params in levels.items():
        wav_path = os.path.join(DATASET_DIR, f"{lvl_name}.wav")
        audio = create_synthetic_voice(
            f0_base=150.0,
            f0_variance=params["f0_var"],
            dead_pause_sec=params["pause"],
            energy_drop=params["drop"]
        )
        sf.write(wav_path, audio, SR)
        manifest["levels"][lvl_name] = {
            "file": f"{lvl_name}.wav",
            "description": params["desc"],
            "parameters": params
        }
        print(f"Generated: {wav_path}")

    manifest_path = os.path.join(DATASET_DIR, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest written to {manifest_path}")

if __name__ == "__main__":
    generate_spectrum()
