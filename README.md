# 企业内网服务健康巡检系统

Django / DRF 提供资产管理和按需 HTTP 巡检，MySQL 8.4（InnoDB）保存执行记录和日报，Celery Worker 通过 Redis 接收 Beat 定时派发的任务。默认使用 Asia/Shanghai 业务日期。

## 启动与验收

需要 Docker Compose v2 和 Python 3。进入本目录后执行：

```sh
python init_env.py
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
docker compose ps
```

Compose 为 MySQL 创建独立的 health-mysql 数据卷。已有旧版 .env 的用户请保留原有加密密钥并新增随机 MYSQL_PASSWORD。旧库的业务数据需要单独导入，新建卷只运行 Django 迁移。测试使用独立的 test_health 库，容器初始化脚本仅为该测试库补充权限。

初始化脚本用系统随机源创建本地 `.env`，已有文件时停止，避免更换数据库密码或 Fernet 密钥。Web、Worker 和 Beat 共享该配置。仅 Web 端口发布在宿主机回环地址。管理 API 使用 Django staff 账号和 HTTP Basic 鉴权；对外部署应使用 HTTPS。演示目标单独使用巡检 Bearer Token。

### 五分钟演示（PowerShell 7）

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
