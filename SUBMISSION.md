# Devpost submission kit — SplitMate

Everything in this file is written to be **pasted into the Devpost submission form**. The
only things to change are the bracketed placeholders: repo URL, YouTube URL, and (if
different) the AI model used in the recorded demo.

Deadline: **Thursday 2026-11-12, 12:00 pm Pacific Time = 3:00 pm Eastern Time.**
Leave at least 24 hours of buffer — do not submit at the deadline.

---

## 1. Form fields (copy/paste)

**Project name**

```
SplitMate
```

**Elevator pitch (≤200 characters)**

```
SplitMate turns a messy group-expense plan into a fair, explained split and collects each person's net share with real PayPal sandbox Orders v2 payments — AI parsing plus a live payment dashboard.
```

Character count: 196 (verified). If you edit it, keep it under 200.

**Repository URL**

```
https://github.com/bgrubbs1/splitmate-paypal-ai
```

**Demo video URL (YouTube, must be public)**

```
[PASTE YOUTUBE URL AFTER UPLOAD]
```

**Built with (tags)**

```
python, paypal, paypal-orders-v2, paypal-sandbox, oauth2, ag-grid, openai, gpt-4o-mini, ai, llm, nlp, fintech, payments, split-expenses, javascript, html5, css3, rest-api, standard-library
```

**AG Grid paragraph (add to "About the project" and to "Tools", so sponsor judges see it)**

```markdown
### AG Grid: checking the AI's work before money moves

SplitMate uses AG Grid Community for the moment that matters most: a person checking AI output before anyone is asked to pay.

- **Allocation matrix.** After the AI parses a plan, each row is an expense and each column is a person. Every cell shows that person's exact share in cents. Items, amounts and payers are edited in place, and clicking or pressing Space on a person's cell includes or excludes them. Each edit is recalculated by the server's exact-cent split engine, and the cells that changed flash, so you can see what your correction moved.
- **Reconciliation you can see.** Pinned rows show each person's fair share, what they fronted, and their net position. A strip above the grid shows whether the allocation matches the total, whether net positions sum to zero, and how many PayPal orders the plan will create. Rows the AI annotated carry the parser's note as a tooltip.
- **Payment ledger.** A second grid reports each share and its PayPal order, capture and status, with quick filtering, CSV export and cell flashes when a sync or capture changes something. It only reports: payment actions stay on the dashboard rows, and checkout links are kept out of the grid and its CSV.

AG Grid Community 36.2.0 (MIT) is committed to the repo, so the app still needs no install and no network in mock mode. Features used: custom value getters and setters, select cell editors, cell class rules, pinned columns and rows, cell change flash, tooltips, quick filter, CSV export and the Theming API. AG Grid was added on November 4, 2026, during the submission period. The grids have been tested in mock mode only.
```

> If your recorded demo used a different LLM (e.g. a local Ollama model), replace
> `openai, gpt-4o-mini` with the tool you actually used. Do not claim a model you didn't use.

**"Try it out" links**

```
Run locally in one command (no accounts needed): python3 app.py → http://127.0.0.1:8000
Full instructions: README.md → Quickstart (mock mode) + Real PayPal sandbox mode
```

**Image gallery / thumbnail** — use the two screenshots in `docs/screenshots/`
(`01-parse-and-split.png`, `02-paypal-dashboard.png`), or your own captured stills. The
first one works as a thumbnail.

---

## 2. About the project (paste into the Devpost "About the project" field)

