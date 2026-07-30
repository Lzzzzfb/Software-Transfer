#include "PUL.h"
#include "stm32f1xx_hal.h"
#include "tim.h"
#include "USB_Command.h"
#include "TMC2209.h"

// UART 句柄（用于 USART1 TX）
extern UART_HandleTypeDef huart1;

// 定义四个电机设备
Motor_Device motor1, motor2, motor3;

// 外部触发状态变量
static uint8_t trigger_sent = 0;     // 触发命令发送标志

// 私有函数声明
static void PUL_GPIO_Configuration(void);

// 电机初始化
void Motor_Init(Motor_Device* motor, 
               GPIO_TypeDef* pulse_port, uint16_t pulse_pin,
               GPIO_TypeDef* dir_port, uint16_t dir_pin,
               TIM_HandleTypeDef* htim, Motor_Params params) {
    motor->pulse_port = pulse_port;
    motor->pulse_pin = pulse_pin;
    motor->dir_port = dir_port;
    motor->dir_pin = dir_pin;
    motor->htim = htim;
    motor->params = params;
    motor->pulse_count = 0;
    motor->current_pos = 0.0f;
    motor->target_pulses = 0;
    motor->direction = DIR_CW;
    motor->is_moving = 0;
}

// 设置电机速度
void Motor_SetSpeed(Motor_Device* motor, uint16_t speed) {
    if (speed > 0) {
        motor->params.default_speed = speed;
    }
}

// 停止电机
void Motor_Stop(Motor_Device* motor) {
    HAL_TIM_PWM_Stop(motor->htim, TIM_CHANNEL_1);
}

// 按步数移动
void Motor_MoveSteps(Motor_Device* motor, uint8_t direction, uint32_t steps) {
    // 检查是否超过最大行程
    if (steps > motor->params.max_steps) {
        steps = motor->params.max_steps;
    }
    
    // 设置方向
    if(direction == DIR_CW) {
        HAL_GPIO_WritePin(motor->dir_port, motor->dir_pin, GPIO_PIN_SET);
    } else {
        HAL_GPIO_WritePin(motor->dir_port, motor->dir_pin, GPIO_PIN_RESET);
    }
    
    // 计算定时器参数（根据当前速度）
    uint16_t arr_value = (1000000 / motor->params.default_speed) - 1;
    motor->htim->Instance->ARR = arr_value;
    __HAL_TIM_SET_COMPARE(motor->htim, TIM_CHANNEL_1, arr_value / 2);
    
    // 启动PWM
    motor->pulse_count = 0;
    HAL_TIM_PWM_Start(motor->htim, TIM_CHANNEL_1);
    
    // 等待移动完成
    while(motor->pulse_count < steps);
    
    // 更新位置信息
    float mm = (float)steps / motor->params.pulses_per_mm;
    if (direction == DIR_CW) {
        motor->current_pos += mm;
    } else {
        motor->current_pos = (mm > motor->current_pos) ? 0 : motor->current_pos - mm;
    }
    
    Motor_Stop(motor);
}

// 按毫米移动（自动计算步数）
void Motor_MoveMM(Motor_Device* motor, uint8_t direction, float mm) {
    if (mm <= 0) return;
    uint32_t steps = (uint32_t)(mm * motor->params.pulses_per_mm);
    Motor_MoveSteps(motor, direction, steps);
}

// 重置位置为0
void Motor_ResetPos(Motor_Device* motor) {
    motor->current_pos = 0.0f;
}

// 获取当前位置(mm)
float Motor_GetPos(Motor_Device* motor) {
    return motor->current_pos;
}

// 非阻塞式按步数移动电机
void Motor_MoveStepsNonBlocking(Motor_Device* motor, uint8_t direction, uint32_t steps) {
    // 检查是否超过最大行程
    if (steps > motor->params.max_steps) {
        steps = motor->params.max_steps;
    }
    
    // 停止当前运动（如果有）
    if (motor->is_moving) {
        Motor_Stop(motor);
    }
    
    // 设置方向
    motor->direction = direction;
    if(direction == DIR_CW) {
        HAL_GPIO_WritePin(motor->dir_port, motor->dir_pin, GPIO_PIN_SET);
    } else {
        HAL_GPIO_WritePin(motor->dir_port, motor->dir_pin, GPIO_PIN_RESET);
    }
    
    // 计算定时器参数（根据当前速度）
    uint16_t arr_value = (1000000 / motor->params.default_speed) - 1;
    motor->htim->Instance->ARR = arr_value;
    __HAL_TIM_SET_COMPARE(motor->htim, TIM_CHANNEL_1, arr_value / 2);
    
    // 设置目标脉冲数和移动状态
    motor->pulse_count = 0;
    motor->target_pulses = steps;
    motor->is_moving = 1;
    
    // 启动PWM
    HAL_TIM_PWM_Start(motor->htim, TIM_CHANNEL_1);
}

