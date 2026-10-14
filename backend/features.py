"""
backend/features.py
Extracts core acoustic footprints using librosa (pYIN pitch, RMS energy, MFCC, pauses, syllable rate).
Translates raw frequencies to gender-agnostic log-scale semitone cents.
"""

import json
import numpy as np
import librosa
from scipy.signal import find_peaks
from typing import Dict, Any

import os
import shutil
import subprocess
import tempfile
import soundfile as sf
import scipy.io.wavfile

def hz_to_cents(f0_hz: np.ndarray, ref_hz: float = 55.0) -> np.ndarray:
    """Converts Hz to musical semitone cents: Cents = 1200 * log2(F0 / 55Hz)"""
    cents = np.full_like(f0_hz, np.nan, dtype=np.float64)
    valid = ~np.isnan(f0_hz) & (f0_hz > 0)
    cents[valid] = 1200.0 * np.log2(f0_hz[valid] / ref_hz)
    return cents

def load_audio_resilient(audio_path: str, target_sr: int = 22050):
    """Loads audio safely using soundfile, scipy.io.wavfile, ffmpeg transcoding, or librosa."""
    # 1. Try soundfile directly
    try:
        y, sr = sf.read(audio_path, dtype="float32", always_2d=False)
        if y.ndim > 1:
            y = np.mean(y, axis=1)
        if sr != target_sr:
            y = librosa.resample(y, orig_sr=sr, target_sr=target_sr)
        return y, target_sr
    except Exception:
        pass

    # 2. Try scipy.io.wavfile (standard PCM WAV)
    try:
        sr, data = scipy.io.wavfile.read(audio_path)
        if data.dtype == np.int16:
            y = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            y = data.astype(np.float32) / 2147483648.0
        elif data.dtype == np.uint8:
            y = (data.astype(np.float32) - 128.0) / 128.0
        else:
            y = data.astype(np.float32)
        if y.ndim > 1:
            y = np.mean(y, axis=1)
        if sr != target_sr:
            y = librosa.resample(y, orig_sr=sr, target_sr=target_sr)
        return y, target_sr
    except Exception:
        pass

    # 3. Transcode using bundled ffmpeg to PCM WAV (handles WebM, Opus, OGG, AAC, M4A, etc.)
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        tmp_wav = None
        try:
            fd, tmp_wav = tempfile.mkstemp(suffix=".wav", prefix="sce_ffmpeg_")
            os.close(fd)
            cmd = [
                ffmpeg_bin, "-nostdin", "-v", "error", "-threads", "1", "-y", "-i", audio_path,
                "-vn", "-acodec", "pcm_s16le", "-ar", str(target_sr), "-ac", "1",
                tmp_wav
            ]
            # Uploaded media is untrusted. Bound decoder CPU use and never let ffmpeg
            # read commands from the process standard input.
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 timeout=30, check=False)
            if res.returncode == 0 and os.path.isfile(tmp_wav) and os.path.getsize(tmp_wav) > 44:
                y, sr = sf.read(tmp_wav, dtype="float32", always_2d=False)
                if y.ndim > 1:
                    y = np.mean(y, axis=1)
                return y, target_sr
        except Exception:
            pass
        finally:
            if tmp_wav and os.path.isfile(tmp_wav):
                try:
                    os.remove(tmp_wav)
                except OSError:
                    pass

    # 4. Fallback to librosa's built-in loader
    return librosa.load(audio_path, sr=target_sr, mono=True)

_FOOTPRINT_CACHE: Dict[str, Dict[str, Any]] = {}

