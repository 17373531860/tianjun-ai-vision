# GitHub Actions Workflows 一览

本目录下共 5 个 workflow，彼此正交：

| 文件 | 触发条件 | 关键职责 | 失败意味着什么 |
|---|---|---|---|
| `build.yml` | push tag `v*` / push 到 main、master / 手动 | Windows 安装包全链：Nuitka 编译白名单 `.pyd` → conda-pack → 前端 build → Electron 打包 → Inno Setup → GitHub Release（约 4 小时）。**tag 触发时先过 `test-gate` job（复用 test-virtual 的 pytest+BDD 虚拟集），红了不进打包** | 发版链断（客户拿不到安装包）；`test-gate` 红 = 带回归的版本被拦下 |
| `plugin-tooling.yml` | push/PR 触碰 `scripts/plugin/`, `docs/plugin-system/`, `plugins-examples/`, `tests/plugin_system/`, `tests/step_defs/test_plugin_*.py`, `backend/plugin_system/`, `backend/api/plugins.py` | 插件系统全栈：schema、CLI 单测、`pack→sign→verify→install` 端到端、上传/激活/停用 BDD | 插件发布/分发链路被破坏（不可发版） |
| `db-matrix.yml` | push/PR 触碰 `backend/`, `alembic/`, `scripts/db/`, `tests/`, `docs/database-migration/` | SQLite + PostgreSQL 双库矩阵跑同一套单元 + BDD；PG 端额外跑 Alembic baseline upgrade 和数据搬迁脚本 dry-run | 数据库 dialect 兼容性回归（影响多客户/PG 部署） |
| `test-virtual.yml` | push 到 main（触碰 backend/tests 等路径）/ 任意 PR / 手动 | 虚拟剧本源 `synthetic` 路径下的 BDD + Playwright E2E（无 GPU/无摄像头/无模型） | 业务主链路（检测、Session、Cycle、MES）被破坏 |
| `gitee-upload.yml` | 仅手动（workflow_dispatch，必填 version 如 `v2.0.2`） | 从 GitHub Release（`17373531860/tianjun-releases`）取产物推到 Gitee 双镜像仓（客户下载走 Gitee） | 国内镜像下载断 |

## 触发矩阵速查（谁在什么时候跑）

| 事件 | build | test-virtual | db-matrix | plugin-tooling | gitee-upload |
|---|---|---|---|---|---|
| push tag `v*` | ✅（先 test-gate 再打包） | — | — | — | — |
| push 到 main | ✅ | ✅（触碰对应路径时） | ✅（触碰对应路径时） | ✅（触碰对应路径时） | — |
| PR 到 main | — | ✅ | ✅（触碰对应路径时） | ✅（触碰对应路径时） | — |
| 手动 workflow_dispatch | ✅ | ✅ | — | — | ✅（必填 version） |

## 必跑 / 选跑

- **每次 PR 必跑**：`plugin-tooling`（如改插件相关）、`db-matrix`（如改后端/测试）、`test-virtual`（任何 PR）。
- **发版链**：临时 public → push tag → `build.yml`（test-gate → 打包 → GitHub Release）→ 手动触发 `gitee-upload` 推国内镜像 → 用 `scripts/ci/watch_and_private.sh` 盯盘并自动切回 private（见 `update-release` skill 第 8 步）。

## 本地复刻 CI

### 插件签名往返（plugin-tooling 的 signing-roundtrip job）

```bash
mkdir -p /tmp/keys /tmp/out
python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode(), end='')" > /tmp/keys/secret.txt
python - <<'PY'
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
k = rsa.generate_private_key(public_exponent=65537, key_size=3072)
open("/tmp/keys/priv.pem","wb").write(k.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption()))
open("/tmp/keys/pub.pem","wb").write(k.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
PY

python scripts/plugin/pack-plugin.py --src plugins-examples/tier1-theme --out /tmp/out --skip-build
UNS=$(ls /tmp/out/*-uns.tjvplugin | head -1)

# 关键：CI 用 PLUGIN_KEY_PASSWORD 环境变量绕过 getpass tty 等待
mkdir -p /tmp/out/signed
PLUGIN_KEY_PASSWORD="" python scripts/plugin/sign-plugin.py \
  --in "$UNS" --out /tmp/out/signed \
  --key /tmp/keys/priv.pem --secret /tmp/keys/secret.txt --signed-by local-ci

SIGNED=$(ls /tmp/out/signed/*.tjvplugin | head -1)
python scripts/plugin/verify-plugin.py --in "$SIGNED" --pub /tmp/keys/pub.pem
python scripts/plugin/install-plugin.py --in "$SIGNED" --pub /tmp/keys/pub.pem \
  --secret /tmp/keys/secret.txt --data-dir /tmp/install-real
```

### DB 矩阵（db-matrix）

```bash
# SQLite 模式（默认）
python -m pytest tests/plugin_system/ tests/step_defs/ -v

# PostgreSQL 模式
docker compose up -d postgres
DATABASE_URL=postgresql+psycopg2://tianjun:tianjun_dev_pwd@127.0.0.1:5433/tianjun \
  alembic upgrade head
DATABASE_URL=postgresql+psycopg2://tianjun:tianjun_dev_pwd@127.0.0.1:5433/tianjun \
TIANJUN_TEST_PG_SCHEMA=tianjun_test \
  python -m pytest tests/plugin_system/ tests/step_defs/ -v
```

## 关键设计取舍

- `sign-plugin.py` 默认仍用交互式 `getpass`（保护主作者隔离机器的工作流），CI 通过 `PLUGIN_KEY_PASSWORD` 环境变量旁路 tty 要求。空字符串等价于"私钥未加密"。
- `db-matrix` 的 PG 任务通过 `services.postgres` 起内置 PG 16 服务（不依赖 `docker-compose.yml`），密码用 CI 专用 `tianjun_test`，与本地 `tianjun_dev_pwd` 隔离。
- 测试态 `conftest.py` 自动检测 `DATABASE_URL` 前缀：以 `postgresql` 开头则切到 PG 模式，并把 `search_path` 注入 DSN options，做到 schema 级隔离不污染 public。
