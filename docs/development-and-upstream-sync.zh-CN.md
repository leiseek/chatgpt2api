# 二次开发与官方同步手册

本文用于维护本项目的定制版本，涵盖日常二次开发、同步官方更新、冲突取舍、测试发布和回滚。当前定制版本包含 Agnes 图片渠道、ChatGPT/Agnes 双渠道多图生图、普通用户权限、用户自定义登录密钥、图片缩略图和前端静态 Release 产物等扩展。

## 1. 远程仓库和分支约定

当前仓库使用两个远程：

| 名称 | 地址 | 用途 |
| --- | --- | --- |
| `origin` | `https://github.com/leiseek/chatgpt2api.git` | 个人 fork，提交和部署目标 |
| `upstream` | `https://github.com/basketikun/chatgpt2api.git` | 官方仓库，只用于获取更新 |

建议保持以下分支职责：

- `feature/agnes-multi-channel`：当前定制功能开发分支。
- `main`：用于对外发布或部署的稳定分支。
- `sync/upstream-vX.Y.Z`：每次同步官方版本时临时创建的整合分支。
- `backup-before-vX.Y.Z`：同步前的本地保护分支，确认稳定后再清理。

检查当前状态：

```bash
git remote -v
git branch -vv
git status --short
```

## 2. 日常二次开发流程

### 2.1 开始开发前

先确保工作区干净，并从定制分支创建功能分支：

```bash
git switch feature/agnes-multi-channel
git pull --ff-only origin feature/agnes-multi-channel
git switch -c feature/<功能名称>
```

如果有未完成的本地修改，不要直接切换分支。先提交一个临时检查点，或者使用 Git 提供的 stash 功能保存修改。

### 2.2 本地启动

后端：

```bash
uv sync
uv run main.py
```

前端：

```bash
cd web
bun install
bun run dev
```

Windows 环境也可以使用已安装的 npm：

```powershell
cd web
npm install
npm run dev
```

### 2.3 提交前检查

```bash
uv run pytest -q

cd web
bun run lint
bun run typecheck
bun run build
cd ..
```

没有真实上游账号和网络时，不要运行 live 用例；需要运行时再显式执行：

```bash
uv run pytest --run-live -m live
```

每个功能使用独立提交，提交信息写清楚行为变化，例如：

```bash
git add <相关文件>
git commit -m "feat: improve dual-channel image results"
git push -u origin feature/<功能名称>
```

## 3. 同步官方新版本

以下示例以官方发布 `v1.9.0` 为例。同步前先备份运行数据和本地配置：

```powershell
Copy-Item data data.backup-before-v1.9.0 -Recurse
Copy-Item config.json config.backup-before-v1.9.0
```

然后获取官方提交和标签：

```bash
git switch feature/agnes-multi-channel
git status --short
git fetch upstream --tags
git show --stat v1.9.0
```

创建保护分支和整合分支：

```bash
git branch backup-before-v1.9.0
git switch -c sync/upstream-v1.9.0
```

正式合并官方标签：

```bash
git merge --no-ff v1.9.0
```

如果官方没有发布标签，使用官方主分支：

```bash
git merge --no-ff upstream/main
```

不要在定制分支上使用 `git reset --hard upstream/main`，也不要用官方代码直接覆盖整个工作区，这会绕过冲突检查并删除定制功能。

## 4. 冲突处理和取舍原则

查看冲突文件：

```bash
git status
git diff --name-only --diff-filter=U
```

建议使用 VS Code 等编辑器的三方合并界面逐文件处理：

- Current Change：当前定制分支内容。
- Incoming Change：官方新版本内容。
- 两边都需要时，手动合并，而不是选择“全部接受”。

### 4.1 必须重点保护的定制功能

官方改动涉及以下目录或文件时，需要手动确认，不要整文件覆盖：

- `services/image_providers/agnes.py`
- `services/account_service.py`、`services/image_task_service.py`
- `api/image_inputs.py`、`api/image_tasks.py`、`api/accounts.py`
- `services/protocol/` 下的图片生成、编辑、Responses 和聊天协议
- `web/src/app/image/` 下的双渠道、多图和结果分组逻辑
- `web/src/components/image-thumbnail.tsx`
- `web/src/store/image-conversations.ts`
- `web/src/app/debug/` 的普通用户访问和 PPT/PSD 页面
- `services/auth_service.py` 和用户密钥管理组件

