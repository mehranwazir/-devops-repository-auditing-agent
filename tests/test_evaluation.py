from pathlib import Path

from evaluate import create_fixture, scenarios
from tools import RepositoryTools


def test_regression_fixtures_are_committed_without_executing_target_code(tmp_path):
    cases = scenarios()
    root = tmp_path / "obvious"
    create_fixture(root, cases["obvious"][0])
    tools = RepositoryTools(root)
    result = tools.read_file(".github/workflows/ci.yml")
    assert any("continue-on-error: true" in line["text"] for line in result["lines"])
    assert len(cases["obvious"][1]) == 2
    assert cases["healthy"][1] == []
    assert not (Path(root) / "dist").exists()
