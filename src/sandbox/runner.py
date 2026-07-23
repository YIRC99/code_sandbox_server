import ast
import os
import runpy
import sys
from pathlib import Path

ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        # 标准库 - 常用数据处理、数学工具与内置支持
        "__future__",
        "ast",
        "base64",
        "bisect",
        "calendar",
        "collections",
        "copy",
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "hashlib",
        "heapq",
        "hmac",
        "io",
        "itertools",
        "json",
        "math",
        "numbers",
        "operator",
        "os",
        "pathlib",
        "pprint",
        "random",
        "re",
        "string",
        "struct",
        "sys",
        "time",
        "traceback",
        "types",
        "typing",
        "urllib",
        "warnings",
        # 数据科学、回测、可视化与数据获取第三方库
        "backtesting",
        "httpx",
        "loguru",
        "matplotlib",
        "numpy",
        "pandas",
        "pandas_ta",
        "plotly",
        "requests",
        "scipy",
        "seaborn",
        "sklearn",
        "yfinance",
        "statistics",
    }
)

_DANGEROUS_AUDIT_EVENTS: frozenset[str] = frozenset(
    {
        "os.system",
        "os.popen",
        "os.posix_spawn",
        "os.spawn",
        "os.exec",
        "subprocess.Popen",
        "pty.spawn",
    }
)


class SecurityError(PermissionError):
    pass


def validate_script_ast(script_path: Path) -> None:
    content = script_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(content, filename=str(script_path))
    except SyntaxError:
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_module = alias.name.split(".")[0]
                if top_module not in ALLOWED_IMPORTS:
                    raise SecurityError(
                        f"Importing module '{alias.name}' is not allowed by sandbox policy"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_module = node.module.split(".")[0]
                if top_module not in ALLOWED_IMPORTS:
                    raise SecurityError(
                        f"Importing from module '{node.module}' is not allowed by sandbox policy"
                    )
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                if (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    top_module = node.args[0].value.split(".")[0]
                    if top_module not in ALLOWED_IMPORTS:
                        raise SecurityError(
                            f"Importing module '{node.args[0].value}' "
                            "via __import__ is not allowed by sandbox policy"
                        )


def _audit_hook(event: str, args: tuple[object, ...]) -> None:
    if event in _DANGEROUS_AUDIT_EVENTS:
        raise SecurityError(f"Operation '{event}' is blocked by sandbox runtime audit policy")


def install_audit_hook() -> None:
    sys.addaudithook(_audit_hook)


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
    if len(sys.argv) != 3:
        raise SystemExit("usage: runner.py <script.py> <data.csv>")
    script = Path(sys.argv[1]).resolve(strict=True)
    data_file = Path(sys.argv[2]).resolve(strict=True)
    validate_script_ast(script)
    install_audit_hook()
    apply_resource_limits()
    os.chdir(script.parent)
    sys.argv = [str(script), str(data_file)]
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
