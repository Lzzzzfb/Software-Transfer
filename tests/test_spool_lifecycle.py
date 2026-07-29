from pathlib import Path

from spectrometer.storage.spool_lifecycle import cleanup_spool_files


def test_cleanup_removes_existing_spools_and_ignores_duplicates(tmp_path):
    first = tmp_path / "first.zgs"
    second = tmp_path / "second.zgs"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    result = cleanup_spool_files([first, second, first])

    assert result.removed == (first, second)
    assert result.retained == ()
    assert result.warnings == ()
    assert not first.exists()
    assert not second.exists()


def test_cleanup_treats_missing_spool_as_already_removed(tmp_path):
    missing = tmp_path / "missing.zgs"

    result = cleanup_spool_files([missing])

    assert result.removed == (missing,)
    assert result.retained == ()
    assert result.warnings == ()


def test_cleanup_reports_failed_path_without_touching_other_files(tmp_path):
    blocked = tmp_path / "blocked.zgs"
    removable = tmp_path / "removable.zgs"
    blocked.write_bytes(b"keep")
    removable.write_bytes(b"remove")

    def unlink(path: Path) -> None:
        if path == blocked:
            raise PermissionError("injected permission failure")
        path.unlink(missing_ok=True)

    result = cleanup_spool_files([blocked, removable], unlink=unlink)

    assert result.removed == (removable,)
    assert result.retained == (blocked,)
    assert len(result.warnings) == 1
    assert str(blocked) in result.warnings[0]
    assert "injected permission failure" in result.warnings[0]
    assert blocked.read_bytes() == b"keep"
    assert not removable.exists()
