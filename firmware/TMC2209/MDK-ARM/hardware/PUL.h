#ifndef __PUL_H
#define __PUL_H

#include "stm32f1xx_hal.h"
#include "stm32f1xx_hal_uart.h"

#define DIR_CW                      1U
#define DIR_CCW                     0U

#define MOTOR_PULSES_PER_MM       640U
#define MOTOR_TRAVEL_MM           15.0f
#define MOTOR_MAX_TRAVEL_PULSES   9600U
#define MOTOR_SPEED_MIN_HZ        100U
#define MOTOR_SPEED_MAX_HZ        14000U
#define MOTOR_SPEED_DEFAULT_HZ    10000U

#define X_ZERO_LIMIT_PIN          GPIO_PIN_3
#define Y_ZERO_LIMIT_PIN          GPIO_PIN_4
#define Z_ZERO_LIMIT_PIN          GPIO_PIN_5
#define LIMIT_REFERENCE_PIN       GPIO_PIN_6
#define LIMIT_ACTIVE_LEVEL        GPIO_PIN_RESET

typedef enum {
    MOTOR_AXIS_X = 0,
    MOTOR_AXIS_Y = 1,
    MOTOR_AXIS_Z = 2
} Motor_Axis;

typedef enum {
    MOTOR_OK = 0,
    MOTOR_ERR_AXIS,
    MOTOR_ERR_VALUE,
    MOTOR_ERR_SPEED_RANGE,
    MOTOR_ERR_TRAVEL_RANGE,
    MOTOR_ERR_BUSY,
    MOTOR_ERR_LIMIT_ACTIVE,
    MOTOR_ERR_FAULT_LATCHED,
    MOTOR_ERR_POSITION_UNKNOWN,
    MOTOR_ERR_HOME_ACTIVE
} Motor_Result;

typedef enum {
    MOTOR_STOP_NONE = 0,
    MOTOR_STOP_DONE,
    MOTOR_STOP_USER,
    MOTOR_STOP_LIMIT,
    MOTOR_STOP_LIMIT_UNEXPECTED,
    MOTOR_STOP_HOME_TIMEOUT,
    MOTOR_STOP_HOME_SWITCH,
    MOTOR_STOP_HOMED,
    MOTOR_STOP_REPLACED
} Motor_StopReason;

typedef enum {
    HOME_IDLE = 0,
    HOME_LEAVE_SWITCH,
    HOME_FAST_APPROACH,
    HOME_BACKOFF,
    HOME_SLOW_APPROACH,
    HOME_DONE,
    HOME_FAILED
} Motor_HomeState;

typedef struct {
    uint16_t default_speed;
    uint16_t pulses_per_mm;
    uint32_t max_steps;
    float max_travel_mm;
} Motor_Params;

typedef struct {
    Motor_Axis axis;
    char axis_name;
    GPIO_TypeDef *pulse_port;
    uint16_t pulse_pin;
    GPIO_TypeDef *dir_port;
    uint16_t dir_pin;
    TIM_HandleTypeDef *htim;
    Motor_Params params;
    uint8_t home_direction;
    uint16_t limit_pin;

    volatile uint32_t pulse_count;
    volatile uint32_t target_pulses;
    volatile uint8_t direction;
    volatile uint8_t is_moving;
    volatile uint8_t finalize_pending;

    float current_pos;
    float motion_start_pos;
    float target_position;
    uint8_t motion_start_valid;
    uint8_t target_position_valid;
    uint8_t position_valid;

    volatile uint8_t limit_irq_pending;
    uint8_t limit_raw;
    uint8_t limit_active;
    uint32_t limit_changed_at;

    uint8_t fault_latched;
    Motor_StopReason stop_reason;
    Motor_HomeState home_state;
    uint16_t saved_speed;
    uint32_t home_started_at;
} Motor_Device;

extern Motor_Device motor1;
extern Motor_Device motor2;
extern Motor_Device motor3;

void PUL_Init(void);
void Motor_Update(void);
void Motor_UpdateMotion(void);

Motor_Device *Motor_GetByNumber(uint8_t motor_num);
Motor_Device *Motor_GetByAxis(char axis);

Motor_Result Motor_SetSpeed(Motor_Device *motor, uint16_t speed);
Motor_Result Motor_MoveStepsNonBlocking(
    Motor_Device *motor,
    uint8_t direction,
    uint32_t steps
);
Motor_Result Motor_MoveMMNonBlocking(
    Motor_Device *motor,
    uint8_t direction,
    float mm
);
Motor_Result Motor_MoveMM(Motor_Device *motor, uint8_t direction, float mm);

void Motor_Stop(Motor_Device *motor);
void Motor_StopAll(void);
uint8_t Motor_IsMoving(Motor_Device *motor);

Motor_Result Motor_SetPosition(Motor_Device *motor, float position_mm);
float Motor_GetPos(Motor_Device *motor);
void Motor_ResetPos(Motor_Device *motor);

uint8_t Motor_LimitActive(Motor_Device *motor);
Motor_Result Motor_StartHome(Motor_Device *motor);
Motor_Result Motor_StartHomeAll(void);
uint8_t Motor_HomeAllActive(void);
void Motor_ClearFaults(void);

const char *Motor_ResultName(Motor_Result result);
const char *Motor_StopReasonName(Motor_StopReason reason);
const char *Motor_HomeStateName(Motor_HomeState state);

#endif
