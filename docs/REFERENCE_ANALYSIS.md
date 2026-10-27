# 参考仓库与网站研究

研究日期：2026-10-27。范围为公开代码、网页页面与公开前端资源。不包含登录他人账号、调用付费 CDK 或提交真实支付。

> 本文保留初始研究时的观察与验证范围。后续用户已在本地成功跑通提链，当前功能和验证状态以根目录 README 为准；下文待验证事项不代表当前全部仍未验证。

## 1. 两个参考的定位不同

| 项目 | GitHub 参考仓库 | bridge.pp.ua |
| --- | --- | --- |
| 当前核心 | Plus / Go 的 checkout 与支付服务商跳转提取 | Team 托管结账链接 |
| 源码可见性 | 含后端、前端、部署文件和解析文档 | 可读取网页与公开 app.js，未获得服务端源码 |
| 前端实际状态 | iDEAL 链路控制台，含邮箱池和账号池等入口 | 简洁结账表单，包含自有代理和平台代理 |
| 工作区参数 | 请求模型没有 Team 工作区与席位字段 | 新建／已有工作区、名称、席位数 |
| 付费功能 | 不是该仓库的主要业务模型 | CDK 激活、剩余次数、48 小时内链接历史 |

因此不能把仓库部署后视为已复现 bridge.pp.ua。

## 2. 仓库代码阅读结论

读取版本：`e3107aa63b48ad11fce38737b609d233d1761bd4`。

主要代码在 `source/gpthel-/app/`，重点阅读了 `app.py`、前端 `index.html` / `app.js`、requirements、Dockerfile、compose、项目说明与根目录解析文档。

### 2.1 主调用链

```text
Access Token 或 Session JSON
  → 准备会话与代理
  → ChatGPT 创建 checkout
  → Stripe payment page 初始化
  → 得到托管结账 URL
  → hosted：返回托管链
  → iDEAL / PayPal / GoPay：创建 payment method、confirm、approve、轮询及解析 provider redirect
```

代码中的 `create_checkout()` 当前只在 GoPay 与 Plus 两种 plan_name 之间选择，没有 Team 分支。

`LongLinkRequest` 包含账号凭证、代理、链类型、地区、指纹和诊断参数，没有工作区名称、席位、已有工作区等 Team 字段。

### 2.2 API 与任务模型

公开了 `/api/long-link`、`/api/long-link/start`、任务轮询、诊断下载、代理测试、二维码等接口。

任务状态保存在进程内存字典中，重启即失效。不能直接用多个独立 worker 共享此任务状态。

### 2.3 文档与代码的差异

历史项目说明描述普通 hosted、PayPal、GoPay 等前端选项，而当前 `public/index.html` 的页面标题为 iDEAL 链路控制台；应以实际代码为准。

仓库标题提及 UPI、印度、韩国等参考用途，不代表所有这些链路都已实现并可稳定运行。

### 2.4 不适合直接作为自用第一版的部分

- `app.py` 引用了 `account_pool.py` 和 `mail_pool.py`，Dockerfile 未复制这两个模块，原样打包可能启动失败。
- 账号池读取 Token、邮箱验证码等敏感接口未看到全局鉴权依赖，不能直接对外开放。
- 前端持久化了代理地址，需要注意其中可能有用户名密码。
- `to_openai_pay_url()` 存在按字符串前缀替换域名的路径，不能拿此前缀判断作为可靠的 URL 信任验证。
- 默认优惠逻辑、金额阈值和重试用于特定多支付链实验，不应默认套到自用 Team 工具。
- 没有在检出文件中找到独立 LICENSE 文件。新项目采用独立实现，并将原仓库保留为研究参考。

## 3. 网站实际观察

页面标题：TEAM 结账链接生成器。公开资源为 `/app.js?v=20261018f`、`/style.css?v=20261018f`。

### 3.1 可见功能

