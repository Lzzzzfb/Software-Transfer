#include "PUL.h"

#include "main.h"
#include "tim.h"
#include "TMC2209.h"
#include <stddef.h>
#include <string.h>

#define MOTOR_TIMER_CLOCK_HZ       1000000UL
#define MOTOR_POSITION_EPSILON_MM  0.001f
#define LIMIT_DEBOUNCE_MS          8U
#define MOTOR_HOME_BACKOFF_MM      0.5f
#define MOTOR_HOME_SLOW_SEARCH_MM  1.0f
#define MOTOR_HOME_FAST_HZ         2000U
#define MOTOR_HOME_SLOW_HZ         500U
#define MOTOR_HOME_TIMEOUT_MS      15000U

extern UART_HandleTypeDef huart1;

Motor_Device motor1;
Motor_Device motor2;
Motor_Device motor3;

static uint8_t home_all_active = 0U;
static uint8_t home_all_index = 0U;

static void PUL_GPIO_Configuration(void);
static void Motor_Init(
    Motor_Device *motor,
    Motor_Axis axis,
    char axis_name,
    GPIO_TypeDef *pulse_port,
    uint16_t pulse_pin,
    GPIO_TypeDef *dir_port,
    uint16_t dir_pin,
    TIM_HandleTypeDef *htim,
    uint16_t limit_pin
);
static void Motor_HardwareStop(Motor_Device *motor);
static void Motor_StopWithReason(
    Motor_Device *motor,
    Motor_StopReason reason,
    uint8_t invalidate_position
);
static Motor_Result Motor_StartSteps(
    Motor_Device *motor,
    uint8_t direction,
    uint32_t steps,
    uint8_t enforce_position
);
static void Motor_FinalizeMotion(Motor_Device *motor);
static void Motor_UpdateLimit(Motor_Device *motor);
static void Motor_HandleStableLimit(Motor_Device *motor);
static void Motor_UpdateHome(Motor_Device *motor);
static Motor_Result Motor_StartHomeInternal(Motor_Device *motor);
static void Motor_FailHome(Motor_Device *motor, Motor_StopReason reason);
static void Motor_RestoreHomeSpeed(Motor_Device *motor);
static uint8_t Motor_ReadLimit(Motor_Device *motor);
static void Motor_StartHomeMove(
    Motor_Device *motor,
    uint8_t direction,
    float distance_mm,
    uint16_t speed_hz
);
static void Motor_UpdateHomeAll(void);
static uint8_t Motor_HomeInProgress(Motor_Device *motor);

static uint8_t Motor_HomeInProgress(Motor_Device *motor) {
    if (motor == NULL) {
        return 0U;
    }
    return (
        motor->home_state == HOME_LEAVE_SWITCH
        || motor->home_state == HOME_FAST_APPROACH
        || motor->home_state == HOME_BACKOFF
        || motor->home_state == HOME_SLOW_APPROACH
    ) ? 1U : 0U;
}

static void Motor_Init(
    Motor_Device *motor,
    Motor_Axis axis,
    char axis_name,
    GPIO_TypeDef *pulse_port,
    uint16_t pulse_pin,
    GPIO_TypeDef *dir_port,
    uint16_t dir_pin,
    TIM_HandleTypeDef *htim,
    uint16_t limit_pin
) {
    motor->axis = axis;
    motor->axis_name = axis_name;
    motor->pulse_port = pulse_port;
    motor->pulse_pin = pulse_pin;
    motor->dir_port = dir_port;
    motor->dir_pin = dir_pin;
    motor->htim = htim;
    motor->params.default_speed = MOTOR_SPEED_DEFAULT_HZ;
    motor->params.pulses_per_mm = MOTOR_PULSES_PER_MM;
    motor->params.max_steps = MOTOR_MAX_TRAVEL_PULSES;
    motor->params.max_travel_mm = MOTOR_TRAVEL_MM;
    motor->home_direction = DIR_CCW;
    motor->limit_pin = limit_pin;

    motor->pulse_count = 0U;
    motor->target_pulses = 0U;
    motor->direction = DIR_CW;
    motor->is_moving = 0U;
    motor->finalize_pending = 0U;

    motor->current_pos = 0.0f;
    motor->motion_start_pos = 0.0f;
    motor->target_position = 0.0f;
    motor->motion_start_valid = 0U;
    motor->target_position_valid = 0U;
    motor->position_valid = 0U;

    motor->limit_irq_pending = 0U;
    motor->limit_raw = 0U;
    motor->limit_active = 0U;
    motor->limit_changed_at = 0U;

    motor->fault_latched = 0U;
    motor->stop_reason = MOTOR_STOP_NONE;
    motor->home_state = HOME_IDLE;
    motor->saved_speed = MOTOR_SPEED_DEFAULT_HZ;
    motor->home_started_at = 0U;
}

