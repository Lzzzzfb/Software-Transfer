from spectrometer.acquisition.frame_quality import FrameQualityAnalyzer
from spectrometer.domain.models import SpectrumFrame


def make_frame(values):
    return SpectrumFrame.create(0, 1 << 8, values)


def test_normal_dark_and_smooth_saturated_frames_are_accepted():
    analyzer = FrameQualityAnalyzer()
    normal = [2000 + (index % 31) for index in range(3648)]
    dark = [10 + (index % 5) for index in range(3648)]
    saturated = [65000 + (index % 20) for index in range(3648)]

    assert analyzer.inspect(make_frame(normal)).accepted
    assert analyzer.inspect(make_frame(dark)).accepted
    assert analyzer.inspect(make_frame(saturated)).accepted


def test_full_range_splice_signature_is_rejected():
    pattern = [20, 2040, 65000, 9000, 64000, 2000, 400, 62000]
    values = [pattern[index % len(pattern)] for index in range(3648)]

    quality = FrameQualityAnalyzer().inspect(make_frame(values))

    assert not quality.accepted
    assert quality.suspicious
    assert quality.high_fraction > 0.02
    assert quality.rough_fraction > 0.05