- Token / Session 输入与获取地址。
- 自有代理模式：可选访问口令和 HTTP/HTTPS/SOCKS 代理地址。
- 平台代理模式：CDK 激活、余额显示、历史链接查询。
- 新建工作区：工作区名称，默认 My Team。
- 已有工作区：查询、下拉选择、手填 Workspace UUID。
- 席位数量，页面最少为 2。
- 国家与货币，国家选择联动默认货币；公开列表为 36 个国家／地区。
- 优惠码或优惠链接。
- 生成结果、复制、打开、给顾客复制话术以及错误详情。
- 前端失败后有 5 秒冷却。

### 3.2 公开前端确认的请求接口

| 接口 | 前端用途 |
| --- | --- |
| `GET /api/health` | 是否要求口令、平台代理是否可用、自有代理是否开放、维护状态 |
| `POST /api/account_country` | 根据凭证查询站点记忆的国家；不能据此认定它能发现真实账单国家 |
| `POST /api/workspaces` | 查询已有工作区 |
| `POST /api/checkout` | 提交结账参数 |
| `POST /api/cdk/activate` | 激活卡密与查看剩余次数 |
| `POST /api/cdk/history` | 查询卡密对应的历史链接 |

`/api/checkout` 公开前端发送 token、proxyMode、cdk、proxy、workspaceName、existingWorkspaceId、seatQuantity、country、currency、promoCode。口令通过 `X-Access-Token` 头传递，与账号 token 是不同的凭证。

### 3.3 存储行为的澄清

公开 JS 中，`SS_KEY = checkout-link-access-token` 实际保存的是页面“访问口令”（DOM id 为 accessToken），而账号 Session 输入框 DOM id 为 token。不能仅根据常量名字推断网站保存了账号 accessToken。

代理地址与 CDK 存入 sessionStorage；工作区等非敏感参数存入 localStorage。网页声明服务端只在请求内使用账号 Token，但未获得服务端代码，无法独立审计其落盘行为。

网站宣称“成功扣一次、失败不扣”和“48 小时内找回链接”，这些是可见产品规则；没有实际付费 CDK，因此没有验证扣费事务或历史查询的服务端实现。

## 4. 自用版实现选择

已采用：FastAPI + curl_cffi + 原生 HTML/CSS/JS，无数据库。

```text
本地表单
  → POST /api/checkout
  → 验证 Team 参数与凭证
  → 单次请求 ChatGPT checkout，hosted 模式
  → 优先使用响应里的完整托管 URL
  → 如只返回 session_id + publishable_key，使用独立 Stripe 会话初始化
  → HTTPS 与准确域名校验
  → 展示、复制、打开
```

Team 请求使用 `chatgptteamplan` 和 `team_plan_data`。工作区查询路径及已有工作区参数还参考了公开的 ChatGPT-Workspace-Helper 源码，但未经当前账号实际网络请求验证。

新版本不自动改写 Stripe 链接域名，不使用仓库内置的 Stripe key，不反复切换地区刷优惠，不运行 payment method / confirm / approve；它的目标是拿到托管结账页，由用户在页面中确认付款。

新版本提供月／年周期，但真实价格及可用性由上游决定。空优惠码不附带优惠参数。

## 5. 还需要真实账号验证的事项

1. 当前 ChatGPT 网页接口是否继续接受新建 Team 的请求字段。
2. 已有工作区的不同状态（有效订阅、已过期、重新启用）是否需要不同字段。
3. 当前账号是否有账单管理权限及优惠资格。
4. Stripe 是否返回完整链接或初始化所需的 publishable_key。
5. 代理出口、账号地区与反自动化校验是否允许后端访问。

只需在本地页面填入自己的有效 Session 验证，不需要把凭证贴给助手。若失败，可提供页面显示的错误文字与阶段；不要发送原始 Session 或含敏感参数的支付链接。

## 6. 来源

- https://github.com/rolandbaku011/chatgpt-codex-link-analysis-05
- https://bridge.pp.ua/
- https://bridge.pp.ua/app.js?v=20261018f
- https://greasyfork.org/en/scripts/581963-chatgpt-workspace-helper/code

本报告明确区分公开源码实现、网页可见行为、网站自行声明及尚未验证的上游兼容性。
