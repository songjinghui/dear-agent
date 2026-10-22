# 本机 CLI 插件接口

项目通过 `companion.providers.CLIProvider` 约定接入本机 CLI Agent。Codex 是内置实现；其他实现可以作为 Python 包安装到运行后端的同一个 Python 环境中，并通过 setuptools entry point 注册。

## 插件接口

一个插件需要提供以下成员：

- `provider_id`：稳定且唯一的设置值，不能使用 `openai-compatible`。
- `display_name`、`description`：显示在模型设置页的名称和说明。
- `status() -> ProviderStatus`：报告 CLI 是否可用、是否已登录，以及给用户看的状态消息。若该 CLI 不需要单独登录，可将 `authenticated` 设为 `None`。
- `async run_turn(context, on_delta) -> str`：执行一轮对话。通过 `await on_delta(text)` 推送增量，最终返回完整回复。

`ProviderTurnContext` 提供当前应用会话 ID、用户内容、系统提示、此前消息、本地数据目录，以及由应用管理的 `get_state(key)` / `set_state(key, value)`。插件可用这些状态保存自己的 CLI 会话标识；状态按“应用会话 + provider id”隔离。插件不应直接读取应用数据库。

## 注册插件

在插件自己的 `pyproject.toml` 中声明 entry point：

```toml
[project.entry-points."personal_companion.cli_providers"]
my-agent = "my_agent_plugin:MyAgentProvider"
```

entry point 可以指向 provider 类（应用会实例化），也可以指向已构造的 provider 对象。安装或升级插件后，重启个人生活 Agent；设置页会显示已发现的插件，聊天请求会通过统一的增量流协议交给所选 provider。

## 安全与边界

- 插件由用户显式安装到后端 Python 环境；应用不会执行设置页输入的任意命令行字符串。
- 每个插件自行定义 CLI 启动参数、认证检查和输出解析，但应将工具权限和工作目录限制在合理范围内。
- 当前设置仅选择一个全局 provider。会话保存各插件自己的续接状态；切换 provider 后，新 provider 会根据应用历史开始自己的会话。
- 第三方插件在应用启动时发现。某个可选插件加载失败不会阻止应用启动；它不会出现在插件列表中。