// 非阻塞式按毫米移动电机
void Motor_MoveMMNonBlocking(Motor_Device* motor, uint8_t direction, float mm) {
    if (mm <= 0) return;
    uint32_t steps = (uint32_t)(mm * motor->params.pulses_per_mm);
    Motor_MoveStepsNonBlocking(motor, direction, steps);
}

// 定期调用以更新电机运动状态
void Motor_UpdateMotion(void) {
    // 检查并更新X轴状态
    if (motor1.is_moving) {
        if (motor1.pulse_count >= motor1.target_pulses) {
            Motor_Stop(&motor1);
            motor1.is_moving = 0;
            
            // 更新位置信息
            float mm = (float)motor1.target_pulses / motor1.params.pulses_per_mm;
            if (motor1.direction == DIR_CW) {
                motor1.current_pos += mm;
            } else {
                motor1.current_pos = (mm > motor1.current_pos) ? 0 : motor1.current_pos - mm;
            }
        }
    }
    
    // 检查并更新Y轴状态
    if (motor2.is_moving) {
        if (motor2.pulse_count >= motor2.target_pulses) {
            Motor_Stop(&motor2);
            motor2.is_moving = 0;
            
            // 更新位置信息
            float mm = (float)motor2.target_pulses / motor2.params.pulses_per_mm;
            if (motor2.direction == DIR_CW) {
                motor2.current_pos += mm;
            } else {
                motor2.current_pos = (mm > motor2.current_pos) ? 0 : motor2.current_pos - mm;
            }
        }
    }
    
    // 检查并更新Z轴状态
    if (motor3.is_moving) {
        if (motor3.pulse_count >= motor3.target_pulses) {
            Motor_Stop(&motor3);
            motor3.is_moving = 0;
            
            // 更新位置信息
            float mm = (float)motor3.target_pulses / motor3.params.pulses_per_mm;
            if (motor3.direction == DIR_CW) {
                motor3.current_pos += mm;
            } else {
                motor3.current_pos = (mm > motor3.current_pos) ? 0 : motor3.current_pos - mm;
            }
        }
    }
    
    
}

// 检查电机是否正在移动
uint8_t Motor_IsMoving(Motor_Device* motor) {
    return motor->is_moving;
}

// 各电机单步控制函数
void Motor1_MoveForwardOneMM(void) {
    Motor_MoveMM(&motor1, DIR_CW, 1.0f);
}
void Motor1_MoveBackwardOneMM(void) {
    Motor_MoveMM(&motor1, DIR_CCW, 1.0f);
}
void Motor2_MoveForwardOneMM(void) {
    Motor_MoveMM(&motor2, DIR_CW, 1.0f);
}
void Motor2_MoveBackwardOneMM(void) {
    Motor_MoveMM(&motor2, DIR_CCW, 1.0f);
}
void Motor3_MoveForwardOneMM(void) {
    Motor_MoveMM(&motor3, DIR_CW, 1.0f);
}
void Motor3_MoveBackwardOneMM(void) {
    Motor_MoveMM(&motor3, DIR_CCW, 1.0f);
}

// 初始化所有电机
void PUL_Init(void) {
    
    // 配置各电机参数
    Motor_Params param1 = {10000,640, 160000};
    Motor_Params param2 = {10000,640, 160000};
    Motor_Params param3 = {10000,640, 160000};

    // 初始化电机（调整脉冲引脚与定时器映射）
    // X轴: PA8 -> TIM1 CH1, 方向 PA7
    Motor_Init(&motor1, GPIOA, GPIO_PIN_8,  GPIOA, GPIO_PIN_7,  &htim1, param1);
    // Y轴: PA6 -> TIM3 CH1, 方向 PA5
    Motor_Init(&motor2, GPIOA, GPIO_PIN_6,  GPIOA, GPIO_PIN_5,  &htim3, param2);
    // Z轴: PA0 -> TIM2 CH1, 方向 PA1
    Motor_Init(&motor3, GPIOA, GPIO_PIN_0,  GPIOA, GPIO_PIN_1,  &htim2, param3);


    // 配置GPIO
    PUL_GPIO_Configuration();

    // 启动定时器中断（仅 TIM1/TIM2/TIM3 用于脉冲输出）
    HAL_TIM_Base_Start_IT(&htim1);
    HAL_TIM_Base_Start_IT(&htim2);
    HAL_TIM_Base_Start_IT(&htim3);


    // 初始化 USART1 (PA9 TX) -- 115200, 8N1, 仅 TX
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
	
	// 定义四个轴的地址
    TMC_AxisAddr axes[] = {TMC_AXIS_X, TMC_AXIS_Y1, TMC_AXIS_Y2, TMC_AXIS_Z};

    for (int i = 0; i < 4; i++) {
        // 1. GCONF: 0x000000C4
        TMC2209_WriteRegister(axes[i], 0x00, 0x000000C4);
		// 清楚异常位
		TMC2209_WriteRegister(axes[i], 0x01, 0x00000003);
        // 2. IHOLD_IRUN: 0x00081F08
        TMC2209_WriteRegister(axes[i], 0x10, 0x00080F00);
        // 3. CHOPCONF: 0x140100C3
        TMC2209_WriteRegister(axes[i], 0x6C, 0x140100C3);
        // 4. TPOWERDOWN: 0x00000014
        TMC2209_WriteRegister(axes[i], 0x11, 0x00000014);
    }

}

