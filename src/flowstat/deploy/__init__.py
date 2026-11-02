"""B5 packaging a workflow as a service. Layer 8.

Jinja2 service template, Docker SDK, fallback to a separate uvicorn process. The generated
service embeds the engine and its own write-ahead log and ships events to the core by outbox.

Built in I5.
"""