```markdown
### Inspiration

Every group trip ends the same way: one person fronts the cabin, another covers groceries, someone leaves Saturday morning, and weeks later the group chat is still arguing about who owes what. Existing split apps assume a single payer and an equal split. Real life has partial attendance, items only some people joined, and rounding that never quite reconciles.

Meanwhile PayPal already moves money for millions of people, and AI can now reason over a messy plan. SplitMate is our answer: let AI handle the messy language and the fair-split reasoning, let PayPal handle the money, and keep the human in the loop for one approval click.

### What it does

Paste a natural-language event plan — for example:

"Cabin weekend for 4: Alex, Sam, Priya and Jordan. Cabin $620 paid by Alex. Groceries $180 paid by Priya. Gas $95 paid by Priya. Firewood $40 paid by Jordan. Wine $90 paid by Alex, split between Alex and Priya only. Sam left Saturday morning, so he only shares the cabin and gas. Jordan skipped the wine."

SplitMate:
- **Parses it with AI** into a strict JSON plan: participants, items, amounts, who paid, who shares each item, and the participation caveats. If the model is unavailable or returns invalid JSON, a deterministic rule-based parser takes over with a visible warning.
- **Proposes a fair itemised split** with integer-cent, largest-remainder rounding, so every item sums exactly. Partial attendance narrows who shares an expense. Every person's net position is "paid minus share", and the reasoning is shown line by line and is fully editable.
- **Creates one real PayPal order per person who owes** using the PayPal Orders v2 API in the sandbox (OAuth2 client credentials → create order → hosted checkout link → capture). Netting means a web of pairwise IOUs collapses into the smallest set of payments: in the demo, four people become three orders ($178.75 + $22.08 + $212.08 = $412.91) with one payer receiving a $412.91 credit.
- **Tracks every share** on a dashboard: order created → approved → captured, with per-share Sync/Capture/Retry actions.
- **Drafts the awkward reminder** — an AI-written nudge per participant (or a deterministic offline template). Nothing is ever sent automatically.

Mock mode is the default: it runs fully offline with a deterministic parser and a simulated PayPal checkout, so judges can try the entire flow with no accounts, no credentials and no network. Add PayPal sandbox credentials to `.env` and the same UI creates genuine Orders v2 orders and redirects to PayPal's sandbox checkout.

### How we built it

Pure Python 3.9+ standard library: an `http.server` JSON API + static single-page app, no pip/npm dependencies and no build step. The UI is vanilla HTML/CSS/JS served by the app.

Architecture: `app.py` → HTTP server → service layer → plan parser, split engine, PayPal client. The PayPal client has two implementations of one interface — a real Orders v2 client (`urllib`, OAuth2 token cache, create/get/capture, normalised errors) and a deterministic offline mock that mirrors the same states. That is also how the tests stay honest: the same service code runs against both.

AI is used twice: (1) natural-language → validated JSON plan via an OpenAI-compatible Chat Completions endpoint, with strict schema validation, prompt-injection-resistant treatment of the pasted text, and a deterministic fallback; (2) AI-drafted payment reminders. The model never generates amounts or performs the split arithmetic — that is deterministic Python, so the numbers are always auditable.

The split engine allocates each expense in integer cents with the largest-remainder method (`divmod`, remainder cents in deterministic participant order), so sums always reconcile; partial attendance is modelled as the expense's `shared_by` set; and net positions (`paid − share`) decide who gets a PayPal order and who gets a credit.

Testing: 151 stdlib `unittest` tests plus a 56-assertion one-command smoke script (`bash scripts/smoke.sh`) that starts a fake PayPal + LLM server and proves both the offline E2E flow and the real-mode HTTP path — OAuth header, order payload shape, capture-before-approval 422 → 409 UX, sync, capture, token caching and secret redaction.

### Challenges

- "Fair" is much harder than total divided by N. Partial attendance, a payer covering something they do not share, and cent rounding all broke naive approaches; integer-cent allocation plus netting made it provable and explainable.
- Trusting an LLM with money-adjacent output. The model's JSON is never executed as truth — it is schema-validated, normalised, capped, and every failure path degrades to a deterministic parser with a visible warning.
- Building a real PayPal integration without live credentials in the build environment. We encoded the request/response shapes from PayPal's published Orders v2 OpenAPI schema and auth docs, then wrote a local fake PayPal server mirroring them so the real client is exercised over HTTP — including capture-before-approval.
- Making the demo bulletproof: zero dependencies, offline mode, deterministic sample plan, one-command smoke proof.

### Accomplishments

A complete product loop, not a mockup: natural language → validated plan → editable fair split → real PayPal sandbox orders → hosted checkout links → status sync → capture → AI reminder. 151 tests and a 56-assertion smoke script prove both modes. The split engine always reconciles to the cent and explains every line.

### What we learned

- PayPal Orders v2 is a clean REST API, and its HATEOAS links are the real contract — the approve URL comes from the create-order response, and the captured payment lives under `purchase_units[].payments.captures[]`.
- API schemas evolve: `application_context` is deprecated in favour of `payment_source.paypal.experience_context`. We kept the compatible flow as default and added an opt-in flag for the newer placement, documenting the trade-off instead of ignoring it.
- AI is best used as a constrained structured-extraction layer, not as an oracle. JSON mode + validation + deterministic fallback gives LLM flexibility with script-like reliability.
- Determinism is a feature in demos: fixed sample plan, fixed mock ids, exact integer cents — the video, docs and tests all agree to the cent.

### What's next

PayPal Payouts to automatically reimburse people who fronted money; a hosted demo URL; webhook-driven status updates instead of manual sync; persistent storage with explicit PII boundaries; percentage/weighted splits, refunds and partial captures; richer reminder automation. Sandbox-only today — production would need refunds, disputes, webhooks and hardening.

### Tools

We used an **OpenAI-compatible Chat Completions API with the `gpt-4o-mini` model** as the AI tool (configurable via `LLM_MODEL`; if the recorded demo used a different model, name the one actually used). It is used in two ways:

1. **Plan parsing:** the pasted natural-language plan is sent as data (not instructions) to the model with a strict JSON schema; the model extracts participants, expenses, amounts, payers, per-item sharers and participation notes. The response is schema-validated in Python before use; malformed/incomplete responses fall back to a deterministic rule-based parser.
2. **Reminder drafting:** the model writes a short, polite payment reminder for a selected participant based on the event and their share.

The model never computes the split arithmetic and never moves money — amounts are integer cents in Python, and all PayPal actions are explicit user clicks. In offline mock mode, a deterministic rule-based parser and a templated reminder replace the LLM, so the demo runs with zero network and zero accounts.

PayPal technology used: **PayPal Developer platform — Orders v2 REST API and OAuth 2.0 client credentials, sandbox environment** (create order, hosted checkout approve link, get order, capture order).

### Try it out

```bash
git clone <this repo> && cd splitmate
python3 app.py          # offline mock mode, no accounts needed
# open http://127.0.0.1:8000, click "Load demo plan"
```

Real PayPal sandbox mode: copy `.env.example` to `.env`, set `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` from a sandbox app at developer.paypal.com, optionally set `LLM_API_KEY`, then `python3 app.py`. Details and troubleshooting are in README.md / RUNBOOK.md.
```

