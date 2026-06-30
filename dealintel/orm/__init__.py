"""SQLAlchemy ORM layer — persistence shapes, kept separate from Pydantic.

Pydantic models (``dealintel.models``) are the data contracts passed between
layers; the ORM tables here are how those contracts are stored. Keeping them
separate means storage concerns (indexes, JSONB columns, FKs) never leak into
the data contracts, and either side can evolve independently.
"""

from dealintel.orm.tables import (
    AuditLogORM,
    Base,
    CanonicalEntityORM,
    ChunkORM,
    FactORM,
    MandateORM,
    RawDocumentORM,
    ScoreResultORM,
)

__all__ = [
    "Base",
    "CanonicalEntityORM",
    "ChunkORM",
    "FactORM",
    "MandateORM",
    "RawDocumentORM",
    "ScoreResultORM",
    "AuditLogORM",
]
