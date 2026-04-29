"""VellumMem exception hierarchy.

All VellumMem-specific exceptions inherit from VellumMemError.
"""


class VellumMemError(Exception):
    """Base exception for all VellumMem errors."""


class StoreError(VellumMemError):
    """Store-level errors (invalid input, missing data, etc.)."""


class VectorError(VellumMemError):
    """Vector search engine errors."""


class InitError(VellumMemError):
    """Initialization failures."""
