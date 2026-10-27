# Rules notes — PayPal AI Hackathon (SplitMate)

Research record for the submission. All quotes below were copied on **2026-10-27 (America/New_York)**
from the official hackathon pages, fetched with a browser user-agent (`curl -s -L -A 'Mozilla/5.0 …'`;
`web_fetch` may 404 on Devpost). Quote text is verbatim except that page line-wrapping was
normalised to markdown. **This is not legal advice; re-read the live rules before submitting —
hackathon pages can change.**

Sources:

- Overview: <https://paypalaihackathon.devpost.com/>
- Official Rules: <https://paypalaihackathon.devpost.com/rules>
- PayPal Orders v2 API reference: <https://developer.paypal.com/docs/api/orders/v2/>
  (machine-readable schema: `https://developer.paypal.com/api/orders/v2/schema.json`; the
  spec used to verify request/response shapes was `paypal-rest-api-specifications`
  `openapi/checkout_orders_v2.json`, version 2.32)
- PayPal authentication: <https://developer.paypal.com/api/rest/authentication/>
- PayPal Orders overview (integration samples): <https://developer.paypal.com/whats-an-order>

---

## 1. Deadline and dates (verbatim)

> **1. Dates and Timing**
>
> **Submission Period:**
> Thursday Oct 1, 2026 (9:15 am Pacific Time) – Thursday Nov 12, 2026 (12:00 pm Pacific Time) (“Submission Period”).
>
> **Judging Period:**
> Tuesday Dec 1, 2026 (2:00 pm Pacific Time) – Tuesday Dec 15, 2026 (8:00 am Pacific Time) (“Judging Period”).
>
> **Winners Announced:**
> On or around Monday, Dec 21, 2026 (2:00 pm Pacific Time).

The overview page header displays the same moment as: “Deadline: Nov 12, 2026 @ 12:00pm PST” and
“November 12 at 3:00pm EST to deadline”.

**Our conversion:** deadline = **Thu 2026-11-12 12:00 pm Pacific = 3:00 pm Eastern (EST) on
2026-11-12 = 20:00 UTC**. November is PST/EST, so the “PST” label on the page and
“12:00 pm Pacific Time” in the rules agree. Aim to submit by **Tue 2026-11-10** to leave buffer.

## 2. Eligibility (verbatim)

> The Hackathon IS open to:
> Individuals who are at least the age of majority where they reside as of the time of entry (“Eligible Individuals”);
> Teams of Eligible Individuals (“Teams”); and
> Organizations (including corporations, not-for-profit corporations and other nonprofit organizations, limited liability companies, partnerships, and other legal entities) that exist and have been organized or incorporated at the time of entry.
> (the above are collectively, “Entrants”)

Not open to, verbatim:

> Individuals who are residents of, or Organizations domiciled in, a country, state, province or territory where the laws of the United States or local law prohibits participating or receiving a prize in the Hackathon (including, but not limited to, Brazil, Quebec, Russia, Crimea, Cuba, Iran, and North Korea and any other country which is comprehensively sanctioned by the U.S. Treasury’s Office of Foreign Assets Control)
> Organizations involved with the design, production, paid promotion, execution, or distribution of the Hackathon, including the Sponsor and Administrator (“Promotion Entities”).
> Employees, representatives and agents** of such Promotion Entities, and all members of their immediate family or household*
> Any other individual involved with the design, production, promotion, execution, or distribution of the Hackathon, and each member of their immediate family or household*
> Any Judge (defined below), or company or individual that employs a Judge
> Any parent company, subsidiary, or other affiliate*** of any organization described above
> Any other individual or organization whose participation in the Hackathon would create, in the sole discretion of the Sponsor and/or Administrator, a real or apparent conflict of interest

Overview page also lists: “Above legal age of majority in country of residence” and “All
countries/territories, excluding standard exceptions”.

**Action:** the entrant must be of legal age of majority where they live and not reside in an
excluded jurisdiction. This is a user-confirmed item; the package cannot verify it.

