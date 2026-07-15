from spectrometer.acquisition.sequence_tracker import SequenceTracker


def test_tracker_counts_gap_duplicate_and_out_of_order():
    tracker = SequenceTracker()
    tracker.observe(10)
    gap = tracker.observe(13)
    duplicate = tracker.observe(13)
    old = tracker.observe(12)

    assert gap.missing == 2
    assert duplicate.duplicate
    assert old.out_of_order
    assert tracker.missing == 2
    assert tracker.duplicates == 1
    assert tracker.out_of_order == 1


def test_tracker_accepts_24_bit_wrap_without_false_gap():
    tracker = SequenceTracker()
    tracker.observe(0xFFFFFE)
    tracker.observe(0xFFFFFF)
    wrapped = tracker.observe(0)

    assert wrapped.wrapped
    assert wrapped.missing == 0
    assert tracker.wraps == 1
