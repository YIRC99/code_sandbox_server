import os
import runpy
import sys
from pathlib import Path


def _limit_value(name: str) -> int:
    return int(os.environ[name])


def _set_limit(resource_module: object, resource_name: str, value: int) -> None:
    limit_type = getattr(resource_module, resource_name, None)
    if limit_type is None:
        return
    getrlimit = resource_module.getrlimit
    setrlimit = resource_module.setrlimit
    infinity = resource_module.RLIM_INFINITY
    _, current_hard = getrlimit(limit_type)
    hard = value if current_hard == infinity else min(value, current_hard)
    setrlimit(limit_type, (min(value, hard), hard))


def apply_resource_limits() -> None:
    if os.name == "nt":
        return
    import resource

    _set_limit(resource, "RLIMIT_CPU", _limit_value("SANDBOX_LIMIT_CPU_SECONDS"))
    _set_limit(resource, "RLIMIT_AS", _limit_value("SANDBOX_LIMIT_MEMORY_BYTES"))
    _set_limit(resource, "RLIMIT_NPROC", _limit_value("SANDBOX_LIMIT_PROCESS_COUNT"))
    _set_limit(resource, "RLIMIT_FSIZE", _limit_value("SANDBOX_LIMIT_FILE_BYTES"))
    _set_limit(resource, "RLIMIT_NOFILE", _limit_value("SANDBOX_LIMIT_OPEN_FILES"))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: runner.py <script.py>")
    script = Path(sys.argv[1]).resolve(strict=True)
    apply_resource_limits()
    os.chdir(script.parent)
    sys.argv = [str(script)]
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
