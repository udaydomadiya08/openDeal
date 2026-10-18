"""OpenDeal entities package."""

from opendeal.entities.models import (
    CompanyEntity,
    EntityRelationship,
    EntityResolutionQuery,
    EntityResolutionResult,
    LegalIdentifier,
    RelationshipType,
    ResolutionOutcome,
)
from opendeal.entities.resolution import EntityResolver, normalize_corporate_name

__all__ = [
    "CompanyEntity",
    "LegalIdentifier",
    "EntityRelationship",
    "RelationshipType",
    "ResolutionOutcome",
    "EntityResolutionQuery",
    "EntityResolutionResult",
    "EntityResolver",
    "normalize_corporate_name",
]
