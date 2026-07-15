from spectrometer.domain.models import SpectrumReference
from spectrometer.processing.references import ReferenceRepository


def test_references_never_cross_device_or_pixel_configuration(tmp_path):
    repository = ReferenceRepository(tmp_path)
    compatible = SpectrumReference.create("SN-A", "background", [1, 2])
    wrong_device = SpectrumReference.create("SN-B", "background", [3, 4])
    wrong_pixels = SpectrumReference.create("SN-A", "background", [1, 2, 3])
    for item in (compatible, wrong_device, wrong_pixels):
        repository.save(item)

    matches = repository.list_compatible("SN-A", 2, "background")
    assert [item.reference_id for item in matches] == [compatible.reference_id]
    assert repository.latest_compatible("SN-B", 2, "reference") is None
