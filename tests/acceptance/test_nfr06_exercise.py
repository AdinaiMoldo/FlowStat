"""The reference solution of the usability exercise is a valid five-step workflow (NFR-06, DOC-7).

The exercise itself (a colleague writes the workflow in 30 minutes) cannot be run by the author
alone and its result is not in the repository. What can be checked is that the task has a
solution: the one in ``examples/exercise_one_region.yaml`` is saved without a diagnostic, is
published, and both of its answers end with the goal reached.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import make_env

ROOT = Path(__file__).resolve().parents[2]
SOLUTION = (ROOT / "examples" / "exercise_one_region.yaml").read_text(encoding="utf8")
NAME = "one_region_publication"


def run_with(tmp_path: Path, decision: str) -> dict[str, object]:
    env = make_env(tmp_path)
    saved = env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": SOLUTION})
    assert saved.status_code == 201, saved.text
    assert saved.json()["diagnostics"] == []  # no error, no warning
    published = env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    assert published.status_code == 200, published.text
    write_all_forms(env.config.data_dir, "2025Q1")
    started = env.call(
        "operator", "POST", f"/workflows/{NAME}/runs", json={"inputs": {"quarter": "2025Q1"}}
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    answer = {"step": "approve", "decision": decision}
    assert env.call("operator", "POST", f"/runs/{run_id}/decisions", json=answer).status_code == 202
    report: dict[str, object] = env.call("operator", "GET", f"/runs/{run_id}").json()
    return report


def test_the_solution_has_exactly_five_steps() -> None:
    names = [
        s.strip().rstrip(":")
        for s in SOLUTION.split("steps:\n", 1)[1].split("goals:", 1)[0].splitlines()
        if s.startswith("  ") and not s.startswith("   ")
    ]
    assert names == ["load", "check", "approve", "publish", "return_to_region"]


def test_an_approval_publishes_and_the_goal_is_reached(tmp_path: Path) -> None:
    report = run_with(tmp_path, "approved")
    assert report["outcome"] == "SUCCEEDED"
    states = {s["step"]: s["state"] for s in report["steps"]}  # type: ignore[attr-defined]
    assert (states["publish"], states["return_to_region"]) == ("SUCCEEDED", "SKIPPED")


def test_a_rejection_returns_the_form_and_the_goal_is_reached(tmp_path: Path) -> None:
    report = run_with(tmp_path, "rejected")
    assert report["outcome"] == "SUCCEEDED"
    states = {s["step"]: s["state"] for s in report["steps"]}  # type: ignore[attr-defined]
    assert (states["publish"], states["return_to_region"]) == ("SKIPPED", "SUCCEEDED")
