"""PromptManager — registry for prompts and completions."""

from __future__ import annotations

import inspect
from collections.abc import Callable

from fastermcp.prompts.prompt import RegisteredCompletion, RegisteredPrompt


class PromptManager:
    """Owns the prompt and completion registries."""

    def __init__(self) -> None:
        self._prompts: dict[str, RegisteredPrompt] = {}
        self._completions: dict[str, RegisteredCompletion] = {}

    def prompt(self, *, description: str | None = None) -> Callable[[Callable], Callable]:
        def decorator(fn: Callable) -> Callable:
            desc = description or (fn.__doc__ or "").strip()
            sig = inspect.signature(fn)
            arguments = [
                {
                    "name": p_name,
                    "description": "",
                    "required": p.default is inspect.Parameter.empty,
                }
                for p_name, p in sig.parameters.items()
            ]
            self._prompts[fn.__name__] = RegisteredPrompt(
                name=fn.__name__,
                description=desc,
                arguments=arguments,
                handler=fn,
            )
            return fn

        return decorator

    def completion(self, ref_name: str) -> Callable:
        def decorator(fn: Callable) -> Callable:
            self._completions[ref_name] = RegisteredCompletion(
                ref_name=ref_name,
                handler=fn,
            )
            return fn

        return decorator

    def list_registered_prompts(self) -> list[RegisteredPrompt]:
        return list(self._prompts.values())