## 3. What to build (verbatim)

> **What to Create:**
> Entrants must build or update an application that integrates the PayPal developer platform (using the free sandbox environment) along with an AI tool, model, or platform of their choice (each a “Project”).
>
> This includes all the services provided by PayPal such as Venmo, Braintree, Xoom, Honey, Zettle, Paidy, Hyperwallet, Simility, Swift Financial, and others.
>
> PayPal will provide access to select AI sponsor tools that entrants are welcome to use, though using one of these partner tools is not required. Any AI tool works, as long as PayPal integration is central to the project.

Overview page “Requirements” adds:

> Build or update an application that integrates the PayPal developer platform (using the free sandbox environment) along with an AI tool, model, or platform of your choice.

> Meaningfully integrate at least one PayPal technology, API, SDK, product, or developer capability
> Meaningfully incorporate AI into the experience or functionality
> Demonstrate a working prototype or proof of concept
> Be sufficiently documented for the judges to understand and evaluate it
> Comply with the Official Rules

> **Functionality:**
> The Project must be capable of being successfully installed and running consistently on the platform for which it is intended and must function as depicted in the video and/or expressed in the text description.

**How SplitMate complies:** central PayPal integration = Orders v2 (OAuth2 token, create order,
get order, capture) in the **sandbox**; AI is central to the experience = LLM parsing of the
plan into the structured split plus AI-drafted reminders, with an honest deterministic fallback
for offline mode. Mock mode provides a working, installable prototype that judges can run.

## 4. Submission requirements (verbatim)

From the Official Rules, “Submission Requirements”:

> Submissions to the Hackathon must meet the following requirements:
>
> Include a Project built with the required developer tools and meets the above Project Requirements.
>
> Include a text description that should explain the features and functionality of your Project.
>
> Provide access to a functional demo of your application. Your project does not need to be publicly hosted or deployed — but judges must be able to actually run or interact with a working build.
> This can be satisfied through clear, complete setup and run instructions in your code repository (so judges can build and run it themselves) OR
> A hosted demo URL.
> A design mockup, static prototype, or non-functional demo does not meet this requirement.
>
> Provide a URL to your public code repository for judging and testing on GitHub, Bitbucket, or Gitlab.
>
> The repository must contain all necessary source code, assets, and instructions required for the project to be functional.
>
> The repository must be public and open source by including an open source license file. This license should be detectable and visible at the top of the repository page (in the About section).
>
> Include a demonstration video of your Project. The video portion of the Submission:
> should be less than three (3) minutes. Judges are not required to watch beyond three minutes
> should include footage that shows the Project functioning on the device for which it was built
> must be uploaded to and made publicly visible on YouTube and a link to the video must be provided on the submission form on the Hackathon Website; and
> must not include third party trademarks, or copyrighted music or other material unless the Entrant has permission to use such material.

The overview page “What to Submit” additionally requires (verbatim):

> **Tools** — identify which tools (model or AI service) were used and explain how the tools were used in your project.

> **Public code repository** — on GitHub, Gitlab or Bitbucket. Must contain all source code, assets, and instructions needed to run the project. Must be public and open source, with a license file visible at the top of the repo (in the About section).

