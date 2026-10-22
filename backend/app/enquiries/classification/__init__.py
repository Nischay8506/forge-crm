"""Local email classification, independent of LLM extraction."""

from app.enquiries.classification.model import Decision, classify

__all__ = ["Decision", "classify"]
