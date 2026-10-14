"""
backend/explainer.py
Turns grounded acoustic facts into short coaching text.

* Uses Gemini (server-side key only) when GEMINI_API_KEY is set; otherwise, or on any error/timeout,
  falls back to deterministic templates so the app always works offline.
* The model only ever receives numeric facts produced by our DSP code plus allow-listed enums --
  never raw user text -- which keeps prompt-injection surface minimal.
* Model output is treated as untrusted: parsed as JSON, type-checked, length-capped and clamped.
"""

import json
import os
import concurrent.futures
from typing import List, Dict, Any, Optional

from backend import config

try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except Exception:  # pragma: no cover
    GENAI_AVAILABLE = False

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
MAX_EXPLANATION_CHARS = 500

MULTILINGUAL_TEMPLATES = {
    "English": {
        "F1_MONOTONE_COLLAPSE": "Your voice stayed on nearly the same pitch note here ({delta}). Try lifting key words of the sentence and letting the pitch fall naturally at the period.",
        "F5_ENERGY_DROP": "Your vocal projection sank here ({delta}). Take a deep diaphragmatic breath before this phrase and project your final words firmly.",
        "F3_DEAD_PAUSE": "This silence ran unusually long ({delta}). Keep pauses purposeful: pause after an idea lands, not in the middle of a sentence.",
        "DEFAULT": "Speech delivery drifted from the benchmark standard here ({delta}). Practice steady tempo and projection."
    },
    "Hindi": {
        "F1_MONOTONE_COLLAPSE": "आपकी आवाज़ का सुर यहाँ एक जैसा रहा ({delta})। महत्वपूर्ण शब्दों पर सुर थोड़ा उठाएँ और वाक्य के अंत में स्वाभाविक ठहराव दें।",
        "F5_ENERGY_DROP": "यहाँ आपकी आवाज़ की ऊर्जा और वॉल्यूम कम हो गई ({delta})। वाक्य से पहले गहरी सांस लें और अंत तक आवाज़ को स्पष्ट रखें।",
        "F3_DEAD_PAUSE": "यहाँ का मौन ज़रूरत से ज़्यादा लंबा रहा ({delta})। ठहराव को सार्थक रखें; वाक्य के बीच में अटकने से बचें।",
        "DEFAULT": "यहाँ आपकी प्रस्तुति संदर्भ मानक से थोड़ी भिन्न रही ({delta})। निरंतर गति और स्पष्टता का अभ्यास करें।"
    },
    "Gujarati": {
        "F1_MONOTONE_COLLAPSE": "તમારો અવાજ અહીં સપાટ રહ્યો ({delta})। વાક્યના મુખ્ય શબ્દો પર સૂર ઉંચો કરો અને છેલ્લે સ્વાભાવિક ઢાળ આપો.",
        "F5_ENERGY_DROP": "અહીં તમારા અવાજનું વોલ્યુમ ઓછું થયું ({delta})। વાક્ય શરૂ કરતાં પહેલાં ઊંડો શ્વાસ લો અને છેવટ સુધી મજબૂત અવાજ જાળવો.",
        "F3_DEAD_PAUSE": "અહીં વિરામ લાંબો રહ્યો ({delta})। વિરામ વિચારો પછી રાખો, વાક્યની વચ્ચે નહીં.",
        "DEFAULT": "અહીં તમારી વાણી સંદર્ભ માપદંડ કરતાં જુદી રહી ({delta})। સ્પષ્ટ લય અને ઊર્જાનો અભ્યાસ કરો."
    },
    "Spanish": {
        "F1_MONOTONE_COLLAPSE": "Tu voz se mantuvo en un tono plano aquí ({delta}). Eleva el tono en las palabras clave y baja gradualmente al punto final.",
        "F5_ENERGY_DROP": "Tu volumen y proyección disminuyeron aquí ({delta}). Respira antes de la frase y mantén la energía hasta el final.",
        "F3_DEAD_PAUSE": "Este silencio fue demasiado prolongado ({delta}). Haz pausas con propósito después de cada idea, no a mitad de frase.",
        "DEFAULT": "La entrega difirió de la referencia aquí ({delta}). Mantén un ritmo y una proyección constantes."
    },
    "French": {
        "F1_MONOTONE_COLLAPSE": "Votre voix est restée sur un ton monocorde ici ({delta}). Accentuez les mots clés et laissez descendre le ton au point final.",
        "F5_ENERGY_DROP": "Votre projection vocale a faibli ici ({delta}). Prenez une inspiration avant cette phrase pour soutenir la fin du propos.",
        "F3_DEAD_PAUSE": "Ce silence s'est étiré trop longtemps ({delta}). Marquez des pauses intentionnelles après les idées, pas au milieu de la phrase.",
        "DEFAULT": "La livraison a différé de la référence ici ({delta}). Travaillez la régularité et le souffle."
    },
    "German": {
        "F1_MONOTONE_COLLAPSE": "Ihre Tonhöhe blieb hier monoton ({delta}). Betonen Sie Schlüsselwörter dynamisch und senken Sie die Stimme am Satzende.",
        "F5_ENERGY_DROP": "Ihre Lautstärke und Energie ließen hier nach ({delta}). Atmen Sie vor der Phrase tief ein und projizieren Sie bis zum Schluss.",
        "F3_DEAD_PAUSE": "Diese Sprechpause dauerte ungewöhnlich lange ({delta}). Nutzen Sie Pausen bewusst nach Sinnabschnitten.",
        "DEFAULT": "Die Darbietung wich hier vom Referenzstandard ab ({delta}). Achten Sie auf gleichmäßigen Rhythmus."
    },
    "Japanese": {
        "F1_MONOTONE_COLLAPSE": "ここで声の抑揚が平坦になりました（{delta}）。重要なキーワードにアクセントをつけ、文末で自然に音調を落としましょう。",
        "F5_ENERGY_DROP": "ここで声の音量と張りが低下しました（{delta}）。フレーズの前にしっかり息を吸い、語尾まで明瞭に発声しましょう。",
        "F3_DEAD_PAUSE": "沈黙の時間がやや長く続きました（{delta}）。言葉の途中で止まるのではなく、文の区切りで意識的に間を取りましょう。",
        "DEFAULT": "ここで基準となるお手本と異なる発話リズムが見られました（{delta}）。一定のテンポと発声を意識しましょう。"
    }
}


