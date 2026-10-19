import os, json, requests
from config import MODEL
URL = "https://api.x.ai/v1/chat/completions"
def ask(system: str, payload, timeout: int = 60) -> dict:
    r = requests.post(URL, headers={"Authorization": f"Bearer {os.environ['XAI_API_KEY']}", "Content-Type": "application/json"},
        json={"model": MODEL, "temperature": 0,
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload)}]},
        timeout=timeout)
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    a, b = text.find("{"), text.rfind("}")
    if a == -1 or b <= a:
        raise ValueError("no JSON in reply: " + text[:200])
    return json.loads(text[a:b + 1])
