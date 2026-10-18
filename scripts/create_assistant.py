"""Create the three tools and the assistant in your Vapi account.

Run from the project root, after the backend is reachable on a public https URL
(see README: use a tunnel such as ngrok or Cloudflare Tunnel while developing):

    export VAPI_API_KEY=...                       # Vapi dashboard -> API keys (private key)
    export PUBLIC_BASE_URL=https://your-backend.example.com
    export VAPI_CREDENTIAL_ID=...                 # recommended, see README step 3
    python -m scripts.create_assistant            # add --dry-run to just print the payloads

It prints the new assistant id; put it in VAPI_ASSISTANT_ID.

Tool creation (POST /tool) follows Vapi's documented request shape. The
assistant body uses Vapi's standard fields but was not checked against the
assistant API reference: if Vapi answers 400, the error text is printed, and you
can create the assistant in the dashboard instead (paste app/assistant_config.py's
SYSTEM_PROMPT and FIRST_MESSAGE, and attach the tool ids this script printed).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

from app.assistant_config import build_assistant_payload, build_tool_payloads


def _post(client: httpx.Client, path: str, body: dict) -> dict:
    response = client.post(path, json=body)
    if response.status_code >= 400:
        print(f"Vapi rejected POST {path} ({response.status_code}):\n{response.text}", file=sys.stderr)
        sys.exit(1)
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the payloads, call nothing")
    args = parser.parse_args()

    base_url = os.getenv("PUBLIC_BASE_URL", "").strip()
    credential_id = os.getenv("VAPI_CREDENTIAL_ID", "").strip()
    provider = os.getenv("VAPI_LLM_PROVIDER", "openai").strip()
    model = os.getenv("VAPI_LLM_MODEL", "gpt-4o").strip()
    api_key = os.getenv("VAPI_API_KEY", "").strip()
    vapi_url = os.getenv("VAPI_BASE_URL", "https://api.vapi.ai").rstrip("/")

    if not base_url.startswith("https://"):
        sys.exit("Set PUBLIC_BASE_URL to your backend's public https URL.")
    if not credential_id:
        print("Warning: no VAPI_CREDENTIAL_ID, so Vapi will call your webhook WITHOUT a secret "
              "and the backend will reject it (401).", file=sys.stderr)

    tool_payloads = build_tool_payloads(base_url, credential_id)
    if args.dry_run:
        placeholder_ids = [f"<id of {p['function']['name']}>" for p in tool_payloads]
        print(json.dumps({"tools": tool_payloads,
                          "assistant": build_assistant_payload(base_url, placeholder_ids, credential_id, provider, model)},
                         indent=2))
        return

    if not api_key:
        sys.exit("Set VAPI_API_KEY.")

    with httpx.Client(
        base_url=vapi_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30
    ) as client:
        tool_ids: list[str] = []
        for payload in tool_payloads:
            created = _post(client, "/tool", payload)
            tool_ids.append(created["id"])
            print(f"created tool {payload['function']['name']}: {created['id']}")
        assistant = _post(
            client, "/assistant",
            build_assistant_payload(base_url, tool_ids, credential_id, provider, model),
        )
    print(f"\ncreated assistant: {assistant['id']}\nSet VAPI_ASSISTANT_ID={assistant['id']}")


if __name__ == "__main__":
    main()
