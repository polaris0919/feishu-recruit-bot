#!/usr/bin/env python3
"""HTTP / 第三方调用通用重试装饰器。

用于 DashScope / Feishu / SMTP 等不稳定的网络调用。

设计：
- 指数退避（base * 2^n + jitter），上限 cap 秒。
- 仅对 transient 异常（urllib URLError / TimeoutError / 自定义可重试异常）重试，
  对业务级 4xx 不重试（caller 自行判断后再 raise）。
- 副作用禁用环境（RECRUIT_DISABLE_SIDE_EFFECTS=1）下，重试间隔被压成 0，
  避免测试拖慢。
"""
from __future__ import print_function

import os
import random
import sys
import time
from typing import Any, Callable, Optional, Tuple, Type


def _sleep_seconds(attempt, base=1.0, cap=8.0, jitter=0.25):
    # type: (int, float, float, float) -> float
    if (os.environ.get("RECRUIT_DISABLE_SIDE_EFFECTS") or "").strip().lower() in ("1", "true", "yes", "on"):
        return 0.0
    delay = min(cap, base * (2 ** attempt))
    delay += random.uniform(0, jitter)
    return delay


def call_with_retry(fn,
                    args=None,
                    kwargs=None,
                    retries=2,
                    base=1.0,
                    cap=8.0,
                    retriable=(Exception,),
                    label="http"):
    # type: (Callable[..., Any], Optional[tuple], Optional[dict], int, float, float, Tuple[Type[BaseException], ...], str) -> Any
    """同步调用 fn，遇到 retriable 异常重试。

    Args:
        fn: 要调用的函数。
        args/kwargs: 透传给 fn。
        retries: 额外重试次数（总尝试 = 1 + retries）。
        base: 第 0 次重试基准秒。
        cap: 单次最长退避秒数。
        retriable: 哪些异常类型允许重试，默认 Exception（caller 应收窄）。
        label: 日志前缀。

    Raises:
        最后一次尝试的异常。
    """
    args = args or ()
    kwargs = kwargs or {}
    last_err = None  # type: Optional[BaseException]
    total = max(1, retries + 1)
    for attempt in range(total):
        try:
            return fn(*args, **kwargs)
        except retriable as e:
            last_err = e
            if attempt + 1 >= total:
                break
            wait = _sleep_seconds(attempt, base=base, cap=cap)
            print("[{}] attempt {}/{} 失败: {}；{:.2f}s 后重试".format(
                label, attempt + 1, total, str(e)[:160], wait), file=sys.stderr)
            time.sleep(wait)
    if last_err is not None:
        raise last_err
    raise RuntimeError("call_with_retry: no attempts executed (retries={})".format(retries))
