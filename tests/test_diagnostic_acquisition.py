from spectrometer.diagnostics.acquisition_observer import AcquisitionObserver


def test_observer_reports_interval_deltas_and_rates():
    observer = AcquisitionObserver()
    first = observer.observe(
        0,
        {"raw_complete_frames": 10, "persisted_frames": 10},
        monotonic_ns=1_000_000_000,
    )
    second = observer.observe(
        0,
        {"raw_complete_frames": 100, "persisted_frames": 100},
        monotonic_ns=2_000_000_000,
    )
    assert first["rates"]["raw_complete_frames_per_second"] == 0
    assert second["deltas"]["raw_complete_frames"] == 90
    assert second["rates"]["persisted_frames_per_second"] == 90


def test_counter_reset_starts_a_new_baseline():
    observer = AcquisitionObserver()
    observer.observe(0, {"raw_complete_frames": 100}, monotonic_ns=1)
    result = observer.observe(0, {"raw_complete_frames": 2}, monotonic_ns=2)
    assert result["deltas"]["raw_complete_frames"] == 0
