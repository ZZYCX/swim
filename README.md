# WeChat Ticket Assistant

Windows 电脑微信小程序抢票自动化脚本。程序根据本机日期选择次日的小程序链接，在微信 `swim` 聊天中发送并点击链接，进入小程序后关闭须知弹窗，并单次点击“提交订单”。

项目只使用本机桌面自动化和图像检测，不调用微信内部接口，不逆向小程序协议。

## 环境要求

- Windows 10/11
- Python 3.12（项目要求 `>=3.12`）
- Windows 微信APP已安装并登录
- 执行抢票时，用户保持登录、桌面保持解锁、当前微信聊天为 `swim`

进入项目目录：

```powershell
cd D:\Desktop\swim
```

## 安装依赖

以下两种方式任选其一。

### 使用 uv 安装（推荐）

```powershell
pip install uv
uv sync
```

### 使用 pip 安装

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

如果 PowerShell 禁止执行虚拟环境激活脚本，可在当前终端临时执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## 创建每日计划任务

安装命令只需执行一次。安装完成后，Windows 任务计划程序会按照配置每天自动启动任务，无需每天重新创建。仅在需要修改 `TargetTime`、`WakeLeadSeconds`、任务名称或其他配置时，才需要重新执行安装命令。

例如，让调度器每天 `23:59:00` 启动，并使 `main.py` 尽量在 `23:59:54` 开始：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_scheduled_task.ps1 -TargetTime "23:59:54" -WakeLeadSeconds 54
```

默认任务名为 `SwimTicketAssistant`。自定义任务名：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_scheduled_task.ps1 -TargetTime "23:59:54" -WakeLeadSeconds 54 -TaskName "SwimTicketAssistant"
```

重复执行安装命令会更新同名任务，不会创建重复任务。

安装脚本会配置：

- 每天执行一次。
- 仅在当前用户已登录时运行，以便桌面自动化访问微信窗口。
- 工作目录为项目目录。
- 已有任务运行时忽略新的并发实例。
- 不唤醒计算机。
- 如果 Windows 错过触发时间，则尽快启动；`scheduler.py` 再根据 10 秒晚到容限决定是否继续。

## 项目组成

每日定时任务由三个文件协作完成：

- `install_scheduled_task.ps1`：创建或更新 Windows 每日计划任务。
- `scheduler.py`：检查锁屏状态、校准启动耗时、精确等待、启动 `main.py`，并将日志同时输出到控制台和文件。
- `main.py`：执行实际微信小程序抢票流程。

## 完整执行顺序

1. Windows 任务计划程序在预定的调度器启动时间触发任务。
2. 计划任务使用 `.venv\Scripts\python.exe` 启动 `scheduler.py`。
3. `scheduler.py` 检查当前桌面是否解锁；锁屏时记录错误并退出。
4. `scheduler.py` 连续执行 3 次：

   ```powershell
   uv run python main.py --startup-probe
   ```

   `--startup-probe` 只测量 `uv + Python + 项目模块导入`耗时，不查找微信，也不执行鼠标键盘操作。
5. 调度器取 3 次探测耗时的中位数作为启动缓冲，并精确等待到正式进程拉起时刻。
6. 调度器执行：

   ```powershell
   uv run python main.py
   ```

7. `main.py` 找到并激活微信窗口，确认当前聊天为 `swim`。
8. `main.py` 根据次日星期选择 URL，清空输入框草稿，粘贴并发送链接。
9. 优先通过 UIA 定位刚发送的完整链接；失败时使用发送前后截图差分定位。
10. 点击链接进入小程序，关闭须知弹窗，检测“提交订单”按钮并只点击一次。
11. `scheduler.py` 记录正式进程启动时间、首条 `main.py` 日志相对目标时间的偏差和最终退出码。

## TargetTime 说明

`TargetTime` 表示每天希望 `main.py` 进入 `main()` 的目标时刻，格式必须为 `HH:mm:ss`。

它不是以下时间：

- 不是 Windows 任务计划程序启动 `scheduler.py` 的时间。
- 不是进入小程序页面的时间。
- 不是点击“提交订单”的时间。

时间关系如下：