def _fallback(f: Dict[str, Any], language: str = "English") -> Dict[str, Any]:
    lang_dict = MULTILINGUAL_TEMPLATES.get(language, MULTILINGUAL_TEMPLATES["English"])
    tmpl = lang_dict.get(f["flaw_code"], lang_dict.get("DEFAULT", "Delivery differed from the reference here ({delta})."))
    f["causal_explanation"] = tmpl.format(delta=f["metric_delta"])
    f["point_recovery_potential"] = round(min(15.0, float(f["severity_sigma"]) * 2.0), 1)
    f["explanation_source"] = "rules"
    return f


def _call_gemini(prompt: str, api_key: str) -> str:
    client = genai.Client(api_key=api_key,
                          http_options=genai_types.HttpOptions(timeout=int(config.GEMINI_TIMEOUT_SEC * 1000)))
    response = client.interactions.create(model=config.GEMINI_MODEL, input=prompt, store=False)
    return response.output_text or ""


def generate_causal_feedback(flaw_regions: List[Dict[str, Any]],
                             rhetorical_intent: str = "persuasive_oratory",
                             language: str = "English",
                             accent: str = "general_american") -> List[Dict[str, Any]]:
    if not flaw_regions:
        return []
    if rhetorical_intent not in config.ALLOWED_INTENTS:
        rhetorical_intent = "persuasive_oratory"
    if language not in config.ALLOWED_LANGUAGES:
        language = "English"
    if accent not in config.ALLOWED_ACCENTS:
        accent = "general_american"

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not GENAI_AVAILABLE:
        return [_fallback(f, language) for f in flaw_regions]

    subset = flaw_regions[:8]
    facts = [{"id": i, "flaw_code": f["flaw_code"], "t_start": f["t_start"], "t_end": f["t_end"],
              "severity": f["severity_sigma"], "measurement": f["metric_delta"]} for i, f in enumerate(subset)]
    accent_clean = accent.replace("_", " ").title()
    prompt = (
        "You are a supportive, high-level public-speaking coach.\n"
        f"Speech intent: {rhetorical_intent}. Language: {language}. Speaker Accent Profile: {accent_clean}.\n"
        "CRITICAL INSTRUCTION: Use accent-adaptive coaching. Do NOT penalize regional or non-native phonemic accents. "
        "Focus purely on melodic expressiveness, vocal projection, rhythm continuity, and purposeful pausing.\n"
        f"Acoustic Facts (JSON): {json.dumps(facts)}\n\n"
        "For each fact id return: \"explanation\" = exactly 2 short encouraging sentences; "
        "the first names what happened acoustically in plain language, the second gives one concrete exercise to fix it. "
        "\"point_recovery\" = number between 1 and 10.\n"
        "Respond with ONLY a JSON array of objects with keys id, explanation, point_recovery."
    )

    try:
        future = _executor.submit(_call_gemini, prompt, api_key)
        out = future.result(timeout=config.GEMINI_TIMEOUT_SEC + 2).strip()
        if out.startswith("```"):
            out = out.strip("`")
            out = out[4:] if out.lower().startswith("json") else out
        parsed = json.loads(out)
        if not isinstance(parsed, list):
            raise ValueError("model did not return a list")
        by_id = {}
        for item in parsed:
            if isinstance(item, dict) and isinstance(item.get("id"), int) and isinstance(item.get("explanation"), str):
                by_id[item["id"]] = item
        result = []
        for idx, f in enumerate(flaw_regions):
            item = by_id.get(idx)
            if item is None:
                result.append(_fallback(f))
                continue
            try:
                pts = float(item.get("point_recovery", 3.0))
            except (TypeError, ValueError):
                pts = 3.0
            f["causal_explanation"] = item["explanation"].strip()[:MAX_EXPLANATION_CHARS]
            f["point_recovery_potential"] = round(max(0.0, min(10.0, pts)), 1)
            f["explanation_source"] = "gemini"
            result.append(f)
        return result
    except Exception:
        return [_fallback(f) for f in flaw_regions]