---

## 3. Demo video script (target 2:35, hard limit 3:00)

Record with a **real PayPal sandbox account** if possible — that is the strongest version of
the demo. A mock-mode-only fallback script is included below if you cannot get credentials
in time; if you use it, say clearly in the video and caption that it is the offline mock.

### Before you hit record (10 min)

- [ ] Real mode running: `cp .env.example .env`, fill `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET`, optionally `LLM_API_KEY`; `python3 app.py`; open <http://127.0.0.1:8000>; mode pill should say **PayPal sandbox** (and the AI model name if an LLM key is set).
- [ ] Log in to the **PayPal sandbox** in a second browser tab/incognito with a sandbox *personal* buyer account (developer.paypal.com → Testing Tools → Sandbox Accounts), so the checkout scene shows the sandbox login, not a real account.
- [ ] Browser zoom 100%, window 1440×900+, bookmarks bar hidden, notifications off, desktop clean, no other personal data visible.
- [ ] Fresh app instance (dashboard empty), plan text ready to paste.
- [ ] Microphone tested; no copyrighted music; no third-party logos/trademarks in shot (use the app UI, no PayPal logo images; naming "PayPal" in speech/UI is fine and expected).
- [ ] Timer/checklist ready. Keep a clock visible in the corner if it helps you stay under 3:00.

### Scene-by-scene

