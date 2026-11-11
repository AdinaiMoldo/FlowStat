# Test report

Made by `scripts/test_report.py` from the output of one run of the suite (2026-11-12, commit `2b85ef9`). The run is `python -m pytest --cov --junitxml=junit.xml`; nothing here was written by hand.

## Totals

| Passed | Failed | Skipped | Total |
| ---: | ---: | ---: | ---: |
| 2277 | 0 | 17 | 2294 |

## By area

| Area | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| acceptance | 115 | 0 | 1 |
| architecture | 73 | 0 | 1 |
| browser | 22 | 0 | 15 |
| crash | 202 | 0 | 0 |
| reference | 41 | 0 | 0 |
| unit | 1 | 0 | 0 |
| unit/actions | 67 | 0 | 0 |
| unit/analysis | 218 | 0 | 0 |
| unit/api | 196 | 0 | 0 |
| unit/compiler | 113 | 0 | 0 |
| unit/deploy | 24 | 0 | 0 |
| unit/dsl | 210 | 0 | 0 |
| unit/engine | 333 | 0 | 0 |
| unit/migration | 47 | 0 | 0 |
| unit/model | 50 | 0 | 0 |
| unit/recovery | 52 | 0 | 0 |
| unit/repo | 84 | 0 | 0 |
| unit/scripts | 36 | 0 | 0 |
| unit/serve | 8 | 0 | 0 |
| unit/service | 193 | 0 | 0 |
| unit/solver | 192 | 0 | 0 |

## Skipped

| Test | Reason |
| --- | --- |
| `tests.acceptance.test_us30::test_a_service_started_as_a_container_answers_and_matches_the_core` | no Docker daemon here: Error while fetching server API version: ('Connection aborted.', FileNotFoundError(2, 'No such file or directory')) |
| `tests.architecture.test_diagrams::test_every_source_renders_without_an_error` | PlantUML is not installed (PLANTUML_JAR) |
| `tests.browser.test_completion::test_nothing_is_suggested_outside_a_reference` | the editor was not fetched: python scripts/fetch_ui.py |
| `tests.browser.test_completion::test_only_what_is_available_at_the_cursor_is_suggested` | the editor was not fetched: python scripts/fetch_ui.py |
| `tests.browser.test_completion::test_the_list_follows_the_step_the_cursor_is_in` | the editor was not fetched: python scripts/fetch_ui.py |
| `tests.browser.test_completion::test_typing_the_opening_of_a_reference_opens_the_list_and_a_choice_is_written` | the editor was not fetched: python scripts/fetch_ui.py |
| `tests.browser.test_editor::test_a_new_workflow_is_saved_and_published` | the editor was not fetched: python scripts/fetch_ui.py |
| `tests.browser.test_editor::test_an_error_gets_a_marker_at_its_position_after_a_pause_of_500_ms` | the editor was not fetched: python scripts/fetch_ui.py |
| `tests.browser.test_editor::test_an_operator_is_told_that_the_token_may_not_edit` | the editor was not fetched: python scripts/fetch_ui.py |
| `tests.browser.test_editor::test_fixing_the_error_removes_the_marker` | the editor was not fetched: python scripts/fetch_ui.py |
| `tests.browser.test_editor::test_the_editor_is_loaded` | the editor was not fetched: python scripts/fetch_ui.py |
| `tests.browser.test_graph::test_a_click_on_a_step_moves_the_cursor_to_it` | the editor was not fetched: python scripts/fetch_ui.py |
| `tests.browser.test_graph::test_a_fan_out_collapses_and_opens_on_a_click` | the editor was not fetched: python scripts/fetch_ui.py |
| `tests.browser.test_graph::test_a_large_fan_out_starts_collapsed_and_is_drawn_quickly` | the editor was not fetched: python scripts/fetch_ui.py |
| `tests.browser.test_graph::test_every_step_instance_is_a_node_and_every_dependency_an_arrow` | the editor was not fetched: python scripts/fetch_ui.py |
| `tests.browser.test_graph::test_the_graph_follows_the_text_and_keeps_the_last_good_one_on_an_error` | the editor was not fetched: python scripts/fetch_ui.py |
| `tests.browser.test_graph::test_the_graph_is_not_on_the_page_until_it_is_asked_for` | the editor was not fetched: python scripts/fetch_ui.py |

## Coverage by package

Statements and branches together. The gate is 90% for each package marked C1-C6 (`scripts/check_coverage.py`, NFR-09).

| Package | Coverage | Gate |
| --- | ---: | --- |
| actions | 97.4% | - |
| analysis | 92.2% | passes |
| api | 96.0% | - |
| compiler | 98.4% | passes |
| deploy | 98.4% | - |
| dsl | 99.4% | passes |
| engine | 98.9% | passes |
| migration | 90.8% | passes |
| model | 99.4% | passes |
| recovery | 100.0% | passes |
| repo | 97.3% | - |
| serve | 95.5% | - |
| service | 95.7% | - |
| solver | 96.7% | passes |
