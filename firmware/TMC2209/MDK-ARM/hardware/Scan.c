#include "Scan.h"
#include "PUL.h"
#include "USB_Command.h"

// 扫描状态全局变量
Scan_Status scan_status;

// 局部变量
static uint8_t scan_in_progress = 0;      // 扫描是否正在进行
static uint8_t scan_step_complete = 0;    // 扫描步骤是否完成
static uint8_t scan_loop_limit = 10;      // 单个方格内扫描循环次数
static uint8_t scan_stage = 0;            // 10x10扫描阶段
static uint8_t scan_loop_count = 0;       // 10x10扫描循环计数
static uint8_t scan_direction = DIR_CW;   // 10x10扫描方向
static uint8_t reset_stage = 0;           // 复位阶段


static void Scan_ResetRuntimeState(void)
{
    scan_in_progress = 0;
    scan_step_complete = 0;
    scan_stage = 0;
    scan_loop_count = 0;
    scan_direction = DIR_CW;
    reset_stage = 0;

}

/**
 * @brief 初始化扫描功能
 * @param 无
 * @retval 无
 */
void Scan_Init(void)
{
    // 初始化扫描参数
    scan_status.config.grid_size = 10.0f;
    scan_status.config.scan_step_x = 5.0f;
    scan_status.config.scan_step_y = 0.50f;
    scan_status.config.grid_count_x = 1;   // X轴方格数量
    scan_status.config.grid_count_y = 1;  // Y轴方格数量
    scan_status.config.move_step_x = 10.0f;
    scan_status.config.move_step_y = 10.0f;
    
    // 初始化扫描状态
    scan_status.state = SCAN_IDLE;
    scan_status.current_grid_x = 0;
    scan_status.current_grid_y = 0;
    scan_status.scan_step = 0;
    
    // 初始化运行时状态
    Scan_ResetRuntimeState();
}

/**
 * @brief 10*10mm方格扫描功能
 * @param 无
 * @retval void
 * @note 新扫描流程：
 * 1. X轴正向运动10mm
 * 2. 进入10次循环：
 *    - Y轴正向运动1mm
 *    - X轴运动10mm（第一次反向，每次调转方向）
 */
void Scan_10x10Grid(void)
{
    if (scan_in_progress == 0)
    {
        // 开始新的扫描
        scan_in_progress = 1;
        scan_stage = 0;
        scan_loop_count = 0;
        scan_direction = DIR_CW;
        scan_step_complete = 0;
    }
    
    switch (scan_stage)
    {
        case 0: // 第一步：X轴正向运动（非阻塞）
            // X轴正向移动一个X步距（电机1）
            Motor_MoveMMNonBlocking(&motor1, DIR_CCW, scan_status.config.scan_step_x);
            scan_stage++;
            break;
            
        case 1: // 等待X轴初始移动完成
            if (!motor1.is_moving)
            {
                scan_stage++;
            }
            break;
            
        case 2: // 第二步：进入固定次数循环，先移动Y轴
            if (scan_loop_count < scan_loop_limit)
            {
                // Y轴正向移动一个Y步距（电机2）
                Motor_MoveMMNonBlocking(&motor2, DIR_CCW, scan_status.config.scan_step_y);
                scan_stage++;
            }
            else
            {
                // 循环完成，扫描结束
                scan_in_progress = 0;
                scan_step_complete = 1;
                scan_stage = 0;
                scan_loop_count = 0;
            }
            break;
            
        case 3: // 等待Y轴移动完成
            if (!motor2.is_moving)
            {
                // Y轴移动完成，开始移动X轴
                Motor_MoveMMNonBlocking(&motor1, scan_direction, scan_status.config.scan_step_x);
                scan_stage++;
            }
            break;
            
        case 4: // 等待X轴移动完成
            if (!motor1.is_moving)
            {
                // 调转X轴方向
                scan_direction = (scan_direction == DIR_CW) ? DIR_CCW : DIR_CW;
                scan_loop_count++;
                scan_stage = 2; // 回到Y轴移动阶段
            }
            break;
            
        default:
            scan_stage = 0;
            break;
    }
}

/**
 * @brief 扫描复位功能
 * @param 无
 * @retval void
 * @note 扫描完成后复位（X反向移动10mm，Y反向移动10mm）
 */
void Scan_Reset(void)
{
    float reset_x = scan_status.config.scan_step_x;
    float reset_y = scan_status.config.scan_step_y * scan_loop_limit;
    
    switch (reset_stage)
    {
        case 0: // 开始X轴复位
            Motor_MoveMMNonBlocking(&motor1, DIR_CW, reset_x);
            reset_stage++;
            break;
        
        case 1: // 等待X轴复位完成
            if (!motor1.is_moving)
            {
                // X轴复位完成，开始Y轴复位
                Motor_MoveMMNonBlocking(&motor2, DIR_CW, reset_y);
                reset_stage++;
            }
            break;
        
        case 2: // 等待Y轴复位完成
            if (!motor2.is_moving)
            {
                // 复位完成
                scan_step_complete = 1;
                reset_stage = 0;
            }
            break;
        
        default:
            reset_stage = 0;
            break;
    }
}

