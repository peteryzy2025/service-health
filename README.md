# 企业内网服务健康巡检系统

## 项目介绍

这是一个面向运维团队的 HTTP 服务巡检 API。管理员可以登记各环境的服务、负责团队和健康检查路径，按需发起检查，立即看到目标返回的状态码、耗时或失败原因。每天零点，系统将前一天的巡检明细汇总为服务日报，支持按环境和团队查看。

例如，订单服务当日完成 10 次巡检，其中 8 次返回预期状态码，日报健康率就是 80%。全天没有完成巡检时，日报保留样本数 0，健康率为空。

| 组件 | 在项目中的职责 |
|---|---|
| Python 3.13、Django 5.2、Django REST Framework | 提供环境、团队、服务的 CRUD 和巡检 API，通过 Django 中间件记录请求耗时。 |
| Requests | 携带服务专属 Bearer Token 请求指定路径，按预期状态码判定结果，记录超时、连接和 TLS 错误。 |
| MySQL 8.4（InnoDB） | 保存配置、加密凭据、巡检记录和日报。使用事务与行锁处理并发轮换和日报重算。 |
| Celery Beat、Redis 7、Celery Worker | Beat 定时投递，Redis 传递消息，Worker 执行每 8 小时一次的 Token 轮换和每日统计。业务时区为 `Asia/Shanghai`。 |
| cryptography.Fernet | 加密保存随机生成的巡检 Token，执行器发起请求时解密。 |
| Gunicorn、Docker Compose | 运行 Django Web 进程，编排数据库、消息队列和后台任务进程。 |

## 部署与启动

### 1. 准备环境

安装 Python 3 和带 Compose v2 的 Docker，启动 Docker 引擎。Windows 可以使用 Docker Desktop 的 Linux 容器模式。打开终端，进入仓库根目录（能看到 `compose.yaml`、`manage.py` 和 `init_env.py`），确认以下命令可用。

```sh
python --version
docker compose version
docker info
```

宿主机上的 Python 仅用于生成本地配置。Compose 会构建应用镜像，并启动 MySQL、Redis、Web、Worker 和 Beat。

### 2. 初始化配置并启动服务

```sh
python init_env.py
docker compose up -d --build
docker compose ps -a
```

首次启动先等待 `db` 和 `redis` 健康，再由 `migrate` 建表并初始化 `dev/test/prod` 三个环境。看到 `migrate` 显示 `Exited (0)`，且 `web`、`worker`、`beat` 都已运行，即可创建管理员。若迁移失败，用 `docker compose logs migrate db` 查看原因。

Compose 为 MySQL 创建独立的 health-mysql 数据卷。已有旧版 .env 的用户请保留原有加密密钥并新增随机 MYSQL_PASSWORD。旧库的业务数据需要单独导入，新建卷只运行 Django 迁移。测试使用独立的 test_health 库，容器初始化脚本仅为该测试库补充权限。

初始化脚本用系统随机源创建本地 `.env`，已有文件时停止，避免更换数据库密码或 Fernet 密钥。Web、Worker 和 Beat 共享该配置。仅 Web 端口发布在宿主机回环地址。管理 API 使用 Django staff 账号和 HTTP Basic 鉴权；对外部署应使用 HTTPS。演示目标单独使用巡检 Bearer Token。

### 3. 创建管理员并验证启动

```sh
docker compose exec web python manage.py createsuperuser
docker compose exec worker celery -A config inspect ping
```

