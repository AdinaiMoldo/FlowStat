"""B5 packaging a workflow as a service. Layer 8.

``package.py`` makes the bundle of a published version (a frozen copy, hashes, a Dockerfile from a
Jinja2 template); ``launchers.py`` starts it as a process or as a container. This is the only
place in the code base that touches the operating system (NFR-07). The service itself is the app
``flowstat.api.service``; this package starts it by module name and does not import it.
"""
