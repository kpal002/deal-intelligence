"""Document processing pipeline: ingest -> parse -> classify -> extract -> resolve.

Each stage is a focused module that consumes and produces Pydantic contracts
from :mod:`dealintel.models`. :mod:`dealintel.pipeline.runner` wires them together
into a single end-to-end call and writes the audit trail.
"""
