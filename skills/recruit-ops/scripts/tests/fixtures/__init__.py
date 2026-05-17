"""可执行测试 fixture 子模块。

里面的模块都是为了让 `cli_subprocess.run_module()` / 其他需要真起子进程
的测试用 `python -m tests.fixtures.<name>` 调起来,验证完整路径(env 注入、
返回码、stdout/stderr、JSON 反向扫描等)而不是只 mock subprocess.run。
"""