| Time | On screen | Narration (approx.) |
| --- | --- | --- |
| 0:00–0:10 | SplitMate hero page, mode pill visible | "Group trips always end with the same question: who owes what? SplitMate answers it with AI and PayPal. Here's the whole flow in under three minutes." |
| 0:10–0:30 | Paste the cabin-weekend plan into the textarea, click **Parse with AI**. Show "Parsed with `gpt-4o-mini`" status. | "I paste the plan exactly as it appeared in the group chat — including Sam leaving Saturday morning and Jordan skipping the wine. The AI turns that mess into a structured plan: four people, five expenses, who paid, and who shares each item." (This is the AI scene — pause briefly on the parse status line.) |
| 0:30–0:55 | Editor: point at participants and expense rows; hover the Wine row (split between Alex and Priya), Groceries row (Sam excluded). | "The AI's work is editable. You can see Sam is excluded from groceries and firewood because he left early, and the wine is split only between Alex and Priya. I can fix any of this before collecting money." |
| 0:55–1:10 | Scroll to **The fair split**: person cards and reasoning. | "SplitMate nets each person's share against what they paid, explains every line, and rounds in integer cents so totals always reconcile. Four people become three payments — with Alex receiving a $412.91 credit." |
| 1:10–1:25 | Click **Create PayPal orders**. Dashboard appears with 3 orders and status "order created". | "On approval, SplitMate creates a real order for each person who owes through the PayPal Orders v2 API in the sandbox. OAuth2, create order, hosted checkout link — all standard PayPal." |
| 1:25–1:55 | Click **Open checkout** on Sam's row → PayPal sandbox login/approval page. Log in as the sandbox buyer, approve and pay. | "This is the real PayPal sandbox checkout. I'm paying Sam's $178.75 share with a PayPal sandbox buyer account — no real money moves." (Show the PayPal sandbox URL/host once.) |
| 1:55–2:10 | Return to SplitMate `/return` page or dashboard; click **Sync**, then **Capture**. Status flips to captured; captured total updates. | "Back in SplitMate, the order is approved; one click captures it. The dashboard now tracks captured amounts and credits — no spreadsheet, no screenshots of receipts." |
| 2:10–2:25 | Click **AI reminder** on Priya's row; modal shows the drafted message; copy it. | "AI also drafts the awkward reminder for whoever hasn't paid — warm, specific, nothing sent automatically. Copy and paste into the group chat." |
| 2:25–2:40 | Quick wide shot: dashboard totals, then cut to the "How SplitMate uses AI + PayPal" section / repo README. | "SplitMate: AI parses the plan, Python does the fair math, PayPal collects the money. It runs offline in mock mode with zero accounts, so judges can try it with one command. Thanks for watching." |

**Total target: ~2:35.** If you run long, cut the reminder scene (0:20) first, then shorten
the editor scene. Never let it exceed 3:00 — judges are not required to watch past that.

### Mock-mode fallback (if no PayPal credentials by recording time)

Same script, with these substitutions:

- Mode pill shows **Mock PayPal · rule-based AI**: in Scene 2 say "this offline mode uses the
  app's deterministic parser; in the repo, the same step calls an OpenAI-compatible model when
  an API key is configured."
- Scene 6: click **Open checkout** → SplitMate's own page clearly labelled *Simulated PayPal
  checkout* → **Approve & pay (simulated)** → status becomes captured immediately.
- Scene 7 works unchanged (offline template reminder).
- On screen and in the video description, state: "Demo runs in SplitMate's offline mock mode
  (no PayPal account used); real sandbox mode creates actual Orders v2 orders — see repo
  README." Do **not** imply a PayPal sandbox payment happened if it didn't.

### Upload

1. Export MP4 (1080p, ≤3:00).
2. YouTube → Upload → **Visibility: Public** (rules require publicly visible; Unlisted is not enough). Title: `SplitMate — AI group-expense agent for PayPal | PayPal AI Hackathon 2026`. Description: one paragraph + repo link + "Built with PayPal Orders v2 (sandbox) + gpt-4o-mini". No copyrighted music.
3. Open the URL in an incognito window to confirm it plays publicly.
4. Paste the URL into Devpost.

---

## 4. User-only checklist (things an agent cannot do for you)

Times are estimates. Total ≈ **1h 45m**, and none of it needs to happen on the deadline day.