Motor_Device *Motor_GetByNumber(uint8_t motor_num) {
    if (motor_num == 1U) {
        return &motor1;
    }
    if (motor_num == 2U) {
        return &motor2;
    }
    if (motor_num == 3U) {
        return &motor3;
    }
    return NULL;
}

Motor_Device *Motor_GetByAxis(char axis) {
    if (axis == 'X' || axis == 'x') {
        return &motor1;
    }
    if (axis == 'Y' || axis == 'y') {
        return &motor2;
    }
    if (axis == 'Z' || axis == 'z') {
        return &motor3;
    }
    return NULL;
}

Motor_Result Motor_SetSpeed(Motor_Device *motor, uint16_t speed) {
    if (motor == NULL) {
        return MOTOR_ERR_AXIS;
    }
    if (speed < MOTOR_SPEED_MIN_HZ || speed > MOTOR_SPEED_MAX_HZ) {
        return MOTOR_ERR_SPEED_RANGE;
    }
    if (motor->is_moving || Motor_HomeInProgress(motor)) {
        return MOTOR_ERR_BUSY;
    }
    if (motor->home_state == HOME_DONE) {
        motor->home_state = HOME_IDLE;
    }
    motor->params.default_speed = speed;
    return MOTOR_OK;
}

static void Motor_HardwareStop(Motor_Device *motor) {
    if (motor == NULL) {
        return;
    }
    HAL_TIM_PWM_Stop(motor->htim, TIM_CHANNEL_1);
}

static void Motor_StopWithReason(
    Motor_Device *motor,
    Motor_StopReason reason,
    uint8_t invalidate_position
) {
    if (motor == NULL) {
        return;
    }
    Motor_HardwareStop(motor);
    if (motor->is_moving) {
        motor->is_moving = 0U;
        motor->finalize_pending = 1U;
    }
    motor->stop_reason = reason;
    if (invalidate_position) {
        motor->position_valid = 0U;
    }
}

void Motor_Stop(Motor_Device *motor) {
    Motor_StopWithReason(motor, MOTOR_STOP_USER, 1U);
}

void Motor_StopAll(void) {
    Motor_Device *motors[3];
    uint8_t index;

    motors[0] = &motor1;
    motors[1] = &motor2;
    motors[2] = &motor3;
    for (index = 0U; index < 3U; index++) {
        uint8_t homing = Motor_HomeInProgress(motors[index]);
        Motor_Stop(motors[index]);
        if (homing) {
            Motor_RestoreHomeSpeed(motors[index]);
            motors[index]->home_state = HOME_FAILED;
            motors[index]->fault_latched = 1U;
        }
    }
    home_all_active = 0U;
}

