"""Start the local Live2D preview stack.

The default command starts two Vite servers:
  - the official Cubism demo (8084)
  - the XinQing action panel (8083)

Optional backend processes can be enabled explicitly with --with-data,
--with-alert, and --with-langgraph.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "xinqing"
SDK_DEMO = ROOT / "CubismSdkForWeb-5-r.5" / "Samples" / "TypeScript" / "Demo"
PANEL = ROOT / "web" / "live2d_demo"


def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_for_port(port: int, process: subprocess.Popen[object], timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_is_open(port):
            return
        if process.poll() is not None:
            raise RuntimeError(f"服务启动失败，进程退出码: {process.returncode}")
        time.sleep(0.2)
    raise TimeoutError(f"等待 127.0.0.1:{port} 超时")


def locate_node(explicit: str | None) -> str:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env_node = os.environ.get("XINQING_NODE")
    if env_node:
        candidates.append(Path(env_node))
    node_on_path = shutil.which("node")
    if node_on_path:
        candidates.append(Path(node_on_path))
    candidates.append(Path(r"E:\Program Files\nodejs\node.exe"))
    candidates.append(Path(r"C:\Users\27732\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(
        "找不到 node.exe。请安装 Node.js，或设置 XINQING_NODE / 使用 --node 指定路径。"
    )


def locate_langgraph(explicit: str | None) -> list[str]:
    """Find the LangGraph CLI in the project .venv or PATH."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_langgraph = os.environ.get("XINQING_LANGGRAPH")
    if env_langgraph:
        candidates.append(Path(env_langgraph))
    found = shutil.which("langgraph")
    if found:
        candidates.append(Path(found))

    python_path = Path(sys.executable).resolve()
    candidates.append(python_path.with_name("langgraph.exe"))
    if python_path.parent.name.lower() == "python":
        candidates.append(python_path.parent.parent / "Scripts" / "langgraph.exe")
    # uv venv layout: .venv/Scripts/langgraph.exe
    candidates.append(ROOT / ".venv" / "Scripts" / "langgraph.exe")
    candidates.append(ROOT / ".venv" / "bin" / "langgraph")
    for candidate in candidates:
        if candidate.is_file():
            return [str(candidate)]

    # This also supports environments where the console-script shim was not
    # generated but the langgraph_cli package itself is installed.
    try:
        __import__("importlib").import_module("langgraph_cli")
    except ImportError:
        pass
    else:
        return [sys.executable, "-m", "langgraph_cli"]
    raise FileNotFoundError(
        "找不到 langgraph 命令。请运行 uv pip install langgraph-cli[inmem]，或设置 XINQING_LANGGRAPH 指向 langgraph.exe。"
    )


def start_process(
    name: str,
    command: list[str],
    cwd: Path,
    port: int,
    owned: list[tuple[str, subprocess.Popen[object]]],
    env_overrides: dict[str, str] | None = None,
    startup_timeout: float = 20,
) -> None:
    if port_is_open(port):
        print(f"[{name}] 端口 {port} 已被占用，复用现有服务。")
        return
    print(f"[{name}] 启动: {' '.join(command)}")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env={**os.environ, **(env_overrides or {})},
        creationflags=creationflags,
    )
    owned.append((name, process))
    wait_for_port(port, process, timeout=startup_timeout)
    print(f"[{name}] 已就绪: http://127.0.0.1:{port}")


def stop_processes(owned: list[tuple[str, subprocess.Popen[object]]]) -> None:
    for name, process in reversed(owned):
        if process.poll() is not None:
            continue
        print(f"[{name}] 正在停止...")
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait(timeout=3)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动心晴助手 Live2D 本地预览")
    parser.add_argument("--node", help="node.exe 的完整路径")
    parser.add_argument("--langgraph", help="langgraph.exe 的完整路径")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--skip-copy", action="store_true", help="跳过 SDK 资源复制")
    parser.add_argument("--check-only", action="store_true", help="只检查依赖和目录，不启动服务")
    parser.add_argument("--with-data", action="store_true", help="同时启动 data_service.py (8001)")
    parser.add_argument("--with-alert", action="store_true", help="同时启动 alert.py (5000)")
    parser.add_argument("--with-langgraph", action="store_true", help="同时启动 langgraph dev (2024)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    node = locate_node(args.node)
    copy_resources = SDK_DEMO / "copy_resources.js"
    sdk_vite = SDK_DEMO / "node_modules" / "vite" / "bin" / "vite.js"
    panel_vite = PANEL / "node_modules" / "vite" / "bin" / "vite.js"
    required = [SDK_DEMO, PANEL, copy_resources, sdk_vite, panel_vite]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print("缺少 Live2D 预览文件:")
        print("\n".join(f"  - {path}" for path in missing))
        print("请先在两个前端目录执行 pnpm install。")
        return 2
    if args.check_only:
        print(f"node: {node}")
        print("Live2D 预览目录检查通过。")
        return 0

    if not args.skip_copy:
        print("[Cubism] 同步 SDK public 资源...")
        subprocess.run([node, str(copy_resources)], cwd=str(SDK_DEMO), check=True)

    owned: list[tuple[str, subprocess.Popen[object]]] = []
    try:
        start_process("Cubism Demo", [node, str(sdk_vite), "--host", "127.0.0.1", "--port", "8084"], SDK_DEMO, 8084, owned)
        start_process("Live2D Panel", [node, str(panel_vite), "--host", "127.0.0.1", "--port", "8083"], PANEL, 8083, owned)

        if args.with_data:
            start_process(
                "Data API",
                [sys.executable, str(SRC / "data_service.py")],
                ROOT,
                8001,
                owned,
                {"PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            )
        if args.with_alert:
            print("[Alert] 已显式启用预警服务；请确认 .env 中的 SMTP 配置和 ALERT_CHANNEL。")
            start_process(
                "Alert API",
                [sys.executable, str(SRC / "alert.py")],
                ROOT,
                5000,
                owned,
                {"PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            )
        if args.with_langgraph:
            langgraph = locate_langgraph(args.langgraph)
            start_process(
                "LangGraph",
                [*langgraph, "dev"],
                ROOT,
                2024,
                owned,
                {
                    "PYTHONNOUSERSITE": "1",
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1",
                },
                startup_timeout=120,
            )

        panel_url = "http://127.0.0.1:8083/live2d_demo/"
        print(f"\nLive2D 预览地址: {panel_url}")
        print("按 Ctrl+C 停止本脚本启动的服务。")
        if not args.no_browser:
            webbrowser.open(panel_url)
        while True:
            for name, process in owned:
                if process.poll() is not None:
                    raise RuntimeError(f"[{name}] 已退出，退出码: {process.returncode}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C。")
    except (FileNotFoundError, RuntimeError, TimeoutError) as error:
        print(f"启动失败: {error}")
        return 1
    finally:
        stop_processes(owned)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
