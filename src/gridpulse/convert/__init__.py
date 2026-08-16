"""The conversion layer: pure functions from bronze payloads to clean records.

Phase 1 ships the demand path (``pipeline.convert_demand``); the full suite
(quality flags, dedupe survivorship module, derived fields, swappable
backends) lands in Phase 2 per the build plan.
"""
