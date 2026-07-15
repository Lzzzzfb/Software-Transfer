import pytest

from spectrometer.domain.enums import StorageFormat, SyncMode
from spectrometer.domain.models import AcquisitionSession, DeviceConfig, SpectrumFrame


def test_spectrum_frame_exposes_protocol_sequence_fields():
    frame = SpectrumFrame.create(2, 0xABCDEF7A, [1, 2, 65535])
    assert frame.sequence == 0xABCDEF
    assert frame.reserved == 0x7A
    assert frame.pixel_count == 3


def test_frame_is_immutable_and_validates_u16():
    frame = SpectrumFrame.create(0, 0, [1])
    with pytest.raises(Exception):
        frame.device_id = 3
    with pytest.raises(ValueError, match="U16"):
        SpectrumFrame.create(0, 0, [65536])


def test_device_config_validates_trigger_and_gain():
    assert DeviceConfig().trigger_mode == 0
    with pytest.raises(ValueError, match="触发模式"):
        DeviceConfig(trigger_mode=3)
    with pytest.raises(ValueError, match="增益"):
        DeviceConfig(gain=64)


def test_session_defaults_to_500_frames_and_csv_excel():
    session = AcquisitionSession.create([0, 1], sync_mode=SyncMode.SOFTWARE)
    assert session.batch_size == 500
    assert session.storage_format == StorageFormat.CSV_EXCEL
    with pytest.raises(ValueError, match="1–1000"):
        AcquisitionSession.create([0], batch_size=1001)
