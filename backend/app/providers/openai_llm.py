"""
OpenAI models, through the official OpenAI SDK (Chat Completions).

- Structured output: `chat.completions.parse(response_format=<Pydantic model>)`
- Streaming: `chat.completions.create(stream=True)`
- `reasoning_effort` (gpt-5 family) follows the effort setting: low for quick tasks.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import openai

from app.providers.llm_base import ModelRefusal, ProviderUnavailable, T


@contextmanager
def _translate_errors():
    try:
        yield
    except (openai.APIConnectionError, openai.RateLimitError,
            openai.AuthenticationError, openai.PermissionDeniedError) as error:
        raise ProviderUnavailable(f"openai: {error}") from error
    except openai.APIStatusError as error:
        if error.status_code >= 500:
            raise ProviderUnavailable(f"openai: {error}") from error
        raise


class OpenAILLM:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ProviderUnavailable("OPENAI_API_KEY is not set")
        self.client = openai.OpenAI(api_key=api_key, max_retries=2, timeout=120)

    def complete_json(self, model: str, system: str, user: str, schema: type[T],
                      max_tokens: int = 4000, effort: str = "low") -> T:
        with _translate_errors():
            response = self.client.chat.completions.parse(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                response_format=schema,
                max_completion_tokens=max_tokens,
                reasoning_effort=effort,
            )
        message = response.choices[0].message
        if message.refusal or message.parsed is None:
            raise ModelRefusal(message.refusal or "no structured output returned")
        return message.parsed

    def stream_text(self, model: str, system: str, messages: list[dict],
                    max_tokens: int = 8000, effort: str = "medium") -> Iterator[str]:
        with _translate_errors():
            stream = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, *messages],
                max_completion_tokens=max_tokens,
                reasoning_effort=effort,
                stream=True,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