static Motor_Result Motor_StartSteps(
    Motor_Device *motor,
    uint8_t direction,
    uint32_t steps,
    uint8_t enforce_position
) {
    uint32_t arr_value;
    float delta;
    float target;

    if (motor == NULL) {
        return MOTOR_ERR_AXIS;
    }
    if (direction != DIR_CW && direction != DIR_CCW) {
        return MOTOR_ERR_VALUE;
    }
    if (steps == 0U || steps > motor->params.max_steps) {
        return MOTOR_ERR_TRAVEL_RANGE;
    }
    if (
        motor->params.default_speed < MOTOR_SPEED_MIN_HZ
        || motor->params.default_speed > MOTOR_SPEED_MAX_HZ
    ) {
        return MOTOR_ERR_SPEED_RANGE;
    }
    if (motor->is_moving) {
        return MOTOR_ERR_BUSY;
    }
    if (motor->fault_latched && !Motor_HomeInProgress(motor)) {
        return MOTOR_ERR_FAULT_LATCHED;
    }
    if (
        direction == motor->home_direction
        && Motor_ReadLimit(motor)
        && !Motor_HomeInProgress(motor)
    ) {
        return MOTOR_ERR_LIMIT_ACTIVE;
    }

    delta = (float)steps / (float)motor->params.pulses_per_mm;
    motor->motion_start_pos = motor->current_pos;
    motor->motion_start_valid = motor->position_valid;
    motor->target_position_valid = motor->position_valid;
    target = motor->current_pos;
    if (motor->position_valid) {
        target += (direction == DIR_CW) ? delta : -delta;
        motor->target_position = target;
        if (
            enforce_position
            && (
                target < -MOTOR_POSITION_EPSILON_MM
                || target > motor->params.max_travel_mm
                    + MOTOR_POSITION_EPSILON_MM
            )
        ) {
            return MOTOR_ERR_TRAVEL_RANGE;
        }
    } else {
        motor->target_position = 0.0f;
        motor->target_position_valid = 0U;
    }

    motor->direction = direction;
    HAL_GPIO_WritePin(
        motor->dir_port,
        motor->dir_pin,
        direction == DIR_CW ? GPIO_PIN_SET : GPIO_PIN_RESET
    );

    arr_value = (MOTOR_TIMER_CLOCK_HZ / motor->params.default_speed) - 1U;
    motor->htim->Instance->ARR = arr_value;
    __HAL_TIM_SET_COMPARE(
        motor->htim,
        TIM_CHANNEL_1,
        (arr_value + 1U) / 2U
    );
    __HAL_TIM_SET_COUNTER(motor->htim, 0U);

    motor->pulse_count = 0U;
    motor->target_pulses = steps;
    motor->finalize_pending = 0U;
    motor->stop_reason = MOTOR_STOP_NONE;
    motor->is_moving = 1U;
    if (HAL_TIM_PWM_Start(motor->htim, TIM_CHANNEL_1) != HAL_OK) {
        motor->is_moving = 0U;
        motor->stop_reason = MOTOR_STOP_REPLACED;
        return MOTOR_ERR_VALUE;
    }
    return MOTOR_OK;
}

Motor_Result Motor_MoveStepsNonBlocking(
    Motor_Device *motor,
    uint8_t direction,
    uint32_t steps
) {
    if (motor != NULL && Motor_HomeInProgress(motor)) {
        return MOTOR_ERR_HOME_ACTIVE;
    }
    if (motor != NULL && motor->home_state == HOME_FAILED) {
        return MOTOR_ERR_FAULT_LATCHED;
    }
    if (motor != NULL && motor->home_state == HOME_DONE) {
        motor->home_state = HOME_IDLE;
    }
    return Motor_StartSteps(motor, direction, steps, 1U);
}

Motor_Result Motor_MoveMMNonBlocking(
    Motor_Device *motor,
    uint8_t direction,
    float mm
) {
    uint32_t steps;

    if (motor == NULL) {
        return MOTOR_ERR_AXIS;
    }
    if (mm <= 0.0f || mm > MOTOR_TRAVEL_MM) {
        return MOTOR_ERR_TRAVEL_RANGE;
    }
    steps = (uint32_t)(
        mm * (float)motor->params.pulses_per_mm + 0.5f
    );
    if (steps == 0U) {
        return MOTOR_ERR_TRAVEL_RANGE;
    }
    return Motor_MoveStepsNonBlocking(motor, direction, steps);
}

Motor_Result Motor_MoveMM(
    Motor_Device *motor,
    uint8_t direction,
    float mm
) {
    Motor_Result result;

    result = Motor_MoveMMNonBlocking(motor, direction, mm);
    while (result == MOTOR_OK && motor->is_moving) {
        Motor_Update();
    }
    return result;
}

Motor_Result Motor_SetPosition(Motor_Device *motor, float position_mm) {
    if (motor == NULL) {
        return MOTOR_ERR_AXIS;
    }
    if (motor->is_moving || Motor_HomeInProgress(motor)) {
        return MOTOR_ERR_BUSY;
    }
    if (motor->home_state == HOME_FAILED) {
        return MOTOR_ERR_FAULT_LATCHED;
    }
    if (motor->home_state == HOME_DONE) {
        motor->home_state = HOME_IDLE;
    }
    if (
        position_mm < 0.0f
        || position_mm > motor->params.max_travel_mm
    ) {
        return MOTOR_ERR_TRAVEL_RANGE;
    }
    motor->current_pos = position_mm;
    motor->motion_start_pos = position_mm;
    motor->target_position = position_mm;
    motor->position_valid = 1U;
    motor->motion_start_valid = 1U;
    motor->target_position_valid = 1U;
    motor->stop_reason = MOTOR_STOP_NONE;
    return MOTOR_OK;
}

