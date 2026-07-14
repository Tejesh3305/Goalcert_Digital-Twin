"""check_runpod.py — quick CLI check of RunPod account balance and TRELLIS endpoint health.

Usage: python check_runpod.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).resolve().parent / ".env")

API_KEY = os.getenv("RUNPOD_API_KEY", "")
ENDPOINT_ID = os.getenv("RUNPOD_ENDPOINT_ID", "")

if not API_KEY:
    print("RUNPOD_API_KEY not set in .env")
    sys.exit(1)

headers = {"Authorization": f"Bearer {API_KEY}"}

print("== Account balance ==")
resp = requests.post(
    "https://api.runpod.io/graphql",
    headers={**headers, "Content-Type": "application/json"},
    json={"query": "query myself { myself { id clientBalance currentSpendPerHr } }"},
    timeout=15,
)
resp.raise_for_status()
data = resp.json()
myself = data.get("data", {}).get("myself", {})
balance = myself.get("clientBalance")
spend = myself.get("currentSpendPerHr")
if balance is None:
    print("Could not read balance:", json.dumps(data, indent=2))
else:
    print(f"  Credit balance : ${balance:.4f}")
    print(f"  Current spend  : ${spend}/hr")
    if balance <= 0:
        print("  [WARN] Out of credits — the endpoint will not run jobs until you add funds.")
    elif balance < 1:
        print("  [WARN] Balance is low.")

if ENDPOINT_ID:
    print(f"\n== Endpoint health (id={ENDPOINT_ID}) ==")
    resp = requests.get(
        f"https://api.runpod.ai/v2/{ENDPOINT_ID}/health",
        headers=headers,
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"  Health check failed: HTTP {resp.status_code} — {resp.text}")
    else:
        h = resp.json()
        jobs = h.get("jobs", {})
        workers = h.get("workers", {})
        print(f"  Jobs   : {jobs}")
        print(f"  Workers: {workers}")
        if workers.get("unhealthy", 0) > 0:
            print("  [WARN] One or more workers are unhealthy.")
        if workers.get("ready", 0) == 0 and workers.get("idle", 0) == 0:
            print("  [WARN] No ready/idle workers — endpoint may be cold or failing to start (often a credits/billing issue).")
else:
    print("\nRUNPOD_ENDPOINT_ID not set — skipping endpoint health check.")
