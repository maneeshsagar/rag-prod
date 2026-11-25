"""
Claude, through the official Anthropic SDK.

- Structured output: `messages.parse(output_format=<Pydantic model>)`
- Streaming: `messages.stream(...).text_stream`
- Thinking is adaptive by default on current models; `effort` controls depth.
- If Claude declines (refusal), the server-side fallback (`fallbacks="default"`)
  re-runs the request on a fallback model inside the same call.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import anthropic

from app.providers.llm_base import ModelRefusal, ProviderUnavailable, T

FALLBACK_BETA = "server-side-fallback-2026-07-01"


@contextmanager
def _translate_errors():
    """Turn SDK errors that mean "try another provider" into ProviderUnavailable."""
    try:
        yield
    except (anthropic.APIConnectionError, anthropic.RateLimitError,
            anthropic.AuthenticationError, anthropic.PermissionDeniedError) as error:
        raise ProviderUnavailable(f"anthropic: {error}") from error
    except anthropic.APIStatusError as error:
        if error.status_code >= 500:  # includes 529 overloaded
            raise ProviderUnavailable(f"anthropic: {error}") from error
        raise


class AnthropicLLM:
    def __init__(self, api_key: str, refusal_fallback: bool = True) -> None:
        if not api_key:
            raise ProviderUnavailable("ANTHROPIC_API_KEY is not set")
        # The SDK already retries connection errors, 429 and 5xx (with backoff).
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=120)
        self.refusal_fallback = refusal_fallback

    def complete_json(self, model: str, system: str, user: str, schema: type[T],
                      max_tokens: int = 4000, effort: str = "low") -> T:
        # `effort` is not sent here: Claude's default suits these short structured tasks.
        with _translate_errors():
            response = self.client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
        if response.stop_reason == "refusal":
            raise ModelRefusal("Claude declined this request")
        return response.parsed_output

    def stream_text(self, model: str, system: str, messages: list[dict],
                    max_tokens: int = 8000, effort: str = "medium") -> Iterator[str]:
        request = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "output_config": {"effort": effort},
        }
        with _translate_errors():
            if self.refusal_fallback:
                stream_context = self.client.beta.messages.stream(
                    **request, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                stream_context = self.client.messages.stream(**request)

            with stream_context as stream:
                yield from stream.text_stream
                final = stream.get_final_message()

        if final.stop_reason == "refusal":
            raise ModelRefusal("Claude declined this request")
