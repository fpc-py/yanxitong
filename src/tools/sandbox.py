"""Docker-based code execution sandbox.

Security posture (v3.0): the in-process ``MockSandbox`` executes LLM-generated
code directly on the host and is therefore NOT a safe fallback. It is only
constructed when (a) pytest is running, or (b) the operator explicitly opts
in via ``YXT_ALLOW_MOCK_SANDBOX=1``. In any other environment, if the Docker
daemon is unreachable, :func:`get_sandbox` raises
:class:`SandboxUnavailableError` so callers return 503 / failed results
instead of silently running untrusted code on the host.
"""

import asyncio, base64, logging, os, re, shutil, sys, subprocess, tempfile
from pathlib import Path
from typing import Optional
from src.core.config import get_settings
from src.core.exceptions import SandboxError, SandboxUnavailableError

logger = logging.getLogger(__name__)

#: /workspace 是容器内 tmpfs（随容器销毁），图表必须写到挂载出来的 /output 才能带出容器。
FIGURE_MOUNT = "/output"
MAX_FIGURES = 4
MAX_FIGURE_BYTES = 2_000_000

#: 静态环境锁回退（沙箱内 pip freeze 不可用时使用；版本随镜像构建日快照）。
STATIC_ENV_LOCK = """# yanxitong-sandbox:2.1 (python:3.12-slim)
pandas
numpy
scipy
matplotlib
seaborn
scikit-learn
openpyxl
pyarrow
"""

_SAFE_MOUNT_NAME = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_mount_name(name: str, default: str = "data.csv") -> str:
    """把原始文件名收敛成安全挂载名（仅保留扩展名语义与字符白名单）。"""
    candidate = _SAFE_MOUNT_NAME.sub("_", Path(name).name).lstrip(".") or default
    return candidate[:64]


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


def _list_artifacts(out_dir: str) -> list[dict]:
    """列出 /output 下全部产物（递归），供产出打包清单使用。"""
    artifacts: list[dict] = []
    try:
        paths = sorted(Path(out_dir).rglob("*"))
    except OSError:
        return artifacts
    for path in paths:
        try:
            if not path.is_file():
                continue
            artifacts.append({
                "name": path.relative_to(out_dir).as_posix(),
                "size": path.stat().st_size,
            })
        except OSError:
            continue
    return artifacts


def _copy_artifacts(out_dir: str, dest_dir: str) -> None:
    """把容器产物全量复制到宿主机持久目录（产出包 run 目录）。"""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    for path in Path(out_dir).rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(out_dir)
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


