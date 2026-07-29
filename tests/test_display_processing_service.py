import threading
import time

from spectrometer.domain.models import SpectrumFrame
from spectrometer.processing.display_service import DisplayProcessingService
from spectrometer.processing.processor import ProcessingSnapshot


def frame(sequence):
    return SpectrumFrame.create(0, sequence << 8, [sequence, sequence + 1])


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_latest_pending_frame_replaces_older_pending_frame():
    release = threading.Event()
    processed = []
    results = []

    def process(item, _snapshot):
        processed.append(item.sequence)
        if item.sequence == 1:
            assert release.wait(2)
        return item.sequence

    service = DisplayProcessingService(
        process_callback=process,
        result_callback=lambda result: results.append(result),
        max_workers=1,
    )
    snapshot = ProcessingSnapshot(wavelengths=(1.0, 2.0))
    service.submit(0, 7, frame(1), snapshot)
    assert wait_until(lambda: processed == [1])
    service.submit(0, 7, frame(2), snapshot)
    service.submit(0, 7, frame(3), snapshot)
    assert service.pending_count == 1
    release.set()

    assert wait_until(lambda: processed == [1, 3])
    assert wait_until(lambda: [item.frame.sequence for item in results] == [1, 3])
    service.shutdown()


def test_discard_device_drops_pending_and_late_results():
    release = threading.Event()
    results = []

    def process(item, _snapshot):
        release.wait(2)
        return item.sequence

    service = DisplayProcessingService(
        process_callback=process,
        result_callback=lambda result: results.append(result),
        max_workers=1,
    )
    snapshot = ProcessingSnapshot(wavelengths=(1.0, 2.0))
    service.submit(0, 1, frame(1), snapshot)
    service.submit(0, 1, frame(2), snapshot)
    service.discard_device(0)
    release.set()

    assert wait_until(lambda: service.running_count == 0)
    assert results == []
    service.shutdown()


def test_processing_error_is_reported_and_next_latest_job_runs():
    failures = []
    results = []

    def process(item, _snapshot):
        if item.sequence == 1:
            raise ValueError("bad baseline")
        return item.sequence

    service = DisplayProcessingService(
        process_callback=process,
        result_callback=lambda result: results.append(result),
        error_callback=lambda failure: failures.append(failure),
        max_workers=1,
    )
    snapshot = ProcessingSnapshot(wavelengths=(1.0, 2.0))
    service.submit(0, 2, frame(1), snapshot)
    assert wait_until(lambda: len(failures) == 1)
    service.submit(0, 2, frame(2), snapshot)

    assert wait_until(lambda: len(results) == 1)
    assert failures[0].message == "bad baseline"
    assert results[0].frame.sequence == 2
    service.shutdown()
