"""Docker-based code execution sandbox + MockSandbox fallback for Phase 2."""

import asyncio, base64, logging, shutil, sys, subprocess, tempfile
from pathlib import Path
from typing import Optional
from src.core.config import get_settings
from src.core.exceptions import SandboxError

logger = logging.getLogger(__name__)

#: /workspace 是容器内 tmpfs（随容器销毁），图表必须写到挂载出来的 /output 才能带出容器。
FIGURE_MOUNT = "/output"
MAX_FIGURES = 4
MAX_FIGURE_BYTES = 2_000_000


def _collect_figures(out_dir: str) -> list[dict]:
    """把容器挂载出来目录里的 PNG 读成 data URL，供前端直接展示。"""
    figures: list[dict] = []
    try:
        candidates = sorted(Path(out_dir).glob("*.png"))
    except OSError:
        return figures
    for path in candidates[:MAX_FIGURES]:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if not data or len(data) > MAX_FIGURE_BYTES:
            continue
        figures.append({
            "name": path.name,
            "data_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
        })
    return figures


class DockerSandbox:
    """Executes Python code in an isolated Docker container."""

    def __init__(self):
        self.settings = get_settings()
        self._image = self.settings.sandbox.image
        self._ready = False

    async def _image_exists(self) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", self._image,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        return proc.returncode == 0

    async def _ensure_image(self):
        if self._ready:
            return
        if await self._image_exists():
            self._ready = True
            return
        dockerfile = Path(__file__).parent.parent.parent / "docker" / "sandbox.Dockerfile"
        if dockerfile.exists():
            proc = await asyncio.create_subprocess_exec(
                "docker", "build", "-t", self._image, "-f", str(dockerfile),
                str(dockerfile.parent),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning("Sandbox image build warning: %s", stderr.decode())
        self._ready = True

    async def _run_container(self, script_path: str, extra_mounts: list[str], timeout: int) -> dict:
        """在隔离容器里执行脚本，并把 /output 里生成的图表带回宿主机。

        容器内 /workspace 是 tmpfs，会随容器一起销毁；因此额外挂载一个宿主机临时目录
        到 /output，脚本把图存到那里后由 _collect_figures 读成 data URL。
        """
        out_dir = tempfile.mkdtemp(prefix="yxt_figures_")
        try:
            cmd = [
                "docker", "run", "--rm",
                "--network", "none" if self.settings.sandbox.network_disabled else "bridge",
                "--memory", self.settings.sandbox.memory_limit,
                "--cpus", str(self.settings.sandbox.cpu_limit),
                "--read-only",
                "--tmpfs", "/tmp:exec", "--tmpfs", "/workspace:exec",
                "-e", "MPLCONFIGDIR=/tmp", "-e", "XDG_CACHE_HOME=/tmp",
                "-v", f"{script_path}:/workspace/script.py:ro",
                *extra_mounts,
                "-v", f"{out_dir}:{FIGURE_MOUNT}",
                "--workdir", "/workspace",
                self._image, "python", "/workspace/script.py",
            ]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                timed_out = False
            except asyncio.TimeoutError:
                proc.kill(); await proc.wait()
                stdout, stderr = b"", f"Timed out after {timeout}s".encode()
                timed_out = True
            return {
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "exit_code": proc.returncode if not timed_out else -1,
                "timed_out": timed_out,
                "figures": _collect_figures(out_dir),
            }
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    async def execute(self, code: str, timeout: Optional[int] = None) -> dict:
        await self._ensure_image()
        timeout = timeout or self.settings.sandbox.timeout
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            temp_path = f.name
        try:
            return await self._run_container(temp_path, [], timeout)
        finally:
            try: Path(temp_path).unlink(missing_ok=True)
            except Exception: pass

    async def execute_with_file(self, code: str, input_file: str, timeout: Optional[int] = None) -> dict:
        await self._ensure_image()
        timeout = timeout or self.settings.sandbox.timeout
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            temp_path = f.name
        try:
            return await self._run_container(
                temp_path, ["-v", f"{input_file}:/workspace/data.csv:ro"], timeout
            )
        finally:
            try: Path(temp_path).unlink(missing_ok=True)
            except Exception: pass

    async def execute_with_data(self, code: str, csv_content: str, timeout: Optional[int] = None) -> dict:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write(csv_content)
            data_path = f.name
        try:
            return await self.execute_with_file(code, data_path, timeout)
        finally:
            try: Path(data_path).unlink(missing_ok=True)
            except Exception: pass


class MockSandbox:
    """In-process sandbox for testing without Docker. NOT for production."""

    def __init__(self):
        self.settings = get_settings()
        self._python = sys.executable

    async def execute(self, code: str, timeout: Optional[int] = None) -> dict:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            temp_path = f.name
        try:
            proc = subprocess.run(
                [self._python, temp_path],
                capture_output=True, text=True,
                timeout=timeout or 30,
            )
            return {"stdout": proc.stdout, "stderr": proc.stderr, "exit_code": proc.returncode, "timed_out": False, "figures": []}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Timeout", "exit_code": -1, "timed_out": True, "figures": []}
        finally:
            try: Path(temp_path).unlink(missing_ok=True)
            except Exception: pass

    async def execute_with_file(self, code: str, input_file: str, timeout: Optional[int] = None) -> dict:
        return await self.execute(code, timeout)

    async def execute_with_data(self, code: str, csv_content: str, timeout: Optional[int] = None) -> dict:
        return await self.execute(code, timeout)


_sandbox = None

def get_sandbox():
    global _sandbox
    if _sandbox is None:
        try:
            subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False)
            _sandbox = DockerSandbox()
            logger.info("Docker sandbox available")
        except Exception:
            logger.warning("Docker not available, using MockSandbox (no container isolation!)")
            _sandbox = MockSandbox()
    return _sandbox