### 4.2 取舍顺序

1. 官方安全修复、协议兼容修复和数据迁移：优先合入官方逻辑。
2. Agnes 渠道、双渠道并行、多图分组和缩略图：保留定制行为，再把官方修复嵌入其中。
3. 同一函数发生冲突时，先理解官方变更目的，再在函数边界保留定制分支；不要只按代码行数选择一方。
4. `config.json` 是本地配置，代理、后台密钥和运行参数必须手动合并，不能用官方文件整体替换。
5. `data/` 是运行数据，不参与 Git 同步；`uv.lock`、`web/bun.lock` 等锁文件冲突时，解决依赖声明后重新生成锁文件。

处理完成后执行：

```bash
git add <已解决的文件>
git commit
```

如果冲突处理结果不可靠，可以放弃本次合并，保护分支不会受影响：

```bash
git merge --abort
```

## 5. 同步后的回归验证

后端：

```bash
uv run pytest -q
```

前端：

```bash
cd web
bun run lint
bun run typecheck
bun run build
cd ..
```

至少验证以下功能：

- ChatGPT 图片生成和编辑。
- Agnes API Key 导入、模型列表和图片生成。
- ChatGPT/Agnes 双渠道同时生成。
- 每个渠道生成多张图片时的分组展示和单张失败重试。
- 普通用户登录、画图和开发调试权限。
- 用户自定义登录密钥和管理员权限边界。
- 图片管理缩略图和原图预览。
- 代理配置和 Session/账号刷新链路。

## 6. 发布前端静态产物

目标服务器如果无法安装 Node.js 或执行 Next.js 构建，只需在可构建环境运行：

```bash
cd web
bun run build
```

构建结果在 `web/out/`。将整个目录作为后端项目根目录的 `web_dist/` 发布，不需要把 Node.js 带到目标服务器。

当前定制版本的前端资产位于：

<https://github.com/leiseek/chatgpt2api/releases/tag/v1.8.0-custom>

下载并部署：

```bash
tar -xzf chatgpt2api-v1.8.0-custom-frontend-web_dist.tar.gz
```

解压后得到 `web_dist/`，停止服务、备份旧目录后覆盖项目根目录中的 `web_dist/`，再重启后端。发布前检查构建产物是否包含最新的站点标题和前端功能。

## 7. 推送整合结果

建议先把整合分支推送到个人 fork，确认 CI 和页面测试通过：

```bash
git push -u origin sync/upstream-v1.9.0
```

确认无误后更新定制分支和部署分支：

```bash
git switch feature/agnes-multi-channel
git merge --ff-only sync/upstream-v1.9.0
git push origin feature/agnes-multi-channel
git push origin HEAD:main
```

如果希望先人工审核，可以只推送 `sync/upstream-v1.9.0`，在 GitHub 上检查变更后再合并，不要直接覆盖远程 `main`。

## 8. 回滚

同步或发布后发现问题时，优先使用保护分支或已发布标签进行回滚验证：

```bash
git worktree add ../chatgpt2api-rollback backup-before-v1.9.0
```

运行数据恢复前先停止服务，并从备份恢复 `config.json`、`.env` 和 `data/`。确认旧版本可用后，再决定是否通过 `git revert` 回滚正式分支，避免改写已经推送的公共历史。

## 9. 最终检查清单

- [ ] 工作区无未提交的业务修改。
- [ ] `data/`、`.env`、真实 API Key 和管理员密钥未进入 Git。
- [ ] 官方同步使用了独立整合分支和保护分支。
- [ ] 冲突文件已逐个检查，没有整目录覆盖定制代码。
- [ ] 后端测试、前端 lint、typecheck 和 build 全部通过。
- [ ] 前端静态产物已重新生成并验证站点标题、登录和主要页面。
- [ ] 推送前已备份服务器运行数据。
