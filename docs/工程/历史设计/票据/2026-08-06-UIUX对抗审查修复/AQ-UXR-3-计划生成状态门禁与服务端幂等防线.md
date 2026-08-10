# AQ-UXR-3 — 计划生成状态门禁与服务端幂等防线

**What to build:** 让完整计划只在可验证的账户事实和生命周期状态下生成；缺事实时直达目标账户维护，readiness 错误可重试，生成中禁止重复提交，且服务端同样拒绝同计划日的并发重复生成。

**Blocked by:** None — can start immediately.

**Status:** completed（本地票据；GitHub 发布曾因集成权限 403 失败）

- [x] `needs_facts` 状态不允许生成，并直接定位、展开和聚焦需要维护的账户。
- [x] readiness 读取错误不允许生成，提供页面内重试；不会把未知状态视为已就绪。
- [x] `running` 状态在前端禁用重复提交并给出等待语义；`stale` 与 `failed` 保留重新生成入口。
- [x] 计划生成用例对同计划日的进行中生成设有服务端最终互斥防线，并以结构化、可恢复结果表达冲突。
- [x] API、页面和真实浏览器覆盖 needs-facts、readiness-error、running、stale、failed、可生成及并发重复提交。
