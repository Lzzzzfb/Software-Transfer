#include "USB_Command.h"

#include "PUL.h"
#include "main.h"
#include "usbd_cdc.h"
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern uint8_t CDC_Transmit_FS(uint8_t *Buf, uint16_t Len);
extern USBD_HandleTypeDef hUsbDeviceFS;

char usb_response_buffer[USB_RESPONSE_BUFFER_SIZE];

static char rx_line_buffer[USB_COMMAND_LINE_SIZE];
static uint16_t rx_line_length = 0U;
static uint8_t rx_discard_until_newline = 0U;

static char tx_queue[USB_TX_QUEUE_DEPTH][USB_RESPONSE_BUFFER_SIZE];
static volatile uint8_t tx_queue_head = 0U;
static volatile uint8_t tx_queue_tail = 0U;
static volatile uint8_t tx_queue_count = 0U;
static uint8_t tx_in_flight = 0U;

static void Process_Single_Command(
    const char *cmd,
    char *out_buf,
    size_t buf_size
);
static Motor_Device *Motor_FromAxis(char axis);
static uint8_t ParseUnsigned(const char *text, uint32_t *value);
static uint8_t ParseFloat(const char *text, float *value);
static void WriteMotorError(
    char *out_buf,
    size_t buf_size,
    Motor_Result result
);
static void WriteStatus(char *out_buf, size_t buf_size);
static uint8_t AnyMotorMoving(void);

void USB_Command_Init(void) {
    memset(usb_response_buffer, 0, sizeof(usb_response_buffer));
    memset(rx_line_buffer, 0, sizeof(rx_line_buffer));
    memset(tx_queue, 0, sizeof(tx_queue));
    rx_line_length = 0U;
    rx_discard_until_newline = 0U;
    tx_queue_head = 0U;
    tx_queue_tail = 0U;
    tx_queue_count = 0U;
    tx_in_flight = 0U;
}

void USB_Send_Response(const char *response) {
    uint32_t interrupt_state;
    size_t length;

    if (response == NULL || response[0] == '\0') {
        return;
    }
    length = strlen(response);
    if (length >= USB_RESPONSE_BUFFER_SIZE) {
        length = USB_RESPONSE_BUFFER_SIZE - 1U;
    }

    interrupt_state = __get_PRIMASK();
    __disable_irq();
    if (tx_queue_count < USB_TX_QUEUE_DEPTH) {
        memcpy(tx_queue[tx_queue_tail], response, length);
        tx_queue[tx_queue_tail][length] = '\0';
        tx_queue_tail = (uint8_t)(
            (tx_queue_tail + 1U) % USB_TX_QUEUE_DEPTH
        );
        tx_queue_count++;
    }
    if (interrupt_state == 0U) {
        __enable_irq();
    }
}

void USB_Command_Update(void) {
    USBD_CDC_HandleTypeDef *cdc_handle;
    uint32_t interrupt_state;
    uint16_t length;

    cdc_handle = (USBD_CDC_HandleTypeDef *)hUsbDeviceFS.pClassData;
    if (cdc_handle == NULL) {
        return;
    }

    if (tx_in_flight && cdc_handle->TxState == 0U) {
        interrupt_state = __get_PRIMASK();
        __disable_irq();
        if (tx_queue_count > 0U) {
            tx_queue_head = (uint8_t)(
                (tx_queue_head + 1U) % USB_TX_QUEUE_DEPTH
            );
            tx_queue_count--;
        }
        tx_in_flight = 0U;
        if (interrupt_state == 0U) {
            __enable_irq();
        }
    }

    if (tx_in_flight || tx_queue_count == 0U) {
        return;
    }
    length = (uint16_t)strlen(tx_queue[tx_queue_head]);
    if (
        length > 0U
        && CDC_Transmit_FS(
            (uint8_t *)tx_queue[tx_queue_head],
            length
        ) == USBD_OK
    ) {
        tx_in_flight = 1U;
    }
}

static Motor_Device *Motor_FromAxis(char axis) {
    return Motor_GetByAxis(axis);
}

