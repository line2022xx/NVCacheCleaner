# NVIDIA 着色器缓存清理工具

一个面向 Windows 的 NVIDIA 着色器缓存清理小工具：一键强制清空 `%LOCALAPPDATA%\NVIDIA` 下的全部缓存，清理完成后自动重启电脑。

- 作者：**linexx**
- 版本：1.0.0
- 平台：Windows 10 / 11（需要管理员权限）
- 环境：Python 3.8+（仅依赖标准库 tkinter）

## 功能特性

- **一键清理**：单击一次即可完成进程结束、缓存删除、系统重启的完整流程。
- **路径自动识别**：读取 `%LOCALAPPDATA%` 拼接 `NVIDIA` 目录，不同用户、不同电脑无需修改代码。
- **强制删除**：针对被驱动的内存映射或进程占用的缓存文件，采用多级兜底策略，尽最大可能清空目录。
- **实时反馈**：界面显示缓存文件数量与占用大小，运行日志按级别着色滚动输出。
- **自动重启**：清理结束后弹出 15 秒倒计时并自动重启，期间可选择「立即重启」或「取消重启」。
- **安全校验**：目标路径必须是 `...\AppData\Local\NVIDIA`，校验失败时直接禁用清理按钮，避免误删。

## 使用方法

### 方式一：直接运行 exe（推荐）

1. 下载 `NVCacheCleaner.exe`。
2. 双击运行，在 UAC 弹窗中允许管理员权限。
3. 点击「一键清理缓存」，等待清理完成后系统将自动重启。

> 建议先关闭正在运行的游戏或其他图形程序，可减少文件占用。

### 方式二：从源码运行

```bat
python NVCacheCleaner.py
```

首次启动若未以管理员身份运行，程序会自动请求提权。

## 清理机制

清理按以下顺序逐级处理，前一级成功后不再进入下一级：

| 级别 | 处理方式 | 说明 |
| --- | --- | --- |
| 1 | 结束占用进程 | `nvcontainer.exe`、`nvsphelper64.exe`、`NVIDIA Share/Overlay.exe` |
| 2 | 常规删除 | 清除只读属性后删除文件与目录 |
| 3 | 接管所有权后重试 | `takeown` / `icacls` 重置权限，并启用 `SeTakeOwnership`、`SeRestore`、`SeBackup` 特权 |
| 4 | 重启时删除 | `MoveFileExW(..., MOVEFILE_DELAY_UNTIL_REBOOT)` 标记为下次开机时清除 |

被标记为「重启时删除」的文件需要重启后才会真正消失，因此清理完成后会提示重启。

## 项目结构

```
NVCacheCleaner/
├── NVCacheCleaner.py     # 主程序（GUI + 清理逻辑）
├── NVCacheCleaner.spec   # PyInstaller 打包配置
├── build.bat             # 一键打包脚本
├── LICENSE               # MIT 许可证
├── .gitignore
└── README.md
```

## 构建

```bat
build.bat
```

或手动执行：

```bat
pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --uac-admin --name NVCacheCleaner NVCacheCleaner.py
```

生成结果位于 `dist\NVCacheCleaner.exe`。

## 注意事项

- 清理会删除 NVIDIA 的着色器缓存，首次重新进入游戏或图形程序时可能需要重新编译着色器，出现短暂卡顿属于正常现象。
- 程序会结束 NVIDIA 容器相关进程，可能造成显示器短暂黑屏或闪烁，重启后自动恢复。
- 自动重启默认开启，可在界面中取消勾选；若取消重启，被占用的缓存将在下次开机时删除。
- 本工具只操作 `%LOCALAPPDATA%\NVIDIA` 目录，不会修改驱动、注册表或其他系统文件。

## 免责声明

本程序按「现状」提供，作者不对因使用本工具造成的任何数据丢失或系统异常承担责任。请在使用前自行确认目标目录内容。

## 许可证

[MIT](LICENSE) © 2026 linexx
