"""Tests of the app of a deployed service: one frozen version, one key, the core's run routes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from flowstat.api.service import create_service_app, main
from flowstat.service.bundle import CATALOGUE, WORKFLOW, BundleError, read_bundle
from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import NAME
from tests.unit.api.service_helpers import KEY, TITLE_KEY, ServiceEnv, make_service

INPUTS = {"inputs": {"quarter": "2025Q1"}}


def body(response: httpx2.Response) -> Any:
    return response.json()


@pytest.fixture
def svc(tmp_path: Path) -> ServiceEnv:
    env = make_service(tmp_path)
    write_all_forms(env.config.data_dir, "2025Q1")
    return env


def test_health_names_the_frozen_version_and_needs_no_key(svc: ServiceEnv) -> None:
    response = svc.call(None, "GET", "/health")
    assert response.status_code == 200
    assert body(response) == {"status": "ok", "workflow": NAME, "version": 4}


def test_the_key_is_needed_and_only_the_key_of_this_service_works(svc: ServiceEnv) -> None:
    assert svc.call(None, "GET", "/schema").status_code == 401
    assert svc.call("0" * 64, "GET", "/schema").status_code == 401
    assert svc.call(KEY, "GET", "/schema").status_code == 200


def test_the_schema_lists_the_inputs_and_the_hashes_of_the_frozen_files(svc: ServiceEnv) -> None:
    schema = body(svc.call(KEY, "GET", "/schema"))
    bundle = read_bundle(svc.bundle)
    assert schema["workflow"] == NAME and schema["version"] == 4
    [quarter] = schema["inputs"]
    assert quarter["name"] == "quarter" and quarter["type"] == "string" and quarter["required"]
    assert quarter["pattern"] == "^20[0-9]{2}Q[1-4]$"
    assert schema["manifest"]["text_sha256"] == bundle.manifest.text_sha256
    assert schema["manifest"]["catalogue_sha256"] == bundle.manifest.catalogue_sha256
    assert "api_key" not in str(schema) and bundle.manifest.api_key_sha256 not in str(schema)


def test_a_run_is_started_followed_and_approved_with_the_key_of_the_title(svc: ServiceEnv) -> None:
    started = svc.call(KEY, "POST", "/runs", json=INPUTS)
    assert started.status_code == 202
    assert body(started) == {
        "run_id": "sr-0001",
        "workflow": NAME,
        "version": 4,
        "status": "CREATED",
    }
    assert body(svc.call(KEY, "GET", "/runs/sr-0001"))["status"] == "WAITING"
    answer = {"step": "approve", "decision": "approved"}
    assert svc.call(TITLE_KEY, "POST", "/runs/sr-0001/decisions", json=answer).status_code == 202
    report = body(svc.call(KEY, "GET", "/runs/sr-0001"))
    assert report["status"] == "FINISHED" and report["outcome"] == "SUCCEEDED"
    assert [r["run_id"] for r in body(svc.call(KEY, "GET", "/runs"))] == ["sr-0001"]


def test_the_key_that_starts_runs_cannot_approve_but_the_key_of_the_title_can(
    svc: ServiceEnv,
) -> None:
    """DR-04 holds in a service too: starting a run and answering its approval are two keys."""
    svc.call(KEY, "POST", "/runs", json=INPUTS)
    answer = {"step": "approve", "decision": "approved"}
    refused = svc.call(KEY, "POST", "/runs/sr-0001/decisions", json=answer)
    assert refused.status_code == 403
    assert "chief_statistician" in body(refused)["errors"][0]["message"]
    assert body(svc.call(KEY, "GET", "/runs/sr-0001"))["status"] == "WAITING"
    done = svc.call(TITLE_KEY, "POST", "/runs/sr-0001/decisions", json=answer)
    assert done.status_code == 202 and body(done)["by"] == "approver.chief_statistician"


def test_invalid_inputs_are_422_with_a_field_error_as_in_the_core(svc: ServiceEnv) -> None:
    response = svc.call(KEY, "POST", "/runs", json={"inputs": {"quarter": "nope"}})
    assert response.status_code == 422
    assert body(response)["errors"][0]["field"] == "inputs.quarter"


def test_a_request_cannot_choose_another_version(svc: ServiceEnv) -> None:
    response = svc.call(KEY, "POST", "/runs", json=INPUTS | {"version": 1})
    assert response.status_code == 422 and body(response)["errors"][0]["code"] == "E-API-001"


def test_the_service_has_no_route_for_workflows_the_catalogue_or_deployments(
    svc: ServiceEnv,
) -> None:
    for method, path in (
        ("GET", "/workflows"),
        ("POST", "/validate"),
        ("PUT", "/catalogue/regions"),
        ("GET", "/deployments"),
        ("POST", f"/workflows/{NAME}/runs"),
    ):
        assert svc.call(KEY, method, path, json={}).status_code in (404, 405), (method, path)


def test_a_service_restarted_on_its_database_keeps_its_runs_and_its_key(tmp_path: Path) -> None:
    first = make_service(tmp_path)
    write_all_forms(first.config.data_dir, "2025Q1")
    first.call(KEY, "POST", "/runs", json=INPUTS)
    again = create_service_app(first.config, read_bundle(first.bundle))  # seeds a second time
    client = TestClient(again)
    headers = {"Authorization": f"Bearer {KEY}"}
    assert client.get("/runs", headers=headers).json()[0]["run_id"] == "sr-0001"


def test_a_database_with_another_text_under_the_number_is_refused(tmp_path: Path) -> None:
    env = make_service(tmp_path)
    bundle = read_bundle(env.bundle)
    other = tmp_path / "other"
    other.mkdir()
    (other / "service.db").write_bytes((env.bundle / "service.db").read_bytes())
    clash = type(bundle)(bundle.manifest, bundle.text + "# edited\n", bundle.catalogue, other)
    with pytest.raises(BundleError, match="another version"):
        create_service_app(replace(env.config, db_path=str(other / "service.db")), clash)


# -- the executable ---------------------------------------------------------------------------
def test_main_serves_a_good_bundle(tmp_path: Path) -> None:
    env = make_service(tmp_path)
    served: list[Any] = []
    code = main(
        ["--bundle", str(env.bundle), "--port", "9123"],
        serve=lambda app, host, port: served.append((app.title, host, port)),
    )
    assert code == 0 and served == [(f"FlowStat service: {NAME} v4", "127.0.0.1", 9123)]


@pytest.mark.parametrize("name", [WORKFLOW, CATALOGUE])
def test_main_refuses_a_bundle_in_which_a_file_changed(
    tmp_path: Path, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    env = make_service(tmp_path)
    path = env.bundle / name
    path.write_bytes(path.read_bytes() + b" ")
    served: list[Any] = []
    assert main(["--bundle", str(env.bundle)], serve=lambda *a: served.append(a)) == 2
    assert served == [] and f"{name} was changed" in capsys.readouterr().err


def test_main_refuses_a_directory_that_is_not_a_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--bundle", str(tmp_path)]) == 2
    assert "no manifest.json" in capsys.readouterr().err


def test_main_ships_only_when_the_environment_gives_the_address_the_key_and_the_name(
    tmp_path: Path,
) -> None:
    from flowstat.api.edge import Shipper
    from flowstat.api.service import shipper_from

    env = {
        "FLOWSTAT_INGEST_URL": "http://127.0.0.1:8000",
        "FLOWSTAT_INGEST_KEY": "k",
        "FLOWSTAT_DEPLOYMENT_ID": "d-1",
    }
    assert isinstance(shipper_from(env, str(tmp_path / "s.db")), Shipper)
    assert shipper_from({}, "s.db") is None
    for missing in env:
        assert shipper_from({k: v for k, v in env.items() if k != missing}, "s.db") is None


def test_main_with_a_shipper_that_cannot_reach_the_core_still_serves_and_ends(
    tmp_path: Path,
) -> None:
    env = make_service(tmp_path)
    order: list[str] = []
    settings = {
        "FLOWSTAT_INGEST_URL": "http://127.0.0.1:1",  # nobody listens: rounds fail, nothing breaks
        "FLOWSTAT_INGEST_KEY": "k",
        "FLOWSTAT_DEPLOYMENT_ID": "d-1",
    }
    code = main(
        ["--bundle", str(env.bundle), "--stop-limit", "5"],
        serve=lambda app, host, port: order.append("served"),
        env=settings,
    )
    assert code == 0 and order == ["served"]


def test_main_gives_the_service_the_list_of_hosts_that_the_environment_names(
    tmp_path: Path,
) -> None:
    env = make_service(tmp_path)
    found: list[Any] = []
    main(
        ["--bundle", str(env.bundle)],
        serve=lambda app, host, port: found.append(app.state.registry.signatures()["http.call"]),
        env={"FLOWSTAT_ALLOWED_HOSTS": "a.kg,b.kg"},
    )
    main(
        ["--bundle", str(env.bundle)],
        serve=lambda app, host, port: found.append(app.state.registry.signatures()["http.call"]),
        env={},
    )
    assert found[0].hosts == frozenset({"a.kg", "b.kg"}) and found[1].hosts is None