void Motor_ResetPos(Motor_Device *motor) {
    (void)Motor_SetPosition(motor, 0.0f);
}

float Motor_GetPos(Motor_Device *motor) {
    return motor == NULL ? 0.0f : motor->current_pos;
}

uint8_t Motor_IsMoving(Motor_Device *motor) {
    return motor == NULL ? 0U : motor->is_moving;
}

static void Motor_FinalizeMotion(Motor_Device *motor) {
    uint32_t executed;
    float delta;
    float position;

    if (motor == NULL || !motor->finalize_pending) {
        return;
    }
    motor->finalize_pending = 0U;
    executed = motor->pulse_count;
    if (executed > motor->target_pulses) {
        executed = motor->target_pulses;
    }
    if (motor->motion_start_valid) {
        delta = (float)executed / (float)motor->params.pulses_per_mm;
        position = motor->motion_start_pos;
        position += (motor->direction == DIR_CW) ? delta : -delta;
        if (position < 0.0f) {
            position = 0.0f;
        }
        if (position > motor->params.max_travel_mm) {
            position = motor->params.max_travel_mm;
        }
        motor->current_pos = position;
    }
}

static uint8_t Motor_ReadLimit(Motor_Device *motor) {
    GPIO_PinState state;

    if (motor == NULL) {
        return 0U;
    }
    state = HAL_GPIO_ReadPin(GPIOB, motor->limit_pin);
    return state == LIMIT_ACTIVE_LEVEL ? 1U : 0U;
}

uint8_t Motor_LimitActive(Motor_Device *motor) {
    return motor == NULL ? 0U : motor->limit_active;
}

static void Motor_HandleStableLimit(Motor_Device *motor) {
    uint8_t expected_zero_target;

    if (motor == NULL) {
        return;
    }
    motor->current_pos = 0.0f;
    motor->position_valid = 1U;
    motor->motion_start_valid = 1U;

    if (
        motor->home_state == HOME_FAST_APPROACH
        && motor->stop_reason == MOTOR_STOP_LIMIT
    ) {
        motor->home_state = HOME_BACKOFF;
        Motor_StartHomeMove(
            motor,
            motor->home_direction == DIR_CCW ? DIR_CW : DIR_CCW,
            MOTOR_HOME_BACKOFF_MM,
            MOTOR_HOME_FAST_HZ
        );
        return;
    }

    if (
        motor->home_state == HOME_SLOW_APPROACH
        && motor->stop_reason == MOTOR_STOP_LIMIT
    ) {
        Motor_RestoreHomeSpeed(motor);
        motor->home_state = HOME_DONE;
        motor->stop_reason = MOTOR_STOP_HOMED;
        motor->fault_latched = 0U;
        return;
    }

    expected_zero_target = (
        motor->target_position_valid
        && motor->target_position <= MOTOR_POSITION_EPSILON_MM
    ) ? 1U : 0U;
    if (!expected_zero_target && motor->home_state == HOME_IDLE) {
        motor->fault_latched = 1U;
        motor->stop_reason = MOTOR_STOP_LIMIT_UNEXPECTED;
    }
}

static void Motor_UpdateLimit(Motor_Device *motor) {
    uint8_t raw;
    uint32_t now;

    if (motor == NULL) {
        return;
    }
    raw = Motor_ReadLimit(motor);
    now = HAL_GetTick();
    if (raw != motor->limit_raw) {
        motor->limit_raw = raw;
        motor->limit_changed_at = now;
    }
    if (
        raw != motor->limit_active
        && (uint32_t)(now - motor->limit_changed_at) >= LIMIT_DEBOUNCE_MS
    ) {
        motor->limit_active = raw;
        if (raw) {
            Motor_HandleStableLimit(motor);
        }
    }
    motor->limit_irq_pending = 0U;
}