按提示设置管理员用户名和密码。Worker 应返回 `pong`。用浏览器打开 [环境列表](http://127.0.0.1:8000/api/environments/)，按提示输入管理员凭据，分页 JSON 中应包含三个预置环境。也可以使用下方 PowerShell 示例验证管理 API。

后续启动使用 `docker compose up -d`，查看日志使用 `docker compose logs --tail=100 web worker beat`。执行 `docker compose down` 可停止并移除容器，MySQL 数据保存在命名卷中。

## 跑通一次巡检（PowerShell 7）

在宿主机访问管理 API 使用 `http://127.0.0.1:8000`。示例中的目标地址 `http://web:8000` 由执行巡检的容器访问，其中 `web` 是 Compose 服务名。

以下命令创建团队、注册服务并指向它自己的演示目标，使用 API 实际返回的 ID。数据库已迁移时 `dev/test/prod` 自动可用。

```powershell
$api = 'http://127.0.0.1:8000/api'
$auth = @{Authentication='Basic'; Credential=(Get-Credential); AllowUnencryptedAuthentication=$true}
$dev = (Invoke-RestMethod "$api/environments/" @auth).results | Where-Object code -eq dev
$team = Invoke-RestMethod "$api/teams/" @auth -Method Post -ContentType application/json -Body '{"name":"订单团队"}'
$data = @{name='订单服务'; environment=$dev.id; team=$team.id; base_url='http://web:8000'; health_path='/health/'}
$svc = Invoke-RestMethod "$api/services/" @auth -Method Post -ContentType application/json -Body ($data | ConvertTo-Json)
$id = $svc.id
Invoke-RestMethod "$api/services/$id/" @auth -Method Patch -ContentType application/json -Body (@{health_path="/demo/services/$id/health/"} | ConvertTo-Json)
Invoke-RestMethod "$api/services/$id/checks/" @auth -Method Post
docker compose exec web python manage.py rotate_token $id
Invoke-RestMethod "$api/services/$id/checks/" @auth -Method Post
Invoke-RestMethod "$api/services/$id/" @auth
$day = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, 'Asia/Shanghai').ToString('yyyy-MM-dd')
docker compose exec web python manage.py health_report $day
Invoke-RestMethod "$api/daily-health/?stat_date=$day" @auth
```

两次巡检应均返回 201、`status=success`、`http_status=200`，第二次使用更高的 Token 版本。把服务的 `expected_status` 改为 204 并再次巡检，可以观察到 201 响应中包含 `failed/unexpected_status` 和实际的 200。重算当天日报后，三次采样中两次成功，健康率为 66.67%。演示中手动补算当天得到截至当前的结果，次日零点重新生成完整日报。

## API

| 地址 | 用途 |
|---|---|
| `/api/environments/`、`/api/teams/`、`/api/services/` | GET 列表、POST 新建；详情地址追加 `{id}/` 后支持 GET、PUT、PATCH、DELETE。归档后的服务退出管理列表和巡检入口，历史仍可查。 |
| `POST /api/services/{id}/checks/` | 服务端按固定配置请求一次目标，连接超时 2 秒、读取超时 3 秒；不重试、不跟随重定向。 |
| `GET /api/checks/`、`GET /api/checks/{id}/` | 按 service、environment、team、status、started_from（含）、started_before（不含）筛选。时间参数使用带偏移的 ISO 8601。 |
| `GET /api/daily-health/` | 逐服务日报，支持 stat_date、environment、team、service。无样本时 health_rate 为 null。 |
| `GET /api/daily-health/summary/` | 同样的过滤条件，按日期、环境和团队汇总。用总成功数除以总样本数。 |
| `GET /demo/services/{id}/health/` | 仅 DEMO_ENABLED=1 时注册。校验当前 Token；失配返回 401，服务已归档或不存在返回 404。 |

列表使用分页，默认每页 100 条。配置目标时，管理员必须先将准确的协议、主机和端口加入 `PROBE_ORIGINS`（多个用逗号分隔，无尾斜杠），路径单独填写。密钥与凭据内容不出现在 API 和请求耗时日志里。客户端只读取响应头以判断状态码。

## 设计与统计口径

- 创建服务、初始凭据和首条归属记录同事务提交。巡检先固定服务配置、团队、环境和凭据版本并提交 running 记录，再执行网络请求，最后保存终态。进程异常退出留下的 running 记录可按开始时间查询；仅终态参与统计。
- 目标正常响应但状态码不符记为 unexpected_status；超时、TLS 错误、连接错误分别记录。X-Request-Duration-Ms 衡量端到端 API 耗时，CheckRun.duration_ms 衡量目标 HTTP 调用耗时。
- Beat 每天 00:00、08:00、16:00 派发轮换。逐服务任务在 InnoDB 行锁下比较时段，再生成 32 字节随机 Token，以 Fernet 加密保存。重复或过期任务跳过。手动轮换以实际执行时刻为标记，可在同一时段立即演示。演示端读取共享凭据表；独立服务需要对接密钥发布机制。在途请求撞上轮换时，HTTP 401 会记录在原凭据版本下。
- Beat 每天 00:00 触发前一业务日期日报。按完成时间使用左闭右开窗口，归属使用巡检快照。零样本的服务取当天最后有效的归属。事务先创建或锁定以业务日期为主键的 ReportDayLock 行，按日期串行化重算，单次事务替换整天的数据。支持晚到记录补算。
- 8/10 表示十次按需观测中八次成功。采样频率由调用者决定，该指标不能推算全天的时间可用率。

## 测试和运维命令

```sh
docker compose exec web python manage.py test
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec worker celery -A config inspect ping
docker compose logs worker beat
docker compose exec web celery -A config call monitor.tasks.dispatch_rotation
docker compose exec web celery -A config call monitor.tasks.report_day --args='["2026-11-21"]'
```

`dispatch_rotation` 对本轮已更新的服务会跳过；`rotate_token` 用于立即轮换。部署一个 Beat 实例；Worker 可以增加。请持续保管 `TOKEN_ENCRYPTION_KEY`，已有密文依赖同一密钥。

也可用 Python 3.13 创建虚拟环境，安装 `requirements.txt`，将 `.env` 的键值导入进程环境后运行 `python manage.py migrate` 和 `python manage.py test`。未设置 MYSQL_HOST 时使用 SQLite，适合开发和测试。行锁与日报的跨进程串行写入应在 MySQL 8 下验证。
