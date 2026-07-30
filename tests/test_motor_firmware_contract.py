from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "firmware" / "TMC2209"
HARDWARE = FIRMWARE / "MDK-ARM" / "hardware"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_complete_motor_firmware_project_is_present():
    required = {
        FIRMWARE / "TMC2209.ioc",
        FIRMWARE / "Core" / "Src" / "main.c",
        FIRMWARE / "MDK-ARM" / "TMC2209.uvprojx",
        HARDWARE / "PUL.c",
        HARDWARE / "PUL.h",
        HARDWARE / "TMC2209.c",
        HARDWARE / "TMC2209.h",
        HARDWARE / "USB_Command.c",
        HARDWARE / "USB_Command.h",
        FIRMWARE / "MDK-ARM" / "TMC2209" / "TMC2209.hex",
    }
    assert not {str(path.relative_to(ROOT)) for path in required if not path.is_file()}


def test_motor_truth_constants_are_centralized():
    header = _text(HARDWARE / "PUL.h")
    assert "#define MOTOR_PULSES_PER_MM       640U" in header
    assert "#define MOTOR_TRAVEL_MM           15.0f" in header
    assert "#define MOTOR_MAX_TRAVEL_PULSES   9600U" in header
    assert "#define MOTOR_SPEED_MIN_HZ        100U" in header
    assert "#define MOTOR_SPEED_MAX_HZ        14000U" in header
    assert "#define MOTOR_SPEED_DEFAULT_HZ    10000U" in header

    source = _text(HARDWARE / "PUL.c")
    assert "160000" not in source
    assert "Motor_Params param1 = {10000,640" not in source


def test_zero_limit_gpio_contract_is_active_low():
    header = _text(HARDWARE / "PUL.h")
    assert "#define X_ZERO_LIMIT_PIN          GPIO_PIN_3" in header
    assert "#define Y_ZERO_LIMIT_PIN          GPIO_PIN_4" in header
    assert "#define Z_ZERO_LIMIT_PIN          GPIO_PIN_5" in header
    assert "#define LIMIT_REFERENCE_PIN       GPIO_PIN_6" in header
    assert "#define LIMIT_ACTIVE_LEVEL        GPIO_PIN_RESET" in header

    source = _text(HARDWARE / "PUL.c")
    assert "GPIO_MODE_IT_FALLING" in source
    assert "GPIO_PULLUP" in source
    assert "GPIO_PIN_3|GPIO_PIN_4|GPIO_PIN_5" in source
    assert "HAL_GPIO_WritePin(GPIOB, LIMIT_REFERENCE_PIN, GPIO_PIN_RESET)" in source
    assert "HAL_GPIO_WritePin(GPIOB, LIMIT_REFERENCE_PIN, GPIO_PIN_SET)" not in source
    assert "HAL_GPIO_DeInit(GPIOA, GPIO_PIN_3)" in source
    assert "EXTI4_IRQHandler" in source
    assert "EXTI9_5_IRQHandler" in source
    assert "HAL_GPIO_EXTI_Callback" in source


def test_motion_limit_and_homing_api_exists():
    header = _text(HARDWARE / "PUL.h")
    for symbol in (
        "position_valid",
        "fault_latched",
        "limit_active",
        "home_state",
        "Motor_SetPosition",
        "Motor_LimitActive",
        "Motor_StartHome",
        "Motor_StartHomeAll",
        "Motor_ClearFaults",
        "Motor_StopAll",
        "Motor_Update",
    ):
        assert symbol in header

    source = _text(HARDWARE / "PUL.c")
    assert "MOTOR_HOME_BACKOFF_MM" in source
    assert "HOME_FAST_APPROACH" in source
    assert "HOME_SLOW_APPROACH" in source
    assert "HAL_Delay(" not in source


def test_main_loop_services_motor_and_usb_without_firmware_scan():
    main = _text(FIRMWARE / "Core" / "Src" / "main.c")
    assert "Motor_Update();" in main
    assert "USB_Command_Update();" in main
    assert "Scan_Update();" not in main


def test_protocol_v2_commands_and_safe_buffers_are_present():
    header = _text(HARDWARE / "USB_Command.h")
    source = _text(HARDWARE / "USB_Command.c")

    assert "#define MOTOR_PROTOCOL_VERSION 2U" in header
    assert "#define USB_COMMAND_LINE_SIZE 96U" in header
    assert "#define USB_RESPONSE_BUFFER_SIZE 384U" in header
    assert "void USB_Command_Update(void);" in header

    for command in (
        'strcmp(cmd, "ID?")',
        'strcmp(cmd, "LIMIT?")',
        'strcmp(cmd, "POS?")',
        'strncmp(cmd, "POSSET=", 7)',
        'strncmp(cmd, "HOME=", 5)',
        'strcmp(cmd, "STOP")',
        'strcmp(cmd, "FAULT?")',
        'strcmp(cmd, "CLEARFAULT")',
    ):
        assert command in source

    assert "HOST_SCAN_REQUIRED" in source
    assert "ALIAS=RESET" in source
    assert "Reset all motors to origin" not in source
    assert "rx_line_buffer" in source
    assert "tx_queue" in source
    assert "out_buf[buf_size - 1] = '\\0'" in source
    assert "strncpy(usb_response_buffer, status_msg" not in source


def test_interrupt_handlers_do_not_send_usb_or_delay():
    source = _text(HARDWARE / "PUL.c")
    callback = source[source.index("void HAL_GPIO_EXTI_Callback") :]
    assert "USB_Send_Response" not in callback
    assert "HAL_Delay" not in callback
