#include "USB_Command.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

// 添加PB14控制相关头文件
#include "main.h"

// 响应缓冲区定义
// 用于存储USB命令的响应字符串，大小由USB_RESPONSE_BUFFER_SIZE宏定义
char usb_response_buffer[USB_RESPONSE_BUFFER_SIZE];

// 外部函数声明（在usbd_cdc_if.c中实现）
// CDC_Transmit_FS: 通过USB CDC接口发送数据
extern uint8_t CDC_Transmit_FS(uint8_t* Buf, uint16_t Len);

/**
  * @brief  USB命令模块初始化
  * @param  无
  * @retval 无
  */
void USB_Command_Init(void) {
    // 初始化响应缓冲区，将所有字节设置为0
    memset(usb_response_buffer, 0, sizeof(usb_response_buffer));
}

/**
  * @brief  发送USB响应
  * @param  response: 响应字符串
  * @retval 无
  */
void USB_Send_Response(char* response) {
    // 检查响应字符串是否有效且非空
    if (response != NULL && strlen(response) > 0) {
        // 调用CDC_Transmit_FS函数通过USB发送响应
        CDC_Transmit_FS((uint8_t*)response, strlen(response));
    }
}

/**
  * @brief  设置电机速度
  * @param  motor_num: 电机编号(1-4)
  * @param  speed: 速度值(Hz)
  * @retval 无
  */
static void Set_Motor_Speed(uint8_t motor_num, uint16_t speed) {
    // 仅保留下限，解除上限限制
    if (speed < 100) speed = 100;   // 最小速度限制
    
    // 根据电机编号设置对应电机的速度
    switch(motor_num) {
        case 1:
            motor1.params.default_speed = speed;
            snprintf(usb_response_buffer, sizeof(usb_response_buffer), 
                    "Motor1 speed set to %d Hz\r\n", speed);
            break;
        case 2:
            motor2.params.default_speed = speed;
            snprintf(usb_response_buffer, sizeof(usb_response_buffer), 
                    "Motor2 speed set to %d Hz\r\n", speed);
            break;
        case 3:
            motor3.params.default_speed = speed;
            snprintf(usb_response_buffer, sizeof(usb_response_buffer), 
                    "Motor3 speed set to %d Hz\r\n", speed);
            break;   
        default:
            snprintf(usb_response_buffer, sizeof(usb_response_buffer), 
                    "Error: Invalid motor number %d\r\n", motor_num);
            break;
    }
}


/**
  * @brief  显示系统状态
  * @param  无
  * @retval 无
  */
static void Show_System_Status(void) {
    // 格式化系统状态信息，包括各个电机的速度、位置、运动状态
    char status_msg[256];
    snprintf(status_msg, sizeof(status_msg),
            "=== System Status ===\r\n"
            "Motor1: Speed=%dHz, Pos=%.1fmm, Moving=%d\r\n"
            "Motor2: Speed=%dHz, Pos=%.1fmm, Moving=%d\r\n"
            "Motor3: Speed=%dHz, Pos=%.1fmm, Moving=%d\r\n"
            "====================\r\n",
            motor1.params.default_speed, motor1.current_pos, motor1.is_moving,
            motor2.params.default_speed, motor2.current_pos, motor2.is_moving,
            motor3.params.default_speed, motor3.current_pos, motor3.is_moving);

    // 将状态信息复制到响应缓冲区
    strncpy(usb_response_buffer, status_msg, sizeof(usb_response_buffer));
}

/**
  * @brief  显示帮助信息
  * @param  无
  * @retval 无
  */
static void Show_Help(void) {
    // 定义帮助信息字符串，包含所有可用命令的说明
    const char* help_msg = 
        "=== Available Commands ===\r\n"
        "Motor Speed Control:\r\n"
        "  SPEED1=1000 - Set motor1 speed to 1000Hz\r\n"
        "  SPEED2=1500 - Set motor2 speed to 1500Hz\r\n"
        "  SPEED3=800  - Set motor3 speed to 800Hz\r\n"
        
        "\r\n"
        "Motor Step Control:\r\n"
        "  STEPLEN1=100 - Set motor1 step length to 100\r\n"
        "  STEPLEN2=100 - Set motor2 step length to 100\r\n"
        "  STEPLEN3=100 - Set motor3 step length to 100\r\n"
        
        "  STEPCOUNT1=500 - Set motor1 step count to 500\r\n"
        "  STEPCOUNT2=500 - Set motor2 step count to 500\r\n"
        "  STEPCOUNT3=500 - Set motor3 step count to 500\r\n"
        
        "\r\n"
        "Non-blocking Motor Movement:\r\n"
        "  MOVE1=100:1 - Move motor1 100mm in clockwise direction\r\n"
        "  MOVE2=50:0  - Move motor2 50mm in counter-clockwise direction\r\n"
        "  MOVE3=75:1  - Move motor3 75mm in clockwise direction\r\n"
        
        "  (Format: MOVE<num>=<distance>:<direction>, direction: 0=CCW, 1=CW)\r\n"
        "\r\n"
        "Scan Commands:\r\n"
        "  SCAN_*      - Disabled in firmware command layer\r\n"
        "               Use host-side scan controller only\r\n"
        "\r\n"
        "System Commands:\r\n"
        "  STATUS - Show system status\r\n"
        "  HELP   - Show this help message\r\n"
        "  RESET  - Reset all motors to origin\r\n"
        "\r\n"
        "GPIO Control:\r\n"
        "  PB14=1 - Disable motor (non-enable)\r\n"
        "  PB14=0 - Enable motor (enable)\r\n"
        "==========================\r\n";
    
    // 将帮助信息复制到响应缓冲区
    strncpy(usb_response_buffer, help_msg, sizeof(usb_response_buffer));
}

