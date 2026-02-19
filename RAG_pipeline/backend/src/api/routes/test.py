from fastapi import APIRouter

router = APIRouter()
import requests
import json
from ollama import chat


@router.get("/test")
def test_route():
  

    url = "http://localhost:11434/api/generate"

    stream = chat(
        model='qwen3',
        messages=[{'role': 'user', 'content': 'What is 17 × 23?'}],
        stream=True,
        )

    with requests.post(url, json=payload, stream=True) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line:
                data = json.loads(line)
                print(data.get("response", ""), end="", flush=True)

    return {"message": "Test route is working!"}