> **Demo video** — Under 3 minutes (judges aren't required to watch past that). Must show the project functioning on the device it was built for. Must be uploaded to YouTube, publicly visible, with the link on the submission form. Must not include third-party trademarks or copyrighted music/material without permission.

**How SplitMate complies:**

- Text description: `README.md` + `SUBMISSION.md` §2 (paste-ready About).
- Functional demo: `python3 app.py` runs offline with no dependencies or accounts; judge can
  run the full flow in mock mode and verify via `scripts/smoke.sh`. Real sandbox mode is
  documented in `RUNBOOK.md`.
- Public repo: user step — push this folder to a public GitHub repo (see `RUNBOOK.md` §5).
- License: `LICENSE` (MIT) at repo root; user must confirm GitHub detects and displays it in
  the About sidebar.
- Demo video: script in `SUBMISSION.md` §3 (<3:00), user records/uploads to YouTube as **public**.
- **Tools section:** included in `SUBMISSION.md` §2 under “### Tools”, naming the
  OpenAI-compatible Chat Completions model and how it is used (parse + reminder drafting),
  plus PayPal Orders v2.
- **No third-party trademarks in video:** we do not use PayPal/Venmo logos or any brand
  imagery in the app; “PayPal” appears only as the necessary nominative API/integration name.
  The video must not include copyrighted music — the script calls for voice-over only.

### Testing / judge access (verbatim)

> **Testing**
> Access must be provided to an Entrant’s working Project for judging and testing by providing a link to a website, functioning demo, or a test build. If Entrant’s website is private, Entrant must include login credentials in its testing instructions. The Entrant must make the Project available free of charge and without any restriction, for testing, evaluation and use by the Sponsor, Administrator and Judges until the Judging Period ends. Judges are not required to test the Project and may choose to judge based solely on the text description, images, and video provided in the Submission.
>
> Access can be provided to the Sponsor and Administrator by including complete setup and run instructions in your project's code repository (see Repository Requirements above), along with any test account credentials, API keys, or sandbox login details necessary to run and test the Project. If your submission requires special access, please include clear instructions for obtaining access within your repository's README or project description.

**How SplitMate complies:** README quickstart runs with zero accounts; **mock mode requires
no credentials at all**. Real sandbox mode needs the judge's own PayPal sandbox app — split
mode means judges can still evaluate the working product without one. No private test
credentials are needed for the mock demo.

## 5. New & existing project rule (verbatim)

> **New & Existing:**
> Projects must be either newly created by the Entrant or, if the Entrant’s Project existed prior to the Hackathon Submission Period, must have been significantly updated after the start of the Hackathon Submission Period. Entrants should explain how their Project was significantly updated during the Submission Period. Sponsor and Administrator will determine what constitutes a “significant change” at their sole discretion.

**How SplitMate complies:** the entire repo was created during the hackathon submission
period (Oct 1 – Nov 12, 2026) and has no prior history. No prior-project explanation needed.

## 6. Third-party integrations / IP (verbatim)

> **Third Party Integrations:**
> If a Project integrates any third-party SDK, APIs and/or data, Entrant must be authorized to use them in accordance with any terms and conditions or licensing requirements of the tool.

> **Intellectual Property**
> Your Submission must: (a) be your (or your Team, or Organization’s) original work product; (b) be solely owned by you, your Team, your Organization with no other person or entity having any right or interest in it; and (c) not violate the intellectual property rights or other rights including but not limited to copyright, trademark, patent, contract, and/or privacy rights, of any other person or entity. …

> **Submission ownership**
> Be the original work of the Entrant, be solely owned by the Entrant, and not violate the IP rights of any other person or entity.

**How SplitMate complies:** 100% original code and copy, MIT licensed. Uses the PayPal
Developer platform and an AI API under their normal terms (user supplies their own sandbox
credentials and LLM key); no third-party code, data, fonts, images or trademarks are bundled.
The demo uses fictional names only; no personal data is included or transmitted.

## 7. Judging criteria (verbatim)

> **6. Judges & Criteria**
> …
> **Stage One)**
> The first stage will determine via pass/fail whether the ideas meet a baseline level of viability, in that the Project reasonably fits the theme and reasonably applies the required APIs/SDKs featured in the Hackathon.
>
> **Stage Two)**
> All Submissions that pass Stage One will be evaluated in Stage Two based on the following equally weighted criteria (the “Judging Criteria”):
> Entries will be judged on the following equally weighted criteria, and according to the sole and absolute discretion of the judges:
>
> **Technological Implementation:**
> How thoroughly and skillfully does the project use PayPal Developer Platform and AI tool(s)? Does the project reflect genuine effort and a working, non-trivial implementation?
>
> **Design:**
> Does the project deliver a complete, coherent product experience — not just a technical proof of concept?
>
> **Potential Impact:**
> Does the project make a credible, specific case for solving a real problem for a real audience — and does the solution actually address that problem based on what's demonstrated?
>
> **Innovation/Idea:**
> How creative and novel is the concept and does the project differ from existing concepts?
>
> **Presentation:**
> Does the video clearly demonstrate the project working end-to-end? Does the pitch communicate what problem is solved, who it's for, and why it matters? Is the overall presentation easy to follow?

