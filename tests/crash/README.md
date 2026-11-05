# tests/crash

Crash and fault injection.

`test_every_event.py`: the engine dies after every event of a run (the nine-region example approved
and rejected, a saga with parallel undos, a failing undo, random sagas, two crashes in a row), is
restored from the cut history and finished; the result, the keys of all calls and the order of the
undos are compared with the uninterrupted run (NFR-02, US-15, US-16).

Lost acknowledgements and a server that drops responses (US-17) come with the fault-injecting
server in `tests/support/`.
