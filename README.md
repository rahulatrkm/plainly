# Plainly

**Understand anything, in plain words.** Paste a medical report, a legal letter,
a bank statement, a government form, an insurance policy, a scary email — anything
confusing — and Plainly explains it in language you actually understand, **at your
reading level, in your language.**

It also includes **RealCheck** — paste a claim, message or headline and get a
calm, reasoned "how to think about whether this is true", with the scam/
misinformation red flags called out.

👉 **Live:** deploy on Render (free) — see below.

## Why it exists

Everyone, everywhere, constantly runs into documents and jargon that gatekeep
their own life — health, money, law, government. A doctor/lawyer/expert costs
money and time. Plainly is a free, universal translator from "official/technical"
into "plain human", in any major language.

## Features

- 🔤 **Explain this** — plain-language rewrite with a TL;DR, key points, things to
  watch out for, decoded jargon, and practical next steps
- 🎚️ **Your reading level** — simple, like-I'm-10, teenager, or precise/expert
- 🌍 **Your language** — English, Hindi, Spanish, Arabic, and more
- 🔎 **Is this real?** — calm reasoning about a claim, with red flags and concrete
  ways to verify it yourself
- 🔒 **Nothing stored**, no signup, free

## How it works

A tiny Python (WSGI, standard-library) backend forwards your text to a free,
OpenAI-compatible LLM and asks for a **structured** result; the frontend is a
single static page. Getting reliable JSON from a free model is handled with a
strict "JSON only" contract, reasoning-strip and retries.

Honest about limits: it's an **AI explainer/reasoner, not a doctor, lawyer or
fact-checking oracle** — great for understanding and for asking better questions,
not a final authority.

## Run locally

```bash
python3 app.py        # http://localhost:8000
```

## Deploy free (Render)

New → Blueprint → this repo → Apply. Free tier, sleeps when idle.

## License

MIT.
