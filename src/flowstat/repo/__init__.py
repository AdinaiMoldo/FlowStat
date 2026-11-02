"""A1-A6 persistence. Layer 2.

SQLite repository: immutable workflow versions (A1), event history (A2), roles (A3),
catalogue (A4), statistics (A5), service registry and event ingest (A6).
Imports only model. Never imports the engine.

Built from I1 (versions, catalogue) onwards.
"""
