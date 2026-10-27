<p align="center"><img src="public/assets/brand.png" width="80" alt="GetTheLongChain 图标"></p>
<h1 align="center">GetTheLongChain · 长链接生成器</h1>
<p align="center">在本机生成 ChatGPT Team 托管结账链接，支持自有代理、国家搜索与优惠链接解析。</p>

## 功能

- **Team 结账**：新建或选择已有工作区，设置席位数和月／年计费周期。
- **凭证输入**：支持 accessToken、Bearer Token 或完整 Session JSON。
- **国家与货币**：250 个国家／地区选项（ISO 249 项 + XK），支持中文、英文、代码搜索；选择国家自动匹配货币，也可手动调整。
- **优惠码解析**：支持纯优惠码、`https://chatgpt.com/p/CODE`、`chatgpt.com/?promoCode=CODE` 等链接。
- **自有代理**：支持 HTTP、HTTPS、SOCKS 代理，以及出口和 ChatGPT 连通性测试。
- **结果操作**：复制或打开生成链接，显示结账参数。
- **本地排错**：运行日志、错误日志、请求编号、敏感信息脱敏与自动轮转。
- **轻量页面**：原生 HTML/CSS/JavaScript，适配桌面与手机，无需前端构建或数据库。

面向本机单用户使用，仅生成结账链接；付款由用户在结账页核对后完成。不包含 Plus／Go、CDK、账号池、平台代理池或链接历史服务。

## 快速开始

### 环境要求

- Python **3.11+**，安装时加入 PATH。
- 可以访问依赖源和 ChatGPT 的网络，必要时使用自己的代理。
- Windows 可使用启动脚本；其他系统可按下文手动启动。

### Windows 一键启动

```powershell
git clone https://github.com/Jankin-Chen123/GetTheLongChain.git
cd GetTheLongChain
.\start.cmd
```