static void Motor_StartHomeMove(
    Motor_Device *motor,
    uint8_t direction,
    float distance_mm,
    uint16_t speed_hz
) {
    uint32_t steps;
    Motor_Result result;

    motor->params.default_speed = speed_hz;
    steps = (uint32_t)(
        distance_mm * (float)motor->params.pulses_per_mm + 0.5f
    );
    result = Motor_StartSteps(motor, direction, steps, 0U);
    if (result != MOTOR_OK) {
        Motor_FailHome(motor, MOTOR_STOP_HOME_SWITCH);
    }
}

static Motor_Result Motor_StartHomeInternal(Motor_Device *motor) {
    uint8_t active;

    if (motor == NULL) {
        return MOTOR_ERR_AXIS;
    }
    if (motor->is_moving) {
        return MOTOR_ERR_BUSY;
    }
    if (
        motor->home_state != HOME_IDLE
        && motor->home_state != HOME_DONE
        && motor->home_state != HOME_FAILED
    ) {
        return MOTOR_ERR_HOME_ACTIVE;
    }

    motor->fault_latched = 0U;
    motor->saved_speed = motor->params.default_speed;
    motor->home_started_at = HAL_GetTick();
    motor->position_valid = 0U;
    motor->stop_reason = MOTOR_STOP_NONE;
    active = Motor_ReadLimit(motor);
    motor->limit_raw = active;
    motor->limit_active = active;
    motor->limit_changed_at = HAL_GetTick();

    if (active) {
        motor->current_pos = 0.0f;
        motor->position_valid = 1U;
        motor->home_state = HOME_LEAVE_SWITCH;
        Motor_StartHomeMove(
            motor,
            motor->home_direction == DIR_CCW ? DIR_CW : DIR_CCW,
            MOTOR_HOME_BACKOFF_MM,
            MOTOR_HOME_FAST_HZ
        );
    } else {
        motor->home_state = HOME_FAST_APPROACH;
        Motor_StartHomeMove(
            motor,
            motor->home_direction,
            MOTOR_TRAVEL_MM,
            MOTOR_HOME_FAST_HZ
        );
    }
    return motor->home_state == HOME_FAILED
        ? MOTOR_ERR_HOME_ACTIVE
        : MOTOR_OK;
}

Motor_Result Motor_StartHome(Motor_Device *motor) {
    if (home_all_active) {
        return MOTOR_ERR_HOME_ACTIVE;
    }
    return Motor_StartHomeInternal(motor);
}

Motor_Result Motor_StartHomeAll(void) {
    Motor_Result result;

    if (
        home_all_active
        || motor1.is_moving
        || motor2.is_moving
        || motor3.is_moving
    ) {
        return MOTOR_ERR_BUSY;
    }
    home_all_active = 1U;
    home_all_index = 0U;
    result = Motor_StartHomeInternal(&motor1);
    if (result != MOTOR_OK) {
        home_all_active = 0U;
    }
    return result;
}

uint8_t Motor_HomeAllActive(void) {
    return home_all_active;
}

static void Motor_RestoreHomeSpeed(Motor_Device *motor) {
    if (motor == NULL) {
        return;
    }
    motor->params.default_speed = motor->saved_speed;
}

static void Motor_FailHome(
    Motor_Device *motor,
    Motor_StopReason reason
) {
    if (motor == NULL) {
        return;
    }
    Motor_StopWithReason(motor, reason, 1U);
    Motor_RestoreHomeSpeed(motor);
    motor->home_state = HOME_FAILED;
    motor->fault_latched = 1U;
}

