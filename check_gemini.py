"""Gemini API diagnostic — run: venv/bin/python check_gemini.py"""
import os
import sys

api_key = os.environ.get("GEMINI_API_KEY", "").strip()
if not api_key:
    print("❌ GEMINI_API_KEY environment variable is not set.")
    print("   Usage: GEMINI_API_KEY=your_key venv/bin/python check_gemini.py")
    sys.exit(1)

print(f"Key prefix : {api_key[:8]}…  (length {len(api_key)})")

# ── 1. Import ────────────────────────────────────────────────────────────────
try:
    from google import genai
    from google.genai import types
    print("✅ google-genai SDK imported OK")
except ImportError as e:
    print(f"❌ Import failed: {e}")
    print("   Run: venv/bin/pip install google-genai")
    sys.exit(1)

client = genai.Client(api_key=api_key)

# ── 2. List models ───────────────────────────────────────────────────────────
print("\n── Available models ──────────────────────────────────────────────────")
try:
    models = list(client.models.list())
    flash_models = [m.name for m in models if "flash" in m.name.lower()]
    for name in flash_models:
        print(f"  {name}")
    if not flash_models:
        print("  (no flash models found — key may be invalid or API not enabled)")
except Exception as e:
    print(f"❌ models.list() failed: {e}")

# ── 3. Simple generation ─────────────────────────────────────────────────────
MODEL = "gemini-2.5-flash"
print(f"\n── generate_content with {MODEL} ─────────────────────────────────────")
try:
    resp = client.models.generate_content(
        model=MODEL,
        contents="Reply with exactly: OK",
        config=types.GenerateContentConfig(temperature=0),
    )
    print(f"✅ Response: {resp.text.strip()}")
except Exception as e:
    msg = str(e)
    if "429" in msg:
        import re
        m = re.search(r"retry_delay\s*\{\s*seconds:\s*(\d+)", msg)
        retry = f" (retry in {m.group(1)}s)" if m else ""
        print(f"❌ 429 Quota exceeded{retry}")
        if "limit: 0" in msg:
            print("   ⚠️  limit=0 means NO free-tier quota is assigned.")
            print("   Possible causes:")
            print("   1. API key region is outside free-tier coverage")
            print("      → try creating a new key with VPN set to US/EU")
            print("   2. 'Generative Language API' not enabled in GCP project")
            print("      → https://console.cloud.google.com → APIs & Services → Enable")
            print("   3. Key was created in GCP Console, not AI Studio")
            print("      → use https://aistudio.google.com/app/apikey instead")
    elif "API_KEY_INVALID" in msg or "400" in msg:
        print("❌ API key is invalid or revoked — generate a new one at:")
        print("   https://aistudio.google.com/app/apikey")
    elif "not found" in msg.lower() or "404" in msg:
        print(f"❌ Model '{MODEL}' not found — it may not be available for your key yet.")
        print("   Try gemini-1.5-flash instead.")
    else:
        print(f"❌ Unexpected error: {msg[:300]}")
