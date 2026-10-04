"""Typed Jev/Laya System One adapter. Only normalized choice labels leave this module."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class DecisionProviderError(RuntimeError):
    pass


def choose(profile: dict, state: dict, criteria: dict, timeout: float) -> tuple[str, dict]:
    """Ask one bounded choice question and return only its configured label."""
    if len(criteria) < 2 or len(criteria) > 255:
        raise DecisionProviderError("A typed choice requires 2 to 255 configured candidates")
    endpoint = os.getenv(profile.get("endpoint_binding", ""), "").strip()
    if not endpoint.startswith(("https://", "http://")) or len(endpoint) > 2048:
        raise DecisionProviderError("Provider endpoint binding is missing or invalid")
    key = os.getenv(profile.get("credential_binding", ""), "") if profile.get("credential_binding") else ""
    if not key and profile.get("credential_binding"):
        raise DecisionProviderError("Provider credential binding is not configured")
    payload = {
        "model": profile["model"],
        "state": state,
        "questions": {
            "action": {
                "type": "choice",
                "instructions": "Choose the single action that best advances the authored goals while respecting the supplied game state. Choose wait when no action is clearly useful.",
                "criteria": criteria,
            }
        },
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > 64 * 1024:
        raise DecisionProviderError("Decision request exceeds the 64 KiB limit")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=max(1, min(float(timeout), 120))) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise DecisionProviderError("Provider response exceeds 1 MB")
        decoded = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise DecisionProviderError(f"Provider returned HTTP status {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecisionProviderError(f"Provider request failed: {type(exc).__name__}") from None
    answers = decoded.get("answers") if isinstance(decoded, dict) else None
    answer = answers.get("action") if isinstance(answers, dict) else None
    choice = answer.get("choice") if isinstance(answer, dict) else None
    if not isinstance(choice, str) or choice not in criteria:
        raise DecisionProviderError("Provider returned a missing or unrecognized typed choice")
    # Confidence/probabilities are diagnostics only and never affect permissions.
    metadata = {"model": decoded.get("model")} if isinstance(decoded.get("model"), str) else {}
    return choice, metadata
