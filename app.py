"""Plainly — universal plain-language explainer + reality checker (WSGI, stdlib).

Two free tools sharing one tiny backend that forwards to a keyless free LLM:

  * POST /api/explain  — turn any confusing text (medical, legal, bank, government,
    jargon) into plain language at a chosen reading level and language.
  * POST /api/check    — paste a claim/message and get a calm, reasoned
    "what's actually known" with caveats (not a verdict machine).

Serves the static frontends from web/. No secrets, no storage. Pure stdlib.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
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
    "You are ResumeFit. Tell a job seeker honestly how well their CV matches a "
    "specific job and how to improve it. Applications are filtered by keyword-matching "
    "software before a human reads them, so be concrete about wording.\n\n"
    "Return STRICT JSON ONLY (no markdown, no reasoning, no preamble):\n"
    "{\n"
    '  "match_score": number,              // 0-100\n'
    '  "verdict": string,                  // one short honest sentence\n'
    '  "missing_keywords": [string],       // terms in the job ad absent from the CV\n'
    '  "strengths": [string],\n'
    '  "gaps": [{"issue":string,"fix":string}],\n'
    '  "ats_issues": [string],             // wording or formatting that trips machines\n'
    '  "rewrite_suggestions": [{"before":string,"after":string}],\n'
    '  "summary_line": string              // a tailored professional summary\n'
    "}\n\n"
    "Never invent experience they do not have — suggest how to phrase what they DO have. "
    "Prefer measurable, active phrasing. Be encouraging but honest; if it is a weak match, "
    "say so and say what would close the gap.\n"
    "Do NOT explain your reasoning or think out loud. "
    "CRITICAL: output ONE single JSON object with ALL of the keys above. "
    "Start with { and end with }."
)


HR_SYS = (
    "You score a CV using the published rubric from interviewstreet/hiring-agent, "
    "the resume-ranking tool HackerRank open-sourced. Score four categories:\n"
    "  open_source (0-35): contributions to OTHER people's projects. Popular projects "
    "or Google Summer of Code: 25-35. Smaller real contributions: 15-24. IMPORTANT: the "
    "candidate's own repositories are NOT open source — if every project is their own, "
    "this MUST be 10 or less. No GitHub at all: 0-4.\n"
    "  self_projects (0-30): complex work with real impact: 20-30. Moderate: 10-19. "
    "Tutorial-grade (todo app, calculator, CRUD, weather app, classwork): 1-9. "
    "Projects with no link score much lower.\n"
    "  production (0-25): real jobs, internships, production work. Founders and early "
    "startup employees score higher.\n"
    "  technical_skills (0-10): breadth and evidence of problem-solving.\n"
    "Bonus (max 20): +5 GSoC, +3-5 founder, +2-3 early startup employee, +2 portfolio "
    "site, +1 LinkedIn. Deductions: tutorial-only projects, generic project names, "
    "projects with no link.\n"
    "Never let the score depend on name, gender, university, GPA or location.\n"
    "You only have the resume text — you cannot see their GitHub. Say so in the evidence "
    "where it matters.\n\n"
    "Return STRICT JSON ONLY (no markdown, no reasoning, no preamble) with this shape:\n"
    "{\n"
    '  "scores": {\n'
    '    "open_source":      {"score": 0, "max": 35, "evidence": string},\n'
    '    "self_projects":    {"score": 0, "max": 30, "evidence": string},\n'
    '    "production":       {"score": 0, "max": 25, "evidence": string},\n'
    '    "technical_skills": {"score": 0, "max": 10, "evidence": string}\n'
    "  },\n"
    '  "bonus_points": {"total": 0, "breakdown": string},\n'
    '  "deductions": {"total": 0, "reasons": string},\n'
    '  "key_strengths": [string],\n'
    '  "areas_for_improvement": [string],\n'
    '  "verdict": string\n'
    "}\n\n"
    "Fill all four categories, keep each within its maximum, and give non-empty evidence "
    "for each. Do NOT explain your reasoning or think out loud. "
    "CRITICAL: output ONE single JSON object. Start with { and end with }."
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


def _stream_llm(system: str, user: str):
    """Yield raw content deltas from the gateway as they arrive."""
    body = {
        "model": MODEL, "temperature": 0.15, "max_tokens": 4000, "stream": True,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        GATEWAY, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                 "User-Agent": "plainly/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = (chunk.get("choices") or [{}])[0].get("delta", {}) or {}
            piece = delta.get("content") or delta.get("reasoning")
            if piece:
                yield piece


def _sse(start_response, system: str, user: str):
    """Proxy the model stream to the browser as Server-Sent Events."""
    headers = _cors([
        ("Content-Type", "text/event-stream; charset=utf-8"),
        ("Cache-Control", "no-cache, no-transform"),
        ("X-Accel-Buffering", "no"),
    ])
    start_response("200 OK", headers)

    def generate():
        buffer = []
        try:
            for piece in _stream_llm(system, user):
                buffer.append(piece)
                yield b"data: " + json.dumps({"delta": piece}).encode() + b"\n\n"
        except Exception as exc:
            yield b"data: " + json.dumps({"error": str(exc)[:200]}).encode() + b"\n\n"
            return
        text = "".join(buffer)
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        parsed = _extract_json(text)
        yield b"data: " + json.dumps({"done": True, "result": parsed}).encode() + b"\n\n"

    return generate()


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
    """Ask the model for JSON, retrying within a budget that fits the worker.

    gunicorn kills a worker at 120 seconds. Three attempts at ninety seconds
    each could ask for two hundred and seventy, so a slow spell upstream got the
    worker killed mid-request and Render served its own HTML error page instead
    of anything this code could explain. Attempts now share one deadline and
    stop while there is still time to answer.
    """
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    deadline = time.monotonic() + 100
    for attempt in range(3):
        left = deadline - time.monotonic()
        if left < 12:
            break
        body = {"model": MODEL, "temperature": 0.1 if attempt else 0.2,
                "max_tokens": 4000, "messages": messages}
        req = urllib.request.Request(
            GATEWAY, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "plainly/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=min(55, left)) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise
            continue
        except Exception:
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


class _Text(HTMLParser):
    """Body text, with the furniture removed and JSON-LD job postings kept."""

    SKIP = {"script", "style", "nav", "footer", "header", "aside", "svg", "noscript", "form", "button"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ld: list[str] = []
        self._skip = 0
        self._ld = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self._ld = True
        if tag in ("p", "li", "br", "div", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "script":
            self._ld = False

    def handle_data(self, data):
        if self._ld:
            self.ld.append(data)
        elif not self._skip:
            self.parts.append(data)


def _job_description_from_ld(chunks):
    """Most real job boards publish a schema.org JobPosting. It is far cleaner
    than anything scraped out of the page body, so it is preferred when present."""
    for raw in chunks:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                types = node.get("@type")
                types = types if isinstance(types, list) else [types]
                if "JobPosting" in types:
                    bits = [node.get("title") or "", node.get("description") or ""]
                    text = "\n".join(b for b in bits if b)
                    if text.strip():
                        return text
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return ""


def _guard_url(url: str) -> urllib.parse.ParseResult:
    """Refuse anything that is not a public web page.

    This endpoint fetches a URL chosen by the caller, which is a server-side
    request forgery hole if left open: without these checks anyone could point
    it at 169.254.169.254 and read the host's cloud credentials, or sweep the
    private network behind it. Every hop of every redirect goes through here.
    """
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        raise ValueError("That doesn't look like a web address.")
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Only http and https links can be read.")
    if u.port is not None and u.port not in (80, 443):
        raise ValueError("Only standard web ports can be read.")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except socket.gaierror:
        raise ValueError("That address could not be found.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError("That address is not reachable from here.")
    return u


_MAX_PAGE = 1_500_000


def _fetch_job(url: str) -> tuple[str, str]:
    """Fetch a job ad and return (text, host). Redirects are followed by hand so
    each new location is checked as strictly as the first."""
    seen = 0
    while True:
        u = _guard_url(url)
        req = urllib.request.Request(url, method="GET", headers={
            # Presented honestly. Boards that do not want to be read this way
            # will refuse, and the front end tells the user to paste instead.
            "User-Agent": "Mozilla/5.0 (compatible; ResumeFit/1.0; +https://rahulatrkm.github.io/resumefit/)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
            "Accept-Language": "en",
        })
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            resp = opener.open(req, timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and seen < 4:
                nxt = exc.headers.get("Location")
                if not nxt:
                    raise ValueError("That page redirected somewhere unreadable.")
                url = urllib.parse.urljoin(url, nxt)
                seen += 1
                continue
            if exc.code in (401, 403):
                raise ValueError("That site refused the request — it wants a signed-in browser.")
            if exc.code == 404:
                raise ValueError("That page was not found.")
            raise ValueError("That site returned an error.")
        except (urllib.error.URLError, socket.timeout, OSError):
            raise ValueError("Couldn't reach that page.")

        with resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype and not (ctype.startswith("text/") or ctype in ("application/xhtml+xml", "application/json")):
                raise ValueError("That link isn't a web page.")
            raw = resp.read(_MAX_PAGE)
            charset = "utf-8"
            match = re.search(r"charset=([\w-]+)", resp.headers.get("Content-Type") or "", re.I)
            if match:
                charset = match.group(1)
            try:
                body = raw.decode(charset, "replace")
            except LookupError:
                body = raw.decode("utf-8", "replace")

        parser = _Text()
        try:
            parser.feed(body)
        except Exception:  # a broken page should not take the endpoint down
            pass
        text = _job_description_from_ld(parser.ld)
        if len(text.strip()) < 200:
            text = "".join(parser.parts)
        # The JobPosting description is itself HTML more often than not.
        if "<" in text and ">" in text:
            inner = _Text()
            try:
                inner.feed(text)
                text = "".join(inner.parts)
            except Exception:
                pass
        text = re.sub(r"[ \t\u00a0]+", " ", text)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
        text = "\n".join(line.strip() for line in text.splitlines()).strip()
        if len(text) < 120:
            raise ValueError("That page had almost no readable text — it is probably rendered by JavaScript.")
        return text[:12000], u.hostname or ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are handled in _fetch_job so every hop is re-checked."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
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

    if method == "POST" and path == "/api/resume-hr":
        p = _read(environ)
        if not p:
            return _json(start_response, "400 Bad Request", {"error": "Please paste your CV."})
        cv = (p.get("resume") or "").strip()
        if len(cv) < 60:
            return _json(start_response, "400 Bad Request",
                         {"error": "Paste more of your CV so it can be assessed properly."})
        # The rubric scores a candidate, not a match, so the job ad is not sent:
        # passing it would only invite the model to quietly grade the fit instead.
        try:
            return _json(start_response, "200 OK",
                         _call(HR_SYS, f"Resume to evaluate:\n\n{cv[:MAX_CHARS]}", required=("scores",)))
        except ValueError as e:
            return _json(start_response, "502 Bad Gateway", {"error": str(e)})
        except urllib.error.HTTPError:
            return _json(start_response, "429 Too Many Requests",
                         {"error": "The free AI is busy. Try again in a minute."})
        except Exception:
            return _json(start_response, "504 Gateway Timeout", {"error": "The AI took too long."})

    if method == "POST" and path == "/api/job":
        p = _read(environ)
        if not p:
            return _json(start_response, "400 Bad Request", {"error": "No link given."})
        url = (p.get("url") or "").strip()
        if not url:
            return _json(start_response, "400 Bad Request", {"error": "No link given."})
        try:
            text, host = _fetch_job(url)
            return _json(start_response, "200 OK", {"text": text, "host": host})
        except ValueError as e:
            return _json(start_response, "400 Bad Request", {"error": str(e)})
        except Exception:
            return _json(start_response, "502 Bad Gateway", {"error": "Couldn't read that page."})

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

    if method == "POST" and path in ("/api/explain/stream", "/api/check/stream"):
        p = _read(environ)
        if not p:
            return _json(start_response, "400 Bad Request", {"error": "Please paste some text."})
        text = (p.get("text") or "").strip()
        if len(text) < 10:
            return _json(start_response, "400 Bad Request", {"error": "That's too short."})
        if path.startswith("/api/explain"):
            user = (f"Reading level: {(p.get('level') or 'simple').strip()}. "
                    f"Language: {(p.get('language') or 'English').strip()}.\n\n"
                    f"Explain this in plain language:\n\n{text[:MAX_CHARS]}")
            return _sse(start_response, EXPLAIN_SYS, user)
        return _sse(start_response, CHECK_SYS, f"Analyse this claim:\n\n{text[:MAX_CHARS]}")

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
                         else "image/png" if rel.endswith(".png")
                         else "image/svg+xml" if rel.endswith(".svg")
                         else "application/xml" if rel.endswith(".xml")
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
