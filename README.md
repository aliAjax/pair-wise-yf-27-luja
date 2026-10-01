# 博物馆藏品来源与返还审查

标准库实现、SQLite 持久化的独立项目。它管理藏品、历史流转事件、来源引用、证据、权利主张和审查阶段，并提供面向公众、主张人、审查员和工作人员的分层视图。

## 运行

```bash
python3 app.py --init --seed
python3 app.py
```

访问 <http://127.0.0.1:8103>。数据库默认是 `provenance.db`。测试命令：

```bash
python3 -m unittest -v
```

演示身份通过 `X-User-Id` 传入：`staff`、`reviewer1`、`claimant1`、`public`。

## 主要接口

- `POST /api/objects`、`GET /api/objects`、`GET /api/objects/{id}`：藏品登记与分层查看。
- `POST /api/objects/{id}/update`：更新藏品并创建完整快照。
- `POST /api/sources`、`POST /api/objects/{id}/events`：来源与流转事件。
- `POST /api/objects/{id}/evidence`：上传证据，服务端计算 SHA-256。
- `POST /api/objects/{id}/claims`：提交权利主张。
- `POST /api/claims/{id}/transition`：按 `submitted → under_review → negotiating → resolved_return/rejected` 流转。
- `GET /api/objects/{id}/gaps`：当前来源链缺口（仅审查员/研究员，公众与主张人越权返回 403）。
- `GET /api/claims/{id}/conclusion`、`POST /api/claims/{id}/conclusion`：查看/重新冻结受理结论。
- `GET /api/objects/{id}/history` 与 `/history/{version}`：版本历史及历史快照。

## 受理结论（冻结与作废）

- 审查员受理主张（`submitted → under_review`）时，系统把**当时藏品版本、每段流转事件引用的来源与证据、还缺的环节**冻成结论。
- 受理后研究员补事件（或改藏品）必须在请求体带 `accepted_version`（即受理版本）。版本一变，旧结论立即作废；带错版本会返回 `conclusion_stale`，须先查看最新结论、重新冻结后再补。
- 结论作废后，审查员 `POST /api/claims/{id}/conclusion` 按当前藏品版本重新冻结（重来）。
- 缺口未补齐时，主张不能离开受理阶段；除非审查员的审查说明里含“例外”二字（例外说明），否则返回 `gap_not_resolved` 并留在 `under_review`。
- 升级后，旧数据里没有结论的主张会按当前藏品状态自动回填一份结论。

公众看不到持有人和内部事件；主张人只能查看自己的主张；阶段不能跳跃或从终态重新打开；每次对象变化都会保存 JSON 快照和审计记录。
