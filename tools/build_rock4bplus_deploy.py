"""生成并校验发送到 ROCK 4B+ 的最小运行源码副本。"""

import argparse
import filecmp
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
APP_TARGET = ROOT / "deploy" / "rock4bplus" / "app"
SOURCE_ROOTS = (ROOT / "main.py", ROOT / "spectrometer")
IGNORED_PARTS = {"__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


def source_files():
    for source in SOURCE_ROOTS:
        if source.is_file():
            yield source, Path(source.name)
            continue
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT)
            if any(part in IGNORED_PARTS for part in relative.parts):
                continue
            if path.suffix in IGNORED_SUFFIXES:
                continue
            yield path, relative


def expected_relatives():
    return {relative for _, relative in source_files()}


def sync():
    staging = APP_TARGET.with_name(f"{APP_TARGET.name}.next")
    previous = APP_TARGET.with_name(f"{APP_TARGET.name}.previous")
    for path in (staging, previous):
        if path.exists():
            shutil.rmtree(path)
    staging.mkdir(parents=True, exist_ok=True)
    for source, relative in source_files():
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    try:
        if APP_TARGET.exists():
            APP_TARGET.replace(previous)
        staging.replace(APP_TARGET)
    except OSError:
        if not APP_TARGET.exists() and previous.exists():
            previous.replace(APP_TARGET)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    if previous.exists():
        shutil.rmtree(previous)


def check():
    errors = []
    expected = expected_relatives()
    actual = {
        path.relative_to(APP_TARGET)
        for path in APP_TARGET.rglob("*")
        if path.is_file()
    } if APP_TARGET.exists() else set()
    for relative in sorted(expected - actual):
        errors.append(f"缺少部署文件: {relative}")
    for relative in sorted(actual - expected):
        errors.append(f"存在额外部署文件: {relative}")
    for source, relative in source_files():
        target = APP_TARGET / relative
        if target.exists() and not filecmp.cmp(source, target, shallow=False):
            errors.append(f"部署副本与源码不一致: {relative}")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if not args.check:
        sync()
    errors = check()
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"ROCK 4B+ 部署源码校验通过，共 {len(expected_relatives())} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
