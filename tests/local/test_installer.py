import json
import shutil
import subprocess
import pytest
from pathlib import Path


def make_repo(root: Path):
    (root / "vsd").mkdir(parents=True)
    (root / "pyproject.toml").write_text('''[project]\nname="visual-system-designer-app"\nrequires-python = ">=3.9"\n[project.scripts]\nvsd = "vsd.__main__:main"\n[tool.setuptools.packages.find]\ninclude = ["vsd"]\n''')
    (root / "vsd" / "__main__.py").write_text('''from vsd.init import init, vsd_update_workspace, vsd_workspace_info\napp = object()\napp.command("update")(vsd_update_workspace)\n''')


def test_install_check_remove(tmp_path):
    overlay = next(
        (parent for parent in Path(__file__).resolve().parents if (parent / "apply_overlay.py").is_file()),
        None,
    )
    if overlay is None:
        pytest.skip("standalone overlay installer is not present in an installed source tree")
    repo = tmp_path / "repo"
    make_repo(repo)
    original_pyproject = (repo / "pyproject.toml").read_text()
    subprocess.check_call(["python", str(overlay / "apply_overlay.py"), str(repo)])
    subprocess.check_call(["python", str(overlay / "apply_overlay.py"), str(repo), "--check"])
    assert (repo / "vsd" / "local" / "api.py").is_file()
    assert 'vsd-studio' in (repo / "pyproject.toml").read_text()
    subprocess.check_call(["python", str(overlay / "apply_overlay.py"), str(repo), "--remove"])
    assert (repo / "pyproject.toml").read_text() == original_pyproject