static void Motor_UpdateHome(Motor_Device *motor) {
    uint32_t now;

    if (motor == NULL) {
        return;
    }
    if (
        motor->home_state == HOME_IDLE
        || motor->home_state == HOME_DONE
        || motor->home_state == HOME_FAILED
    ) {
        return;
    }

    now = HAL_GetTick();
    if (
        (uint32_t)(now - motor->home_started_at)
        > MOTOR_HOME_TIMEOUT_MS
    ) {
        Motor_FailHome(motor, MOTOR_STOP_HOME_TIMEOUT);
        return;
    }

    if (motor->is_moving) {
        return;
    }

    if (motor->home_state == HOME_LEAVE_SWITCH) {
        if (motor->stop_reason != MOTOR_STOP_DONE) {
            Motor_FailHome(motor, MOTOR_STOP_HOME_SWITCH);
            return;
        }
        if (motor->limit_active) {
            if (!motor->limit_raw) {
                return;
            }
            Motor_FailHome(motor, MOTOR_STOP_HOME_SWITCH);
            return;
        }
        motor->position_valid = 0U;
        motor->home_state = HOME_FAST_APPROACH;
        Motor_StartHomeMove(
            motor,
            motor->home_direction,
            MOTOR_TRAVEL_MM,
            MOTOR_HOME_FAST_HZ
        );
        return;
    }

    if (motor->home_state == HOME_FAST_APPROACH) {
        if (
            motor->stop_reason == MOTOR_STOP_DONE
            && !motor->limit_active
        ) {
            Motor_FailHome(motor, MOTOR_STOP_HOME_TIMEOUT);
        }
        return;
    }

    if (motor->home_state == HOME_BACKOFF) {
        if (motor->stop_reason != MOTOR_STOP_DONE) {
            Motor_FailHome(motor, MOTOR_STOP_HOME_SWITCH);
            return;
        }
        if (motor->limit_active) {
            if (!motor->limit_raw) {
                return;
            }
            Motor_FailHome(motor, MOTOR_STOP_HOME_SWITCH);
            return;
        }
        motor->home_state = HOME_SLOW_APPROACH;
        Motor_StartHomeMove(
            motor,
            motor->home_direction,
            MOTOR_HOME_SLOW_SEARCH_MM,
            MOTOR_HOME_SLOW_HZ
        );
        return;
    }

    if (
        motor->home_state == HOME_SLOW_APPROACH
        && motor->stop_reason == MOTOR_STOP_DONE
        && !motor->limit_active
    ) {
        Motor_FailHome(motor, MOTOR_STOP_HOME_SWITCH);
    }
}

static void Motor_UpdateHomeAll(void) {
    Motor_Device *current;
    Motor_Result result;

    if (!home_all_active) {
        return;
    }
    current = home_all_index == 0U
        ? &motor1
        : (home_all_index == 1U ? &motor2 : &motor3);
    if (current->home_state == HOME_FAILED) {
        home_all_active = 0U;
        return;
    }
    if (current->home_state != HOME_DONE) {
        return;
    }
    if (home_all_index >= 2U) {
        home_all_active = 0U;
        return;
    }
    home_all_index++;
    current = home_all_index == 1U ? &motor2 : &motor3;
    result = Motor_StartHomeInternal(current);
    if (result != MOTOR_OK) {
        home_all_active = 0U;
    }
}

void Motor_ClearFaults(void) {
    Motor_Device *motors[3];
    uint8_t index;

    motors[0] = &motor1;
    motors[1] = &motor2;
    motors[2] = &motor3;
    for (index = 0U; index < 3U; index++) {
        if (!motors[index]->is_moving) {
            motors[index]->fault_latched = 0U;
            if (
                motors[index]->home_state == HOME_FAILED
                || motors[index]->home_state == HOME_DONE
            ) {
                motors[index]->home_state = HOME_IDLE;
            }
        }
    }
}

void Motor_Update(void) {
    Motor_Device *motors[3];
    uint8_t index;

    motors[0] = &motor1;
    motors[1] = &motor2;
    motors[2] = &motor3;

    for (index = 0U; index < 3U; index++) {
        if (
            motors[index]->is_moving
            && motors[index]->pulse_count
                >= motors[index]->target_pulses
        ) {
            Motor_StopWithReason(
                motors[index],
                MOTOR_STOP_DONE,
                0U
            );
        }
        Motor_FinalizeMotion(motors[index]);
        Motor_UpdateLimit(motors[index]);
        Motor_UpdateHome(motors[index]);
    }
    Motor_UpdateHomeAll();
}

void Motor_UpdateMotion(void) {
    Motor_Update();
}

