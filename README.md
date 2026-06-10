# WeChat Ticket Assistant

Windows 电脑微信小程序抢票页面桌面自动化脚本。脚本只使用本机桌面自动化和图像检测，不调用微信内部接口，不逆向小程序协议。

## 安装

```powershell
cd D:\Desktop\swim\wechat_ticket_assistant
uv sync
```

## 运行

调试检测，不执行鼠标/键盘点击：

```powershell
uv run python main.py --chat-name "swim" --dry-run
```

默认完整流程：

```powershell
uv run python main.py --chat-name "swim"
```

可选参数：

- `--chat-name`：目标聊天名称，默认 `swim`
- `--debug-dir`：debug 截图目录，默认 `./debug`
- `--no-ocr`：兼容参数，默认不依赖 OCR
- `--max-wait`：等待小程序页面最长秒数，默认 `10`
- `--dry-run`：只检测，不执行鼠标/键盘点击

## Debug 截图

脚本会保存以下截图：

- `debug/debug_01_wechat_found.png`
- `debug/debug_02_swim_chat.png`
- `debug/debug_03_qr_detected.png`
- `debug/debug_04_image_viewer.png`
- `debug/debug_05_context_menu.png`
- `debug/debug_06_after_qr_recognition.png`
- `debug/debug_07_miniprogram_page.png`
- `debug/debug_08_submit_button.png`
- `debug/debug_09_after_click.png`

任一步失败会输出失败步骤、原因和最近一张 debug 截图路径，然后立即退出。

## 行为限制

- 不绕过验证码
- 不绕过微信登录
- 不自动支付
- 不高频点击
- 不循环抢票
- 不并发请求
- `提交订单` 只点击一次
