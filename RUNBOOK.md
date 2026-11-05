# SplitMate runbook

Operational guide: run it, demo it, record it, submit it, and fix it when something goes
wrong. For the "why" and architecture see [README.md](README.md); for paste-ready Devpost
copy and the video script see [SUBMISSION.md](SUBMISSION.md).

---

## 1. Prerequisites

- **Python 3.9+** (`python3 --version`). The project uses the standard library only — no
  `pip install`, no virtualenv, no Node.
- A modern browser.
- For real mode: a free PayPal Developer account and an LLM API key (both optional; mock
  mode needs neither).
- Port 8000 free (or pass `--port`).

---

## 2. 60-second verification (mock mode)

```bash
git clone https://github.com/bgrubbs1/splitmate-paypal-ai.git
cd splitmate-paypal-ai
python3 app.py
# → SplitMate is running at http://127.0.0.1:8000
#   PayPal: MOCK (fully offline demo)
#   AI:     mock rule-based planner
```

Open <http://127.0.0.1:8000>:

1. Click **Load demo plan** (fills the deterministic cabin-weekend plan and parses it).
2. Click **Create PayPal orders**.
3. Click **Open checkout** on Sam's row → **Approve & pay (simulated)**.
4. You are back on the dashboard with Sam captured. **Capture approved** captures the rest.
5. Click **AI reminder** on any row to see the drafted message.

Expected demo numbers (deterministic in mock mode):

| Participant | Paid in | Share | Net |
| --- | --- | --- | --- |
| Alex | $710.00 | $297.09 | **+$412.91 credit** |
| Sam | $0.00 | $178.75 | **owes $178.75** |
| Priya | $275.00 | $297.08 | **owes $22.08** |
| Jordan | $40.00 | $252.08 | **owes $212.08** |

Total expenses $1,025.00 · 3 PayPal orders collecting **$412.91** · credits **$412.91**
(always balances).

Run the automated verification any time:

```bash
python3 -m unittest discover -s tests -t . -q    # expect: Ran 151 tests ... OK
bash scripts/smoke.sh                            # expect: SMOKE PASS
```

The smoke script starts its own fake PayPal + LLM servers on `127.0.0.1:8899` and two app
instances on `:8010` (mock) and `:8011` (real mode against the fakes). It kills them when it
finishes. It does not touch port 8000.

---

## 3. Real PayPal sandbox mode

### 3.1 Get sandbox credentials (one time, ~15 min)

1. Sign in to <https://developer.paypal.com/> with a PayPal account (or create one).
2. Open **Apps & Credentials** → make sure you are on the **Sandbox** tab.
3. Click **Create App**, give it a name (e.g. `SplitMate`) and keep the default merchant
   settings (this is the account that receives the payments), then create it.
4. Copy the app's **Client ID** and **Secret**.
5. Optional but recommended for the video: **Testing Tools → Sandbox Accounts** — note/verify
   the **personal (buyer)** sandbox account you'll use to pay. Use its email + password when
   the PayPal sandbox login page appears.

> Sandbox credentials are safe-ish, but still treat them as secrets: they live only in your
> local `.env` (gitignored) and are never printed by the app.

### 3.2 Configure and start

```bash
# From the repository root (the folder containing app.py):
cp .env.example .env
# Edit .env:
#   PAYPAL_CLIENT_ID=...
#   PAYPAL_CLIENT_SECRET=...
#   LLM_API_KEY=...        # optional, for real AI parsing + reminders
#   LLM_MODEL=gpt-4o-mini  # or your provider's model
python3 app.py
```

Startup output should say `PayPal: PAYPAL SANDBOX` and (if configured) `AI: LLM: <model>`.
The header pill should read **PayPal sandbox · `<model>`** (or `rule-based AI` without a key).

If you see a configuration error instead, run in mock mode first to confirm the app is fine,
then re-check the env names — see §7.

### 3.3 Run the real flow

1. **Load demo plan.** With an LLM key the status line should say *Parsed with `<model>`*.
   Without one it says *Parsed with the deterministic parser* — that is honest and fine for
   the video, but a real AI parse is the stronger demo.