```text
任务计划触发时间 = TargetTime - WakeLeadSeconds
正式进程拉起时间 = TargetTime - 本次测得的启动缓冲
main() 开始时间 ≈ TargetTime
提交订单时间 = TargetTime + 微信及小程序操作耗时
```

例如，希望每天按以下时序运行：

```text
23:59:00  Windows 启动 scheduler.py
23:59:54  main() 尽量开始执行
00:00:02  小程序页面大约可操作
00:00:03  大约点击“提交订单”
```

应设置：

```text
TargetTime = 23:59:54
WakeLeadSeconds = 54
```

实际页面和提交时间会受电脑性能、微信状态和网络延迟影响。应根据多次运行日志调整 `TargetTime`，而不是把单次实测耗时当作固定值。


## 手动运行自测抢票实现 main.py

安全检测微信窗口、`swim` 聊天、输入框和次日链接，不发送或点击：

```powershell
uv run python main.py --chat-name "swim" --dry-run
```

执行完整抢票流程：

```powershell
uv run python main.py --chat-name "swim"
```

主要参数：

- `--chat-name`：目标聊天名称，默认 `swim`
- `--debug-dir`：失败截图目录，默认 `./debug`
- `--debug-mode`：`failure` 仅在失败时保存截图，`all` 保存全部过程截图；默认 `failure`
- `--max-wait`：等待小程序页面的最长秒数，默认 `20`
- `--dry-run`：只验证运行环境，不发送链接和点击

## Windows 任务计划程序

### 查看任务

```powershell
Get-ScheduledTask -TaskName "SwimTicketAssistant"
Get-ScheduledTaskInfo -TaskName "SwimTicketAssistant"
```

重点字段：

- `State`：通常为 `Ready`；运行时为 `Running`。
- `NextRunTime`：下一次启动 `scheduler.py` 的时间，不是 `TargetTime`。
- `LastTaskResult`：上一次任务返回值；`0` 表示成功。

查看任务实际参数：

```powershell
Get-ScheduledTask -TaskName "SwimTicketAssistant" |
  Select-Object TaskName, State,
    @{Name='Execute'; Expression={$_.Actions.Execute}},
    @{Name='Arguments'; Expression={$_.Actions.Arguments}},
    @{Name='Trigger'; Expression={$_.Triggers.StartBoundary}} |
  Format-List
```

### 更新任务时间

