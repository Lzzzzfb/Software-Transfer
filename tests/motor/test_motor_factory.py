from spectrometer.motor import factory


class Created:
    def __init__(self, kind, parent, settings_path):
        self.kind = kind
        self.parent = parent
        self.settings_path = settings_path


def test_linux_hardware_uses_process_proxy(monkeypatch, tmp_path):
    monkeypatch.setattr(
        factory,
        "MotorProcessProxy",
        lambda parent, settings_path: Created(
            "process", parent, settings_path
        ),
    )

    result = factory.create_motor_controller(
        "parent",
        settings_path=tmp_path / "motor.json",
        simulation=False,
        platform="linux",
    )

    assert result.kind == "process"


def test_simulation_keeps_direct_controller(monkeypatch, tmp_path):
    monkeypatch.setattr(
        factory,
        "MotorController",
        lambda parent, settings_store: Created(
            "direct", parent, settings_store.path
        ),
    )

    result = factory.create_motor_controller(
        "parent",
        settings_path=tmp_path / "motor.json",
        simulation=True,
        platform="linux",
    )

    assert result.kind == "direct"