也可下载仓库 ZIP，解压后双击 `start.cmd`。首次启动自动创建 `.venv` 并安装依赖。启动成功后，在浏览器打开 **[http://127.0.0.1:8788](http://127.0.0.1:8788)**。保持启动窗口运行，按 `Ctrl+C` 停止。

### 手动启动

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8788 --no-access-log
```

macOS / Linux：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8788 --no-access-log
```

## 使用方法

1. 在已登录自己 ChatGPT 账号的浏览器中打开 [Session 页面](https://chatgpt.com/api/auth/session)。
2. 将完整 Session JSON 或其中的 `accessToken` 粘贴到本地工具。凭证等同于账号访问权限，请勿提交到 GitHub、截图或聊天中。
3. 需要代理时填写代理地址，可先点击“测试连接”；留空为直连。后端不会自动读取系统 `HTTP_PROXY` 等代理环境变量。
4. 选择新建工作区并填写名称；或切换至已有工作区，查询选择或手填 Workspace UUID。
5. 设置席位、周期、真实账单国家和货币，按需填写优惠码或优惠链接。
6. 点击生成，成功后复制或打开链接，在结账页核对金额、优惠和订阅条款。

### 代理格式与测试

支持以下输入形式（均为占位示例）：

```text
http://127.0.0.1:7890
http://user:password@proxy.example.com:8080
socks5://127.0.0.1:1080
host:port:user:password
```

代理测试无需账号凭证，不创建结账会话。后端通过指定代理依次访问 Cloudflare 出口检测页及 ChatGPT Session 接口，每项最多等待 12 秒；页面显示出口 IP／国家、响应状态和耗时。

| 结果 | 含义 |
| --- | --- |
| DNS、连接、超时、TLS 错误 | 按提示检查地址、端口、网络或证书 |
| HTTP 407 | 代理认证失败，检查用户名和密码 |
| ChatGPT HTTP 401 | 收到未登录响应；测试未发送 Token，不能据此判断账号凭证失效 |
| ChatGPT HTTP 403 | 代理路径有响应，但目标访问被拒绝，可能需要浏览器验证 |

代理测试通过不代表优惠码适用，也不保证结账或付款成功。

### 可选访问口令

启动前设置环境变量，页面即会显示口令输入框：

```powershell
$env:GETTHELONGCHAIN_ACCESS_KEY = '替换为自己的口令'
.\start.ps1
```

本项目不会自动加载 `.env`。服务默认仅监听 `127.0.0.1`，校验 Host 和提交 Origin，当前配置用于本机访问。

## 日志与故障定位

运行时自动创建 `logs/`：

| 文件 | 内容 |
| --- | --- |
| `logs/runtime.log` | 启停、请求开始／结束、执行阶段、上游状态与耗时 |
| `logs/error.log` | 参数校验、连接异常、脱敏后的上游错误和程序异常位置 |

每行是一个 JSON 对象，时间为北京时间（`+08:00`）。使用页面错误详情中的 `request_id` 关联同一次请求的记录；响应头也会返回 `X-Request-ID`。

PowerShell 查看最近错误或查找某次请求：

```powershell
Get-Content .\logs\error.log -Tail 20
Select-String -Path .\logs\*.log* -SimpleMatch '替换为请求编号'
```

日志不记录原始请求体、Session、Token、访问口令、代理地址／凭证、优惠码值、工作区名称／ID、生成链接或原始上游响应；代理出口 IP 也不写入日志。程序异常只记录类型和文件／函数／行号。两个日志文件各达到 5 MiB 后轮转，各保留 5 个历史文件，总容量上限约 60 MiB。

反馈问题时提供发生时间、操作、错误阶段和请求编号即可，避免附带凭证或支付链接。

### 常见问题

| 情况 | 处理建议 |
| --- | --- |
| 401 | 按错误阶段检查本机访问口令，或重新获取 ChatGPT Session |
| 403 | 检查账号权限和代理出口；网页验证可能需要浏览器处理 |
| `Discount code is not eligible` | 请求已到达上游，但优惠资格未通过；核对限制或清空优惠码重试 |
| 其他 400 | 检查账单国家、货币、席位及已有工作区状态 |
| 429 | 等待后重试，避免连续创建多个结账会话 |
| 连接超时 | 检查网络或代理；工具不会自动重复提交 checkout |
| 工作区列表为空 | 确认账号有团队工作区权限，也可手填 Workspace UUID |
| 缺少完整链接或 Stripe 参数 | 上游格式可能变化，保留请求编号和错误阶段以便排查 |
| 端口 8788 被占用 | 检查是否已有工具实例运行；也可在手动启动时更换端口 |

## 数据处理与适用范围

- 账号凭证、代理和访问口令仅用于页面内存及当前请求，不写入浏览器存储或数据库；刷新后需重新填写。
- 生成成功后，浏览器只保存工作区名称、席位数、计费周期这三个表单偏好；不保存生成链接。
- ChatGPT 与 Stripe 使用独立会话，账号 Authorization 不发送给 Stripe。
- 不自动跟随上游重定向，返回链接校验 HTTPS 和准确域名。
- 国家列表表示可选参数集合，实际购买地区、价格和优惠资格由账号及结账页决定。
- 已有用户在本地成功跑通提链；已有工作区的不同订阅状态、所有地区和优惠组合尚未逐项实测。
- 本项目依赖 ChatGPT 网页接口，并非官方稳定 API，接口变化时可能需要适配；本项目与 OpenAI、Stripe 无隶属关系。

## 开发

技术栈：**FastAPI + curl_cffi + 原生 HTML/CSS/JavaScript**。

```text
GetTheLongChain/
├── server.py                  后端、参数校验与 ChatGPT/Stripe 适配
├── app_logging.py             请求关联、日志脱敏与轮转
├── data/countries.json        国家／地区与货币数据
├── public/
│   ├── index.html             页面结构
│   ├── app.js                 表单、搜索与请求交互
│   ├── workspace.css          响应式样式
│   └── assets/brand.png       品牌图标
├── tests/                     模拟接口、代理与日志测试
├── docs/REFERENCE_ANALYSIS.md  初始参考研究
├── requirements.txt           运行依赖
├── requirements-dev.txt       测试依赖
└── start.cmd / start.ps1       Windows 启动入口
```

安装测试依赖并检查（Windows）：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q --tb=short
node --check public/app.js
```

Node.js 仅用于 JavaScript 语法检查，运行网站不需要 Node.js。自动化测试使用模拟上游和临时日志目录，无需真实凭证，也不会执行支付。

2026-10-27 检查结果：**78 项测试通过**，JavaScript 语法检查通过。浏览器已检查国家搜索、货币联动、工作区切换、错误显示及窄屏布局。模拟测试不能覆盖上游接口的全部真实行为。

`logs/`、`.venv/`、本地参考仓库 `reference/` 和研究产物 `research/` 均不纳入版本控制。

## 参考来源

本项目独立实现业务代码和页面，初始研究见 [参考分析](docs/REFERENCE_ANALYSIS.md)。

- [chatgpt-codex-link-analysis-05](https://github.com/rolandbaku011/chatgpt-codex-link-analysis-05)
- [bridge.pp.ua](https://bridge.pp.ua/)
- [ChatGPT Workspace Helper](https://greasyfork.org/en/scripts/581963-chatgpt-workspace-helper/code)