使用新的 `TargetTime` 和 `WakeLeadSeconds` 重新执行安装脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_scheduled_task.ps1 -TargetTime "16:23:54" -WakeLeadSeconds 54
```

### 手动启动任务

```powershell
Start-ScheduledTask -TaskName "SwimTicketAssistant"
```

手动启动仍然使用任务中保存的每日 `TargetTime`。如果当前时间距离目标时间很远，不建议用此命令测试；应使用下一节的安全测试方式。

### 停止或删除任务

```powershell
Stop-ScheduledTask -TaskName "SwimTicketAssistant"
Unregister-ScheduledTask -TaskName "SwimTicketAssistant" -Confirm:$false
```

## 安全测试定时精度

将目标时间设置为当前时间之后 20 秒，并让正式流程使用 `main.py --dry-run`：

```powershell
$target = (Get-Date).AddSeconds(20).ToString("HH:mm:ss")
.\.venv\Scripts\python.exe .\scheduler.py --target-time $target --main-dry-run
```

该命令会完成启动校准和微信状态检查，但不会发送链接或点击。日志会显示：

- 3 次启动探测耗时。
- 采用的中位数启动缓冲。
- 正式进程拉起时刻。
- 首条 `main.py` 日志与 `TargetTime` 的偏差。
- `main.py` 退出码。

## 日志

调度器和 `main.py` 的输出保存在：

```text
logs/YYYY-MM-DD.log
```

查看当天最后 100 行日志：

```powershell
Get-Content .\logs\$(Get-Date -Format 'yyyy-MM-dd').log -Tail 100
```

`main.py` 日志格式：

```text
[14:15:20.123 +2.438s] [OK] 已发送次日小程序链接：...
```

页面可操作时会输出：

```text
[14:15:26.106 +8.421s] [TIMING] 从 main.py 启动到小程序页面可操作，总耗时=8.421秒
```

## 运行条件与限制

- 电脑必须保持开机，当前用户必须保持登录。
- 桌面必须保持解锁；锁屏时调度器会退出。
- 微信必须已登录并显示主窗口，当前聊天必须为 `swim`。
- 微信最小化到托盘或窗口不存在时，`main.py` 无法找到微信并会退出。
- 目标时间晚到 10 秒以内会立即执行；超过 10 秒会取消，不等待次日。
- 不绕过验证码或微信登录。
- 不自动支付。
- 不循环、不并发、不高频点击。
- “提交订单”只点击一次。

## 模拟自测

```text
[2026-06-10 16:53:00.228] [SCHEDULER] 目标 main.py 启动时间=2026-06-10 16:53:54，任务提前唤起=54秒，晚到容限=10.0秒
[2026-06-10 16:53:00.229] [SCHEDULER] 项目目录=D:\Desktop\swim
[2026-06-10 16:53:00.231] [SCHEDULER] uv 路径=C:\Users\serendipity\.local\bin\uv.exe
[MAIN] [16:53:01.136 +0.001s] [STARTUP-PROBE] main.py 已进入 main()
[2026-06-10 16:53:01.334] [SCHEDULER] 启动探测 1/3：0.905 秒
[MAIN] [16:53:02.234 +0.001s] [STARTUP-PROBE] main.py 已进入 main()
[2026-06-10 16:53:02.417] [SCHEDULER] 启动探测 2/3：0.901 秒
[MAIN] [16:53:03.305 +0.001s] [STARTUP-PROBE] main.py 已进入 main()
[2026-06-10 16:53:03.484] [SCHEDULER] 启动探测 3/3：0.889 秒
[2026-06-10 16:53:03.484] [SCHEDULER] 启动探测结果=[0.905s, 0.901s, 0.889s]，采用中位数缓冲=0.901秒
[2026-06-10 16:53:03.484] [SCHEDULER] 等待至正式进程拉起时刻=2026-06-10 16:53:53.099419
[2026-06-10 16:53:53.100] [SCHEDULER] 执行命令=C:\Users\serendipity\.local\bin\uv.exe run python D:\Desktop\swim\main.py
[MAIN] [16:53:54.031 +0.001s] [INFO] 当前日期=2026-06-10，当前=星期三，选择次日=星期四，链接=#小程序://奥冠体育/WkeN4N7l2i1n9Hd
[MAIN] [16:53:54.753 +0.723s] [OK] 找到微信窗口：title='微信', left=-9, top=-9, width=2578, height=1408
[MAIN] [16:53:54.912 +0.882s] [OK] 当前已打开目标聊天：swim
[MAIN] [16:53:55.051 +1.021s] [OK] 通过 UIA 定位聊天输入框：rect=Rect(left=607, top=1046, right=2534, bottom=1314), point=(1570, 1180)
[MAIN] [16:53:56.733 +2.703s] [OK] 已发送次日小程序链接：#小程序://奥冠体育/WkeN4N7l2i1n9Hd
[MAIN] [16:53:56.852 +2.822s] [OK] 定位刚发送的链接：center=(2082, 985), method=uia-link
[MAIN] [16:53:58.013 +3.983s] [OK] 已点击次日小程序链接：center=(2082, 985)
[MAIN] [16:54:01.357 +7.327s] [OK] 已关闭须知弹窗：center=(1280, 973)
[MAIN] [16:54:02.423 +8.394s] [OK] 检测到小程序页面并锁定橙色提交区域。
[MAIN] [16:54:02.423 +8.394s] [TIMING] 从 main.py 启动到小程序页面可操作，总耗时=8.394秒
[MAIN] [16:54:02.427 +8.397s] [OK] 复用已验证的提交订单按钮：bbox=Rect(left=1355, top=1120, right=1518, bottom=1174), center=(1436, 1147)
[MAIN] [16:54:03.573 +9.543s] [DONE] 已点击一次“提交订单”。
[2026-06-10 16:54:03.784] [SCHEDULER] 正式进程拉起时刻=2026-06-10 16:53:53.100305
[2026-06-10 16:54:03.785] [SCHEDULER] 首条 main.py 日志时刻=2026-06-10 16:53:54.031723，相对目标偏差=+0.032秒
[2026-06-10 16:54:03.785] [SCHEDULER] main.py 退出码=0
```