static uint8_t ParseUnsigned(const char *text, uint32_t *value) {
    char *end;
    unsigned long parsed;

    if (text == NULL || value == NULL || text[0] == '\0') {
        return 0U;
    }
    parsed = strtoul(text, &end, 10);
    if (end == text || *end != '\0') {
        return 0U;
    }
    *value = (uint32_t)parsed;
    return 1U;
}

static uint8_t ParseFloat(const char *text, float *value) {
    char *end;
    float parsed;

    if (text == NULL || value == NULL || text[0] == '\0') {
        return 0U;
    }
    parsed = strtof(text, &end);
    if (end == text || *end != '\0' || parsed != parsed) {
        return 0U;
    }
    *value = parsed;
    return 1U;
}

static void WriteMotorError(
    char *out_buf,
    size_t buf_size,
    Motor_Result result
) {
    snprintf(
        out_buf,
        buf_size,
        "ERR %s\r\n",
        Motor_ResultName(result)
    );
    out_buf[buf_size - 1] = '\0';
}

static uint8_t AnyMotorMoving(void) {
    return (
        Motor_IsMoving(&motor1)
        || Motor_IsMoving(&motor2)
        || Motor_IsMoving(&motor3)
        || Motor_HomeAllActive()
    ) ? 1U : 0U;
}

static void WriteStatus(char *out_buf, size_t buf_size) {
    snprintf(
        out_buf,
        buf_size,
        "OK STATUS "
        "X_POS=%.3f X_VALID=%u X_MOVING=%u X_LIMIT=%u "
        "X_STOP=%s X_HOME=%s "
        "Y_POS=%.3f Y_VALID=%u Y_MOVING=%u Y_LIMIT=%u "
        "Y_STOP=%s Y_HOME=%s "
        "Z_POS=%.3f Z_VALID=%u Z_MOVING=%u Z_LIMIT=%u "
        "Z_STOP=%s Z_HOME=%s FAULT=%u\r\n",
        motor1.current_pos,
        (unsigned int)motor1.position_valid,
        (unsigned int)motor1.is_moving,
        (unsigned int)motor1.limit_active,
        Motor_StopReasonName(motor1.stop_reason),
        Motor_HomeStateName(motor1.home_state),
        motor2.current_pos,
        (unsigned int)motor2.position_valid,
        (unsigned int)motor2.is_moving,
        (unsigned int)motor2.limit_active,
        Motor_StopReasonName(motor2.stop_reason),
        Motor_HomeStateName(motor2.home_state),
        motor3.current_pos,
        (unsigned int)motor3.position_valid,
        (unsigned int)motor3.is_moving,
        (unsigned int)motor3.limit_active,
        Motor_StopReasonName(motor3.stop_reason),
        Motor_HomeStateName(motor3.home_state),
        (unsigned int)(
            motor1.fault_latched
            || motor2.fault_latched
            || motor3.fault_latched
        )
    );
    out_buf[buf_size - 1] = '\0';
}

