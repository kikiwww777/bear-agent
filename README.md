# Bear Agent

一个面向 Windows 的文件管理 AI Agent。它通过自然语言协助你查找、分析、整理和清理本地文件，并在高风险操作前进行权限校验与人工确认。

项目基于 LangChain、LangGraph 和 DeepAgents 构建，当前以命令行交互方式运行，并通过 Anthropic 兼容接口连接模型。

## 核心能力

- 按文件名、通配符、扩展名、大小、修改时间和目录搜索文件。
- 统计目录容量、文件类型分布、扩展名分布和大文件。
- 通过大小初筛和 MD5 确认查找重复文件。
- 读取图片 EXIF、PDF、Office 文档、音视频等文件的元数据。
- 批量复制、移动、删除文件，以及重命名单个文件或目录。
- 为指定路径配置只读、禁止访问或允许访问权限。

## 环境要求

- Windows
- Python 3
- [Everything](https://www.voidtools.com/downloads/) 桌面程序及 [Everything SDK](https://www.voidtools.com/support/everything/sdk/)
- 可用的 Anthropic 兼容模型服务

## 安装

### 1. 安装 Everything 与 SDK

Bear Agent 会优先通过 Everything SDK 执行文件搜索、目录统计和重复文件检测，以获得毫秒级全盘索引能力。请先完成以下安装：

1. 从 [Everything 下载页](https://www.voidtools.com/downloads/) 安装 64 位 Everything 桌面程序。
2. 从 [Everything SDK 页面](https://www.voidtools.com/support/everything/sdk/) 下载 SDK，并解压到本地目录。
3. 确保 SDK 中的 `Everything64.dll` 可被程序找到。若自动检测失败，将 SDK 解压目录设置为环境变量 `EVERYTHING_SDK_PATH`：

   ```powershell
   [Environment]::SetEnvironmentVariable("EVERYTHING_SDK_PATH", "C:\\path\\to\\Everything-SDK", "User")
   ```

4. 重新打开 PowerShell，使新的环境变量生效。

程序会尝试自动启动已安装的 Everything。未安装 SDK 时，搜索相关功能会降级为 `os.walk` 扫描：速度较慢，且深层目录或大型目录的结果可能不完整。

### 2. 安装 Python 依赖

克隆项目后，在项目根目录创建并激活虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

安装依赖：

```powershell
pip install -r requirements.txt
```

> 若 PowerShell 阻止激活脚本，请在当前终端执行 `Set-ExecutionPolicy -Scope Process Bypass` 后重新激活虚拟环境。

## 配置

在项目根目录创建 `.env` 文件，并填写模型服务的连接信息：

```dotenv
# Anthropic 兼容接口地址
ANTHROPIC_BASE_URL=https://your-api-endpoint/v1

# 服务端签发的访问令牌
ANTHROPIC_AUTH_TOKEN=your-api-token

# 可选；未设置时使用 deepseek-v4-flash
ANTHROPIC_MODEL=your-model-name
```

可选地，设置 LangSmith 相关环境变量以开启链路追踪。`.env` 已被 Git 忽略，请勿将真实令牌提交到仓库。

## 启动

```powershell
python main.py
```

启动后可直接用自然语言描述需求，例如：

```text
查找 D:\Downloads 中大于 500MB 的视频文件
统计 C:\Users\<你的用户名>\Documents 的空间占用
找出 E:\Archive 中重复的 PDF 文件
```

输入 `quit` 退出交互式会话。

## 可用工具

| 工具 | 用途 |
| --- | --- |
| `search_files` | 按关键词、通配符、路径、扩展名、大小和时间筛选文件；支持分页读取搜索结果。 |
| `find_duplicates` | 按大小分组并以 MD5 确认重复文件；结果按可回收空间排序。 |
| `file_stats` | 统计目录中的文件数量、总大小、类型/扩展名分布、子目录和最大文件。 |
| `file_metadata` | 读取文件基础信息，以及图片 EXIF、PDF/Office 属性、音视频信息等元数据。 |
| `set_path_permission` | 为路径设置 `READ_ONLY`、`BLOCKED` 或 `ALLOW` 权限。 |
| `copy_files` | 根据搜索结果句柄或文件路径列表批量复制文件。 |
| `move_files` | 根据搜索结果句柄或文件路径列表批量移动文件。 |
| `delete_files` | 批量删除文件；默认移入回收站。 |
| `rename_file` | 重命名单个文件或目录，且新名称不能包含路径分隔符。 |

对于 `copy_files`、`move_files` 和 `delete_files`，可优先使用 `search_files` 或 `find_duplicates` 返回的 `result_id`，以避免手工复制长路径列表。

## 安全机制

Bear Agent 对文件操作设置了多层保护：

- **路径权限**：`READ_ONLY` 只允许读取；`BLOCKED` 禁止所有访问；`ALLOW` 允许操作。权限配置保存为运行时生成的 `path_permissions.json`。
- **系统目录保护**：`C:\Windows`、`C:\Program Files`、`C:\Program Files (x86)` 与回收站默认只读。
- **输入与策略校验**：中间件会规范化路径、清理输入，并拦截受限路径和不符合策略的操作。
- **人工确认**：中等及更高风险的操作会在 CLI 中展示操作摘要，等待输入 `y/yes` 确认。
- **可恢复删除**：`delete_files` 默认将文件移入回收站；只有明确设置 `to_recycle_bin=False` 才会永久删除。

即使有这些保护，执行复制、移动、删除或覆盖操作前，仍应核对目标路径和文件范围。

## 测试

项目根目录包含工具与中间件的测试。安装依赖后可运行：

```powershell
pytest
```

如只验证某个模块，可指定对应测试文件，例如：

```powershell
pytest test_tool_policy.py -v
```

## 项目结构

```text
.
├── main.py                         # CLI 入口
├── requirements.txt                # Python 依赖
├── backend/
│   ├── agents/
│   │   └── loop.py                 # Agent 创建与交互循环
│   ├── middleware/                 # 权限、策略、确认、错误与可观测性中间件
│   ├── tools/                      # 文件搜索、统计、操作等工具
│   ├── services/                   # Everything 搜索服务适配
│   └── utils/                      # 路径权限、结果缓存和通用工具
└── test_*.py                       # 测试用例
```

## 说明

本项目会操作本地文件系统。请仅在你拥有访问权限且已备份重要数据的目录中使用；对于不可逆操作，务必先确认文件列表与目标位置。
