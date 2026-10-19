import os, json, requests
from config import MODEL

URL = "https://api.x.ai/v1/chat/completions"

class NoResponse(Exception):
    """The request failed before xAI returned a completion (timeout, connection error, 429/5xx).
    Not counted against the daily budget; the coin gets one retry later."""

def ask(system: str, payload, timeout=(10, 60), with_usage=False):
    """timeout = (connect, read) seconds. Returns parsed JSON verdict (and usage dict if with_usage)."""
    try:
        r = requests.post(URL, headers={"Authorization": f"Bearer {os.environ['XAI_API_KEY']}", "Content-Type": "application/json"},
            json={"model": MODEL, "temperature": 0,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload)}]},
            timeout=timeout)
    except (requests.Timeout, requests.ConnectionError) as e:
        raise NoResponse(f"{type(e).__name__}: {str(e)[:120]}") from None
    if r.status_code == 429 or r.status_code >= 500:
        raise NoResponse(f"HTTP {r.status_code}: {r.text[:120]}")
    r.raise_for_status()
    d = r.json()
    text = d["choices"][0]["message"]["content"]
    usage = dict(d.get("usage") or {}, served_model=d.get("model"))
    a, b = text.find("{"), text.rfind("}")
    if a == -1 or b <= a:
        raise ValueError("no JSON in reply: " + text[:200])
    out = json.loads(text[a:b + 1])
    return (out, usage) if with_usage else out
