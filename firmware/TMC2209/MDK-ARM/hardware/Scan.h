#ifndef __SCAN_H
#define __SCAN_H

#include "PUL.h"

// 扫描状态枚举
typedef enum {
    SCAN_IDLE,          // 空闲状态
    SCAN_RUNNING,       // 正在扫描
    SCAN_PAUSED,        // 暂停
    SCAN_COMPLETED,     // 扫描完成
    SCAN_ERROR          // 扫描错误
} Scan_State;

// 扫描配置结构体
typedef struct {
    float grid_size;        // 方格大小（mm）
    float scan_step_x;      // X轴扫描步长（mm）
    float scan_step_y;      // Y轴扫描步长（mm）
    uint8_t grid_count_x;   // X轴方格数量
    uint8_t grid_count_y;   // Y轴方格数量
    float move_step_x;      // 方格间X轴移动距离（mm）
    float move_step_y;      // 方格间Y轴移动距离（mm）
} Scan_Config;

// 扫描状态结构体
typedef struct {
    Scan_State state;       // 当前扫描状态
    uint8_t current_grid_x; // 当前X轴方格索引（0-3）
    uint8_t current_grid_y; // 当前Y轴方格索引（0-7）
    uint8_t scan_step;      // 当前扫描步骤
    Scan_Config config;     // 扫描配置
} Scan_Status;

// 外部声明扫描状态
extern Scan_Status scan_status;

// 函数声明
void Scan_Init(void);
void Scan_Start(void);
void Scan_Stop(void);
void Scan_Pause(void);
void Scan_Resume(void);
void Scan_Update(void);
void Scan_10x10Grid(void);
void Scan_Reset(void);

#endif
