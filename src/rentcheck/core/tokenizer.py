"""Token counting behind a small interface.

Counts are an *approximation* of what any given agent's model will actually
tokenize; they use the ``cl100k_base`` encoding by default. The
:class:`TokenCounter` protocol lets other tokenizers be added later without
touching callers.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

import tiktoken


class TokenCounter(Protocol):
    """Something that can count the tokens in a piece of text."""

    #: Human-readable name of the tokenizer, shown in output.
    name: str

    def count(self, text: str) -> int:
        """Return the approximate number of tokens in ``text``."""
        ...


class TiktokenCounter:
    """A :class:`TokenCounter` backed by a tiktoken encoding.

    Uses ``cl100k_base`` by default, which approximates the tokenization of
    most current chat models closely enough for a cost estimate.
    """

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self.name = encoding_name
        self._encoding = _get_encoding(encoding_name)

    def count(self, text: str) -> int:
        """Return the number of ``cl100k_base`` tokens in ``text``."""
        if not text:
            return 0
        return len(self._encoding.encode(text, disallowed_special=()))


@lru_cache(maxsize=4)
def _get_encoding(encoding_name: str) -> tiktoken.Encoding:
    """Return a cached tiktoken encoding by name."""
    return tiktoken.get_encoding(encoding_name)


__all__ = ["TiktokenCounter", "TokenCounter"]
