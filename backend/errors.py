# backend/errors.py

class LLMConnectionError(RuntimeError):
    """Raised when the LLM service cannot be reached or returns an error."""
    pass
