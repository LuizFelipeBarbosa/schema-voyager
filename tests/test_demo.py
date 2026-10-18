import json

from schema_voyager.demo import main
from schema_voyager.export import export_run

SIM = ["--backend", "sim", "--llm", "fake", "--systems", "D,B,B+"]


def _results(run_dir):
    return {r["episode_id"]: r for r in json.loads((run_dir / "results.json").read_text())}


def test_demo_from_a_trained_run_reruns_only_the_holdout_and_revisits_the_first_world(tmp_path):
    trained, rerun = tmp_path / "trained", tmp_path / "rerun"
    main(["demo", *SIM, "--run-dir", str(trained)])
    main(["demo", *SIM, "--from", str(trained), "--second-visit", "--run-dir", str(rerun)])

    before, after = _results(trained), _results(rerun)
    for episode_id, result in before.items():
        if result["task_id"] != "h3":
            assert after[episode_id] == result, episode_id
    trace = "episodes/B+_t3_L2.jsonl"
    assert (rerun / trace).read_bytes() == (trained / trace).read_bytes()
    assert (rerun / "store_D_2_after_training.json").read_bytes() == (trained / "store_D_2_after_training.json").read_bytes()
    assert (rerun / "TRAINED_IN").read_text().split()[0] == "trained"

    holdout = [r for r in after.values() if r["task_id"] == "h3" and not r["episode_id"].endswith("visit2")]
    assert sorted(r["episode_id"] for r in holdout) == sorted(
        f"{system}/h3/L{seed}" for system in ("D", "B", "B+") for seed in (6, 7, 8)
    )
    assert after["D/h3/L6"]["world_id"] != before["D/h3/L6"]["world_id"]

    visit = after["D/h3/L6/visit2"]
    assert visit["world_id"] == after["D/h3/L6"]["world_id"]
    assert visit["success"] and visit["contradictions"] == 0 and visit["llm_calls"] == 0
    assert (rerun / "store_D_4_after_visit2.json").exists()

    table = (rerun / "results.md").read_text()
    assert "| D | h3 | 3/3 |" in table and "| D | h3 visit2 | 1/1 |" in table


def test_export_says_where_reused_training_came_from(tmp_path):
    trained, rerun = tmp_path / "trained", tmp_path / "rerun"
    main(["demo", *SIM, "--run-dir", str(trained)])
    main(["demo", *SIM, "--from", str(trained), "--second-visit", "--run-dir", str(rerun)])
    site_data = tmp_path / "results.js"
    export_run(rerun, out_dir=tmp_path / "results", site_data=site_data)

    assert "come from `runs/trained`" in (tmp_path / "results" / "README.md").read_text()
    assert (tmp_path / "results" / "episodes" / "D_h3_L6_visit2.jsonl").exists()
    data = json.loads(site_data.read_text().split(" = ", 1)[1].rstrip().rstrip(";"))
    assert {row["task"] for row in data["rows"]} >= {"h3", "h3 visit2"}
    assert data["trace"]["layout"] == 6
