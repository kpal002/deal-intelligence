"""Entity resolution: map name variants to canonical entities within a deal.

Resolves by a normalized match key (lowercased, punctuation and common corporate
suffixes stripped) and accumulates observed variants as aliases. The interface
is a callable ``(raw_name, claim_type) -> entity_id``; a fuzzy or embedding-based
resolver would implement the same signature without caller changes.
"""

from __future__ import annotations

import logging
import re
import uuid

from dealintel.models.fact import CanonicalEntity, ClaimType

logger = logging.getLogger(__name__)

#: Common company suffixes stripped when computing a match key.
_SUFFIXES = {"inc", "inc.", "corp", "corp.", "corporation", "llc", "ltd", "co", "co."}


def _match_key(name: str) -> str:
    """Compute a normalized key for entity matching.

    Lowercases, strips punctuation and common corporate suffixes, and collapses
    whitespace so ``"Acme Corp."``, ``"ACME Corporation"``, and ``"Acme  inc"``
    all share a key.

    Args:
        name: A raw entity name.

    Returns:
        The normalized match key.
    """
    cleaned = re.sub(r"[^\w\s]", " ", name.lower())
    tokens = [t for t in cleaned.split() if t not in _SUFFIXES]
    return " ".join(tokens)


def _infer_entity_type(claim_type: ClaimType) -> str:
    """Heuristically infer an entity type from the claim it appears in.

    Args:
        claim_type: The claim type the entity was mentioned in.

    Returns:
        ``"person"`` for team claims, ``"market"`` for market-size claims,
        else ``"company"``. A scaffold heuristic, refined in a later phase.
    """
    if claim_type == ClaimType.TEAM_BACKGROUND:
        return "person"
    if claim_type == ClaimType.MARKET_SIZE:
        return "market"
    return "company"


class EntityResolver:
    """In-memory entity resolver scoped to a single deal.

    Maintains a map from match key to :class:`CanonicalEntity`, creating a new
    canonical entity on first sight and registering aliases thereafter. Designed
    to be passed as the ``entity_resolver`` callable to the extraction stage.

    Args:
        deal_id: The deal these entities belong to.
    """

    def __init__(self, deal_id: uuid.UUID) -> None:
        self._deal_id = deal_id
        self._by_key: dict[str, CanonicalEntity] = {}

    def __call__(self, raw_name: str, claim_type: ClaimType) -> uuid.UUID:
        """Resolve a raw entity name to a canonical entity_id (callable form).

        Args:
            raw_name: The entity name as it appeared in the text.
            claim_type: The claim the name appeared in (informs entity_type).

        Returns:
            The canonical ``entity_id`` for this name within the deal.
        """
        return self.resolve(raw_name, claim_type).entity_id

    def resolve(self, raw_name: str, claim_type: ClaimType) -> CanonicalEntity:
        """Resolve a raw name to a canonical entity, creating or aliasing it.

        Args:
            raw_name: The entity name as it appeared in the text.
            claim_type: The claim the name appeared in (informs entity_type).

        Returns:
            The matched or newly-created :class:`CanonicalEntity`.
        """
        key = _match_key(raw_name) or "unknown"
        existing = self._by_key.get(key)
        if existing is not None:
            if raw_name not in existing.aliases and raw_name != existing.canonical_name:
                existing.aliases.append(raw_name)
            return existing

        entity = CanonicalEntity(
            deal_id=self._deal_id,
            canonical_name=raw_name.strip(),
            entity_type=_infer_entity_type(claim_type),
            aliases=[],
        )
        self._by_key[key] = entity
        logger.debug("Created canonical entity %r (key=%r)", raw_name, key)
        return entity

    def all_entities(self) -> list[CanonicalEntity]:
        """Return every canonical entity created during this deal's resolution.

        Returns:
            All canonical entities, suitable for persistence after extraction.
        """
        return list(self._by_key.values())