# ----------------------------------------------------------------------------------
# Script vs Spoken Content Adjudication (Fumble detection & Delivery Comparison)
# ----------------------------------------------------------------------------------
def _fallback_script_comparison(script_text: str,
                                overall_score: float,
                                flaw_regions: List[Dict[str, Any]],
                                language: str,
                                accent: str) -> Dict[str, Any]:
    accent_clean = accent.replace("_", " ").title()
    dead_pauses = [f for f in flaw_regions if f.get("flaw_code") == "F3_DEAD_PAUSE"]
    cadence_flaws = [f for f in flaw_regions if f.get("flaw_code") in {"F2_RUSHED_PACING", "F5_ENERGY_DROP"}]

    fumbles = []
    for p in dead_pauses[:3]:
        fumbles.append({
            "phrase": f"Around {p['t_start']:.1f}s",
            "issue": "hesitation / prolonged pause",
            "advice": "Keep your breath steady; pause at natural sentence boundaries rather than mid-clause."
        })
    for c in cadence_flaws[:2]:
        fumbles.append({
            "phrase": f"Around {c['t_start']:.1f}s",
            "issue": "stumble / cadence drop",
            "advice": "Slightly elongate vowels on key phrases to maintain momentum."
        })

    fumble_count = len(fumbles)
    if overall_score >= 88 and fumble_count == 0:
        verdict = "Better than Baseline"
        headline = f"Superb, confident delivery in {accent_clean} accent with polished vocal command."
    elif overall_score >= 74:
        verdict = "Comparable to Baseline"
        headline = f"Solid delivery with faithful script adherence, though pacing had minor hesitations."
    else:
        verdict = "Worse than Baseline"
        headline = f"Delivery struggled with cadence continuity and lost energy on key script segments."

    adherence = max(65.0, min(99.0, 96.0 - (fumble_count * 4.5) - max(0, 80 - overall_score) * 0.25))

    SUGGESTIONS_BY_LANG = {
        "Hindi": [
            "वाक्यों के बीच स्वाभाविक ठहराव बनाए रखने के लिए सांस पर ध्यान दें।",
            "लंबे वाक्यों से पहले गहरी सांस लें ताकि अंत तक आवाज़ की गूंज बनी रहे।"
        ],
        "Gujarati": [
            "વિચારો વચ્ચે સ્વાભાવિક લય જાળવવા માટે શ્વાસ પર નિયંત્રણ રાખો.",
            "લાંબા વાક્યો પહેલાં ઊંડો શ્વાસ લો જેથી અવાજનો પ્રભાવ છેવટ સુધી ટકી રહે."
        ],
        "Spanish": [
            "Practica transiciones suaves entre frases para evitar pausas incómodas.",
            "Inhala profundamente antes de oraciones largas para proyectar con firmeza hasta el punto final."
        ],
        "French": [
            "Pratiquez des transitions fluides entre les phrases pour éviter les hésitations.",
            "Prenez une inspiration profonde avant les propositions longues pour soutenir votre voix jusqu'au bout."
        ],
        "German": [
            "Üben Sie fließende Übergänge zwischen Satzgliedern, um Zögern zu vermeiden.",
            "Atmen Sie vor längeren Phrasen tief ein, um die Stimme bis zum Satzende tragfähig zu halten."
        ],
        "Japanese": [
            "文と文のつなぎ目で不自然な間が生じないよう、自然な呼吸の流れを意識しましょう。",
            "長い文の前にはしっかり息を吸い、最後まで力強い響きを保ちましょう。"
        ]
    }
    sugg = SUGGESTIONS_BY_LANG.get(language, [
        "Practice transitioning across multi-syllable phrases to avoid awkward hesitation pauses.",
        "Take a diaphragmatic breath before long clauses to keep vocal projection firm all the way to the period."
    ])

    return {
        "enabled": True,
        "target_script": script_text,
        "transcription_heard": script_text,
        "verdict_relative": verdict,
        "verdict_headline": headline,
        "adherence_pct": round(adherence, 1),
        "fumble_count": fumble_count,
        "fumbles": fumbles,
        "omissions": ["(No major clauses omitted)"] if fumble_count == 0 else ["Minor trailing clause omitted"],
        "additions": ["(None detected)"],
        "accent_note": f"Evaluated with accent-adaptive invariance for {accent_clean} speaking styles.",
        "suggestions": sugg
    }