**Where the submission leans:** non-trivial Orders v2 implementation + real AI integration
(Technological Implementation); a complete dashboard/product experience (Design); a specific,
familiar problem and audience (Potential Impact); netting + partial-attendance fairness as the
novel angle (Innovation); a tight <3-min end-to-end video (Presentation).

## 8. Prizes (verbatim excerpts)

Official Rules, §8 Prizes table:

> **Grand Prizes**
> 1st Place — $12,000 USD — 1
> 2nd Place — $8,000 USD — 1
> 3rd Place — $5,000 USD — 1
>
> **Honorable Mention Prizes**
> Most Creative — $5,000 USD — 1
> Most Impactful — $5,000 USD — 1
> Best Demo Delivery — $5,000 USD — 1
> Best Use of PayPal + AI — $5,000 USD — 1
> Best Use of Agentic Commerce — $5,000 USD — 1
>
> **Sponsor Prizes**
> Best Use of AG Grid — 1st Place — $5,000 USD — 1; 2nd Place — $2,000 USD — 1; 3rd Place — $1,000 USD — 3
> Best Use of APIMatic — $1,000 USD + 6 months of APIMatic business subscription — 3
> Best Use of Bryntum — $1,000 USD — 3
> Best Use of Channel3 — $1,500 USD — 1
> Best Use of Render — 1st Place — $1,000 Render credits; 2nd Place — $750 Render credits; 3rd Place — $500 Render credits — 1 each

> **IMPORTANT NOTES ON MULTIPLE PRIZE ELIGIBILITY:**
> A project can only win up to one (1) Grand Prize and one (1) Sponsor Prize OR one (1) Honorable Mention Prize and one (1) Sponsor Prize.

The overview page shows a headline of “**$67,500 in cash**” (with “+ other prizes”) and, in a
different section, “Win part of **$69,750 in total prizes**”. Both appear on the page; the
displayed totals and the rules table rows are not fully consistent with one another. This
doesn’t affect our entry — we’re not claiming a specific prize — but don’t quote a single total
without attributing it, and don’t promise a prize to anyone.

Also relevant, from the rules:

> Sponsor in its sole discretion has the right to make a prize substitution of equivalent or greater value. Sponsor will not award a prize if there are no eligible Submissions entered in the Hackathon, or if there are no eligible Entrants or Submissions for a specific prize.

## 9. Other verbatim requirements worth remembering

- Multiple submissions: “An Entrant may submit more than one Submission, however, each
  Submission must be unique and substantially different from each of the Entrant’s other
  Submissions…”. SplitMate is a single unique submission.
- Language: “All Submission materials must be in English or, if not in English, the Entrant
  must provide an English translation…”. Everything in this package is English.
- Project must not have been developed with financial or preferential support from the
  Sponsor/Administrator (rules “Financial or Preferential Support”). SplitMate was built
  independently.
- Entry requires joining the hackathon on the Devpost page and completing the submission form
  (“Register for the Hackathon on the Hackathon Website by clicking the ‘Join Hackathon’
  button…”, rules §4 How To Enter) — user-only step.

---

## 10. PayPal API verification notes

What we verified directly from PayPal's own documentation/schema before writing the client
(fetched 2026-10-27). This is why the request shapes in `splitmate/paypal_client.py` are what
they are.

### 10.1 OAuth 2.0 (verbatim)

> PayPal REST APIs use OAuth 2.0 access tokens to authenticate requests. Your access token authorizes you to use the PayPal REST API server.

> To call a REST API in your integration, you'll need to exchange your client ID and client secret for an access token.

The documented cURL form of the token call:

