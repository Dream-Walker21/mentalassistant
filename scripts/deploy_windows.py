"""快速准备并启动心晴助手 Windows 本地部署环境。

示例（PowerShell）：
    python deploy_windows.py --install --run --with-data --with-langgraph

脚本不会覆盖已有 .env，也不会删除数据库。它适合单机联调和演示；
正式公网部署仍建议使用 Linux + Nginx/HTTPS。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
PANEL = ROOT / "web" / "live2d_demo"
SDK_DEMO = ROOT / "CubismSdkForWeb-5-r.5" / "Samples" / "TypeScript" / "Demo"


def run(command: list[str], cwd: Path = ROOT) -> None:
    print("[执行]", " ".join(command))
    subprocess.run(command, cwd=str(cwd), check=True)


def python_executable() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def npm_executable() -> str:
    for name in ("npm.cmd", "npm"):
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError("找不到 npm。请先安装 Node.js 20 LTS 或更高版本。")


def ensure_env() -> None:
    target = ROOT / ".env"
    example = ROOT / ".env.example"
    if target.exists():
        print(f"[配置] 保留已有 {target}")
        return
    if not example.exists():
        raise FileNotFoundError(f"找不到 {example}")
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[配置] 已创建 {target}，请填写 DEEPSEEK_API_KEY 和管理员配置")


def install_dependencies() -> None:
    uv = shutil.which("uv") or shutil.which("uv.exe")
    if not uv:
        raise FileNotFoundError("找不到 uv。请先安装 uv：pip install uv 或 powershell iex (irm https://astral.sh/uv/install.ps1)")
    if not python_executable().exists():
        print("[Python] 创建虚拟环境 (uv venv)")
        run([uv, "venv", str(VENV)])
    run([uv, "pip", "install", "--python", str(python_executable()), "-r", str(ROOT / "requirements.txt")])

    npm = npm_executable()
    if not (PANEL / "node_modules").exists():
        print("[前端] 安装 Live2D 面板依赖")
        run([npm, "install"], PANEL)
    if SDK_DEMO.is_dir() and not (SDK_DEMO / "node_modules").exists():
        print("[Cubism] 安装官方 Demo 依赖")
        run([npm, "install"], SDK_DEMO)


def write_launcher() -> Path:
    launcher = ROOT / "start_xinqing_windows.bat"
    launcher.write_text(
        "@echo off\r\n"
        f"cd /d \"{ROOT}\"\r\n"
        "echo Starting XinQing services...\r\n"
        f"\"{python_executable()}\" \"{ROOT / 'scripts' / 'start_live2d.py'}\" %*\r\n"
        "pause\r\n",
        encoding="utf-8",
        newline="",
    )
    print(f"[启动器] 已生成 {launcher}")
    return launcher


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="准备并启动心晴助手 Windows 环境")
    parser.add_argument("--install", action="store_true", help="创建虚拟环境并安装 Python/Node 依赖")
    parser.add_argument("--run", action="store_true", help="准备后启动服务")
    parser.add_argument("--with-data", action="store_true", help="启动数据 API")
    parser.add_argument("--with-alert", action="store_true", help="启动预警 API")
    parser.add_argument("--with-langgraph", action="store_true", help="启动 LangGraph")
    parser.add_argument("--disable-rag", action="store_true", help="写入 XINQING_DISABLE_RAG=1")
    parser.add_argument("--node", help="传给 start_live2d.py 的 node.exe 路径")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    return parser.parse_args()


def main() -> int:
    if os.name != "nt":
        print("提示：此脚本面向 Windows；当前系统不是 Windows。", file=sys.stderr)
    args = parse_args()
    ensure_env()
    if args.disable_rag:
        env_path = ROOT / ".env"
        lines = env_path.read_text(encoding="utf-8").splitlines()
        lines = [line for line in lines if not line.startswith("XINQING_DISABLE_RAG=")]
        lines.append("XINQING_DISABLE_RAG=1")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("[配置] 已设置 XINQING_DISABLE_RAG=1")
    if args.install:
        install_dependencies()
    launcher = write_launcher()
    if not args.run:
        print(f"准备完成。可运行：\n  {launcher} --with-data --with-langgraph")
        return 0

    command = [str(python_executable()), str(ROOT / "scripts" / "start_live2d.py")]
    if args.with_data:
        command.append("--with-data")
    if args.with_alert:
        command.append("--with-alert")
    if args.with_langgraph:
        command.append("--with-langgraph")
    if args.node:
        command.extend(["--node", args.node])
    if args.no_browser:
        command.append("--no-browser")
    run(command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
