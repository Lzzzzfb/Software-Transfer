import json

from spectrometer.motor.models import Position
from spectrometer.motor.state_store import MotorStateStore


def test_confirmed_position_survives_restart_for_same_device(tmp_path):
    path = tmp_path / "motor-state.json"
    store = MotorStateStore(path)
    store.confirm_position("SERIAL-A", Position(1.25, 2.5, 0))

    restored = MotorStateStore(path).load("SERIAL-A")

    assert restored is not None
    assert restored.trusted is True
    assert restored.position == Position(1.25, 2.5, 0)
    assert MotorStateStore(path).load("SERIAL-B") is None


def test_power_loss_during_motion_makes_saved_coordinate_untrusted(tmp_path):
    path = tmp_path / "motor-state.json"
    store = MotorStateStore(path)
    store.confirm_position("SERIAL-A", Position(1, 2, 0))
    store.begin_motion("SERIAL-A")

    restored = MotorStateStore(path).load("SERIAL-A")

    assert restored is not None
    assert restored.trusted is False
    assert restored.dirty is True


def test_disconnect_or_fault_invalidates_position_atomically(tmp_path):
    path = tmp_path / "motor-state.json"
    store = MotorStateStore(path)
    store.confirm_position("SERIAL-A", Position(1, 2, 0))
    store.invalidate("SERIAL-A", "connection_lost")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert not path.with_suffix(".json.tmp").exists()
    assert payload["devices"]["SERIAL-A"]["valid"] is False
    assert payload["devices"]["SERIAL-A"]["reason"] == "connection_lost"
    assert store.load("SERIAL-A").trusted is False


def test_corrupt_state_file_fails_closed(tmp_path):
    path = tmp_path / "motor-state.json"
    path.write_text("{not-json", encoding="utf-8")

    assert MotorStateStore(path).load("SERIAL-A") is None