> `curl -v -X POST "https://api-m.sandbox.paypal.com/v1/oauth2/token" \`
> `-u "CLIENT_ID:CLIENT_SECRET" \`
> `-H "Content-Type: application/x-www-form-urlencoded" \`
> `-d "grant_type=client_credentials"`

> **Note:** Encode `CLIENT_ID:CLIENT_SECRET` in Base64 before sending it in the API call.

Sample response includes `access_token`, `token_type`, `expires_in` (e.g. `31668` seconds),
plus `scope`, `app_id`, `nonce`. Also: “When your access token expires, call `/v1/oauth2/token`
again to request a new access token.”

**Implemented as:** `RealPayPalClient.access_token()` — Basic auth with base64
`client_id:client_secret`, form body `grant_type=client_credentials`, caches the token until
`expires_in − 60 s`, re-fetches on expiry. Tests pin the header, form body and caching.

### 10.2 Orders v2 — create order (verified from schema + docs)

From the OpenAPI schema (`order_request`): required fields are `intent` and `purchase_units`;
`intent` enum is `CAPTURE` / `AUTHORIZE`; each `purchase_unit_request` requires `amount`
(`money`: required `currency_code` + `value`, where `value` is a decimal string such as
`"155.00"`; `description` is truncated beyond 127 characters in responses); `purchase_units`
allows 1–10 items.

> **Create Order – Minimal Request and Response** example response (PayPal schema examples):
> `{"id": "5O190127TN364715T", "links": [{"href": "https://api-m.paypal.com/v2/checkout/orders/5O190127TN364715T", "rel": "self", "method": "GET"}, {"href": "https://www.paypal.com/checkoutnow?token=5O190127TN364715T", "rel": "approve", "method": "GET"}, …]}`

> **orders.create 201:** “A successful request returns the HTTP `201 Created` status code and a JSON response body that includes by default a minimal response with the ID, status, and HATEOAS links.”

The integration guide also shows a multi-step create response with `status: "CREATED"` and a
`rel: "approve"` link `https://www.sandbox.paypal.com/checkoutnow?token=ORDER-ID`, and a
single-step `payment_source.paypal` create returning `status: "PAYER_ACTION_REQUIRED"` with
`rel: "payer-action"`:

> After successfully creating the order, redirect the buyer to the RYP page. The Create order API response passes the RYP page URL as a HATEOS link using the `payer-action` parameter in the `links` section.

**Implemented as:** `POST /v2/checkout/orders` with `intent: CAPTURE` and one
`purchase_units[0]` (`reference_id`, `description`, `amount.value` as a decimal string).
The checkout URL is read from the response `links` array (`payer-action` first, then
`approve`) — never hard-coded. Both `200` and `201` are accepted.

### 10.3 Redirect URLs / `application_context` (verified, with a caveat)

The schema's `order_application_context` contains `return_url`, `cancel_url`, `brand_name`,
`user_action`, but each is **marked DEPRECATED**. Verbatim for `return_url`:

> DEPRECATED. The URL where the customer is redirected after the customer approves the payment. The fields in `application_context` are now available in the `experience_context` object under the `payment_source` which supports them (eg. `payment_source.paypal.experience_context.return_url`). Please specify this field in the `experience_context` object instead of the `application_context` object.

The integration guide's “Sample JSON order request 1” puts return/cancel URLs under
`payment_source.paypal.experience_context` and notes for its multi-step example:
“When the buyer selects **Continue to Review Order**, they will be redirected to the `return_url`
in the `experience_context` object.”

**Implemented as:** default = classic `application_context` redirect flow (most compatible,
still accepted per the schema, used by the multi-step no-`payment_source` flow documented at
developer.paypal.com/whats-an-order); opt-in `PAYPAL_EXPERIENCE_CONTEXT=1` switches to
`payment_source.paypal.experience_context`. The deprecation and the compatibility choice are
documented in the README rather than hidden.

### 10.4 Get / capture order (verified)