| # | Task | Time | Done when |
| --- | --- | --- | --- |
| 1 | Create/login to a Devpost account and click **Join Hackathon** on <https://paypalaihackathon.devpost.com/> | 5 min | The hackathon page shows you as a participant ("My projects" shows the event) |
| 2 | Create/login to a free **PayPal Developer** account at <https://developer.paypal.com/> | 10 min | You can open **Apps & Credentials** |
| 3 | In **Apps & Credentials → Sandbox**, create a sandbox app (default merchant settings are fine); copy **Client ID** and **Secret** | 5 min | Credentials stored somewhere safe (not in git) |
| 4 | Create/verify a **sandbox personal (buyer) account** under **Testing Tools → Sandbox Accounts** | 3 min | You can log in to sandbox PayPal as a buyer for the video |
| 5 | (Optional but recommended) get an **LLM API key** (e.g. OpenAI), cost for the demo is well under $0.10 | 5 min | Key works with one `curl`/SDK test or the app's parse status shows the model name |
| 6 | Configure and verify real mode: `cp .env.example .env`, fill credentials, `python3 app.py`, load demo → **Create PayPal orders**; confirm real sandbox order ids appear | 10 min | Mode pill says **PayPal sandbox**; dashboard shows 3 orders; opening one checkout link shows PayPal's sandbox login |
| 7 | **Record the demo video** (script above; do a dry run first) | 45 min | MP4 under 3:00 that shows the end-to-end flow + the PayPal sandbox checkout + how AI is used |
| 8 | Upload to **YouTube as Public**, copy the URL, verify in incognito | 10 min | Video plays when logged out |
| 9 | Create a **public GitHub repo**, push this folder, confirm the **MIT license is detected and visible in the About section** | 10 min | Repo page shows "MIT license" in the sidebar; `LICENSE` is at the repo root; README renders with screenshots |
| 10 | Fill the Devpost submission form: name, pitch, About (Section 2 above), Built with tags, Tools, video URL, repo URL, screenshots/thumbnail | 15 min | The form preview looks right; no placeholder text left in |
| 11 | Final checks (below) and click **Submit** — then re-open the project page in an incognito window | 5 min | Devpost shows your submitted project and you have a confirmation |

### Pre-submit commands (run locally)

```bash
cd contest-entries/paypal

# 1. Tests and smoke must both pass
python3 -m unittest discover -s tests -t . -q
bash scripts/smoke.sh

# 2. No secrets anywhere in the working tree or git history
git status --short
git ls-files | grep -E '(^|/)\.env$' && echo "ERROR: .env is tracked!" || echo "ok: .env not tracked"
grep -RIn --exclude-dir=.git --exclude='*.png' -E '(PAYPAL_CLIENT_SECRET=[^ ]+|sk-[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9]{20,})' . || echo "ok: no obvious secrets"

# 3. Required files present
ls LICENSE README.md SUBMISSION.md RUNBOOK.md RULES-NOTES.md .env.example .gitignore app.py
```

### Final sanity checklist

- [ ] Video is **public**, **under 3:00**, shows the app working end-to-end, and no copyrighted music/trademarks
- [ ] Repo is **public**, has **all source code**, and the **MIT license is visible at the top of the repo page (About section)**
- [ ] README quickstart works from a clean checkout with no packages installed (`python3 app.py`)
- [ ] The About text has all sections, including a **Tools** section naming the AI model/service and how it was used
- [ ] "Built with" tags match the actual tools used in the recorded demo
- [ ] No `.env`, credentials, API keys, or personal data in the repo or video
- [ ] Devpost submission form is complete and submitted with ≥24 h buffer

---

## 5. What is verified vs what only the user can verify

Verified in this repo by automated tests and the smoke script:

- Mock-mode end-to-end: parse → fair split → orders → simulated checkout → capture → reminder (56 assertions).
- Real-mode HTTP path against a local fake PayPal + fake LLM server: OAuth Basic auth, cached token, Orders v2 create/get/capture request/response handling, `ORDER_NOT_APPROVED` 422 → 409 UX, token caching across orders, secret redaction.
- Static UI served, security headers, traversal/size hardening.
- 151 unit tests passing.

Only the user can verify (needs real accounts — flagged **UNVERIFIED** in the code docs):

- A live PayPal sandbox order/capture with your own sandbox app credentials (see RUNBOOK troubleshooting).
- PayPal's exact `return_url` redirect query parameters and the optional LLM provider's
  support for `response_format: json_object` (the app handles both gracefully).
- YouTube public visibility and Devpost submission state.