/**
  * @brief  使用非阻塞模式移动电机指定距离
  * @param  motor_num: 电机编号(1-3)
  * @param  distance: 移动距离(mm)
  * @param  direction: 方向(0=逆时针, 1=顺时针)
  * @retval 无
  */
static void Move_Motor_NonBlocking(uint8_t motor_num, float distance, uint8_t direction) {
    // 限制距离为正数
    if (distance <= 0) {
        snprintf(usb_response_buffer, sizeof(usb_response_buffer), 
                "Error: Distance must be positive\r\n");
        return;
    }
    
    // 根据电机编号和方向控制对应的电机移动
    switch(motor_num) {
        case 1:
            Motor_MoveMMNonBlocking(&motor1, direction, distance);
            snprintf(usb_response_buffer, sizeof(usb_response_buffer), 
                    "Motor1 moving %.1fmm %s\r\n", 
                    distance, direction ? "Clockwise" : "Counter-clockwise");
            break;
        case 2:
            Motor_MoveMMNonBlocking(&motor2, direction, distance);
            snprintf(usb_response_buffer, sizeof(usb_response_buffer), 
                    "Motor2 moving %.1fmm %s\r\n", 
                    distance, direction ? "Clockwise" : "Counter-clockwise");
            break;
        case 3:
            Motor_MoveMMNonBlocking(&motor3, direction, distance);
            snprintf(usb_response_buffer, sizeof(usb_response_buffer), 
                    "Motor3 moving %.1fmm %s\r\n", 
                    distance, direction ? "Clockwise" : "Counter-clockwise");
            break;
        default:
            snprintf(usb_response_buffer, sizeof(usb_response_buffer), 
                    "Error: Invalid motor number %d\r\n", motor_num);
            break;
    }
}

/**
  * @brief  处理单条已去除换行符的命令
  * @param  cmd: 已截断换行符的命令字符串
  * @param  out_buf: 输出缓冲区，用于写入本条命令的响应
  * @param  buf_size: 输出缓冲区大小
  * @retval 无
  * @note   将原 USB_Command_Process 中的 if-else 分支提取至此，便于多命令循环调用。
  */