static void PUL_GPIO_Configuration(void) {
    GPIO_InitTypeDef GPIO_InitStruct;

    memset(&GPIO_InitStruct, 0, sizeof(GPIO_InitStruct));
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_AFIO_CLK_ENABLE();
    __HAL_AFIO_REMAP_SWJ_NOJTAG();

    GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    GPIO_InitStruct.Pin = motor1.pulse_pin;
    HAL_GPIO_Init(motor1.pulse_port, &GPIO_InitStruct);
    GPIO_InitStruct.Pin = motor2.pulse_pin;
    HAL_GPIO_Init(motor2.pulse_port, &GPIO_InitStruct);
    GPIO_InitStruct.Pin = motor3.pulse_pin;
    HAL_GPIO_Init(motor3.pulse_port, &GPIO_InitStruct);

    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Pin = motor1.dir_pin;
    HAL_GPIO_Init(motor1.dir_port, &GPIO_InitStruct);
    GPIO_InitStruct.Pin = motor2.dir_pin;
    HAL_GPIO_Init(motor2.dir_port, &GPIO_InitStruct);
    GPIO_InitStruct.Pin = motor3.dir_pin;
    HAL_GPIO_Init(motor3.dir_port, &GPIO_InitStruct);

    HAL_GPIO_DeInit(GPIOA, GPIO_PIN_3);
    __HAL_GPIO_EXTI_CLEAR_IT(
        GPIO_PIN_3 | GPIO_PIN_4 | GPIO_PIN_5
    );
    GPIO_InitStruct.Mode = GPIO_MODE_IT_FALLING;
    GPIO_InitStruct.Pull = GPIO_PULLUP;
    GPIO_InitStruct.Pin = GPIO_PIN_3|GPIO_PIN_4|GPIO_PIN_5;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
    GPIO_InitStruct.Pin = LIMIT_REFERENCE_PIN;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
    HAL_GPIO_WritePin(GPIOB, LIMIT_REFERENCE_PIN, GPIO_PIN_RESET);

    HAL_NVIC_SetPriority(EXTI3_IRQn, 0U, 0U);
    HAL_NVIC_EnableIRQ(EXTI3_IRQn);
    HAL_NVIC_SetPriority(EXTI4_IRQn, 0U, 0U);
    HAL_NVIC_EnableIRQ(EXTI4_IRQn);
    HAL_NVIC_SetPriority(EXTI9_5_IRQn, 0U, 0U);
    HAL_NVIC_EnableIRQ(EXTI9_5_IRQn);

    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    GPIO_InitStruct.Pin = GPIO_PIN_12;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_12, GPIO_PIN_SET);

    GPIO_InitStruct.Pin = GPIO_PIN_14;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_14, GPIO_PIN_RESET);
}

void PUL_Init(void) {
    TMC_AxisAddr axes[4];
    uint8_t index;

    Motor_Init(
        &motor1,
        MOTOR_AXIS_X,
        'X',
        GPIOA,
        GPIO_PIN_8,
        GPIOA,
        GPIO_PIN_7,
        &htim1,
        X_ZERO_LIMIT_PIN
    );
    Motor_Init(
        &motor2,
        MOTOR_AXIS_Y,
        'Y',
        GPIOA,
        GPIO_PIN_6,
        GPIOA,
        GPIO_PIN_5,
        &htim3,
        Y_ZERO_LIMIT_PIN
    );
    Motor_Init(
        &motor3,
        MOTOR_AXIS_Z,
        'Z',
        GPIOA,
        GPIO_PIN_0,
        GPIOA,
        GPIO_PIN_1,
        &htim2,
        Z_ZERO_LIMIT_PIN
    );

    PUL_GPIO_Configuration();
    motor1.limit_raw = Motor_ReadLimit(&motor1);
    motor1.limit_active = motor1.limit_raw;
    motor2.limit_raw = Motor_ReadLimit(&motor2);
    motor2.limit_active = motor2.limit_raw;
    motor3.limit_raw = Motor_ReadLimit(&motor3);
    motor3.limit_active = motor3.limit_raw;

    HAL_TIM_Base_Start_IT(&htim1);
    HAL_TIM_Base_Start_IT(&htim2);
    HAL_TIM_Base_Start_IT(&htim3);

    __HAL_RCC_USART1_CLK_ENABLE();
    huart1.Instance = USART1;
    huart1.Init.BaudRate = 115200;
    huart1.Init.WordLength = UART_WORDLENGTH_8B;
    huart1.Init.StopBits = UART_STOPBITS_1;
    huart1.Init.Parity = UART_PARITY_NONE;
    huart1.Init.Mode = UART_MODE_TX;
    huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart1.Init.OverSampling = UART_OVERSAMPLING_16;
    HAL_UART_Init(&huart1);

    axes[0] = TMC_AXIS_X;
    axes[1] = TMC_AXIS_Y1;
    axes[2] = TMC_AXIS_Y2;
    axes[3] = TMC_AXIS_Z;
    for (index = 0U; index < 4U; index++) {
        TMC2209_WriteRegister(axes[index], 0x00, 0x000000C4);
        TMC2209_WriteRegister(axes[index], 0x01, 0x00000003);
        TMC2209_WriteRegister(axes[index], 0x10, 0x00080F00);
        TMC2209_WriteRegister(axes[index], 0x6C, 0x140100C3);
        TMC2209_WriteRegister(axes[index], 0x11, 0x00000014);
    }
}

