"""按设备生产序列号和像素配置隔离的背景/参考仓库。"""

import json
from pathlib import Path
from typing import List, Optional

from ..domain.models import SpectrumReference


class ReferenceRepository:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, reference: SpectrumReference) -> Path:
        return self.save_batch([reference])[0]

    def save_batch(self, references) -> List[Path]:
        """Write a group of immutable references with rollback on commit failure."""

        items = list(references)
        if not items:
            raise ValueError("参考批次不能为空")
        if len({item.reference_id for item in items}) != len(items):
            raise ValueError("参考批次不能包含重复 ID")
        temporary_paths = []
        final_paths = []
        committed_paths = []
        try:
            for reference in items:
                path = self.directory / f"{reference.reference_id}.json"
                if path.exists():
                    raise FileExistsError(f"参考记录已存在：{path.name}")
                temporary = path.with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(
                        {
                            "reference_id": reference.reference_id,
                            "device_serial": reference.device_serial,
                            "pixel_count": reference.pixel_count,
                            "kind": reference.kind,
                            "pixels": list(reference.pixels),
                            "created_at": reference.created_at,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                temporary_paths.append(temporary)
                final_paths.append(path)
            for temporary, final in zip(temporary_paths, final_paths):
                temporary.replace(final)
                committed_paths.append(final)
            return final_paths
        except Exception:
            for path in temporary_paths:
                if path.exists():
                    path.unlink()
            for path in committed_paths:
                if path.exists():
                    path.unlink()
            raise

    def load(self, reference_id: str) -> SpectrumReference:
        data = json.loads((self.directory / f"{reference_id}.json").read_text(encoding="utf-8"))
        return SpectrumReference(
            reference_id=data["reference_id"],
            device_serial=data["device_serial"],
            pixel_count=int(data["pixel_count"]),
            kind=data["kind"],
            pixels=tuple(float(value) for value in data["pixels"]),
            created_at=data["created_at"],
        )

    def list_compatible(
        self, device_serial: str, pixel_count: int, kind: str
    ) -> List[SpectrumReference]:
        matches = []
        for path in self.directory.glob("*.json"):
            try:
                reference = self.load(path.stem)
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                reference.device_serial == device_serial
                and reference.pixel_count == pixel_count
                and reference.kind == kind
            ):
                matches.append(reference)
        return sorted(matches, key=lambda item: item.created_at, reverse=True)

    def latest_compatible(
        self, device_serial: str, pixel_count: int, kind: str
    ) -> Optional[SpectrumReference]:
        matches = self.list_compatible(device_serial, pixel_count, kind)
        return matches[0] if matches else None