> `POST /v2/checkout/orders/{id}/capture` — “Capture payment for order”. Sample request uses
> `-d '{}'` with `Content-Type: application/json`.

> **orders.capture 201:** “A successful response to a non-idempotent request returns the HTTP `201 Created` status code with a JSON response body that shows captured payment details. If a duplicate response is retried, returns the HTTP `200 OK` status code.”

Capture response example (verbatim structure): top-level `status: "COMPLETED"`, and
`purchase_units[0].payments.captures[0]` with `id`, `status: "COMPLETED"`, `amount`,
`final_capture`, `seller_receivable_breakdown`, … . Order statuses enum:
`CREATED, SAVED, APPROVED, VOIDED, COMPLETED, PAYER_ACTION_REQUIRED`.

**Implemented as:** `POST /v2/checkout/orders/{id}/capture` with body `{}`; capture id/status
read from `purchase_units[].payments.captures[]`; `GET` order status polled by Sync; `200` and
`201` both accepted; all surfaces normalised so the browser never sees raw PayPal responses
(no payer email/address persisted).

### 10.5 Optional idempotency header

`PayPal-Request-Id` is documented for create order: “The server stores keys for 6 hours. …
It is mandatory for all single-step create order calls (E.g. Create Order Request with payment
source information like Card, PayPal.vault_id, PayPal.billing_agreement_id, etc).” SplitMate
uses the multi-step redirect flow (no payment source in the create call), so the header is
optional; we do not send it and rely on one create attempt per share (with explicit Retry on
failure). If you later add payment sources, send a unique `PayPal-Request-Id` per create call.

---

## 11. UNVERIFIED items (explicitly flagged)

These are honest gaps: verified against PayPal's published docs/schema and/or a local fake
server, but **not** exercised against a live PayPal sandbox or live LLM provider by the build
environment (no accounts/credentials available). They are documented in the README as well.

1. **Live sandbox order creation + capture.** The exact request/response handling is
   implemented from the official schema and exercised against `splitmate/fake_services.py`,
   which mirrors documented shapes, statuses and the `ORDER_NOT_APPROVED` 422. The first live
   end-to-end run must be done by the entrant (`RUNBOOK.md` §3).
2. **`PAYPAL_EXPERIENCE_CONTEXT=1` placement.** Sending return/cancel URLs under
   `payment_source.paypal.experience_context` is the current schema's suggested location, but
   the exact acceptance by a live sandbox (and whether the order returns `payer-action` vs
   `approve`, and whether it requires payer details) was not tested live. The default classic
   `application_context` path is the one intended for the recorded demo.
3. **PayPal `return_url` redirect parameters.** Docs say the buyer is redirected to
   `return_url` after approval; the exact query parameters appended by PayPal (commonly
   `token`/`PayerID` in the classic flow) are not verified from a live redirect. SplitMate
   therefore supports a `token=` deep link **and** a manual “Sync all → Capture approved”
   dashboard path, so the flow never depends on unverified parameters.
4. **Approved checkout URL domain.** Schema examples show both `api-m.paypal.com` /
   `api-m.sandbox.paypal.com` API hosts and `www.sandbox.paypal.com/checkoutnow?token=…`
   checkout hosts. The client uses the `href` returned by PayPal verbatim instead of
   constructing URLs, so this cannot drift.
5. **`response_format: {"type": "json_object"}` with non-OpenAI providers.** Standard for
   OpenAI Chat Completions; some OpenAI-compatible providers ignore or reject it. The parser
   extracts a JSON object from the raw content and falls back deterministically on failure.
   Verify support for the provider/model you use for the recording, or leave mocking on.
6. **Rules/pages are dynamic.** Quotes were fetched 2026-10-27; the live pages could change
   before the deadline. Re-read them before submitting.
7. **Prize total inconsistency on the Devpost page** (“$67,500 in cash” headline vs
   “$69,750 in total prizes” section vs rules table rows). Not material to our entry; do not
   quote one total without attribution.
8. **Not legal advice.** This file is a research log for the submission, not legal or
   compliance advice.