2. Review the split, then **Create PayPal orders**. Three orders are created on your sandbox
   merchant account; each row shows a `MOCK-…` id in mock mode or a PayPal order id (e.g.
   `5O190127TN364715T`) in real mode.
3. Click **Open checkout** on one row. A new tab opens PayPal's **sandbox** checkout
   (`www.sandbox.paypal.com/...`). Log in with the sandbox buyer account and approve/pay.
4. Back on the dashboard, click **Sync** (or capture on return). Status should move to
   `approved`; then **Capture** (or **Capture approved**). Status becomes `captured` and the
   captured total increases.
5. Repeat for the other orders if you want a fully captured dashboard. Mock mode captures
   instantly; real sandbox requires the buyer approval step for each order (that's PayPal's
   flow, not a bug).

### 3.4 Verify in the browser it really worked

- PayPal Developer dashboard → **Sandbox** → your app → the sandbox business account's
  activity should show the orders/captures.
- SplitMate dashboard shows per-share `captured` status and a captured total equal to the
  collected amount when all are captured.

---

## 4. Recording the demo video

Full scene-by-scene script (with timings and narration) is in
[SUBMISSION.md §3](SUBMISSION.md). Practical setup:

1. Close everything you don't want on camera. Hide bookmarks bar, notifications, personal
   tabs. 100% browser zoom, window ≥1440×900.
2. Use a second browser profile or incognito tab for the PayPal sandbox buyer login.
3. Run a dry run once without recording; note where things need waiting.
4. Record with QuickTime Player (File → New Screen Recording) or OBS (macOS: Screen Capture,
   1080p, 30 fps). System audio/voice-over optional; a clean captioned recording is fine too.
5. Keep it **under 3:00** (target 2:35). No copyrighted music. Don't show real personal
   accounts or data — sandbox only.
6. Export MP4, upload to YouTube with visibility **Public**, verify in an incognito window.

During the recording, make the AI usage visible and explicit: pause on the parse status line
showing the model, and mention the deterministic fallback. Make the PayPal usage visible:
show the sandbox checkout page and the dashboard status transition after capture.

---

## 5. Publish the repo (user step)

```bash
# From the repository root (the folder containing app.py):
git init -b main
git add .
git commit -m "SplitMate: AI group-expense agent on PayPal Orders v2 (sandbox)"
# Create an empty public repo on GitHub, then:
git remote add origin https://github.com/<your-github-username>/splitmate.git
git push -u origin main
```

On GitHub: repo **Settings → General → About** → add a description and topics (e.g.
`paypal`, `ai`, `hackathon`, `python`, `payments`). The MIT license in `LICENSE` should be
detected automatically and shown as "MIT license" in the About sidebar — the hackathon rules
require the license to be "detectable and visible at the top of the repository page (in the
About section)". If it isn't detected, re-check that the file is named `LICENSE` at the repo
root and `git push` it.

Never commit `.env` (`.gitignore` covers it). Verify before pushing:

```bash
git ls-files | grep -E '(^|/)\.env$' || echo "ok: .env not tracked"
grep -RIn --exclude-dir=.git --exclude='*.png' -E '(PAYPAL_CLIENT_SECRET=[^ ]+|sk-[A-Za-z0-9]{20,})' . || echo "ok: no secrets"
```

---

## 6. Devpost submission (user step)

Checklist with times is in [SUBMISSION.md §4](SUBMISSION.md). Order that works well:

1. Repo public + license visible.
2. Video public on YouTube.
3. Devpost: join hackathon → **Enter a submission** → paste fields from SUBMISSION.md §1–2 →
   add screenshots from `docs/screenshots/` → submit with at least 24 h buffer.
4. Re-open the project page in an incognito window to confirm everything visible.

---

