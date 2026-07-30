#ifndef __USB_COMMAND_H
#define __USB_COMMAND_H

#include "stm32f1xx_hal.h"
#include "PUL.h"

// 命令响应缓冲区大小
#define USB_RESPONSE_BUFFER_SIZE 128

// 函数声明
void USB_Command_Init(void);
void USB_Command_Process(char* command);
void USB_Send_Response(char* response);

// 外部变量声明
extern char usb_response_buffer[USB_RESPONSE_BUFFER_SIZE];

#endif