def extract_acoustic_footprint(audio_path: str, target_sr: int = 22050, hop_length: int = 512) -> Dict[str, Any]:
    cache_key = None
    try:
        st = os.stat(audio_path)
        cache_key = f"{os.path.realpath(audio_path)}:{st.st_mtime_ns}:{st.st_size}:{target_sr}:{hop_length}"
        if cache_key in _FOOTPRINT_CACHE:
            return json.loads(json.dumps(_FOOTPRINT_CACHE[cache_key]))
    except Exception:
        pass

    y, sr = load_audio_resilient(audio_path, target_sr=target_sr)
    duration_sec = float(len(y) / sr)

    # 1. Energy Envelope (RMS)
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]

    # 2. Pitch tracking (probabilistic YIN, speech range) & semitone-cents conversion
    # Downsample copy to 16kHz for faster autocorrelation if higher sample rate
    if sr > 16000:
        y_pitch = librosa.resample(y, orig_sr=sr, target_sr=16000)
        hop_pitch = int(hop_length * 16000 / sr)
        f0_raw, voiced_flag_raw, _ = librosa.pyin(y_pitch, fmin=65.0, fmax=400.0, sr=16000, frame_length=1024, hop_length=hop_pitch)
        # Interpolate f0 and voiced_flag back to target frames
        target_len = len(rms)
        raw_len = len(f0_raw)
        if raw_len > 0 and raw_len != target_len:
            f0 = np.interp(np.linspace(0, 1, target_len), np.linspace(0, 1, raw_len), np.nan_to_num(f0_raw, nan=0.0))
            f0[f0 <= 0] = np.nan
            voiced_flag = np.interp(np.linspace(0, 1, target_len), np.linspace(0, 1, raw_len), voiced_flag_raw.astype(float)) > 0.5
        else:
            f0, voiced_flag = f0_raw, voiced_flag_raw
    else:
        f0, voiced_flag, _ = librosa.pyin(y, fmin=65.0, fmax=400.0, sr=sr, frame_length=1024, hop_length=hop_length)

    n = min(len(f0), len(rms))
    f0, voiced_flag, rms = f0[:n], voiced_flag[:n], rms[:n]
    # Absolute floor (relative to the take's peak) so inserted silence is never treated as voiced
    f0[(~voiced_flag) | (rms < float(np.max(rms)) * 0.02)] = np.nan
    f0_cents = hz_to_cents(f0)

    # 3. MFCC (13 Coefficients)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=hop_length)

    # 4. Speech-active regions & pauses (silence > 25 dB below peak)
    intervals = librosa.effects.split(y, top_db=25, hop_length=hop_length)
    pauses = []
    for i in range(len(intervals) - 1):
        p_start = float(intervals[i][1] / sr)
        p_end = float(intervals[i+1][0] / sr)
        p_dur = p_end - p_start
        if p_dur >= 0.2:
            pauses.append({"start": round(p_start, 3), "end": round(p_end, 3), "duration": round(p_dur, 3)})
    active_sec = float(sum((b - a) for a, b in intervals) / sr) if len(intervals) else 0.0

    # 5. Articulation rate: syllable nuclei = loudness peaks with >= 2 dB prominence inside voiced speech
    rms_db_raw = 20.0 * np.log10(rms + 1e-6)
    smooth = np.convolve(rms_db_raw, np.ones(3) / 3.0, mode="same")
    peaks, _ = find_peaks(smooth, prominence=2.0, distance=4)
    peaks = [p for p in peaks if not np.isnan(f0_cents[p])]
    speaking_rate = float(round(len(peaks) / max(0.5, active_sec), 2))

    frame_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop_length)
    voiced_cents = f0_cents[~np.isnan(f0_cents)]
    rms_db = 20.0 * np.log10(rms + 1e-6)
    pitch_spread = float(np.percentile(voiced_cents, 90) - np.percentile(voiced_cents, 10)) if len(voiced_cents) >= 10 else 0.0

    out = {
        "duration_sec": round(duration_sec, 3),
        "active_speech_sec": round(active_sec, 3),
        "speaking_rate_syl_per_sec": speaking_rate,
        "pauses": pauses,
        "frame_times": frame_times.tolist(),
        "f0_cents": np.where(np.isnan(f0_cents), None, f0_cents).tolist(),
        "rms": rms.tolist(),
        "rms_db": rms_db.tolist(),
        "mfcc_mean_vector": np.mean(mfcc, axis=1).tolist(),
        "speaker_baseline": {
            "cents_mean": round(float(np.mean(voiced_cents)), 2) if len(voiced_cents) > 0 else 0.0,
            "cents_std": round(float(np.std(voiced_cents)), 2) if len(voiced_cents) > 0 else 1.0,
            "pitch_spread_p10_p90": round(pitch_spread, 1),
            "voiced_db_median": round(float(np.median(rms_db[~np.isnan(f0_cents)])), 2) if len(voiced_cents) > 0 else float(np.median(rms_db)),
            "rms_mean": round(float(np.mean(rms)), 4),
            "rms_std": round(float(np.std(rms)), 4),
        }
    }
    if cache_key:
        _FOOTPRINT_CACHE[cache_key] = out
    return out
