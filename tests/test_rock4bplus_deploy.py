from pathlib import Path

from tools.build_rock4bplus_deploy import APP_TARGET, ROOT, check, expected_relatives


DEPLOY = ROOT / "deploy" / "rock4bplus"


def test_deploy_contains_only_expected_top_level_entries():
    assert {path.name for path in DEPLOY.iterdir()} == {
        "app",
        "requirements-rock4bplus.txt",
        "install.sh",
        "run.sh",
        "99-zgcai-spectrometer.rules",
        "zgcai-spectrometer.desktop",
    }


def test_deploy_app_matches_runtime_source():
    assert Path("main.py") in expected_relatives()
    assert Path("spectrometer/qt.py") in expected_relatives()
    assert not check()


def test_deploy_excludes_development_and_user_files():
    forbidden = {
        ".venv",
        "__pycache__",
        "tests",
        "tools",
        "docs",
        "data",
        "build",
        "dist",
    }
    for path in APP_TARGET.rglob("*"):
        assert not forbidden.intersection(path.relative_to(APP_TARGET).parts)
        assert path.suffix.lower() not in {".xlsx", ".pdf", ".spec", ".pyc"}


def test_udev_rule_is_scoped_and_not_world_writable():
    rule = (DEPLOY / "99-zgcai-spectrometer.rules").read_text(encoding="utf-8")
    assert 'idVendor}=="1a86"' in rule
    assert 'idProduct}=="fe0c"' in rule
    assert 'GROUP="dialout"' in rule
    assert 'MODE="0660"' in rule
    assert "0666" not in rule
    assert "SYMLINK" not in rule


def test_install_includes_and_verifies_pyqtgraph():
    script = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "python3-pyqtgraph" in script
    assert "import pyqtgraph" in script
    assert "pyqtgraph.__version__" in script
    assert "pyqtgraph.Qt.QT_LIB == 'PyQt6'" in script
