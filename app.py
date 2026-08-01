"""Plainly — universal plain-language explainer + reality checker (WSGI, stdlib).

Two free tools sharing one tiny backend that forwards to a keyless free LLM:

  * POST /api/explain  — turn any confusing text (medical, legal, bank, government,
    jargon) into plain language at a chosen reading level and language.
  * POST /api/check    — paste a claim/message and get a calm, reasoned
    "what's actually known" with caveats (not a verdict machine).

Serves the static frontends from web/. No secrets, no storage. Pure stdlib.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

_WEB = Path(__file__).resolve().parent / "web"
GATEWAY = os.environ.get("PLAINLY_GATEWAY", "https://api.kilo.ai/api/gateway/v1/chat/completions")
MODEL = os.environ.get("PLAINLY_MODEL", "kilo-auto/free")
MAX_CHARS = 16000

EXPLAIN_SYS = (
    "You are Plainly, a tool that rewrites confusing real-world text into clear, "
    "calm, plain language that anyone can understand. People paste medical reports, "
    "legal letters, bank statements, government forms, insurance policies, error "
    "messages, or dense jargon. Your job: make it genuinely understandable without "
    "dumbing down the facts.\n\n"
    "Return STRICT JSON ONLY (no markdown) with this shape:\n"
    "{\n"
    '  "tldr": string,                 // one or two sentences: what this is, in plain words\n'
    '  "plain": string,                // the full plain-language explanation\n'
    '  "key_points": [string],         // the most important takeaways\n'
    '  "watch_out": [string],          // anything worrying, deadlines, or that needs action\n'
    '  "jargon": [{"term":string,"meaning":string}], // decode the hard words used\n'
    '  "next_steps": [string]          // practical, concrete things the person can do\n'
    "}\n\n"
    "Write at the requested reading level and in the requested language. Be honest "
    "about uncertainty. If it looks like medical or legal content, add a gentle note "
    "in watch_out that this is an explanation, not professional advice.\n"
    "CRITICAL: output ONLY the JSON object; start with { and end with }."
)

CHECK_SYS = (
    "You are RealCheck, a calm reasoning assistant that helps people think about "
    "whether a claim, message, or piece of news is likely true. You are NOT a "
    "verdict machine and you do not have live internet. You reason from general "
    "knowledge and critical-thinking principles, and you are honest about what you "
    "cannot know.\n\n"
    "Return STRICT JSON ONLY (no markdown) with this shape:\n"
    "{\n"
    '  "assessment": string,           // plain-language: how to think about this claim\n'
    '  "plausibility": string,         // one of: \\"Likely true\\", \\"Likely false\\", \\"Mixed / needs context\\", \\"Can\'t tell\\"\n'
    '  "reasons": [string],            // why — the signals that point each way\n'
    '  "red_flags": [string],          // manipulation/scam/misinformation signs present, if any\n'
    '  "how_to_verify": [string],      // concrete steps the person can take to check it themselves\n'
    '  "caveat": string                // what you can\'t know / limits of this analysis\n'
    "}\n\n"
    "Be balanced and non-partisan. Never fabricate specific sources, studies or "
    "statistics. If it's a scam pattern (prizes, urgency, pay-a-fee, crypto doubling, "
    "account-verify links), say so clearly.\n"
    "CRITICAL: output ONLY the JSON object; start with { and end with }."
)


RESUME_SYS = (
    "You are ResumeFit, an assistant that tells a job seeker — honestly and "
    "practically — how well their CV/résumé matches a specific job, and how to "
    "improve it. Many applications are filtered by keyword-matching software (ATS) "
    "before a human ever reads them, so be concrete about wording.\n\n"
    "Return STRICT JSON ONLY (no markdown) with this shape:\n"
    "{\n"
    '  "match_score": number,              // 0-100 how well the CV fits this job\n'
    '  "verdict": string,                  // one short honest sentence\n'
    '  "missing_keywords": [string],       // important terms in the job ad absent from the CV\n'
    '  "strengths": [string],              // what genuinely lines up well\n'
    '  "gaps": [{"issue":string,"fix":string}],   // real gaps + how to address them\n'
    '  "ats_issues": [string],             // formatting/wording that machines or recruiters trip on\n'
    '  "rewrite_suggestions": [{"before":string,"after":string}], // stronger bullet rewrites\n'
    '  "summary_line": string              // a tailored professional summary they could use\n'
    "}\n\n"
    "Rules: never invent experience the person does not have — suggest how to phrase "
    "what they DO have. Prefer measurable, active phrasing. Be encouraging but honest; "
    "if it's a weak match, say so and explain what would close the gap.\n"
    "CRITICAL: output ONE single JSON object containing ALL of the keys above "
    "(match_score, verdict, missing_keywords, strengths, gaps, ats_issues, "
    "rewrite_suggestions, summary_line). Do not output a fragment or a single item. "
    "Start with { and end with }."
)


INTERVIEW_SYS = (
    "You are a demanding but fair senior interviewer running a live practice job "
    "interview. You ask ONE question at a time, listen to the candidate's answer, "
    "grade it honestly, and then ask a natural follow-up — exactly like a real "
    "interviewer would. You probe weak or vague answers rather than moving on "
    "politely.\n\n"
    "Return STRICT JSON ONLY (no markdown) with this shape:\n"
    "{\n"
    '  "feedback": {                 // omit entirely for the FIRST question\n'
    '    "score": number,            // 0-10 for the answer just given\n'
    '    "verdict": string,          // one honest sentence\n'
    '    "did_well": [string],\n'
    '    "improve": [string],        // specific, actionable\n'
    '    "model_answer": string      // a strong version of THEIR answer, reusing their own facts\n'
    "  },\n"
    '  "question": string,           // the next question to ask (a real follow-up if their answer was thin)\n'
    '  "question_intent": string     // one short line: what a real interviewer is testing here\n'
    "}\n\n"
    "Rules: be specific to what they actually said — never generic. If an answer is "
    "vague, has no measurable result, or dodges the question, say so plainly and "
    "score it low. Reward concrete situation-action-result structure. Never invent "
    "achievements for the candidate; the model answer must only reshape facts they "
    "gave. Keep every field tight and readable.\n"
    "CRITICAL: output ONE single JSON object. Start with { and end with }."
)

DEBRIEF_SYS = (
    "You are a senior interviewer writing the final debrief after a practice "
    "interview. Be honest and useful — this person wants to actually get the job.\n\n"
    "Return STRICT JSON ONLY with this shape:\n"
    "{\n"
    '  "overall_score": number,        // 0-100\n'
    '  "verdict": string,              // would this performance pass a real screen? one honest sentence\n'
    '  "strengths": [string],\n'
    '  "weaknesses": [string],\n'
    '  "patterns": [string],           // habits across answers (rambling, no numbers, hedging...)\n'
    '  "practice_next": [string],      // what to drill before the real thing\n'
    '  "likely_next_questions": [string]  // questions they should prepare for this role\n'
    "}\n\n"
    "Judge the whole transcript, not one answer. Be direct about whether this would "
    "pass. CRITICAL: output ONE single JSON object. Start with { and end with }."
)


def _cors(h):
    return h + [("Access-Control-Allow-Origin", "*"),
                ("Access-Control-Allow-Methods", "POST, GET, OPTIONS"),
                ("Access-Control-Allow-Headers", "Content-Type")]


def _json(start, status, payload):
    body = json.dumps(payload).encode()
    start(status, _cors([("Content-Type", "application/json; charset=utf-8"),
                         ("Content-Length", str(len(body)))]))
    return [body]


def _extract_json(text):
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                cand = text[start:i + 1]
                try:
                    return json.loads(cand)
                except json.JSONDecodeError:
                    try:
                        return json.loads(re.sub(r",\s*([}\]])", r"\1", cand))
                    except json.JSONDecodeError:
                        return None
    return None


def _call(system, user, required=()):
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(3):
        body = {"model": MODEL, "temperature": 0.1 if attempt else 0.2,
                "max_tokens": 4000, "messages": messages}
        req = urllib.request.Request(
            GATEWAY, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "plainly/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise
            continue
        msg = (data.get("choices") or [{}])[0].get("message", {})
        content = msg.get("content") or msg.get("reasoning") or ""
        parsed = _extract_json(content)
        if isinstance(parsed, dict) and len(parsed) >= 2:
            if not required or any(k in parsed for k in required):
                return parsed
    raise ValueError("The AI had trouble with that. Please try again in a moment.")


def _read(environ):
    try:
        size = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        size = 0
    if size <= 0 or size > 200_000:
        return None
    try:
        return json.loads(environ["wsgi.input"].read(size).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")

    if method == "OPTIONS":
        start_response("204 No Content", _cors([("Content-Length", "0")]))
        return [b""]

    if method == "POST" and path == "/api/explain":
        p = _read(environ)
        if not p:
            return _json(start_response, "400 Bad Request", {"error": "Please paste some text."})
        text = (p.get("text") or "").strip()
        level = (p.get("level") or "simple").strip()
        lang = (p.get("language") or "English").strip()
        if len(text) < 10:
            return _json(start_response, "400 Bad Request", {"error": "That's too short to explain."})
        user = (f"Reading level: {level}. Language: {lang}.\n\n"
                f"Explain this in plain language:\n\n{text[:MAX_CHARS]}")
        try:
            return _json(start_response, "200 OK", _call(EXPLAIN_SYS, user))
        except ValueError as e:
            return _json(start_response, "502 Bad Gateway", {"error": str(e)})
        except urllib.error.HTTPError:
            return _json(start_response, "429 Too Many Requests",
                         {"error": "The free AI is busy. Try again in a minute."})
        except Exception:
            return _json(start_response, "504 Gateway Timeout",
                         {"error": "The AI took too long. Try a shorter excerpt."})

    if method == "POST" and path == "/api/check":
        p = _read(environ)
        if not p:
            return _json(start_response, "400 Bad Request", {"error": "Please paste a claim."})
        claim = (p.get("text") or "").strip()
        if len(claim) < 6:
            return _json(start_response, "400 Bad Request", {"error": "That's too short to check."})
        try:
            return _json(start_response, "200 OK", _call(CHECK_SYS, f"Analyse this claim:\n\n{claim[:MAX_CHARS]}"))
        except ValueError as e:
            return _json(start_response, "502 Bad Gateway", {"error": str(e)})
        except urllib.error.HTTPError:
            return _json(start_response, "429 Too Many Requests",
                         {"error": "The free AI is busy. Try again in a minute."})
        except Exception:
            return _json(start_response, "504 Gateway Timeout", {"error": "The AI took too long."})

    if method == "POST" and path == "/api/resume":
        p = _read(environ)
        if not p:
            return _json(start_response, "400 Bad Request", {"error": "Please paste your CV."})
        cv = (p.get("resume") or "").strip()
        job = (p.get("job") or "").strip()
        if len(cv) < 60:
            return _json(start_response, "400 Bad Request",
                         {"error": "Paste more of your CV so it can be assessed properly."})
        user = (f"JOB DESCRIPTION:\n{job[:6000] or '(none provided — assess the CV generally)'}\n\n"
                f"CANDIDATE CV/RESUME:\n{cv[:MAX_CHARS]}")
        try:
            return _json(start_response, "200 OK",
                         _call(RESUME_SYS, user, required=("match_score", "verdict")))
        except ValueError as e:
            return _json(start_response, "502 Bad Gateway", {"error": str(e)})
        except urllib.error.HTTPError:
            return _json(start_response, "429 Too Many Requests",
                         {"error": "The free AI is busy. Try again in a minute."})
        except Exception:
            return _json(start_response, "504 Gateway Timeout", {"error": "The AI took too long."})

    if method == "POST" and path == "/api/interview":
        p = _read(environ)
        if not p:
            return _json(start_response, "400 Bad Request", {"error": "Missing interview details."})
        role = (p.get("role") or "").strip()[:200]
        level = (p.get("level") or "mid").strip()[:60]
        style = (p.get("style") or "mixed").strip()[:60]
        history = p.get("history") or []
        if not role:
            return _json(start_response, "400 Bad Request", {"error": "Tell me the role you're interviewing for."})
        if not isinstance(history, list) or len(history) > 30:
            return _json(start_response, "400 Bad Request", {"error": "Invalid session."})

        lines = [f"ROLE: {role}", f"SENIORITY: {level}", f"INTERVIEW STYLE: {style}", ""]
        if history:
            lines.append("TRANSCRIPT SO FAR:")
            for turn in history[-8:]:
                q = str(turn.get("q", ""))[:1200]
                a = str(turn.get("a", ""))[:4000]
                lines.append(f"Interviewer: {q}")
                lines.append(f"Candidate: {a}")
            lines.append("")
            lines.append("Grade the candidate's LAST answer, then ask the next question.")
        else:
            lines.append("Start the interview. Ask the first question. Do not include feedback.")
        try:
            return _json(start_response, "200 OK",
                         _call(INTERVIEW_SYS, "\n".join(lines), required=("question",)))
        except ValueError as e:
            return _json(start_response, "502 Bad Gateway", {"error": str(e)})
        except urllib.error.HTTPError:
            return _json(start_response, "429 Too Many Requests",
                         {"error": "The free AI is busy. Try again in a minute."})
        except Exception:
            return _json(start_response, "504 Gateway Timeout", {"error": "The AI took too long."})

    if method == "POST" and path == "/api/debrief":
        p = _read(environ)
        if not p:
            return _json(start_response, "400 Bad Request", {"error": "Missing session."})
        role = (p.get("role") or "").strip()[:200]
        history = p.get("history") or []
        if not isinstance(history, list) or not history:
            return _json(start_response, "400 Bad Request", {"error": "Answer at least one question first."})
        lines = [f"ROLE: {role}", f"SENIORITY: {p.get('level', 'mid')}", "", "FULL TRANSCRIPT:"]
        for turn in history[:20]:
            lines.append(f"Interviewer: {str(turn.get('q',''))[:1200]}")
            lines.append(f"Candidate: {str(turn.get('a',''))[:4000]}")
        try:
            return _json(start_response, "200 OK",
                         _call(DEBRIEF_SYS, "\n".join(lines), required=("overall_score", "verdict")))
        except ValueError as e:
            return _json(start_response, "502 Bad Gateway", {"error": str(e)})
        except urllib.error.HTTPError:
            return _json(start_response, "429 Too Many Requests",
                         {"error": "The free AI is busy. Try again in a minute."})
        except Exception:
            return _json(start_response, "504 Gateway Timeout", {"error": "The AI took too long."})

    if path == "/healthz":
        return _json(start_response, "200 OK", {"status": "ok"})

    # static
    if method == "GET":
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        if "/" not in rel and ".." not in rel:
            fp = _WEB / rel
            if fp.exists() and fp.is_file():
                ctype = ("text/html; charset=utf-8" if rel.endswith(".html")
                         else "application/json" if rel.endswith(".json")
                         else "text/plain; charset=utf-8")
                data = fp.read_bytes()
                start_response("200 OK", _cors([("Content-Type", ctype),
                                                ("Content-Length", str(len(data)))]))
                return [data]

    return _json(start_response, "404 Not Found", {"error": "not found"})


app = application


def serve(port=8000):  # pragma: no cover
    from wsgiref.simple_server import make_server
    print(f"Plainly on http://localhost:{port}")
    make_server("", port, application).serve_forever()


if __name__ == "__main__":  # pragma: no cover
    serve(int(os.environ.get("PORT", "8000")))
