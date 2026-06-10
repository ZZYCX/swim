# WeChat Ticket Assistant

Windows 电脑微信小程序抢票页面桌面自动化脚本。脚本根据本机日期选择当天的小程序链接，在指定微信聊天中发送并点击链接，然后检测小程序页面并单次点击“提交订单”。

脚本只使用本机桌面自动化和图像检测，不调用微信内部接口，不逆向小程序协议。

## 安装

```powershell
cd D:\Desktop\swim
uv sync
```

## 运行

只验证微信窗口、`swim` 聊天、输入框和当天链接，不清空草稿、不发送、不点击：

```powershell
uv run python main.py --chat-name "swim" --dry-run
```

完整流程：

```powershell
uv run python main.py --chat-name "swim"
```

可选参数：

- `--chat-name`：目标聊天名称，默认 `swim`
- `--debug-dir`：debug 截图目录，默认 `./debug`
- `--debug-mode`：`all` 保存全部过程图，`failure` 仅失败保存最后截图；默认 `all`
- `--max-wait`：等待小程序页面最长秒数，默认 `20`
- `--dry-run`：只验证可见状态和当天链接，不执行桌面写操作

## 执行流程

1. 找到并激活 Windows 微信窗口。
2. 确认当前聊天名称为 `swim`，不会自动搜索或切换聊天。
3. 根据本机日期选择周一至周日对应的小程序链接。
4. 清空聊天输入框中的原有草稿，粘贴并发送当天链接。
5. 优先通过 UIA 定位刚发送的链接；失败时使用发送前后截图差分定位。
6. 点击链接并等待小程序页面。
7. 必要时关闭须知弹窗，定位并单次点击“提交订单”。

如果链接无法可靠定位，脚本会保存截图并退出，不会按固定位置盲点，也不会回退到二维码流程。

## Debug 截图

`--debug-mode all` 会保存完整过程截图：

- `debug/debug_01_wechat_found.png`
- `debug/debug_02_swim_chat.png`
- `debug/debug_03_before_link_send.png`
- `debug/debug_04_after_link_send.png`
- `debug/debug_05_link_detected.png`
- `debug/debug_06_after_link_click.png`
- `debug/debug_07_miniprogram_page.png`
- `debug/debug_08_submit_button.png`
- `debug/debug_09_after_click.png`

`--debug-mode failure` 仅在失败时保存最后一张可用截图：

- `debug/debug_failure_<step>.png`

任一步失败会输出失败步骤、原因和对应截图路径，然后立即退出。


## 行为限制

- 不绕过验证码
- 不绕过微信登录
- 不自动支付
- 不高频点击
- 不循环抢票
- 不并发请求
- `提交订单` 只点击一次
