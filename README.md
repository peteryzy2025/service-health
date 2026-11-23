# 企业内网服务健康巡检系统

用来检查内网 HTTP 服务是否按预期响应。每个服务登记一个健康检查地址、一个预期状态码，以及所属环境和团队。管理员发起巡检后，可以直接看到这次请求的状态码、耗时和失败原因。系统保留每次检查的明细，次日按服务生成日报。

项目使用 Python 3.13、Django 5.2 和 Django REST Framework。巡检由 Web 进程同步执行，记录写入 MySQL 8.4。Celery Beat 通过 Redis 向 Worker 投递两项定时任务：轮换巡检 Token 和生成日报。

## 本地启动

需要 Python 3、Docker 和 Compose v2。在仓库根目录执行：

```sh
python init_env.py
docker compose up -d --build
docker compose ps -a
```

`init_env.py` 只在首次部署时运行，它会生成包含数据库密码和加密密钥的 `.env`。后续启动沿用这份文件。看到 `migrate` 显示 `Exited (0)` 表示建表完成，数据库已有 `dev`、`test`、`prod` 三个环境。此时创建管理账号：

```sh
docker compose exec web python manage.py createsuperuser
```

管理 API 位于 `http://127.0.0.1:8000/api/`，使用刚创建的账号进行 HTTP Basic 认证。查看后台任务日志用 `docker compose logs --tail=100 worker beat`。结束演示用 `docker compose down`，MySQL 数据保留在命名卷中。

## 第一次巡检

下面在 PowerShell 7 中检查一个名为 `orders-api` 的开发环境服务。项目自带一个会校验 Token 的演示目标，默认配置已启用。请在同一个终端中依次执行。

### 登记服务（首次使用）

输入上一步创建的管理员凭据，取出开发环境并创建负责团队：

```powershell
$api = 'http://127.0.0.1:8000/api'
$auth = @{
    Authentication = 'Basic'
    Credential = Get-Credential
    AllowUnencryptedAuthentication = $true
}

$dev = (Invoke-RestMethod "$api/environments/" @auth).results | Where-Object code -eq 'dev'
$team = Invoke-RestMethod "$api/teams/" @auth -Method Post -ContentType 'application/json' -Body '{"name":"订单组"}'
```

演示目标的路径包含服务 ID，所以先登记服务，拿到 ID 后再填入完整路径。这里的 `http://web:8000` 是 Web 容器自己能访问的地址。接入已有服务时，可以在登记时直接填写真实路径，并将对应的协议、主机和端口加入 `.env` 的 `PROBE_ORIGINS` 后重新创建容器。

```powershell
$body = @{
    name = 'orders-api'
    environment = $dev.id
    team = $team.id
    base_url = 'http://web:8000'
    health_path = '/'
    expected_status = 200
} | ConvertTo-Json
$service = Invoke-RestMethod "$api/services/" @auth -Method Post -ContentType 'application/json' -Body $body
$serviceUrl = "$api/services/$($service.id)/"
$target = @{health_path = "/demo/services/$($service.id)/health/"} | ConvertTo-Json
Invoke-RestMethod $serviceUrl @auth -Method Patch -ContentType 'application/json' -Body $target | Out-Null
```

### 发起检查，查看结果

每执行一次下面的 POST，就会产生一条新的巡检记录。调用者只指定服务，Web 进程负责读取当前 Token 并向目标发送 GET。

```powershell
$run = Invoke-RestMethod "${serviceUrl}checks/" @auth -Method Post
$run | Select-Object id, status, http_status, duration_ms, error_code, token_version
```

第一次检查演示目标时，应看到 `status=success`、`http_status=200`，`error_code` 为空。**判断服务是否健康看响应体里的 `status`**。管理 API 返回的 HTTP 201 表示本次巡检记录已创建。即使目标返回 503，这条记录也会创建成功，此时记录的是 `failed`、`503` 和 `unexpected_status`。如果请求超时，则记录 `timeout`，目标状态码为空。

要复查刚才那条记录，可以按巡检 ID 读取详情：

```powershell
Invoke-RestMethod "$api/checks/$($run.id)/" @auth
```

## API 速查

以下路径均相对于 `/api/`，需要具有 staff 权限的账号。列表结果在 `results` 中，默认每页 100 条。

### 环境、团队和服务

| 资源 | 列表与创建（GET / POST） | 详情与修改（GET / PUT / PATCH / DELETE） |
|---|---|---|
| 环境 | `environments/` | `environments/{id}/` |
| 团队 | `teams/` | `teams/{id}/` |
| 服务 | `services/` | `services/{id}/` |

删除服务会将其归档，已有巡检记录仍可查询。删除仍被引用的环境或团队会返回 409。

### 巡检与记录

| 方法 | 路径 | 操作 |
|---|---|---|
| POST | `services/{id}/checks/` | 对该服务执行一次巡检，返回本次记录。请求体可留空。 |
| GET | `checks/` | 查询巡检历史，例如 `checks/?service=1&status=failed`。 |
| GET | `checks/{id}/` | 按巡检记录 ID 查询详情。 |

历史记录可按 `service`、`environment`、`team`、`status` 筛选。`started_from` 和 `started_before` 按开始时间限定范围，包含起点，排除终点。`duration_ms` 是下游 HTTP 请求耗时，响应头 `X-Request-Duration-Ms` 则包含整次管理 API 请求的处理时间。

### 日报

| 方法 | 路径 | 返回内容 |
|---|---|---|
| GET | `daily-health/` | 各服务的总次数、成功次数和健康率。 |
| GET | `daily-health/{id}/` | 一条日报明细。 |
| GET | `daily-health/summary/` | 按日期、环境和团队汇总的结果。 |

列表和汇总均支持 `stat_date`（YYYY-MM-DD）、`service`、`environment`、`team`。用 `daily-health/?service=1&stat_date=2026-11-21` 可查看某个服务当天的统计。

## 后台任务

**Token 轮换。** 每天 00:00、08:00、16:00 为有效服务生成新 Token，以 Fernet 加密落库。任务会锁定服务行并检查轮换时段，重复投递只产生一个有效的新版本。演示目标使用同一凭据表校验 Token；接入独立服务时，需要另行对接凭据同步。

**日报生成。** 每天 00:00 按上海时间统计前一天已完成的巡检，以完成时间归属日期，以巡检时记录的环境和团队归属服务。10 次检查成功 8 次，健康率为 80%；全天没有样本时，健康率为 `null`。团队健康率按总成功数除以总次数计算。

本地演示时可直接运行管理命令。将下面的服务 ID 和日期换成自己的值：

```sh
docker compose exec web python manage.py rotate_token 1
docker compose exec web python manage.py health_report 2026-11-21
```

## 测试

```sh
docker compose exec web python manage.py test
```

测试使用独立的 `test_health` 数据库，覆盖 HTTP 成败、Token 轮换、归属变更、日报补算和 MySQL 并发写入。