/**
 * @brief 开始扫描
 * @param 无
 * @retval void
 */
void Scan_Start(void)
{
    if (scan_status.state == SCAN_IDLE || scan_status.state == SCAN_COMPLETED)
    {
        Scan_ResetRuntimeState();

        // 初始化扫描参数
        scan_status.current_grid_x = 0;
        scan_status.current_grid_y = 0;
        scan_status.scan_step = 0;
        scan_status.state = SCAN_RUNNING;
        
        // 重置局部变量
        scan_in_progress = 0;
        scan_step_complete = 0;
        
        // 发送扫描开始指令
        USB_Send_Response("SCAN_START\r\n");
    }
}

/**
 * @brief 停止扫描
 * @param 无
 * @retval void
 */
void Scan_Stop(void)
{
    if (scan_status.state == SCAN_RUNNING || scan_status.state == SCAN_PAUSED)
    {
        scan_status.state = SCAN_IDLE;
        Scan_ResetRuntimeState();
        // 发送停止扫描响应
        USB_Send_Response("SCAN_STOP\r\n");
    }
}

/**
 * @brief 暂停扫描
 * @param 无
 * @retval void
 */
void Scan_Pause(void)
{
    if (scan_status.state == SCAN_RUNNING)
    {
        scan_status.state = SCAN_PAUSED;
        // 发送暂停扫描响应
        USB_Send_Response("SCAN_PAUSE\r\n");
    }
}

/**
 * @brief 恢复扫描
 * @param 无
 * @retval void
 */
void Scan_Resume(void)
{
    if (scan_status.state == SCAN_PAUSED)
    {
        scan_status.state = SCAN_RUNNING;
        USB_Send_Response("SCAN_RESUME\r\n");
    }
}

/**
 * @brief 更新扫描状态
 * @param 无
 * @retval void
 * @note 需要定期调用此函数以更新扫描进度
 */
void Scan_Update(void)
{
	static uint8_t move_direction = DIR_CCW;  // 扫描方向，初始为正向
    // 只有在运行状态下才执行扫描逻辑
    if (scan_status.state != SCAN_RUNNING)
    {
        return;
    }
    
    // 状态机实现扫描逻辑
    switch (scan_status.scan_step)
    {
        case 0: // 执行10*10mm扫描
            scan_step_complete = 0;
            Scan_10x10Grid();
            if (scan_step_complete == 1)
            {
                // 扫描完成，进入下一个步骤
                scan_status.scan_step++;
                scan_step_complete = 0;
                USB_Send_Response("SCAN_GRID_COMPLETE\r\n");
            }
            break;
            
        case 1: // 扫描完成后复位
            scan_step_complete = 0;
            Scan_Reset();
            if (scan_step_complete == 1)
            {
                // 复位完成，进入下一个步骤
                scan_status.scan_step++;
                scan_step_complete = 0;
                USB_Send_Response("SCAN_RESET_COMPLETE\r\n");
            }
            break;
            
        case 2: // 移动到下一个方格或换行
            // 检查是否需要移动到下一个方格
            if (scan_status.current_grid_x < scan_status.config.grid_count_x - 1)
            {
                // 移动到下一个方格（按列距移动X轴）
                Motor_MoveMMNonBlocking(&motor1, move_direction, scan_status.config.move_step_x);
                scan_status.current_grid_x++;
                scan_status.scan_step++; // 进入等待移动完成状态
                USB_Send_Response("SCAN_MOVE_TO_NEXT_X\r\n");
            }
            else
            {
                // 完成一行，检查是否需要换行
            if (scan_status.current_grid_y < scan_status.config.grid_count_y - 1)
            {
                // 换行（按行距移动Y轴）
                Motor_MoveMMNonBlocking(&motor2, DIR_CCW, scan_status.config.move_step_y);
                scan_status.current_grid_y++;
		// 调转X轴方向
		move_direction = (move_direction == DIR_CW) ? DIR_CCW : DIR_CW;
		
                    scan_status.current_grid_x = 0;
                    scan_status.scan_step++; // 进入等待移动完成状态
                    USB_Send_Response("SCAN_MOVE_TO_NEXT_Y\r\n");
                }
                else
                {
                    scan_status.state = SCAN_COMPLETED;
                    USB_Send_Response("SCAN_COMPLETED\r\n");
                    scan_status.scan_step = 0;
                }
            }
            break;
            
        case 3: // 等待方格移动完成
            // 检查电机是否还在移动
            if (!motor1.is_moving && !motor2.is_moving)
            {
                // 移动完成，返回扫描步骤
                scan_status.scan_step = 0;
            }
            break;
            
        default:
            scan_status.scan_step = 0;
            break;
    }
}
