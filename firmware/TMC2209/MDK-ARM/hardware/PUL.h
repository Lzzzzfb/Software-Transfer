#ifndef __PUL_H
#define __PUL_H

#include "stm32f1xx_hal.h"
#include "stm32f1xx_hal_uart.h"


// 方向定义
#define DIR_CW       1   // 顺时针
#define DIR_CCW      0   // 逆时针

// 电机参数配置结构体
typedef struct {
    uint16_t default_speed;      // 默认速度(Hz)
    uint16_t pulses_per_mm;      // 每毫米脉冲数
    uint32_t max_steps;          // 最大行程步数限制
} Motor_Params;

// 电机设备结构体
typedef struct {
    GPIO_TypeDef* pulse_port;
    uint16_t pulse_pin;
    GPIO_TypeDef* dir_port;
    uint16_t dir_pin;
    TIM_HandleTypeDef* htim;     // 定时器句柄
    Motor_Params params;         // 电机参数
    volatile uint32_t pulse_count;        // 当前脉冲计数
    float current_pos;           // 当前位置(mm)
    volatile uint32_t target_pulses;      // 目标脉冲数（用于非阻塞模式）
    volatile uint8_t direction;           // 当前方向（用于非阻塞模式）
    volatile uint8_t is_moving;           // 电机移动状态标志（0: 停止, 1: 移动中）
} Motor_Device;

// 外部声明电机设备
extern Motor_Device motor1, motor2, motor3;

// 函数声明
void PUL_Init(void);
void Motor_MoveMM(Motor_Device* motor, uint8_t direction, float mm);
void Motor_Stop(Motor_Device* motor);

// 非阻塞式电机控制函数
void Motor_MoveStepsNonBlocking(Motor_Device* motor, uint8_t direction, uint32_t steps);
void Motor_MoveMMNonBlocking(Motor_Device* motor, uint8_t direction, float mm);
void Motor_UpdateMotion(void); // 定期调用以更新电机运动状态
uint8_t Motor_IsMoving(Motor_Device* motor); // 检查电机是否正在移动

// 按键与控制函数
void motor1_control(void);

// 外部触发检测函数
void External_Trigger_Detect(void);

#endif