// GPIO配置
static void PUL_GPIO_Configuration(void) {
    GPIO_InitTypeDef GPIO_InitStruct = {0};

    // 使能GPIO时钟
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();

    // 电机脉冲引脚（复用推挽输出），仅为已配置的脉冲引脚初始化
    GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;

    GPIO_InitStruct.Pin = motor1.pulse_pin;
    HAL_GPIO_Init(motor1.pulse_port, &GPIO_InitStruct);
    
    GPIO_InitStruct.Pin = motor2.pulse_pin;
    HAL_GPIO_Init(motor2.pulse_port, &GPIO_InitStruct);
    
    GPIO_InitStruct.Pin = motor3.pulse_pin;
    HAL_GPIO_Init(motor3.pulse_port, &GPIO_InitStruct);


    // 方向引脚（推挽输出），仅初始化已配置的方向引脚
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;

    GPIO_InitStruct.Pin = motor1.dir_pin;
    HAL_GPIO_Init(motor1.dir_port, &GPIO_InitStruct);
    
    GPIO_InitStruct.Pin = motor2.dir_pin;
    HAL_GPIO_Init(motor2.dir_port, &GPIO_InitStruct);
    
    GPIO_InitStruct.Pin = motor3.dir_pin;
    HAL_GPIO_Init(motor3.dir_port, &GPIO_InitStruct);


    // PA3: DIAG 输入，外部中断（上升沿）
    GPIO_InitStruct.Mode = GPIO_MODE_IT_RISING;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Pin = GPIO_PIN_3;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);
    HAL_NVIC_SetPriority(EXTI3_IRQn, 0, 0);
    HAL_NVIC_EnableIRQ(EXTI3_IRQn);

    // PA9: USART1 TX (AF Push-Pull)
    GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    GPIO_InitStruct.Pin = GPIO_PIN_9;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

    // PB10: 上拉输出 (3.3V 输出)
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_PULLUP;
    GPIO_InitStruct.Pin = GPIO_PIN_10;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_10, GPIO_PIN_SET);

    // PB0配置为外部中断模式，用于检测外部触发信号
    GPIO_InitStruct.Mode = GPIO_MODE_IT_RISING;  // 上升沿触发
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Pin = GPIO_PIN_0;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
	
	// 配置外部中断优先级
    HAL_NVIC_SetPriority(EXTI0_IRQn, 0, 0);
    HAL_NVIC_EnableIRQ(EXTI0_IRQn);

    // PB12: 推挽输出（切换斩波模式，默认为1）
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Pin = GPIO_PIN_12;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_12, GPIO_PIN_SET);

    // PB14: 推挽输出（驱动芯片使能，0使能，1非使能，初始化为1）
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Pin = GPIO_PIN_14;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_14, GPIO_PIN_RESET);


}


// 定时器更新回调函数
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim) {
    if (htim->Instance == TIM1) {
        motor1.pulse_count++;
    } else if (htim->Instance == TIM2) {
        // TIM2 mapped to motor3 (Z axis)
        motor3.pulse_count++;
    } else if (htim->Instance == TIM3) {
        // TIM3 mapped to motor2 (Y axis)
        motor2.pulse_count++;
    }
}





/**
  * @brief  EXTI0外部中断处理函数（PB0）
  * @param  无
  * @retval 无
  */
void EXTI0_IRQHandler(void) {
    // 检查是否是PB0的外部中断
    if (__HAL_GPIO_EXTI_GET_IT(GPIO_PIN_0) != RESET) {
        // 清除中断标志位
        __HAL_GPIO_EXTI_CLEAR_IT(GPIO_PIN_0);
        
        // 检测到外部触发信号，发送EXT_TRIGGER命令
        if (!trigger_sent) {
            USB_Send_Response("EXT_TRIGGER");
            // 设置发送标志，防止重复发送
            trigger_sent = 1;
            
            // 短暂延时，确保触发信号被处理
            // 注意：这里的延时不会阻塞主程序，因为中断处理函数会快速执行
            for (uint32_t i = 0; i < 1000; i++) {
                // 短暂延时，确保触发信号被处理
            }
            trigger_sent = 0;
        }
    }
}


