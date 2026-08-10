from spectrometer.motor.probe import ReadOnlyProbe, create_parser


def test_probe_help_states_that_it_is_read_only():
    assert "只读" in create_parser().description


def test_probe_cli_defaults_match_driver_defaults():
    args = create_parser().parse_args([])
    assert args.address == 1
    assert args.baud == 9600
    assert args.timeout == 8.0


def test_probe_module_exposes_bounded_abort():
    assert callable(ReadOnlyProbe.abort)
