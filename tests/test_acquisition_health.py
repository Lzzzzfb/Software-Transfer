from spectrometer.acquisition.health_monitor import AcquisitionHealthMonitor


def test_single_missing_frame_continues():
    monitor = AcquisitionHealthMonitor()

    for index in range(100):
        decision = monitor.observe(index * 10_000_000, valid=1)
    decision = monitor.observe(1_100_000_000, valid=1, abnormal=1)

    assert not decision.stop


def test_five_consecutive_missing_frames_stop():
    monitor = AcquisitionHealthMonitor(consecutive_limit=5)
    monitor.observe(0, valid=20)

    decision = monitor.observe(1, valid=1, abnormal=5)

    assert decision.stop
    assert "连续异常帧" in decision.reason


def test_five_percent_window_stops_after_minimum_sample():
    monitor = AcquisitionHealthMonitor(
        ratio_limit=0.05, consecutive_limit=10
    )

    decision = monitor.observe(0, valid=95, abnormal=5)

    assert decision.stop
    assert "异常比例" in decision.reason