class DockerSandbox:
    """Executes Python code in an isolated Docker container."""

    def __init__(self):
        self.settings = get_settings()
        self._image = self.settings.sandbox.image
        self._ready = False
        self._env_lock_cache: Optional[str] = None

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

    async def _run_container(
        self,
        script_path: str,
        extra_mounts: list[str],
        timeout: int,
        collect_to: Optional[str] = None,
    ) -> dict:
        """在隔离容器里执行脚本，并把 /output 里的产物带回宿主机。

        容器内 /workspace 是 tmpfs，会随容器一起销毁；因此额外挂载一个宿主机临时目录
        到 /output，脚本把产物存到那里后由 _collect_figures 读成 data URL、
        由 _list_artifacts 生成清单，并在 collect_to 给出时全量复制到持久目录。
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
            artifacts = _list_artifacts(out_dir)
            if collect_to:
                try:
                    _copy_artifacts(out_dir, collect_to)
                except OSError as exc:
                    logger.warning("Artifact copy to %s degraded: %s", collect_to, exc)
            return {
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "exit_code": proc.returncode if not timed_out else -1,
                "timed_out": timed_out,
                "figures": _collect_figures(out_dir),
                "artifacts": artifacts,
            }
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    async def execute(self, code: str, timeout: Optional[int] = None, *, collect_to: Optional[str] = None) -> dict:
        await self._ensure_image()
        timeout = timeout or self.settings.sandbox.timeout
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            temp_path = f.name
        try:
            return await self._run_container(temp_path, [], timeout, collect_to)
        finally:
            try: Path(temp_path).unlink(missing_ok=True)
            except Exception: pass

    async def execute_with_file(
        self,
        code: str,
        input_file: str,
        timeout: Optional[int] = None,
        *,
        mount_name: str = "data.csv",
        collect_to: Optional[str] = None,
    ) -> dict:
        await self._ensure_image()
        timeout = timeout or self.settings.sandbox.timeout
        safe_name = _sanitize_mount_name(mount_name)
        # Docker -v 只接受绝对宿主路径（相对路径会被当成命名卷并报 invalid characters）
        host_file = str(Path(input_file).resolve())
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            temp_path = f.name
        try:
            return await self._run_container(
                temp_path, ["-v", f"{host_file}:/workspace/{safe_name}:ro"], timeout, collect_to
            )
        finally:
            try: Path(temp_path).unlink(missing_ok=True)
            except Exception: pass

    async def execute_with_data(
        self,
        code: str,
        csv_content: str,
        timeout: Optional[int] = None,
        *,
        mount_name: str = "data.csv",
        collect_to: Optional[str] = None,
    ) -> dict:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write(csv_content)
            data_path = f.name
        try:
            return await self.execute_with_file(code, data_path, timeout, mount_name=mount_name, collect_to=collect_to)
        finally:
            try: Path(data_path).unlink(missing_ok=True)
            except Exception: pass

    async def env_lock(self) -> str:
        """沙箱镜像内 pip freeze 环境锁（进程内缓存；失败回退静态快照）。"""
        if self._env_lock_cache is not None:
            return self._env_lock_cache
        code = (
            "import subprocess, sys\n"
            "r = subprocess.run([sys.executable, '-m', 'pip', 'freeze'], capture_output=True, text=True)\n"
            "print(r.stdout or r.stderr)\n"
        )
        try:
            result = await self.execute(code, timeout=25)
            text = (result.get("stdout") or "").strip()
            if result.get("exit_code") == 0 and text:
                self._env_lock_cache = text
                return text
        except Exception as exc:
            logger.warning("env_lock degraded: %s", exc)
        self._env_lock_cache = STATIC_ENV_LOCK
        return STATIC_ENV_LOCK


class MockSandbox:
    """In-process sandbox for testing without Docker. NOT for production."""

    def __init__(self):
        self.settings = get_settings()
        self._python = sys.executable

    async def execute(self, code: str, timeout: Optional[int] = None, *, collect_to: Optional[str] = None) -> dict:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            temp_path = f.name
        try:
            proc = subprocess.run(
                [self._python, temp_path],
                capture_output=True, text=True,
                timeout=timeout or 30,
            )
            return {"stdout": proc.stdout, "stderr": proc.stderr, "exit_code": proc.returncode, "timed_out": False, "figures": [], "artifacts": []}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Timeout", "exit_code": -1, "timed_out": True, "figures": [], "artifacts": []}
        finally:
            try: Path(temp_path).unlink(missing_ok=True)
            except Exception: pass

    async def execute_with_file(
        self,
        code: str,
        input_file: str,
        timeout: Optional[int] = None,
        *,
        mount_name: str = "data.csv",
        collect_to: Optional[str] = None,
    ) -> dict:
        # 无容器隔离：数据文件路径以环境变量暴露，脚本可通过 os.environ 读取。
        import os
        os.environ["YXT_MOCK_DATA_FILE"] = input_file
        return await self.execute(code, timeout, collect_to=collect_to)

    async def execute_with_data(
        self,
        code: str,
        csv_content: str,
        timeout: Optional[int] = None,
        *,
        mount_name: str = "data.csv",
        collect_to: Optional[str] = None,
    ) -> dict:
        return await self.execute(code, timeout, collect_to=collect_to)

    async def env_lock(self) -> str:
        return STATIC_ENV_LOCK


_sandbox = None


def _mock_sandbox_allowed() -> bool:
    """True only when the in-process sandbox is explicitly sanctioned.

    Two opt-in channels:
      1. ``YXT_ALLOW_MOCK_SANDBOX=1`` in the environment (local dev / demos
         that deliberately accept the risk);
      2. pytest is running (``PYTEST_CURRENT_TEST`` is set, or the pytest
         module is already imported).
    """
    if os.environ.get("YXT_ALLOW_MOCK_SANDBOX") == "1":
        return True
    if "PYTEST_CURRENT_TEST" in os.environ:
        return True
    return "pytest" in sys.modules


def get_sandbox():
    """Return the configured code sandbox.

    Raises:
        SandboxUnavailableError: Docker is unreachable AND the in-process
            fallback is not permitted by the environment. Callers must turn
            this into a 503 / failed result -- never run LLM-generated code
            on the host.
    """
    global _sandbox
    if _sandbox is not None:
        return _sandbox

    docker_ok = False
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5, check=False,
        )
        docker_ok = proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Docker daemon probe failed: %s", exc)
        docker_ok = False

    if docker_ok:
        _sandbox = DockerSandbox()
        logger.info("Docker sandbox available")
        return _sandbox

    if _mock_sandbox_allowed():
        _sandbox = MockSandbox()
        logger.warning(
            "Docker unavailable -- using MockSandbox (in-process, NO container "
            "isolation). Only permitted because pytest is running or "
            "YXT_ALLOW_MOCK_SANDBOX=1 is set."
        )
        return _sandbox

    raise SandboxUnavailableError(
        "Docker daemon is not reachable, and the in-process mock sandbox is "
        "disabled in this environment (it would run AI-generated Python code "
        "directly on the host). Start Docker, or set YXT_ALLOW_MOCK_SANDBOX=1 "
        "explicitly if you accept that risk."
    )