"""Quick connectivity check for the Gemini API. Run when the app feels stuck."""
from __future__ import annotations

import os
import time

from dotenv import load_dotenv

load_dotenv()

key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not key:
    print("GEMINI_API_KEY is not set in this shell.")
    print("Run:  export GEMINI_API_KEY='your-key-here'")
    raise SystemExit(1)

print(f"Key found: {key[:6]}…{key[-4:]}  (length {len(key)})")

try:
    from google import genai
    from google.genai import types
except ImportError as e:
    print(f"google-genai not installed: {e}")
    print("Run:  .venv/bin/pip install -r requirements.txt")
    raise SystemExit(1)

client = genai.Client(api_key=key)

print("\nListing models available to this key...")
t0 = time.perf_counter()
try:
    models = list(client.models.list())
    print(f"  {len(models)} models in {time.perf_counter() - t0:.2f}s")
    gen_models = [m.name for m in models if "generateContent" in (m.supported_actions or [])]
    print("\n  generate_content-capable models:")
    for name in gen_models[:15]:
        print(f"    - {name}")
except Exception as e:
    print(f"  Listing failed in {time.perf_counter() - t0:.2f}s: {type(e).__name__}: {e}")
    raise SystemExit(1)

candidates = [
    os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]
tested: set[str] = set()
working: list[tuple[str, float]] = []

print("\nTesting generate_content latency on each candidate...")
for model in candidates:
    if model in tested:
        continue
    tested.add(model)
    t0 = time.perf_counter()
    try:
        resp = client.models.generate_content(
            model=model,
            contents="Reply with exactly: OK",
            config=types.GenerateContentConfig(temperature=0, max_output_tokens=10),
        )
        elapsed = time.perf_counter() - t0
        text = (resp.text or "").strip()
        print(f"  ok    {model:30s}  {elapsed:5.2f}s  -> {text!r}")
        working.append((model, elapsed))
    except Exception as e:
        elapsed = time.perf_counter() - t0
        msg = str(e)
        if len(msg) > 120:
            msg = msg[:117] + "..."
        print(f"  fail  {model:30s}  {elapsed:5.2f}s  -> {type(e).__name__}: {msg}")

print("\n" + "-" * 60)
if working:
    best = min(working, key=lambda x: x[1])
    print(f"Fastest working model: {best[0]}  ({best[1]:.2f}s)")
    print(f"\nTo use it:")
    print(f"  export GEMINI_MODEL='{best[0]}'")
    print("Then restart Streamlit.")
else:
    print("No models worked. Likely causes:")
    print("  - key invalid or revoked  -> regenerate at aistudio.google.com/apikey")
    print("  - free-tier daily quota exhausted  -> wait or use a different model")
    print("  - network blocking generativelanguage.googleapis.com")
