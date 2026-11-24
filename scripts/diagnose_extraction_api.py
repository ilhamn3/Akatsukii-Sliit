"""Reproduce the synthetic K2 request once and print redacted API diagnostics.

Run from the repository root: python -m scripts.diagnose_extraction_api
Uses the current terminal's credentials; may incur normal API usage charges.
"""

import argparse
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import openai

from project.agents.a2_extraction import PROMPT, ground
from project.core.capture_schemas import CaptureResult
from project.core.extraction_schemas import ExtractedRepair


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-only", action="store_true",
                        help="Check model metadata access without generating text")
    args = parser.parse_args()
    key = os.getenv("OPENAI_API_KEY", "")

    def report(label, value):
        text = str(value)
        if key:
            text = text.replace(key, "[REDACTED]")
        text = re.sub(r"(?:sk-|gsk_)[A-Za-z0-9_-]+", "[REDACTED]", text)
        print(f"{label}: {text[:2000]}")

    if not key:
        print("OPENAI_API_KEY is missing in this terminal. Enter it with Read-Host first.")
        return 1
    report("OpenAI SDK", openai.__version__)
    endpoint = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    host = urlsplit(endpoint).hostname
    report("Configured API host", host)
    expected_prefix = {"api.openai.com": "sk-", "api.groq.com": "gsk_"}.get(host)
    if expected_prefix:
        report(f"Key starts with expected {expected_prefix}", key.startswith(expected_prefix))
    report("Key character count", len(key))
    clean_key = key.isascii() and not any(c.isspace() for c in key) and not any(c in key for c in "\"'")
    report("Key has no whitespace, quotes or non-ASCII characters", clean_key)
    if not clean_key:
        print("Re-enter only the API key with Read-Host, without quotes or spaces.")
        return 1
    if '*' in key or (expected_prefix and not key.startswith(expected_prefix)):
        print("This does not look like the full secret for this provider. Copy the secret value from that provider's key creation page and enter it with Read-Host.")
        return 1
    report("Proxy variables present", [name for name in
           ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY") if os.getenv(name)])
    model = os.getenv("PRIMARY_LLM_MODEL", "gpt-4o-mini")
    report("Model", model)
    report("Custom endpoint configured", bool(os.getenv("OPENAI_BASE_URL")))
    report("Extra SDK configuration present", [name for name in
           ("OPENAI_CUSTOM_HEADERS", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID") if os.getenv(name)])
    capture = CaptureResult.model_validate_json(
        Path("tests/fixtures/extraction/capture.json").read_text(encoding="utf-8")
    )
    prompt = json.dumps({"segments": [
        {"segment_id": s.evidence_id, "text": s.text, "quality_flags": s.quality_flags}
        for s in capture.segments
    ]}, ensure_ascii=False)
    system = (PROMPT.read_text(encoding="utf-8") +
              "\n\nYou MUST output valid JSON complying strictly with this JSON Schema:\n" +
              json.dumps(ExtractedRepair.model_json_schema()))
    try:
        with openai.OpenAI(api_key=key, timeout=30, max_retries=0) as client:
            report("Effective API host", urlsplit(str(client.base_url)).hostname)
            report("Effective API path", urlsplit(str(client.base_url)).path)
            if args.connection_only:
                models = client.models.list()
                report("Selected model listed", any(item.id == model for item in models.data))
                print("Authenticated model list request passed. No text was generated.")
                return 0
            response = client.chat.completions.create(
                model=model, temperature=float(os.getenv("TEMPERATURE", "0")),
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
        record = ExtractedRepair.model_validate_json(response.choices[0].message.content or "")
        ground(record, capture)
        print("API request, output schema and source-quote checks passed.")
        return 0
    except openai.APIStatusError as exc:
        report("HTTP status", exc.status_code)
        report("Request ID", exc.request_id)
        report("Response content type", exc.response.headers.get("content-type"))
        report("API details", exc.body if exc.body is not None else "No response body")
    except Exception as exc:
        report("Error type", type(exc).__name__)
        report("Details", exc)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
