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

The behaviour diagrams (state machines of a step, a run and an owner; an activity and a sequence
diagram for each of C1 to C6) are DOC-9.
