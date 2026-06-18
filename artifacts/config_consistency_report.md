# 配置一致性报告

## 修复内容

### 统一配置读取

**问题：** `service.py` 中的 `is_ai_enabled()`、`get_ai_run_mode()`、`get_prompt_version()` 直接使用 `os.environ.get()` 读取环境变量，而 `config.py` 中已通过 pydantic-settings 定义了对应字段。两者功能重复但实现不同，可能导致配置不一致。

**修复：** 三个函数改为使用 `settings` 对象：

| 函数 | 修改前 | 修改后 |
|------|--------|--------|
| `is_ai_enabled()` | `os.environ.get("ENABLE_AI_PREDICTION", "false").lower() in ("true", "1", "yes")` | `settings.enable_ai_prediction` |
| `get_ai_run_mode()` | `os.environ.get("AI_RUN_MODE", "manual")` | `settings.ai_run_mode` |
| `get_prompt_version()` | `os.environ.get("AI_PROMPT_VERSION", "worldcup-ai-v1")` | `settings.ai_prompt_version` |

### 删除死代码

**问题：** `config.py` 中的 `deepseek_api_key`、`deepseek_base_url`、`xiaomi_api_key`、`xiaomi_base_url` 字段从未被任何代码引用。实际 API key 读取通过 `base.py` 的 `os.environ.get(api_key_env)` 完成。

**修复：** 删除这四个死代码字段。

**边界说明：**
- 系统级开关（ENABLE_AI_PREDICTION 等）→ 通过 `settings` 读取
- Provider API key → 通过 YAML 定义的 `api_key_env` + `os.environ.get()` 读取

**修改文件：** `backend/app/ai/service.py`、`backend/app/config.py`

**新增测试：** `tests/test_config_consistency.py`（7 个测试）

## 剩余风险

- Provider API key 仍通过 `os.environ.get()` 读取，未走 pydantic-settings。这是有意为之——provider key 通过 YAML 配置的 `api_key_env` 动态指定环境变量名，不适合硬编码到 Settings 类。
- `list_ai_model_status` 中仍有一处 `os.environ.get(provider_config.api_key_env)` 读取 provider key，这是正确用法。

## 对准确率链路的影响

无影响。配置读取方式的变化不影响业务逻辑，只是统一了配置来源。