static void Process_Single_Command(const char* cmd, char* out_buf, size_t buf_size) {
    // PB3/PB4/PB5 控制命令已移除
    if (strncmp(cmd, "SPEED", 5) == 0) {
        // 处理电机速度控制命令
        char motor_char = cmd[5];
        if (motor_char >= '1' && motor_char <= '4') {
            uint8_t motor_num = motor_char - '0';
            const char* equal_pos = strchr(cmd, '=');
            if (equal_pos) {
                uint16_t speed = atoi(equal_pos + 1);
                Set_Motor_Speed(motor_num, speed);
            } else {
                snprintf(out_buf, buf_size,
                        "Error: Invalid SPEED command format\r\n");
            }
        } else {
            snprintf(out_buf, buf_size,
                    "Error: Invalid motor number in SPEED command\r\n");
        }
    }
    else if (strcmp(cmd, "STATUS") == 0) {
        // 处理STATUS命令，显示系统状态
        Show_System_Status();
        strncpy(out_buf, usb_response_buffer, buf_size - 1);
        out_buf[buf_size - 1] = '\0';
        return; // Show_System_Status 写入全局缓冲区，需要复制出来
    }
    else if (strcmp(cmd, "HELP") == 0) {
        // 处理HELP命令，显示帮助信息
        Show_Help();
        strncpy(out_buf, usb_response_buffer, buf_size - 1);
        out_buf[buf_size - 1] = '\0';
        return;
    }
    else if (strcmp(cmd, "RESET") == 0) {
        // 处理RESET命令，停止所有电机
        Motor_Stop(&motor1);
        Motor_Stop(&motor2);
        Motor_Stop(&motor3);

        // 清除移动状态标志
        motor1.is_moving = 0;
        motor2.is_moving = 0;
        motor3.is_moving = 0;

        snprintf(out_buf, buf_size,
                "All motors have been stopped and reset\r\n");
    }
    else if (strncmp(cmd, "MOVE", 4) == 0) {
        // 处理非阻塞式电机移动命令
        // 命令格式: MOVE<num>=<distance>:<direction>
        char motor_char = cmd[4];
        if (motor_char >= '1' && motor_char <= '4') {
            uint8_t motor_num = motor_char - '0';
            const char* equal_pos = strchr(cmd, '=');
            const char* colon_pos = strchr(cmd, ':');

            if (equal_pos && colon_pos && equal_pos < colon_pos) {
                // 提取距离和方向参数
                char distance_str[16];
                char direction_str[2];

                // 计算距离字符串的长度并复制
                int distance_len = colon_pos - equal_pos - 1;
                if (distance_len < (int)sizeof(distance_str)) {
                    strncpy(distance_str, equal_pos + 1, distance_len);
                    distance_str[distance_len] = '\0';
                } else {
                    snprintf(out_buf, buf_size,
                            "Error: Distance parameter too long\r\n");
                    return;
                }

                // 复制方向字符串
                strncpy(direction_str, colon_pos + 1, sizeof(direction_str) - 1);
                direction_str[sizeof(direction_str) - 1] = '\0';

                // 转换为浮点数距离和整数方向
                float distance = atof(distance_str);
                uint8_t direction = atoi(direction_str);

                // 方向值只能是0或1
                if (direction != 0 && direction != 1) {
                    snprintf(out_buf, buf_size,
                            "Error: Direction must be 0 (CCW) or 1 (CW)\r\n");
                    return;
                }

                // 调用非阻塞式电机移动函数
                Move_Motor_NonBlocking(motor_num, distance, direction);
                // Move_Motor_NonBlocking 写入全局缓冲区，复制出来
                strncpy(out_buf, usb_response_buffer, buf_size - 1);
                out_buf[buf_size - 1] = '\0';
                return;
            } else {
                snprintf(out_buf, buf_size,
                        "Error: Invalid MOVE command format. Use MOVE<num>=<distance>:<direction>\r\n");
            }
        } else {
            snprintf(out_buf, buf_size,
                    "Error: Invalid motor number in MOVE command\r\n");
        }
    }
    else if (strncmp(cmd, "SCAN_", 5) == 0) {
        // 扫描控制入口在命令层禁用，避免与上位机扫描状态机并存导致误操作。
        snprintf(out_buf, buf_size,
                "Scan command disabled in firmware. Use host-side scan control.\r\n");
    }
    else if (strncmp(cmd, "PB14=", 5) == 0) {
        // 处理PB14控制命令
        const char* value_str = cmd + 5;
        int value = atoi(value_str);

        if (value == 1) {
            // PB14=1: 非使能（关闭电机）
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_14, GPIO_PIN_SET);
            snprintf(out_buf, buf_size,
                    "Motor disabled (PB14=1)\r\n");
        } else if (value == 0) {
            // PB14=0: 使能（开启电机）
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_14, GPIO_PIN_RESET);
            snprintf(out_buf, buf_size,
                    "Motor enabled (PB14=0)\r\n");
        } else {
            // 无效值
            snprintf(out_buf, buf_size,
                    "Error: PB14 value must be 0 or 1\r\n");
        }
    }
    else {
        // 未知命令，输出错误信息
        snprintf(out_buf, buf_size,
                "Error: Unknown command '%s'. Type HELP for available commands.\r\n", cmd);
    }
}

/**
  * @brief  USB命令处理函数（支持批量命令：按换行符分割，逐条处理）
  * @param  command: 接收到的命令字符串（可包含多条\n分隔的命令）
  * @retval 无
  */
void USB_Command_Process(char* command) {
    // 清除全局响应缓冲区
    memset(usb_response_buffer, 0, sizeof(usb_response_buffer));

    char* start = command;
    char cmd[64];
    char single_response[USB_RESPONSE_BUFFER_SIZE];
    int total_offset = 0;

    // 按换行符分割，逐条处理
    while (*start) {
        // 跳过前导换行符
        while (*start == '\r' || *start == '\n') {
            start++;
        }
        if (*start == '\0') break;

        // 找到本条命令结尾
        char* end = start;
        while (*end && *end != '\r' && *end != '\n') {
            end++;
        }

        // 计算命令长度
        size_t len = end - start;
        if (len == 0) {
            start = end;
            continue;
        }
        if (len >= sizeof(cmd)) len = sizeof(cmd) - 1;

        // 复制本条命令
        memcpy(cmd, start, len);
        cmd[len] = '\0';

        // 清空单条响应缓冲区
        memset(single_response, 0, sizeof(single_response));

        // 处理本条命令
        Process_Single_Command(cmd, single_response, sizeof(single_response));

        // 将单条响应追加到全局缓冲区
        int resp_len = strlen(single_response);
        if (resp_len > 0 && total_offset + resp_len + 2 < (int)sizeof(usb_response_buffer)) {
            if (total_offset > 0) {
                usb_response_buffer[total_offset++] = '\r';
                usb_response_buffer[total_offset++] = '\n';
            }
            memcpy(usb_response_buffer + total_offset, single_response, resp_len);
            total_offset += resp_len;
        }

        // 移动到下一条命令
        start = end;
    }

    // 发送汇总响应到USB
    if (total_offset > 0) {
        usb_response_buffer[total_offset] = '\0';
        USB_Send_Response(usb_response_buffer);
    }
}
