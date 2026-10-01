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
- `POST /api/sources`、`POST /api/objects/{id}/events`：来源与流转事件；藏品一旦存在受理结论，补事件必须携带 `base_version`。
- `POST /api/objects/{id}/evidence`：上传证据，服务端计算 SHA-256。
- `POST /api/objects/{id}/claims`：提交权利主张。
- `POST /api/claims/{id}/transition`：按 `submitted → under_review → negotiating → resolved_return/rejected` 流转；受理（进入 `under_review`）时自动冻结结论；离开受理阶段时结论必须仍然有效，缺口未补齐需带 `exception_note`（审查员例外说明）。
- `GET /api/claims/{id}/conclusion`、`POST /api/claims/{id}/conclusion`：查看最新受理结论；审查员按当前藏品版本重新冻结（仅 staff/reviewer）。
- `GET /api/objects/{id}/history` 与 `/history/{version}`：版本历史及历史快照。

受理时冻结的结论包含：当时藏品版本、每段流转事件引用的来源与证据、还缺的环节（`missing_source`/`missing_evidence`/`no_events`）。藏品版本一旦变化，旧结论自动作废；此后研究员补事件必须携带 `base_version`（受理版本），版本不符返回 409，需读取最新结论后重试。缺口未补齐且审查员未给例外说明时，主张留在受理阶段。结论与内部缺口仅审查员和工作人员可见，公众与主张人访问会被拒绝。启动初始化时自动为旧数据中审查中的主张按当前藏品版本回填结论（迁移幂等）。

公众看不到持有人和内部事件；主张人只能查看自己的主张；阶段不能跳跃或从终态重新打开；每次对象变化都会保存 JSON 快照和审计记录。