static void Process_Single_Command(
    const char *cmd,
    char *out_buf,
    size_t buf_size
) {
    Motor_Device *motor;
    Motor_Result result;
    uint32_t unsigned_value;
    float float_value;
    const char *colon;
    char number_buffer[24];
    size_t number_length;
    uint8_t motor_number;
    uint8_t direction;

    if (out_buf == NULL || buf_size == 0U) {
        return;
    }
    out_buf[0] = '\0';

    if (strcmp(cmd, "ID?") == 0) {
        snprintf(
            out_buf,
            buf_size,
            "OK ID=TMC2209 MOTOR_PROTOCOL=%u TRAVEL_MM=%.1f "
            "PULSES_PER_MM=%u\r\n",
            (unsigned int)MOTOR_PROTOCOL_VERSION,
            (double)MOTOR_TRAVEL_MM,
            (unsigned int)MOTOR_PULSES_PER_MM
        );
    } else if (strcmp(cmd, "LIMIT?") == 0) {
        snprintf(
            out_buf,
            buf_size,
            "OK LIMIT X=%u Y=%u Z=%u ACTIVE_LOW=1\r\n",
            (unsigned int)Motor_LimitActive(&motor1),
            (unsigned int)Motor_LimitActive(&motor2),
            (unsigned int)Motor_LimitActive(&motor3)
        );
    } else if (strcmp(cmd, "POS?") == 0) {
        snprintf(
            out_buf,
            buf_size,
            "OK POS X=%.3f X_VALID=%u Y=%.3f Y_VALID=%u "
            "Z=%.3f Z_VALID=%u\r\n",
            motor1.current_pos,
            (unsigned int)motor1.position_valid,
            motor2.current_pos,
            (unsigned int)motor2.position_valid,
            motor3.current_pos,
            (unsigned int)motor3.position_valid
        );
    } else if (
        strcmp(cmd, "STATUS") == 0
        || strcmp(cmd, "STATUS?") == 0
    ) {
        WriteStatus(out_buf, buf_size);
    } else if (strcmp(cmd, "FAULT?") == 0) {
        snprintf(
            out_buf,
            buf_size,
            "OK FAULT X=%u Y=%u Z=%u\r\n",
            (unsigned int)motor1.fault_latched,
            (unsigned int)motor2.fault_latched,
            (unsigned int)motor3.fault_latched
        );
    } else if (strcmp(cmd, "CLEARFAULT") == 0) {
        if (AnyMotorMoving()) {
            snprintf(out_buf, buf_size, "ERR BUSY\r\n");
        } else {
            Motor_ClearFaults();
            snprintf(out_buf, buf_size, "OK CLEARFAULT\r\n");
        }
    } else if (strncmp(cmd, "POSSET=", 7) == 0) {
        motor = Motor_FromAxis(cmd[7]);
        if (
            motor == NULL
            || cmd[8] != ':'
            || !ParseFloat(cmd + 9, &float_value)
        ) {
            snprintf(out_buf, buf_size, "ERR BAD_COMMAND\r\n");
        } else {
            result = Motor_SetPosition(motor, float_value);
            if (result != MOTOR_OK) {
                WriteMotorError(out_buf, buf_size, result);
            } else {
                snprintf(
                    out_buf,
                    buf_size,
                    "OK POSSET AXIS=%c POS=%.3f\r\n",
                    motor->axis_name,
                    motor->current_pos
                );
            }
        }
    } else if (strncmp(cmd, "HOME=", 5) == 0) {
        if (strcmp(cmd + 5, "ALL") == 0) {
            result = Motor_StartHomeAll();
        } else if (cmd[5] != '\0' && cmd[6] == '\0') {
            motor = Motor_FromAxis(cmd[5]);
            result = motor == NULL
                ? MOTOR_ERR_AXIS
                : Motor_StartHome(motor);
        } else {
            result = MOTOR_ERR_VALUE;
        }
        if (result != MOTOR_OK) {
            WriteMotorError(out_buf, buf_size, result);
        } else {
            snprintf(
                out_buf,
                buf_size,
                "OK HOME AXIS=%s\r\n",
                strcmp(cmd + 5, "ALL") == 0 ? "ALL" : cmd + 5
            );
        }
    } else if (
        strncmp(cmd, "SPEED", 5) == 0
        && cmd[5] >= '1'
        && cmd[5] <= '3'
        && cmd[6] == '='
    ) {
        motor_number = (uint8_t)(cmd[5] - '0');
        motor = Motor_GetByNumber(motor_number);
        if (
            !ParseUnsigned(cmd + 7, &unsigned_value)
            || unsigned_value > 65535UL
        ) {
            snprintf(out_buf, buf_size, "ERR BAD_COMMAND\r\n");
        } else {
            result = Motor_SetSpeed(motor, (uint16_t)unsigned_value);
            if (result != MOTOR_OK) {
                WriteMotorError(out_buf, buf_size, result);
            } else {
                snprintf(
                    out_buf,
                    buf_size,
                    "OK SPEED MOTOR=%u HZ=%lu\r\n",
                    (unsigned int)motor_number,
                    (unsigned long)unsigned_value
                );
            }
        }
    } else if (
        strncmp(cmd, "MOVE", 4) == 0
        && cmd[4] >= '1'
        && cmd[4] <= '3'
        && cmd[5] == '='
    ) {
        motor_number = (uint8_t)(cmd[4] - '0');
        motor = Motor_GetByNumber(motor_number);
        colon = strchr(cmd + 6, ':');
        if (colon == NULL) {
            snprintf(out_buf, buf_size, "ERR BAD_COMMAND\r\n");
        } else {
            number_length = (size_t)(colon - (cmd + 6));
            if (
                number_length == 0U
                || number_length >= sizeof(number_buffer)
            ) {
                snprintf(out_buf, buf_size, "ERR BAD_COMMAND\r\n");
            } else {
                memcpy(number_buffer, cmd + 6, number_length);
                number_buffer[number_length] = '\0';
                if (
                    !ParseFloat(number_buffer, &float_value)
                    || (colon[1] != '0' && colon[1] != '1')
                    || colon[2] != '\0'
                ) {
                    snprintf(out_buf, buf_size, "ERR BAD_COMMAND\r\n");
                } else {
                    direction = (uint8_t)(colon[1] - '0');
                    result = Motor_MoveMMNonBlocking(
                        motor,
                        direction,
                        float_value
                    );
                    if (result != MOTOR_OK) {
                        WriteMotorError(out_buf, buf_size, result);
                    } else {
                        snprintf(
                            out_buf,
                            buf_size,
                            "OK MOVE MOTOR=%u MM=%.3f DIR=%u\r\n",
                            (unsigned int)motor_number,
                            float_value,
                            (unsigned int)direction
                        );
                    }
                }
            }
        }
    } else if (strcmp(cmd, "STOP") == 0) {
        Motor_StopAll();
        snprintf(out_buf, buf_size, "OK STOP\r\n");
    } else if (strcmp(cmd, "RESET") == 0) {
        Motor_StopAll();
        snprintf(out_buf, buf_size, "OK STOP ALIAS=RESET\r\n");
    } else if (strncmp(cmd, "SCAN_", 5) == 0) {
        snprintf(out_buf, buf_size, "ERR HOST_SCAN_REQUIRED\r\n");
    } else if (strncmp(cmd, "PB14=", 5) == 0) {
        if (strcmp(cmd + 5, "0") == 0) {
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_14, GPIO_PIN_RESET);
            snprintf(out_buf, buf_size, "OK ENABLE VALUE=1\r\n");
        } else if (strcmp(cmd + 5, "1") == 0) {
            Motor_StopAll();
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_14, GPIO_PIN_SET);
            snprintf(out_buf, buf_size, "OK ENABLE VALUE=0\r\n");
        } else {
            snprintf(out_buf, buf_size, "ERR BAD_COMMAND\r\n");
        }
    } else if (strcmp(cmd, "HELP") == 0) {
        snprintf(
            out_buf,
            buf_size,
            "OK HELP ID? STATUS LIMIT? POS? POSSET=<X|Y|Z>:<mm> "
            "SPEED<n>=<Hz> MOVE<n>=<mm>:<0|1> HOME=<X|Y|Z|ALL> "
            "STOP FAULT? CLEARFAULT PB14=<0|1>\r\n"
        );
    } else {
        snprintf(out_buf, buf_size, "ERR BAD_COMMAND\r\n");
    }
    out_buf[buf_size - 1] = '\0';
}

