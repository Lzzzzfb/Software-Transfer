#include "stm32f1xx_hal.h"
#include "stm32f1xx_hal_uart.h"


typedef enum {
    TMC_AXIS_X 	= 0x00,
    TMC_AXIS_Y1 = 0x01,
    TMC_AXIS_Y2 = 0x02,
    TMC_AXIS_Z 	= 0x03
} TMC_AxisAddr;

extern UART_HandleTypeDef huart1;

/**
 * @brief 计算CRC8（多项式 x^8 + x^2 + x^1 + x^0，初始值0）
 * @param data: 数据指针
 * @param len: 数据长度
 * @retval CRC8值
 */
static uint8_t TMC2209_CRC8(uint8_t *data, uint8_t len) {
    uint8_t crc = 0;
    for (uint8_t i = 0; i < len; i++) {
        uint8_t byte = data[i];
        for (uint8_t j = 0; j < 8; j++) {
            if ((crc >> 7) ^ (byte & 0x01)) {
                crc = (crc << 1) ^ 0x07;   // 0x07 = x^2 + x^1 + x^0
            } else {
                crc = (crc << 1);
            }
            byte >>= 1;
        }
    }
    return crc;
}


/**
 * @brief 通过UART向指定TMC2209轴写寄存器（只发送，不等待响应）
 * @param axis: 轴地址（0x00~0x03）
 * @param reg_addr: 寄存器地址（例如GCONF=0x00，IHOLD_IRUN=0x10等）
 * @param value: 32位寄存器值
 */
void TMC2209_WriteRegister(TMC_AxisAddr axis, uint8_t reg_addr, uint32_t value) {
    uint8_t tx_buf[8];

    // 构建数据帧
    tx_buf[0] = 0x05;                     // 起始字节
    tx_buf[1] = (uint8_t)axis;            // 轴地址
    tx_buf[2] = reg_addr | 0x80;          // 写操作：寄存器地址最高位置1
    tx_buf[3] = (uint8_t)(value >> 24);   // 数据字节3（最高位）
    tx_buf[4] = (uint8_t)(value >> 16);   // 数据字节2
    tx_buf[5] = (uint8_t)(value >> 8);    // 数据字节1
    tx_buf[6] = (uint8_t)(value);         // 数据字节0（最低位）
    tx_buf[7] = TMC2209_CRC8(tx_buf, 7);  // CRC校验码

    // 通过UART发送（假设已配置huart1）
    HAL_UART_Transmit(&huart1, tx_buf, 8, 100);
}

