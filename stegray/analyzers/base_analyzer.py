"""Base Analyzer class for file analysis tools."""

import asyncio
import shutil
from abc import ABC
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, ClassVar, overload

# Bytes kept from each of a subprocess's stdout/stderr. Output beyond this is
# drained (so the child never blocks on a full pipe) but discarded, keeping
# adversarial tool output from ballooning memory and results.json.
MAX_CAPTURED_OUTPUT = 1_000_000

# All concrete analyzer classes, registered automatically on class creation.
# Use stegray.analyzers.registry to consume this (it imports every module).
REGISTRY: list[type["SubprocessAnalyzer"]] = []


class SubprocessAnalyzer(ABC):
    """Analyzer that runs a subprocess command.

    Subclasses declare their behavior with class attributes; creating the class
    is enough to register it — no other file needs to change:

    - ``name``: tool name, used as the ``results.json`` key and extraction folder.
    - ``has_archive``: the tool extracts files, kept under ``<output_dir>/<name>/``.
    - ``needs_password``: the tool receives the ``--password`` value.
    - ``deep_only``: only run when the user requests a deep analysis.
    - ``display_order``: report position (lower prints first).
    - ``register``: opt-out flag for templates/abstract intermediates.
    - ``requires``: executables that must be on ``PATH``; defaults to ``(name,)``.
    - ``accepts``: file-type gate — empty runs on any file; else runs iff
      ``accepts & detected.tags`` is non-empty (see ``stegray.filetype``).
    """

    name: ClassVar[str]
    has_archive: ClassVar[bool] = False
    needs_password: ClassVar[bool] = False
    deep_only: ClassVar[bool] = False
    display_order: ClassVar[int] = 1000
    register: ClassVar[bool] = True
    requires: ClassVar[tuple[str, ...] | None] = None
    # Tools with several command variants (e.g. OpenStego's two crypt
    # algorithms) set this >1; ``analyze`` re-runs ``get_results`` while the
    # previous attempt errored, and ``build_cmd`` supplies the next variant.
    max_attempts: ClassVar[int] = 1
    # File-type gate. Empty = file-agnostic (runs on ANY file). Otherwise runs
    # iff (accepts & detected.tags) is non-empty. See stegray/filetype.py.
    accepts: ClassVar[frozenset[str]] = frozenset()
    # Per-subprocess wall clock in seconds; the CLI overrides it from --timeout.
    timeout: ClassVar[int] = 300

    input_img: Path
    output_dir: Path
    cmd: list[str] | None = None
    img: str
    make_folder: bool = True

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401
        """Register concrete analyzer subclasses declaring a ``name``."""
        super().__init_subclass__(**kwargs)
        if cls.register and getattr(cls, "name", None):
            REGISTRY.append(cls)

    def __init__(self, input_img: Path, output_dir: Path) -> None:
        """Initialize analyzer execution context."""
        self.input_img = input_img.resolve()
        self.img = str(self.input_img)
        self.output_dir = output_dir.resolve()

    @classmethod
    def missing_tools(cls) -> list[str]:
        """Return the required executables that are not installed."""
        requires = (cls.name,) if cls.requires is None else cls.requires
        return [tool for tool in requires if shutil.which(tool) is None]

    @classmethod
    def execute(
        cls,
        input_img: Path,
        output_dir: Path,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Instantiate and run this analyzer, applying the password if supported."""
        analyzer = cls(input_img, output_dir)
        if cls.needs_password and password:
            return analyzer.analyze(password)
        return analyzer.analyze()

    def run_command(
        self,
        cmd: list[str],
        cwd: Path | None = None,
    ) -> CompletedProcess[str]:
        """Run a subprocess command."""
        timeout = self.timeout
        # Windows only launches a bare name when it is an .exe; resolving it first
        # also finds the .cmd/.bat shims some tools are installed as.
        cmd = [shutil.which(cmd[0]) or cmd[0], *cmd[1:]]

        async def _read_capped(stream: asyncio.StreamReader) -> str:
            chunks: list[bytes] = []
            total = 0
            truncated = False
            while chunk := await stream.read(65536):
                take = chunk[: max(0, MAX_CAPTURED_OUTPUT - total)]
                if take:
                    chunks.append(take)
                    total += len(take)
                if len(take) < len(chunk):
                    truncated = True
            text = b"".join(chunks).decode("utf-8", errors="replace")
            if truncated:
                text += "\n[output truncated]"
            return text

        async def _run() -> CompletedProcess[str]:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd) if cwd is not None else None,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if process.stdout is None or process.stderr is None:
                msg = "Subprocess pipes were not created"
                raise RuntimeError(msg)
            try:
                stdout, stderr, _ = await asyncio.wait_for(
                    asyncio.gather(
                        _read_capped(process.stdout),
                        _read_capped(process.stderr),
                        process.wait(),
                    ),
                    timeout=timeout,
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                msg = f"Command timed out after {timeout}s: {cmd[0]}"
                raise TimeoutError(msg) from None
            returncode = process.returncode
            if returncode is None:
                msg = "Subprocess exited without a return code"
                raise RuntimeError(msg)
            return CompletedProcess(
                args=cmd,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )

        return asyncio.run(_run())

    def collect_extracted(self, extracted_dir: Path | None = None) -> list[str]:
        """List the extracted files (relative to the output dir), dropping an empty folder."""
        if extracted_dir is None:
            extracted_dir = self.get_extracted_dir()
        if not extracted_dir.is_dir():
            return []
        files = sorted(
            str(path.relative_to(self.output_dir))
            for path in extracted_dir.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        if not files:
            shutil.rmtree(extracted_dir)
        return files

    def get_extracted_dir(self) -> Path:
        """Get the extracted directory path. Can be overridden but work as it is."""
        return self.output_dir / self.name

    @overload
    def build_cmd(self) -> list[str]: ...

    @overload
    def build_cmd(self, password: str) -> list[str]: ...

    def build_cmd(self, password: str | None = None) -> list[str]:
        """Build the command to run. Can be overridden."""
        _ = password
        if self.cmd is None:
            msg = "cmd must be set or build_cmd overridden"
            raise NotImplementedError(msg)
        return self.cmd

    def get_results(self, password: str | None = None) -> dict[str, Any]:
        """Get results of command before returning."""
        result: dict[str, Any]
        extracted_dir = None
        if self.has_archive:
            extracted_dir = self.get_extracted_dir()
            if self.make_folder:
                extracted_dir.mkdir(parents=True, exist_ok=True)

        cmd: list[str]
        if password:
            cmd = list(map(str, self.build_cmd(password)))
        else:
            cmd = list(map(str, self.build_cmd()))
        data = self.run_command(cmd, cwd=self.output_dir)
        returncode = data.returncode
        stderr = data.stderr
        stdout = data.stdout

        extracted = self.collect_extracted(extracted_dir) if extracted_dir else []

        if self.is_error(returncode, stdout, stderr, zip_exist=bool(extracted)):
            result = {
                "status": "error",
            }
            result["error"] = self.process_error(stdout, stderr)
        else:
            result = {
                "status": "ok",
                "output": self.process_output(stdout, stderr),
            }
            note: str | None = self.process_note(stdout, stderr)
            if note:
                result["note"] = note
            if extracted:
                result["extracted"] = extracted
        return result

    @overload
    def analyze(self) -> dict[str, Any]: ...

    @overload
    def analyze(self, password: str) -> dict[str, Any]: ...

    def analyze(self, password: str | None = None) -> dict[str, Any]:
        """Run the subprocess command and return its result."""
        try:
            result = self.get_results(password)
            for _ in range(1, self.max_attempts):
                if result["status"] != "error":
                    break
                result = self.get_results(password)
        except (RuntimeError, ValueError, OSError, TypeError, TimeoutError) as e:
            # "failed" is a tool that could not run; "error" one that ran and found nothing.
            return {"status": "failed", "error": str(e)}
        return result

    def is_error(self, returncode: int, stdout: str, stderr: str, *, zip_exist: bool) -> bool:
        """Check if the result is an error."""
        _ = returncode, stdout, zip_exist
        return len(stderr) > 0

    def process_output(self, stdout: str, stderr: str) -> str | list[str] | dict[str, str]:
        """Process the stdout into a list of lines."""
        _ = stderr
        return [line for line in stdout.split("\n") if line] if stdout else []

    def process_error(self, stdout: str, stderr: str) -> str:
        """Process the stderr."""
        _ = stdout
        return stderr

    def process_note(self, stdout: str, stderr: str) -> str | None:
        """Process the stdout for informational purposes."""
        _ = stdout, stderr
        return None