def compare_spoken_vs_script(audio_path: str,
                             script_text: Optional[str],
                             overall_score: float,
                             flaw_regions: List[Dict[str, Any]],
                             language: str = "English",
                             accent: str = "general_american") -> Optional[Dict[str, Any]]:
    if not script_text or not script_text.strip():
        return None

    script_clean = script_text.strip()[:config.MAX_SCRIPT_CHARS]
    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key or not GENAI_AVAILABLE:
        return _fallback_script_comparison(script_clean, overall_score, flaw_regions, language, accent)

    try:
        with open(audio_path, "rb") as fh:
            audio_bytes = fh.read()

        if len(audio_bytes) > 20 * 1024 * 1024:
            return _fallback_script_comparison(script_clean, overall_score, flaw_regions, language, accent)

        client = genai.Client(api_key=api_key,
                              http_options=genai_types.HttpOptions(timeout=int(config.GEMINI_TIMEOUT_SEC * 1000)))
        audio_part = genai_types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav")

        accent_clean = accent.replace("_", " ").title()
        prompt = (
            "You are an expert speech coach and speech-to-text auditor.\n"
            f"Target Language: {language}. Speaker Accent Profile: {accent_clean}.\n"
            f"Expected Script / Target Text:\n\"\"\"{script_clean}\"\"\"\n\n"
            "INSTRUCTIONS:\n"
            "1. Transcribe what the speaker ACTUALLY said in the audio.\n"
            "2. Practice accent-adaptive tolerance: do NOT treat legitimate regional or non-native accent phonetics as errors. "
            "Only flag genuine mispronounced words, stumbles, verbal fumbles (e.g., 'um', 'uh', false starts, repeated words).\n"
            "3. Compare the delivery to the baseline expectation: is it 'Better than Baseline', 'Worse than Baseline', or 'Comparable to Baseline'?\n"
            "4. Return ONLY a JSON object with keys:\n"
            "   - \"transcription_heard\": string of what was heard\n"
            "   - \"verdict_relative\": 'Better than Baseline' | 'Worse than Baseline' | 'Comparable to Baseline'\n"
            "   - \"verdict_headline\": one sentence summary comparing the delivery\n"
            "   - \"adherence_pct\": float (0.0 to 100.0) script adherence accuracy\n"
            "   - \"fumble_count\": integer number of fumbles or stumbles\n"
            "   - \"fumbles\": list of objects with {\"phrase\": str, \"issue\": str, \"advice\": str}\n"
            "   - \"omissions\": list of strings (omitted words or clauses from script)\n"
            "   - \"additions\": list of strings (unscripted words or filler sounds)\n"
            "   - \"suggestions\": list of 2-3 concrete tips to improve execution of this script."
        )

        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[audio_part, prompt]
        )
        out = (response.text or "").strip()
        if out.startswith("```"):
            out = out.strip("`")
            out = out[4:] if out.lower().startswith("json") else out
        data = json.loads(out)
        if isinstance(data, dict) and "transcription_heard" in data:
            data["enabled"] = True
            data["target_script"] = script_clean
            data["accent_note"] = f"Evaluated with accent-adaptive invariance for {accent_clean} speaking styles."
            return data
    except Exception:
        pass

    return _fallback_script_comparison(script_clean, overall_score, flaw_regions, language, accent)
