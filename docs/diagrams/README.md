# Diagrams

PlantUML sources (`*.puml`), versioned with the code, and the pictures made from them (`svg/`).

| Diagram | File | What is compared with the code |
|---|---|---|
| Use cases of the three roles and of a deployed service | `use-case.puml` | the file is well formed |
| Context: the core and what is outside it | `context.puml` | the file is well formed |
| Classes: the main classes of `model`, `compiler`, `engine`, `actions`, `repo`, `service`, `api` | `class.puml` | every class, field and operation exists (`tests/architecture/test_diagrams.py`) |
| Components: the packages and the imports between them | `component.puml` | the arrows are exactly the real imports between packages, all point down the layers |
| Deployment: the core, a service, their keys | `deployment.puml` | the modules that are started exist |

Pictures are committed so that the report can show them. To make them again after a change of a
source: `python scripts/render_diagrams.py`. It needs Java, Graphviz and PlantUML (`PLANTUML_JAR`, or
a `plantuml` command on the PATH); `python scripts/render_diagrams.py --check` says which pictures
are missing. Where PlantUML is not installed, the test that renders every source is skipped; the
other tests do not need it.

## Behaviour (DOC-9)

| Diagram | Files | What is compared with the code |
|---|---|---|
| States of a step | `step-states.puml` | exactly the values of `StepState`; every move the scheduler makes in the scenarios of `tests/unit/engine/test_state_diagrams.py` is an arrow, and every arrow is made by a scenario |
| Status of a run | `run-states.puml` | exactly the statuses that `engine/scheduler.py` and `model/history.py` return (read from the source, there is no enumeration); the same two-way check of moves |
| Ownership of a run | `ownership-states.puml` | exactly the values of `OwnerState`; every arrow is made by a call of the stores (`tests/unit/repo/test_ownership_diagram.py`) |
| Activity and sequence of C1 to C6 | `c1-activity.puml`, `c1-sequence.puml`, ... `c6-sequence.puml` | each names the section of its algorithm card (`' Card:` line, the section must exist) and the functions it draws (`' code:` line, they must exist); the activity diagram has every procedure that the pseudocode of the card defines (`tests/architecture/test_behaviour_diagrams.py`) |

A diagram of a C function is drawn from its card and the code together, in the story that changes
the function, and the tests above fail until the diagram agrees.
