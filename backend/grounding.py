"""
backend/grounding.py
Dynamic Time Warping (DTW) alignment, temporal flaw grounding and rubric scoring.

Design notes
------------
* Pitch is compared in cents (log-frequency), so a 5-semitone swing means the same
  thing for a low or a high voice (gender/voice-type agnostic).
* Pitch *variation* in each evaluated window is compared against the DTW-matched
  window of the reference.  Comparing a take only against its own baseline cannot
  detect a take that is monotone from start to finish.
* Energy is z-scored per take, so recording gain / mic distance does not matter;
  what matters is the *shape* of the energy contour versus the reference.
* Every rubric dimension is 100 for an identical take and decreases continuously
  with the size of the deviation, so small deliveries flaws still move the score.
"""

from typing import List, Dict, Any, Optional
import numpy as np
from dtw import dtw

# Intent-specific tolerances. A dramatic/storytelling delivery is allowed longer pauses.
INTENT_PROFILES: Dict[str, Dict[str, float]] = {
    "persuasive_oratory": {"max_pause": 2.0},
    "informative_lecture": {"max_pause": 2.0},
    "storytelling": {"max_pause": 2.6},
    "dramatic_declamation": {"max_pause": 3.0},
    "debate_rebuttal": {"max_pause": 1.6},
}
DEFAULT_INTENT = "persuasive_oratory"

WINDOW = 20          # frames (~0.46 s at 22.05 kHz / hop 512)
MONOTONE_RATIO = 0.55  # eval pitch-variation / reference pitch-variation below this => flaw
ENERGY_Z = 1.6       # z-units below the matched reference => projection collapse


def _clip(v: float) -> float:
    return float(max(0.0, min(100.0, v)))


def _nan_std(a: np.ndarray) -> Optional[float]:
    v = a[~np.isnan(a)]
    return float(np.std(v)) if len(v) >= 6 else None


