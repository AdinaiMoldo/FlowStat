"""The executable of the core, layer 9: it joins the API (``api``) and the launchers (``deploy``).

The two packages on layer 8 may not import each other, so the one place that knows both is above
them. It holds no logic of its own; ``python -m flowstat.serve`` is the command that starts the
server.
"""