## 7. Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `Could not start the server: [Errno 48] Address already in use` | Another app owns port 8000. `python3 app.py --port 8001` (also update `SPLITMATE_PUBLIC_BASE_URL` in `.env` to match, so PayPal return URLs are correct). |
| Header says **Mock PayPal** even though `.env` has credentials | `.env` must be named exactly `.env` **in the same folder you run `python3 app.py` from**. Check `SPLITMATE_MODE` isn't `mock`. Placeholder values like `your-paypal-client-id` are treated as blank. |
| `Configuration error: SPLITMATE_MODE=real requires PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET` | You forced real mode without credentials; add them or use `SPLITMATE_MODE=auto`. |
| `PayPal authentication failed. Check PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET` | Wrong/rotated credentials, or you copied **Live** credentials while `PAYPAL_BASE_URL` points at the sandbox (or vice-versa). Use the Sandbox tab credentials with `https://api-m.sandbox.paypal.com`. |
| `PayPal denied the request (HTTP 403)` | The sandbox app doesn't have the capability needed, or credentials are from a different environment. Check you copied Sandbox credentials, or recreate the sandbox app. |
| `The buyer has not approved this order yet` (409) | Normal: PayPal requires buyer approval before capture. Open the checkout link, pay with the sandbox buyer account, then Sync → Capture. |
| `PayPal could not capture the order. It may already be captured, cancelled, or not approved yet.` | Use **Sync** to read the existing order. If the outcome remains unresolved, review it in the provider account. Do not repeat capture, create a replacement event/order, or restart to bypass the hold. See [Capture outcomes and reconciliation contract](docs/CAPTURE-OUTCOMES.md). |
| Returning from PayPal lands on the app but nothing auto-captures | PayPal did not append an order identifier we could read. Just click **Sync all** then **Capture approved** on the dashboard — the flow is fully supported that way. |
| AI parse warning "AI parsing was unavailable … used the built-in deterministic parser" | LLM key/URL/model wrong, provider down, or `response_format` unsupported. The app still works; fix `LLM_*` values or leave it (mock parser is deterministic). Check the log line, never the key. |
| `POST /api/parse` returns 400 "No expenses found" | The text had no amounts. Use the demo plan or one expense per line: `Cabin $620 paid by Alex`. |
| Real mode 404 on `/mock/checkout` | Expected: the simulated checkout only exists in mock mode. Use the real PayPal checkout link. |
| `python3: command not found` | Install Python 3.9+ or use `python3.x app.py`. |
| Tests fail on port/fake server | Make sure nothing else is on ports 8899/8010/8011, then re-run `bash scripts/smoke.sh`. |

Logs: the app prints one line per request (query strings omitted). Mock/real mode, warnings
and configuration notes are printed at startup. No secrets are printed.

---

## 8. Resetting / clean state

- For an offline mock demo, restart `python3 app.py` to clear the dashboard.
- In configured sandbox mode, first reconcile every payment outcome. Restarting loses all
  event and reconciliation state. Never restart or recreate an event/order to bypass an
  unresolved payment hold; use **Sync** and, if necessary, manual provider-account review.
- Delete `.env` (or blank the credentials) to return to mock mode.
- `make clean` removes Python bytecode caches.
- The fake servers used by tests/smoke are stateless per process; `Ctrl+C` stops everything.

---

## 9. Data handling & safety

- No data is persisted: names/amounts exist only in the running process's memory. PayPal
  capture responses are reduced to capture id/status — payer emails/addresses are neither
  stored nor displayed.
- No secrets reach the browser, logs, error messages or `/api/health`.
- Mock mode makes no network calls; real mode talks only to your configured
  `PAYPAL_BASE_URL` and `LLM_BASE_URL`.
- The server binds `127.0.0.1` by default. If you ever expose it publicly for a hosted demo,
  put it behind HTTPS/reverse proxy and use sandbox credentials only — it is a hackathon
  prototype without user accounts or rate limiting.

---

## 10. One-page pre-submit checklist

```bash
# From the repository root (the folder containing app.py):
python3 -m unittest discover -s tests -t . -q      # Ran 151 tests ... OK
bash scripts/smoke.sh                              # SMOKE PASS
git ls-files | grep -E '(^|/)\.env$' || echo "ok: no .env tracked"
ls LICENSE README.md SUBMISSION.md RUNBOOK.md RULES-NOTES.md
```

Then confirm: video public & <3 min · repo public & MIT visible in About · Devpost form
complete (Tools section included) · submitted before Thu 2026-11-12 12:00 PT / 15:00 ET.
