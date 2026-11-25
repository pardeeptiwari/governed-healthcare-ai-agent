"""HTTP adapter for an OpenAI-compatible classifier endpoint."""

import httpx

from .ports import ModelCompletion


class ChatCompletionsProvider:
    def __init__(self, client: httpx.AsyncClient, url: str, model: str, key: str = ""):
        self.client = client
        self.url = url
        self.model = model
        self.key = key

    async def complete(self, *, instruction: str, projection: str, max_tokens: int) -> ModelCompletion:
        response = await self.client.post(
            self.url,
            headers={"Authorization": f"Bearer {self.key}"} if self.key else {},
            json={
                "model": self.model, "temperature": 0, "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": projection},
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
        usage = payload.get("usage") or {}
        return ModelCompletion(
            content=payload["choices"][0]["message"]["content"],
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
