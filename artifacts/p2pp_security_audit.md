# 安全审计报告（本地使用场景）

> 本项目为本地个人使用，不对外暴露网络服务，因此安全加固以代码质量为主，不涉及认证/限流等生产级安全措施。

## 已评估但跳过的安全项

| 项目 | 原因 | 备注 |
|------|------|------|
| AI 端点认证 | 本地使用，无外部访问 | 如需部署外网，必须添加 |
| Rate limiting | 本地使用，无滥用风险 | 如需部署外网，必须添加 |
| limit 参数上限 | 本地使用，自行控制 | 如需部署外网，必须添加 |
| SPA 路径遍历防护 | 本地使用，无攻击面 | 如需部署外网，必须添加 |

## 已完成的安全相关代码质量改进

### 1. AI 概率校验
防止非法 AI 输出污染 ensemble 和评分链路。详见 `ai_probability_validation.md`。

### 2. 配置一致性
消除 `os.environ.get()` 与 `settings` 的不一致，避免配置错误导致意外行为。详见 `config_consistency_report.md`。

### 3. Provider 重试安全
- `retry-after` 非整数不再导致崩溃
- 5xx 错误有指数退避，避免无限快速重试
- 连接复用减少 TLS 握手次数

### 4. T-30 锁定逻辑统一
避免维护遗漏导致赛后预测参与评分。详见 `accuracy_chain_safety_report.md`。

## 如需部署外网的必做事项

1. AI POST 端点添加认证（X-Admin-Api-Key header）
2. 添加 rate limiting（每分钟限制 AI 调用次数）
3. `limit` 参数添加上限校验
4. SPA fallback 添加路径遍历防护（resolve + relative_to 检查）
5. 添加 HTTPS
6. 添加 CORS 配置
