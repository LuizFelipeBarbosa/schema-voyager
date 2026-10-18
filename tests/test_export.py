import json

from schema_voyager.demo import main
from schema_voyager.export import export_run


def test_export_copies_a_run_and_writes_the_field_guide_data(tmp_path):
    run_dir = tmp_path / "run"
    main(["demo", "--backend", "sim", "--llm", "fake", "--systems", "D,B", "--run-dir", str(run_dir)])

    site_data = tmp_path / "results.js"
    export_run(run_dir, note="Layout 8 was rerun.", out_dir=tmp_path / "results", site_data=site_data)

    assert (tmp_path / "results" / "episodes" / "D_h3_L6.jsonl").exists()
    assert (tmp_path / "results" / "store_D_2_after_training.json").exists()
    assert "Layout 8 was rerun." in (tmp_path / "results" / "README.md").read_text()
    data = json.loads(site_data.read_text().split(" = ", 1)[1].rstrip().rstrip(";"))
    holdout = {row["system"]: row for row in data["rows"] if row["task"] == "h3"}
    assert holdout["D"]["successes"] == 3 and holdout["B"]["successes"] == 0
    assert data["rule"]["id"] == "mine_coal_ore" and data["rule"]["status"] == "certified"
    assert data["skill"]["status"] == "flagged" and data["skill"]["flagged_by"] == "mine_coal_ore"
    assert data["trace"]["layout"] == 6