void USB_Command_Process(char *command) {
    char current;
    char single_response[USB_RESPONSE_BUFFER_SIZE];

    if (command == NULL) {
        return;
    }

    while (*command != '\0') {
        current = *command++;
        if (current == '\r' || current == '\n') {
            if (rx_discard_until_newline) {
                rx_discard_until_newline = 0U;
                rx_line_length = 0U;
                USB_Send_Response("ERR LINE_TOO_LONG\r\n");
            } else if (rx_line_length > 0U) {
                rx_line_buffer[rx_line_length] = '\0';
                Process_Single_Command(
                    rx_line_buffer,
                    single_response,
                    sizeof(single_response)
                );
                strncpy(
                    usb_response_buffer,
                    single_response,
                    sizeof(usb_response_buffer) - 1U
                );
                usb_response_buffer[
                    sizeof(usb_response_buffer) - 1U
                ] = '\0';
                USB_Send_Response(single_response);
                rx_line_length = 0U;
            }
            continue;
        }

        if (rx_discard_until_newline) {
            continue;
        }
        if (rx_line_length >= USB_COMMAND_LINE_SIZE - 1U) {
            rx_line_length = 0U;
            rx_discard_until_newline = 1U;
            continue;
        }
        rx_line_buffer[rx_line_length++] = current;
    }
}
