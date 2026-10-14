"""
dataset/degrade.py
Builds a *cumulative* contrastive gradient (L1..L4) from a gold recording (L0).

Each level contains every flaw of the previous level, only stronger, so the
expected ordering of delivery quality is strictly L0 > L1 > L2 > L3 > L4.

Flaw dials (severity s in [0, 1]):
  F1 monotone      : pitch contour pulled toward the speaker's median (chunk-wise pitch shift)
  F5 energy fade   : gain ramps down across the final 45% of the take
  F3 dead pause    : silence inserted mid-clause (longer at higher levels)
  F2 rushed pacing : global time-compression

Usage:  python dataset/degrade.py                          (rebuilds every dataset folder that has level_0_ideal.wav)
        python dataset/degrade.py speech_001_persuasive    (rebuild one folder)
"""

import os
import sys
import json
import numpy as np
import librosa
import soundfile as sf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LEVELS = [
    # name,                 s,    pitch_flatten, fade_floor, pauses (at_sec, len_sec),   speed
    ("level_1_subtle.wav",   0.25, 0.30, 0.85, [(4.0, 0.9)],                  1.04),
    ("level_2_minor.wav",    0.50, 0.55, 0.65, [(4.0, 1.6)],                  1.08),
    ("level_3_moderate.wav", 0.75, 0.75, 0.40, [(4.0, 2.4)],                  1.12),
    ("level_4_egregious.wav",1.00, 0.92, 0.15, [(4.0, 3.0), (9.0, 2.5)],      1.18),
]


def flatten_pitch(y: np.ndarray, sr: int, amount: float) -> np.ndarray:
    """Pull each ~120 ms chunk's pitch toward the global median by `amount` (0..1)."""
    if amount <= 0:
        return y.copy()
    hop = 256
    f0 = librosa.yin(y, fmin=60, fmax=500, sr=sr, frame_length=2048, hop_length=hop)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    n = min(len(f0), len(rms))
    f0, rms = f0[:n], rms[:n]
    voiced = rms > np.percentile(rms, 35)
    if voiced.sum() < 10:
        return y.copy()
    med = float(np.median(f0[voiced]))

    chunk = int(0.12 * sr)
    step = chunk // 2
    win = np.hanning(chunk)
    out = np.zeros(len(y) + chunk)
    norm = np.zeros(len(y) + chunk)
    for start in range(0, len(y), step):
        seg = y[start:start + chunk]
        if len(seg) < chunk:
            seg = np.pad(seg, (0, chunk - len(seg)))
        a, b = start // hop, min(n, (start + chunk) // hop + 1)
        fv = f0[a:b][voiced[a:b]]
        shifted = seg
        if len(fv) >= 3:
            semis = 12.0 * np.log2(float(np.median(fv)) / med)
            shift = float(np.clip(-amount * semis, -7, 7))
            if abs(shift) > 0.15:
                shifted = librosa.effects.pitch_shift(seg, sr=sr, n_steps=shift, n_fft=1024, hop_length=256)
                shifted = librosa.util.fix_length(shifted, size=chunk)
        out[start:start + chunk] += shifted * win
        norm[start:start + chunk] += win
    norm[norm < 1e-3] = 1.0
    return (out / norm)[:len(y)].astype(np.float32)


def apply_fade(y: np.ndarray, floor: float) -> np.ndarray:
    y = y.copy()
    start = int(len(y) * 0.55)
    y[start:] *= np.linspace(1.0, floor, len(y) - start)
    return y


def insert_pauses(y: np.ndarray, sr: int, pauses) -> np.ndarray:
    """Insert near-silence (tiny noise floor) at the given positions, keeping total length."""
    target = len(y)
    rng = np.random.default_rng(7)
    for at_sec, len_sec in sorted(pauses, reverse=True):
        at = min(int(at_sec * sr), len(y))
        gap = (rng.standard_normal(int(len_sec * sr)) * 1e-4).astype(np.float32)
        y = np.concatenate([y[:at], gap, y[at:]])
    return librosa.util.fix_length(y, size=target)


def speed_up(y: np.ndarray, rate: float) -> np.ndarray:
    if abs(rate - 1.0) < 1e-3:
        return y
    target = len(y)
    z = librosa.effects.time_stretch(y, rate=rate)
    return librosa.util.fix_length(z, size=target)


def build_levels(folder: str) -> None:
    gold_path = os.path.join(folder, "level_0_ideal.wav")
    y, sr = librosa.load(gold_path, sr=22050, mono=True)
    peak = float(np.max(np.abs(y))) or 1.0
    for name, s, flat, floor, pauses, speed in LEVELS:
        z = flatten_pitch(y, sr, flat)
        z = speed_up(z, speed)
        z = apply_fade(z, floor)
        z = insert_pauses(z, sr, pauses)
        z = np.clip(z, -peak, peak)
        sf.write(os.path.join(folder, name), z, sr)
        print(f"  wrote {name} (s={s})")

    manifest_path = os.path.join(folder, "manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    manifest["levels"] = ["level_0_ideal.wav"] + [lv[0] for lv in LEVELS]
    manifest["degradation_recipe"] = [
        {"file": lv[0], "severity": lv[1], "pitch_flatten": lv[2], "fade_floor": lv[3],
         "inserted_pauses_sec": lv[4], "speed_rate": lv[5]} for lv in LEVELS
    ]
    manifest["expected_order"] = "L0 > L1 > L2 > L3 > L4 (cumulative degradation)"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)


if __name__ == "__main__":
    targets = sys.argv[1:] or [d for d in os.listdir(BASE_DIR)
                               if os.path.isfile(os.path.join(BASE_DIR, d, "level_0_ideal.wav"))]
    for t in targets:
        print(f"Building gradient for {t}")
        build_levels(os.path.join(BASE_DIR, t))
