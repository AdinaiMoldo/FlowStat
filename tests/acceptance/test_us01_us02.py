"""Acceptance tests of US-01 (versions that never change) and US-02 (catalogue and its snapshot)."""

from __future__ import annotations

from pathlib import Path

import pytest

from flowstat.model.versions import VersionStatus
from flowstat.repo.db import open_database
from flowstat.service.workflows import PublishError, WorkflowService

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = (ROOT / "examples" / "quarterly_consolidation.yaml").read_text(encoding="utf8")
REGIONS = ["BIS", "OSC", "BAT", "JAL", "ISK", "NAR", "OSH", "TAL", "CHU"]
NAME = "quarterly_consolidation"


def text_for(version: int) -> str:
    assert "version: 1\n" in EXAMPLE
    return EXAMPLE.replace("version: 1\n", f"version: {version}\n")


def service() -> WorkflowService:
    svc = WorkflowService(open_database(":memory:"))
    svc.set_catalogue_list("regions", [{"code": c, "name": c.title()} for c in REGIONS])
    return svc


def test_save_returns_diagnostics_but_keeps_the_draft() -> None:
    svc = service()
    saved = svc.save_draft(NAME, "workflow: quarterly_consolidation\nversion: 1\nsteps: [\n", "t1")
    assert saved.version.status is VersionStatus.DRAFT and saved.version.number == 1
    assert [d.code for d in saved.diagnostics] == ["E-YAML-001"]


def test_save_a_clean_draft_has_no_diagnostics() -> None:
    saved = service().save_draft(NAME, text_for(1), "t1")
    assert saved.diagnostics == []


def test_us01_published_versions_never_change() -> None:
    svc = service()
    svc.save_draft(NAME, text_for(1), "t1")
    v1 = svc.publish(NAME, 1, "t2")
    assert v1.status is VersionStatus.PUBLISHED and v1.published_at == "t2"
    # saving after a publication starts a new draft; v1 is untouched
    saved = svc.save_draft(NAME, text_for(2) + "# edited\n", "t3")
    assert saved.version.number == 2 and saved.version.status is VersionStatus.DRAFT
    assert svc.get(NAME, 1) == v1
    v2 = svc.publish(NAME, 2, "t4")
    assert [v.number for v in svc.versions(NAME)] == [1, 2]
    assert svc.get(NAME, 2) == v2 and svc.get(NAME, 1).text == text_for(1)


def test_publish_refuses_a_document_with_errors() -> None:
    svc = service()
    svc.save_draft(NAME, text_for(1).replace("return_to_regions]}", "nowhere]}"), "t1")
    with pytest.raises(PublishError) as error:
        svc.publish(NAME, 1, "t2")
    assert [d.code for d in error.value.diagnostics] == ["E-REF-001"]
    assert svc.get(NAME, 1).status is VersionStatus.DRAFT


def test_publish_requires_the_version_number_in_the_text() -> None:
    svc = service()
    svc.save_draft(NAME, text_for(3), "t1")  # the draft is version 1
    with pytest.raises(PublishError) as error:
        svc.publish(NAME, 1, "t2")
    assert [d.code for d in error.value.diagnostics] == ["E-VER-001"]
    assert error.value.diagnostics[0].line == 3


def test_publish_requires_the_name_of_the_workflow() -> None:
    svc = service()
    svc.save_draft("other_name", text_for(1), "t1")
    with pytest.raises(PublishError) as error:
        svc.publish("other_name", 1, "t2")
    assert [d.code for d in error.value.diagnostics] == ["E-VER-002"]
    assert error.value.diagnostics[0].line == 2


def test_publish_without_a_draft_fails() -> None:
    from flowstat.repo.versions import VersionNotFoundError

    with pytest.raises(VersionNotFoundError):
        service().publish(NAME, 1, "t1")


def test_us02_input_accepts_only_catalogue_codes() -> None:
    svc = service()
    text = text_for(1).replace(
        "inputs:\n", "inputs:\n  region: {type: string, ref: regions, required: false}\n", 1
    )
    svc.save_draft(NAME, text, "t1")
    svc.publish(NAME, 1, "t2")
    values, errors = svc.validate_inputs(NAME, 1, {"quarter": "2025Q1", "region": "BIS"})
    assert errors == [] and values["region"] == "BIS"
    _, errors = svc.validate_inputs(NAME, 1, {"quarter": "2025Q1", "region": "MOSCOW"})
    assert [e.code for e in errors] == ["E-INPUT-004"]


def test_us02_version_keeps_a_snapshot_of_the_catalogue() -> None:
    svc = service()
    svc.save_draft(NAME, text_for(1), "t1")
    svc.publish(NAME, 1, "t2")
    assert len(svc.get_plan(NAME, 1).instances_of("load")) == 9
    # the live catalogue changes later: a new region and a closed one
    svc.set_catalogue_list("regions", [{"code": c, "name": c} for c in [*REGIONS[:-1], "NEW"]])
    plan = svc.get_plan(NAME, 1)
    assert plan.instances_of("load") == tuple(f"load[{c}]" for c in REGIONS)
    # a new draft sees the live catalogue
    svc.save_draft(NAME, text_for(2), "t3")
    svc.publish(NAME, 2, "t4")
    assert "load[NEW]" in svc.get_plan(NAME, 2).instances_of("load")
    assert "load[CHU]" not in svc.get_plan(NAME, 2).instances_of("load")
    assert "load[CHU]" in svc.get_plan(NAME, 1).instances_of("load")


def test_input_check_uses_the_snapshot_of_the_version() -> None:
    svc = service()
    text = text_for(1).replace("inputs:\n", "inputs:\n  region: {type: string, ref: regions}\n", 1)
    svc.save_draft(NAME, text, "t1")
    svc.publish(NAME, 1, "t2")
    svc.set_catalogue_list("regions", [{"code": "NEW"}])
    _, errors = svc.validate_inputs(NAME, 1, {"quarter": "2025Q1", "region": "NEW"})
    assert [e.code for e in errors] == ["E-INPUT-004"]


def test_save_with_a_missing_catalogue_list_reports_it() -> None:
    svc = WorkflowService(open_database(":memory:"))
    saved = svc.save_draft(NAME, text_for(1), "t1")
    assert {d.code for d in saved.diagnostics} == {"E-REF-012"}


def test_a_draft_can_be_compiled_for_preview() -> None:
    svc = service()
    svc.save_draft(NAME, text_for(1), "t1")
    assert len(svc.get_plan(NAME, 1).steps) == 9 + 9 + 4
    assert svc.get_plan(NAME, 1).name == NAME


def test_everything_survives_a_restart(tmp_path: Path) -> None:
    path = str(tmp_path / "flowstat.db")
    first = WorkflowService(open_database(path))
    first.set_catalogue_list("regions", [{"code": c} for c in REGIONS])
    first.save_draft(NAME, text_for(1), "t1")
    first.publish(NAME, 1, "t2")
    second = WorkflowService(open_database(path))
    assert second.get(NAME, 1).status is VersionStatus.PUBLISHED
    assert len(second.get_plan(NAME, 1).instances_of("check")) == 9
