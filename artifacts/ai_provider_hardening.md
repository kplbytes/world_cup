# P2++ AI Provider 加固报告

## 修复内容

### 1. OpenAI-compatible Provider 基类抽象

**问题：** `deepseek.py` 和 `xiaomi.py` 的 `predict()` 方法有 ~120 行完全重复的代码（API 调用、重试、错误处理、system_prompt）。

**修复：** 新建 `openai_compat.py` 基类，提取所有共享逻辑。子类仅保留 `default_base_url` 和 `provider_name` 两个属性（各 12 行）。

**修改文件：**
- 新增 `backend/app/ai/providers/openai_compat.py`
- 简化 `backend/app/ai/providers/deepseek.py`（150 行 → 12 行）
- 简化 `backend/app/ai/providers/xiaomi.py`（156 行 → 12 行）

**新增测试：** `tests/test_openai_compat_provider.py`（13 个测试）

### 2. httpx.AsyncClient 连接复用

**问题：** 每次请求创建新的 `httpx.AsyncClient`，无法复用 TCP 连接和 TLS 握手。

**修复：** `OpenAICompatProvider` 实例级别持有 `_client`，通过 `_get_client()` 方法复用，`close()` 方法关闭。

### 3. 5xx 指数退避

**问题：** 5xx 错误后立即重试，无退避延迟。

**修复：** 添加 `await asyncio.sleep(min(2 ** attempt, 10))` 指数退避。同样应用于 `TimeoutException` 和 `ConnectError`。

### 4. retry-after 安全解析

**问题：** `int(response.headers.get("retry-after", 60))` 在非整数 header 时抛出 `ValueError`。

**修复：** 添加 `try/except (TypeError, ValueError)` 保护，fallback 到 60 秒。

## 剩余风险

- Provider 的 `_client` 未在应用关闭时统一调用 `close()`，可能导致资源泄漏警告。建议后续在 FastAPI lifespan 的 shutdown 阶段调用。
- 当前 `import asyncio` 在函数内部，后续可考虑移到模块顶部。

## 对准确率链路的影响

无影响。Provider 层的修改仅影响 API 调用方式和重试策略，不影响预测算法和评分逻辑。
