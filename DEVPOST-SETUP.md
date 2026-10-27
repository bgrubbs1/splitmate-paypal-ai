# Devpost submission setup — PayPal AI Hackathon

**Your submission URL:** https://devpost.com/submit-to/31302-paypal-ai-hackathon/manage/submissions
**Hackathon page:** https://paypalaihackathon.devpost.com/ · **Deadline: Thursday 2026-11-12, 12:00 pm Pacific Time = 3:00 pm Eastern Time** (leave ≥24h buffer).
**Prizes:** $67,500+ overall (1st $12,000 · 2nd $8,000 · 3rd $5,000) plus special awards. Free entry; legal age of majority; US eligible (not Brazil/Quebec/Russia/Crimea/Cuba/Iran/North Korea).

## GitHub side — DONE ✅
- Public repository: **https://github.com/bgrubbs1/splitmate-paypal-ai** — full source, `README.md` written as a Devpost project page (Inspiration / What it does / How we built it / Challenges / Accomplishments / What we learned / What's next / Built with), MIT `LICENSE` at root, screenshots in `docs/screenshots/` + cover in `docs/cover.png`, repo description + topics set. Judges can run it with no accounts: `python3 app.py` (mock mode) and `make test` / `make smoke`.
- Verified here: **151 unit tests OK**, **56 smoke assertions pass** (mock-mode E2E + real-mode HTTP path against a local fake PayPal/LLM server), server endpoints curl-tested, no secrets in the tree.

## Only you can do these (they're the submission blockers)
1. **PayPal Developer account + sandbox app** (~15 min): sign in at https://developer.paypal.com/ with your PayPal account → Apps & Credentials → **Sandbox** → create an app → copy **Client ID** and **Secret**. Also confirm you have a **sandbox personal (buyer) account** under Sandbox → Accounts (for the checkout demo).
2. **Optional AI key** (~5 min, <$0.10 of usage): any OpenAI-compatible endpoint. Without it, the demo clearly uses the deterministic parser fallback.
3. **Run real mode and verify** (~10 min):
   ```bash
   cd contest-entries/paypal
   cp .env.example .env      # fill PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET / (optional LLM_* keys)
   python3 app.py
   # open http://127.0.0.1:8000 -> Load demo plan -> Parse -> Approve -> Create PayPal orders
   # open a checkout link in the PayPal sandbox buyer account and approve; click Sync/Capture in the dashboard
   ```
   Do not claim a live PayPal run in the video until this works. If anything errors, paste it and I'll debug.
4. **Record the demo video** (~45 min): follow the scene-by-scene script in `SUBMISSION.md` §Video. Max **3:00**, screen recording of the app + PayPal sandbox checkout + a short "how the AI is used" segment; no third-party trademarks/music. Upload to **YouTube, Public**, verify in an incognito window.
5. **Devpost submit** (~25 min): open the manage URL, log in with GitHub, create/import the project, paste the fields below, add the video + screenshots, submit.

## Devpost form values (also in `SUBMISSION.md` §1, paste-ready)
- **Project name:** `SplitMate`
- **Elevator pitch (196 chars):** `SplitMate turns a messy group-expense plan into a fair, explained split and collects each person's net share with real PayPal sandbox Orders v2 payments — AI parsing plus a live payment dashboard.`
- **Repository URL:** `https://github.com/bgrubbs1/splitmate-paypal-ai`
- **Demo video URL:** your public YouTube link
- **About the project:** paste the Inspiration / What it does / How we built it / Challenges / Accomplishments / What we learned / What's next sections from `SUBMISSION.md`.
- **"Tools" section:** state which AI tools/model were used and how (e.g. "AI parsing + reminder drafting via an OpenAI-compatible LLM (model: …); deterministic rule-based fallback in mock mode"). **Only claim the model you actually used in the recorded demo.**
- **Built with:** `python, paypal, paypal-orders-v2, paypal-sandbox, oauth2, openai, llm, nlp, fintech, payments, split-expenses, vanilla-javascript, html5, css3, rest-api, standard-library` (adjust the LLM tags to what you used).
- **Try it out:** `Run locally with no accounts: python3 app.py → http://127.0.0.1:8000 (mock mode). Full instructions in README.md.`
- **Gallery / thumbnail:** upload `docs/cover.png` as thumbnail and `docs/screenshots/01-parse-and-split.png`, `docs/screenshots/02-paypal-dashboard.png` as gallery images (or your own captured stills).
- **License requirement:** the repo has an MIT `LICENSE`; confirm GitHub shows "MIT license" in the About section (it will detect it).

## Rules to respect (quoted from the official rules, fetched 2026-10-27)
- Build/update an application that **integrates the PayPal developer platform (free sandbox) along with an AI tool, model, or platform**; the project must be newly created (or significantly updated during the submission period).
- Provide a **public repo** on GitHub/GitLab/Bitbucket with an **OSI license file visible in the About section**, **or** a hosted demo; judges must be able to run or interact with a working build.
- Include a **text description** and a **"Tools" section** identifying the AI tools used and how.
- Include a **demonstration video under 3 minutes**, showing the project functioning, uploaded **publicly to YouTube** with the link on the submission form.
- Judging (equally weighted): Technological Implementation, Design, Potential Impact, Innovation/Idea, Presentation.

## Unverified items (documented in `RULES-NOTES.md`; do not overclaim)
- The live PayPal sandbox create/capture path was validated against PayPal's documented request/response shapes with a local fake server, not with real credentials. Your first real sandbox run is the verification step.
- Return-URL redirect query parameters and the optional `PAYPAL_EXPERIENCE_CONTEXT=1` path are untested live (the app supports deep-link **and** a dashboard Sync/Capture fallback).
- Devpost shows both "$67,500 cash" and "$69,750 total prizes"; the entry does not claim a specific prize.
