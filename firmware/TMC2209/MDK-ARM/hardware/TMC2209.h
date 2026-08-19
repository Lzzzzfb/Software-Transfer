#ifndef __TMC2209_H
#define __TMC2209_H

#include "stm32f1xx_hal.h"
#include "stm32f1xx_hal_uart.h"

typedef enum {
    TMC_AXIS_X = 0x00,
    TMC_AXIS_Y1 = 0x01,
    TMC_AXIS_Y2 = 0x02,
    TMC_AXIS_Z = 0x03
} TMC_AxisAddr;

void TMC2209_WriteRegister(TMC_AxisAddr axis, uint8_t reg_addr, uint32_t value);


#endif
