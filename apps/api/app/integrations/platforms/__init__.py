"""Public collector contract. All returned text is untrusted source content."""

from app.integrations.platforms.base import CollectedContent, CollectionError
from app.integrations.platforms.registry import capabilities, collect, detect_platform

CollectorError = CollectionError

__all__ = [
    "CollectedContent",
    "CollectionError",
    "CollectorError",
    "capabilities",
    "collect",
    "detect_platform",
]