def align_and_ground_flaws(ref_features: Dict[str, Any], eval_features: Dict[str, Any],
                           rhetorical_intent: str = DEFAULT_INTENT) -> Dict[str, Any]:
    profile = INTENT_PROFILES.get(rhetorical_intent, INTENT_PROFILES[DEFAULT_INTENT])
    max_pause = profile["max_pause"]

    ref_rms = np.asarray(ref_features["rms"], dtype=float)
    eval_rms = np.asarray(eval_features["rms"], dtype=float)
    ref_rms_z = (ref_rms - ref_rms.mean()) / (ref_rms.std() + 1e-9)
    eval_rms_z = (eval_rms - eval_rms.mean()) / (eval_rms.std() + 1e-9)

    ref_cents = np.array([np.nan if x is None else x for x in ref_features["f0_cents"]], dtype=float)
    eval_cents = np.array([np.nan if x is None else x for x in eval_features["f0_cents"]], dtype=float)
    eval_times = np.asarray(eval_features["frame_times"], dtype=float)

    try:
        alignment = dtw(eval_rms_z, ref_rms_z, keep_internals=False,
                        window_type="sakoechiba", window_args={"window_size": max(len(eval_rms), len(ref_rms)) // 2})
    except Exception:
        # Fallback to unconstrained dynamic time warping if window constraint fails on unequal length takes
        alignment = dtw(eval_rms_z, ref_rms_z, keep_internals=False)

    matched = np.zeros(len(eval_rms), dtype=int)
    for e_idx, r_idx in zip(alignment.index1, alignment.index2):
        matched[e_idx] = r_idx

    flaws: List[Dict[str, Any]] = []

    # --- F3 dead pauses --------------------------------------------------------
    for p in eval_features["pauses"]:
        if p["duration"] > max_pause:
            flaws.append({
                "flaw_code": "F3_DEAD_PAUSE",
                "flaw_name": "Dead pause / hesitation",
                "t_start": p["start"], "t_end": p["end"],
                "severity_sigma": round(float(p["duration"] / max_pause), 2),
                "metric_delta": f"Pause of {p['duration']:.2f}s (tolerance {max_pause:.1f}s for this intent)",
            })

    # --- windowed pitch + energy comparison ------------------------------------
    for i in range(0, max(1, len(eval_rms) - WINDOW), WINDOW // 2):
        j = matched[i]
        r_lo, r_hi = j, min(len(ref_rms_z), j + WINDOW)
        t_start = round(float(eval_times[i]), 2)
        t_end = round(float(eval_times[min(i + WINDOW, len(eval_times) - 1)]), 2)

        ref_var = _nan_std(ref_cents[r_lo:r_hi])
        ev_var = _nan_std(eval_cents[i:i + WINDOW])
        if ref_var is not None and ref_var > 60 and ev_var is not None:
            ratio = ev_var / ref_var
            if ratio < MONOTONE_RATIO:
                flaws.append({
                    "flaw_code": "F1_MONOTONE_COLLAPSE",
                    "flaw_name": "Monotone / flattened pitch",
                    "t_start": t_start, "t_end": t_end,
                    "severity_sigma": round(float((1.0 - ratio) * 3.0), 2),
                    "metric_delta": f"Pitch movement {ev_var:.0f} cents vs {ref_var:.0f} cents in reference ({ratio*100:.0f}%)",
                })

        if r_hi > r_lo:
            delta = float(np.mean(eval_rms_z[i:i + WINDOW]) - np.mean(ref_rms_z[r_lo:r_hi]))
            if delta < -ENERGY_Z:
                flaws.append({
                    "flaw_code": "F5_ENERGY_DROP",
                    "flaw_name": "Vocal projection collapse",
                    "t_start": t_start, "t_end": t_end,
                    "severity_sigma": round(abs(delta), 2),
                    "metric_delta": f"Loudness {abs(delta):.1f} standard deviations below the matched reference",
                })

    # --- merge overlapping flaws of the same type ------------------------------
    merged: List[Dict[str, Any]] = []
    for f in sorted(flaws, key=lambda x: (x["flaw_code"], x["t_start"])):
        last = merged[-1] if merged else None
        if last and last["flaw_code"] == f["flaw_code"] and f["t_start"] <= last["t_end"] + 0.3:
            last["t_end"] = max(last["t_end"], f["t_end"])
            if f["severity_sigma"] > last["severity_sigma"]:
                last["severity_sigma"] = f["severity_sigma"]
                last["metric_delta"] = f["metric_delta"]
        else:
            merged.append(dict(f))
    merged.sort(key=lambda x: x["t_start"])

    # --- continuous rubric scores ----------------------------------------------
    total = max(1.0, float(eval_features["duration_sec"]))

    def penalty(code: str) -> float:
        return sum(f["severity_sigma"] * (f["t_end"] - f["t_start"]) for f in merged if f["flaw_code"] == code) / total

    ref_spread = float(ref_features["speaker_baseline"].get("pitch_spread_p10_p90") or 0.0)
    ev_spread = float(eval_features["speaker_baseline"].get("pitch_spread_p10_p90") or 0.0)
    range_ratio = min(1.0, ev_spread / ref_spread) if ref_spread > 1e-6 else 1.0
    score_pitch = _clip(100.0 * range_ratio ** 0.8 - 25.0 * penalty("F1_MONOTONE_COLLAPSE"))

    # Projection loss: dB below each take's own voiced median, compared frame-by-frame on the DTW path.
    # Gain-invariant (a quieter microphone does not lose points); a fade or swallowed endings does.
    ref_db = np.asarray(ref_features.get("rms_db") or 20 * np.log10(ref_rms + 1e-6), dtype=float)
    ev_db = np.asarray(eval_features.get("rms_db") or 20 * np.log10(eval_rms + 1e-6), dtype=float)
    ref_rel = ref_db - float(ref_features["speaker_baseline"].get("voiced_db_median", np.median(ref_db)))
    ev_rel = ev_db - float(eval_features["speaker_baseline"].get("voiced_db_median", np.median(ev_db)))
    path_e, path_r = np.asarray(alignment.index1), np.asarray(alignment.index2)
    path_e = np.clip(path_e, 0, len(ev_rel) - 1)
    path_r = np.clip(path_r, 0, len(ref_rel) - 1)
    ref_voiced_path = ~np.isnan(ref_cents[np.clip(path_r, 0, len(ref_cents) - 1)])
    loss = np.maximum(0.0, ref_rel[path_r] - ev_rel[path_e])
    projection_loss_db = float(np.mean(loss[ref_voiced_path])) if ref_voiced_path.any() else 0.0
    dyn_cost = float(alignment.normalizedDistance)
    score_dynamics = _clip(100.0 - 4.0 * projection_loss_db - 25.0 * dyn_cost - 20.0 * penalty("F5_ENERGY_DROP"))

    rate_ref = max(0.5, float(ref_features["speaking_rate_syl_per_sec"]))
    rate_diff = abs(float(eval_features["speaking_rate_syl_per_sec"]) - rate_ref) / rate_ref
    dur_ref = max(1.0, float(ref_features["duration_sec"]))
    voiced_ref = float(np.sum(~np.isnan(ref_cents))) / max(1, len(ref_cents))
    voiced_ev = float(np.sum(~np.isnan(eval_cents))) / max(1, len(eval_cents))
    score_cadence = _clip(100.0 - 120.0 * rate_diff - 90.0 * max(0.0, voiced_ref - voiced_ev))

    ref_pause = sum(p["duration"] for p in ref_features["pauses"]) / dur_ref
    ev_pause = sum(p["duration"] for p in eval_features["pauses"]) / total
    score_pause = _clip(100.0 - 160.0 * max(0.0, ev_pause - ref_pause) - 30.0 * penalty("F3_DEAD_PAUSE"))

    overall = 0.35 * score_pitch + 0.25 * score_dynamics + 0.25 * score_cadence + 0.15 * score_pause

    return {
        "overall_score": round(overall, 1),
        "rubric_scores": {
            "melodic_inflection": round(score_pitch, 1),
            "dynamic_projection": round(score_dynamics, 1),
            "cadence_stability": round(score_cadence, 1),
            "pause_architecture": round(score_pause, 1),
        },
        "diagnostics": {
            "pitch_range_ratio": round(range_ratio, 3),
            "energy_dtw_cost": round(dyn_cost, 3),
            "projection_loss_db": round(projection_loss_db, 2),
            "rate_ref": rate_ref,
            "rate_eval": float(eval_features["speaking_rate_syl_per_sec"]),
            "pause_ratio_ref": round(ref_pause, 3),
            "pause_ratio_eval": round(ev_pause, 3),
            "intent_max_pause_sec": max_pause,
        },
        "flaw_regions": merged,
        "total_flaws_count": len(merged),
    }
