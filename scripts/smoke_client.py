#!/usr/bin/env python3
"""Scripted end-to-end checks used by scripts/smoke.sh.

Runs against two already-started SplitMate instances:
  * mock mode  -- fully offline checkout flow
  * real mode  -- real PayPal/LLM clients pointed at the local fake server

Standard library only. Exits non-zero on the first failed assertion.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

PASSED = 0


def check(condition, message):
    global PASSED
    if not condition:
        raise AssertionError(message)
    PASSED += 1
    print("  ok  {}".format(message))


def request(method, url, body=None, raw=False, timeout=20):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = response.read()
            return response.status, payload if raw else json.loads(payload)
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            decoded = json.loads(payload)
        except ValueError:
            decoded = {"raw": payload.decode("utf-8", "replace")}
        return exc.code, decoded


def expect(status, expected, label):
    check(status == expected, "{} -> HTTP {} (expected {})".format(label, status, expected))


def run_mock_flow(base, plan_text):
    print("\n[mock mode] {}".format(base))

    status, page = request("GET", base + "/", raw=True)
    expect(status, 200, "GET /")
    check(b"SplitMate" in page, "single-page UI is served")

    status, health = request("GET", base + "/api/health")
    expect(status, 200, "GET /api/health")
    check(health["paypal_mode"] == "mock", "health reports mock PayPal")
    check(health["ai_mode"] == "mock", "health reports rule-based AI")

    status, parsed = request("POST", base + "/api/parse", {"text": plan_text})
    expect(status, 200, "POST /api/parse")
    check(parsed["ready"] is True, "demo plan parses and is ready")
    check(parsed["parser"] == "rules", "mock mode uses the deterministic parser")
    check(len(parsed["plan"]["participants"]) == 4, "4 participants parsed")
    check(len(parsed["plan"]["expenses"]) == 5, "5 expenses parsed")

    status, created = request("POST", base + "/api/events", {"plan": parsed["plan"]})
    expect(status, 201, "POST /api/events")
    event = created["event"]
    check(len(event["orders"]) == 3, "3 PayPal orders created for 3 net debtors")
    check(event["totals"]["collected_cents"] == 41291, "collecting $412.91")
    check(event["totals"]["credits_cents"] == 41291, "credits match collections")
    order = event["orders"][0]
    check("/mock/checkout?order=" in order["approve_url"], "checkout link uses the app mock page")

    status, page = request(
        "GET", base + "/mock/checkout?order=" + order["order_id"], raw=True
    )
    expect(status, 200, "GET /mock/checkout")
    check(b"Simulated PayPal checkout" in page, "mock checkout page renders")

    status, approved = request(
        "POST", base + "/api/mock/checkout/{}/approve".format(order["order_id"]), {}
    )
    expect(status, 200, "POST mock approve")
    check(approved["share"]["status"] == "captured", "mock approval captures the share")
    check(bool(approved["share"]["capture_id"]), "capture id recorded")

    status, result = request(
        "POST", base + "/api/events/{}/capture-approved".format(event["id"]), {}
    )
    expect(status, 200, "POST /capture-approved")
    check(len(result["captured"]) == 2, "remaining 2 orders captured")
    check(result["event"]["totals"]["captured_cents"] == 41291, "all $412.91 captured")

    status, reminder = request(
        "POST", base + "/api/events/{}/reminders".format(event["id"]), {"name": "Sam"}
    )
    expect(status, 200, "POST /reminders")
    check("Sam" in reminder["message"] and "$178.75" in reminder["message"], "mock reminder drafted")

    status, lookup = request("GET", base + "/api/orders/" + order["order_id"])
    expect(status, 200, "GET /api/orders/{id}")
    check(lookup["share"]["status"] == "captured", "order lookup shows captured")


def run_real_flow(base, fake_base, plan_text):
    print("\n[real mode against fake PayPal/LLM] {}".format(base))

    status, health = request("GET", base + "/api/health")
    expect(status, 200, "GET /api/health")
    check(health["paypal_mode"] == "sandbox", "health reports PayPal sandbox client")
    check(health["ai_mode"] == "llm", "health reports LLM client")
    check(health["paypal_base_url"] == fake_base, "PayPal base URL points at the fake server")

    status, parsed = request("POST", base + "/api/parse", {"text": plan_text})
    expect(status, 200, "POST /api/parse (LLM path)")
    check(parsed["parser"] == "llm", "LLM parser used")
    check(parsed["model"] == "fake-model", "model name reported")

    status, created = request("POST", base + "/api/events", {"plan": parsed["plan"]})
    expect(status, 201, "POST /api/events (real PayPal client)")
    event = created["event"]
    check(len(event["orders"]) == 3, "3 PayPal orders created via the real client")
    check(
        all(order["order_id"].startswith("FAKEORDER") for order in event["orders"]),
        "orders were created on the fake PayPal REST API",
    )
    check(
        all("sandbox.paypal.com/checkoutnow" in order["approve_url"] for order in event["orders"]),
        "approve links come from the PayPal HATEOAS response",
    )

    first = event["orders"][0]
    status, body = request(
        "POST", base + "/api/events/{}/shares/{}/capture".format(event["id"], first["name"]), {}
    )
    check(status == 409, "capture before approval returns 409 (HTTP {})".format(status))
    check("pproved" in body.get("error", {}).get("message", ""), "409 explains approval is missing")

    # Simulate the buyer approving each order on the PayPal sandbox.
    for order in event["orders"]:
        status, _ = request("POST", fake_base + "/__test/approve", {"order_id": order["order_id"]})
        check(status == 200, "fake PayPal buyer approves {}".format(order["name"]))

    status, synced = request(
        "POST", base + "/api/events/{}/shares/{}/sync".format(event["id"], first["name"]), {}
    )
    expect(status, 200, "POST share sync")
    check(synced["share"]["status"] == "approved", "order status syncs to approved")

    status, captured = request(
        "POST", base + "/api/events/{}/capture-approved".format(event["id"]), {}
    )
    expect(status, 200, "POST capture-approved (real client)")
    check(len(captured["captured"]) == 3, "all 3 approved orders captured")
    check(captured["event"]["totals"]["captured_cents"] == 41291, "real flow captured $412.91")

    status, reminder = request(
        "POST", base + "/api/events/{}/reminders".format(event["id"]), {"name": "Sam"}
    )
    expect(status, 200, "POST /reminders (LLM path)")
    check(reminder["mode"] == "llm", "LLM drafted the reminder")

    status, stats = request("GET", fake_base + "/__test/stats")
    expect(status, 200, "GET fake server stats")
    check(stats["token_requests"] == 1, "OAuth token cached across all PayPal calls")
    check(stats["orders_created"] == 3, "fake PayPal created 3 orders")
    check(stats["captures"] == 3, "fake PayPal captured 3 payments")
    check(stats["llm_requests"] == 2, "LLM called for parse + reminder")

    secrets = ("smoke-client-secret", "smoke-api-key")
    serialized = json.dumps(
        {"health": health, "parsed": parsed, "event": captured["event"], "reminder": reminder}
    )
    check(
        not any(secret in serialized for secret in secrets),
        "no credentials leak into API responses",
    )


def wait_for(url):
    for _ in range(150):
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        import time

        time.sleep(0.1)
    raise SystemExit("timed out waiting for {}".format(url))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-base", required=True)
    parser.add_argument("--real-base", required=True)
    parser.add_argument("--fake-base", required=True)
    parser.add_argument("--plan-file", required=True)
    args = parser.parse_args(argv)

    wait_for(args.mock_base + "/api/health")
    wait_for(args.real_base + "/api/health")
    wait_for(args.fake_base + "/healthz")
    with open(args.plan_file, "r", encoding="utf-8") as handle:
        plan_text = handle.read()

    run_mock_flow(args.mock_base, plan_text)
    run_real_flow(args.real_base, args.fake_base, plan_text)
    print("\nAll {} smoke assertions passed.".format(PASSED))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print("\nSMOKE FAIL: {}".format(exc), file=sys.stderr)
        sys.exit(1)