void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim) {
    if (htim->Instance == TIM1 && motor1.is_moving) {
        motor1.pulse_count++;
    } else if (htim->Instance == TIM2 && motor3.is_moving) {
        motor3.pulse_count++;
    } else if (htim->Instance == TIM3 && motor2.is_moving) {
        motor2.pulse_count++;
    }
}

void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin) {
    Motor_Device *motor;

    motor = NULL;
    if (GPIO_Pin == X_ZERO_LIMIT_PIN) {
        motor = &motor1;
    } else if (GPIO_Pin == Y_ZERO_LIMIT_PIN) {
        motor = &motor2;
    } else if (GPIO_Pin == Z_ZERO_LIMIT_PIN) {
        motor = &motor3;
    }
    if (motor == NULL) {
        return;
    }

    motor->limit_irq_pending = 1U;
    motor->limit_raw = 1U;
    motor->limit_changed_at = HAL_GetTick();
    if (
        motor->is_moving
        && motor->direction == motor->home_direction
    ) {
        Motor_StopWithReason(motor, MOTOR_STOP_LIMIT, 0U);
    }
}

void EXTI4_IRQHandler(void) {
    HAL_GPIO_EXTI_IRQHandler(Y_ZERO_LIMIT_PIN);
}

void EXTI9_5_IRQHandler(void) {
    if (__HAL_GPIO_EXTI_GET_IT(Z_ZERO_LIMIT_PIN) != RESET) {
        HAL_GPIO_EXTI_IRQHandler(Z_ZERO_LIMIT_PIN);
    }
}

const char *Motor_ResultName(Motor_Result result) {
    switch (result) {
        case MOTOR_OK: return "OK";
        case MOTOR_ERR_AXIS: return "BAD_AXIS";
        case MOTOR_ERR_VALUE: return "BAD_VALUE";
        case MOTOR_ERR_SPEED_RANGE: return "SPEED_RANGE";
        case MOTOR_ERR_TRAVEL_RANGE: return "TRAVEL_RANGE";
        case MOTOR_ERR_BUSY: return "BUSY";
        case MOTOR_ERR_LIMIT_ACTIVE: return "LIMIT_ACTIVE";
        case MOTOR_ERR_FAULT_LATCHED: return "FAULT_LATCHED";
        case MOTOR_ERR_POSITION_UNKNOWN: return "POSITION_UNKNOWN";
        case MOTOR_ERR_HOME_ACTIVE: return "HOME_ACTIVE";
        default: return "UNKNOWN";
    }
}

const char *Motor_StopReasonName(Motor_StopReason reason) {
    switch (reason) {
        case MOTOR_STOP_NONE: return "NONE";
        case MOTOR_STOP_DONE: return "DONE";
        case MOTOR_STOP_USER: return "USER";
        case MOTOR_STOP_LIMIT: return "LIMIT";
        case MOTOR_STOP_LIMIT_UNEXPECTED: return "LIMIT_UNEXPECTED";
        case MOTOR_STOP_HOME_TIMEOUT: return "HOME_TIMEOUT";
        case MOTOR_STOP_HOME_SWITCH: return "HOME_SWITCH";
        case MOTOR_STOP_HOMED: return "HOMED";
        case MOTOR_STOP_REPLACED: return "REPLACED";
        default: return "UNKNOWN";
    }
}

const char *Motor_HomeStateName(Motor_HomeState state) {
    switch (state) {
        case HOME_IDLE: return "IDLE";
        case HOME_LEAVE_SWITCH: return "LEAVE_SWITCH";
        case HOME_FAST_APPROACH: return "FAST_APPROACH";
        case HOME_BACKOFF: return "BACKOFF";
        case HOME_SLOW_APPROACH: return "SLOW_APPROACH";
        case HOME_DONE: return "DONE";
        case HOME_FAILED: return "FAILED";
        default: return "UNKNOWN";
    }
}
