# 09: 作业存储与资源任务迁移到桌面壳

**Status:** ready-for-agent
**Blocked by:** 01（工程基线，已完成）

## Context

作业读写目前走 `vite.config.ts` 的 `/api/jobs`，资源更新走 `/api/resources`；前端分别在 `src/jobLibrary.ts` 与 `src/components/ResourceCenter.vue` 中直接 `fetch`。这两条路径在打包后都不存在。

资源管线 `scripts/resources.mjs` 与三个索引脚本本身可用，保留；需要迁移的是触发入口，并为多区服引入存储隔离。

## Goal

桌面版中作业保存/列表/打开/删除与资源检查/更新/校验/完整校准均可用；图库与清单按区服分目录；开发服务器的 jobs 与 resources API 移除。

## Constraints

- 作业 JSON v1 格式不变，`jobs/` 目录不变。
- 保留文件名清洗与路径逃逸防护。
- 资源管线的 SHA-256 校验、原子写入、404 记录行为不变。
- 浏览器预览降级为只读，保存与资源更新明确提示不可用。
- 前端只做必要的接口替换与区服路径适配，不重写编队/编排界面。
- `npm run build`、`npm run replay` 全绿。

## Implementation notes

壳侧新建：

- `src-tauri/src/storage.rs`：作业 CRUD。
- `src-tauri/src/resources.rs`：资源任务进程管理与输出转发。

Tauri 命令：

- `list_jobs()`、`read_job(file)`、`save_job(name, job)`、`delete_job(file)`
- `check_resources(region)`、`update_resources(region, refresh)`、`verify_resources(region)`

资源任务通过壳启动现有 Node 脚本；同一时间只允许一个资源任务；输出通过事件转发给 `ResourceCenter.vue`。

多区服存储：

- 根目录改为 `data/<区服>/`，区服键为 `CN` / `JP` / `TW`。
- 每个区服有独立的原始元数据、图库与 `resource-manifest.json`。
- 首次迁移把现有 `data/` 中的国服内容归入 `data/CN/`；不要删除原文件直到校验通过。
- `scripts/resources.mjs` 与索引脚本接受 `--region` 参数，默认 `CN`。
- 前端 `src/atlas.ts`、`src/equips.ts` 的资源基础路径按当前设备区服解析；未选设备时默认 `CN`。

前端接口替换：

- `src/jobLibrary.ts`：Tauri 环境调用壳命令；浏览器环境只读并提示。
- `ResourceCenter.vue`：检查更新、更新、校验、完整校准改走壳；保留现有日志展示。

## Steps

1. 实现壳的作业 CRUD 与文件名/路径防护。
2. 实现壳的资源任务命令与输出事件。
3. 给资源脚本加 `--region`，迁移现有国服数据到 `data/CN/`。
4. 替换前端作业库与资源中心调用。
5. 移除 `vite.config.ts` 中的 jobs 与 resources 中间件。
6. 运行 Verification。

## Verification

```bash
cargo check --manifest-path src-tauri/Cargo.toml
npm run build
npm run replay

# 资源管线仍可独立运行
npm run resources:verify
npm run resources:check

# 桌面手测
npm run tauri dev
# 期望：保存作业、重新打开、资源校验均可用
```

## Acceptance criteria

- [ ] 桌面版保存作业后能在列表看到并重新打开，内容完整。
- [ ] 打包后的应用中作业读写不依赖开发服务器。
- [ ] 文件名清洗与路径逃逸防护有测试或自检覆盖。
- [ ] 资源检查、更新、校验、完整校准由壳触发，前端显示进度与结果。
- [ ] 图库与清单按区服分目录，切换区服不重下已有区服。
- [ ] 浏览器预览保存与资源更新明确提示不可用。
- [ ] Vite 的 jobs 与 resources API 已移除。
- [ ] 构建、回放、资源校验全部通过。

## References

- ADR-0002：作业存储由壳负责。
- ADR-0003：区服属于设备，数据按区服隔离。
- 现有实现：`vite.config.ts`、`src/jobLibrary.ts`、`src/components/ResourceCenter.vue`、`scripts/resources.mjs`。
- 需求：F2、F3。
