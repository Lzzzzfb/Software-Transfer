"""Validated global and per-device airPLS processing profiles."""

from __future__ import annotations

from dataclasses import dataclass
import math


AIRPLS_LAM_MIN = 1e-6
AIRPLS_LAM_MAX = 1e15
AIRPLS_ORDER_MIN = 1
AIRPLS_ORDER_MAX = 4
AIRPLS_ITER_MIN = 1
AIRPLS_ITER_MAX = 100


class ProfileValidationError(ValueError):
    pass


def validate_profile_values(lam, order, max_iter) -> None:
    if (
        not math.isfinite(float(lam))
        or not AIRPLS_LAM_MIN <= float(lam) <= AIRPLS_LAM_MAX
    ):
        raise ProfileValidationError(
            f"airPLS lambda 范围为 {AIRPLS_LAM_MIN:g}–{AIRPLS_LAM_MAX:g}"
        )
    if not AIRPLS_ORDER_MIN <= int(order) <= AIRPLS_ORDER_MAX:
        raise ProfileValidationError(
            f"airPLS 差分阶数范围为 {AIRPLS_ORDER_MIN}–{AIRPLS_ORDER_MAX}"
        )
    if not AIRPLS_ITER_MIN <= int(max_iter) <= AIRPLS_ITER_MAX:
        raise ProfileValidationError(
            f"airPLS 最大迭代次数范围为 "
            f"{AIRPLS_ITER_MIN}–{AIRPLS_ITER_MAX}"
        )


@dataclass(frozen=True)
class AirplsProfile:
    enabled: bool = False
    lam: float = 1e5
    order: int = 2
    max_iter: int = 15
    source: str = "global"

    def __post_init__(self):
        if not isinstance(self.enabled, bool):
            raise ProfileValidationError("airPLS 启用状态必须是布尔值")
        validate_profile_values(self.lam, self.order, self.max_iter)
        if self.source not in ("global", "device"):
            raise ProfileValidationError("airPLS 参数来源必须是 global 或 device")


@dataclass(frozen=True)
class DeviceAirplsOverride:
    follow_global: bool = True
    enabled: bool = False
    lam: float = 1e5
    order: int = 2
    max_iter: int = 15

    def __post_init__(self):
        if not isinstance(self.follow_global, bool) or not isinstance(
            self.enabled, bool
        ):
            raise ProfileValidationError("airPLS 跟随和启用状态必须是布尔值")
        validate_profile_values(self.lam, self.order, self.max_iter)

    def to_dict(self) -> dict:
        return {
            "follow_global": self.follow_global,
            "enabled": self.enabled,
            "lam": float(self.lam),
            "order": int(self.order),
            "max_iter": int(self.max_iter),
        }

    @classmethod
    def from_dict(cls, values) -> "DeviceAirplsOverride":
        try:
            return cls(
                follow_global=values["follow_global"],
                enabled=values["enabled"],
                lam=float(values["lam"]),
                order=int(values["order"]),
                max_iter=int(values["max_iter"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProfileValidationError(f"airPLS 设备配置格式错误：{exc}") from exc


def resolve_effective_profile(
    global_profile: AirplsProfile,
    device_override: DeviceAirplsOverride | None,
) -> AirplsProfile:
    if device_override is None or device_override.follow_global:
        return AirplsProfile(
            enabled=global_profile.enabled,
            lam=global_profile.lam,
            order=global_profile.order,
            max_iter=global_profile.max_iter,
            source="global",
        )
    return AirplsProfile(
        enabled=device_override.enabled,
        lam=device_override.lam,
        order=device_override.order,
        max_iter=device_override.max_iter,
        source="device",
    )
