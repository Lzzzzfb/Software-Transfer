#ifndef __USB_COMMAND_H
#define __USB_COMMAND_H

#include "stm32f1xx_hal.h"

#define MOTOR_PROTOCOL_VERSION 2U
#define USB_COMMAND_LINE_SIZE 96U
#define USB_RESPONSE_BUFFER_SIZE 384U
#define USB_TX_QUEUE_DEPTH 4U

void USB_Command_Init(void);
void USB_Command_Process(char *command);
void USB_Command_Update(void);
void USB_Send_Response(const char *response);

extern char usb_response_buffer[USB_RESPONSE_BUFFER_SIZE];

#endif
