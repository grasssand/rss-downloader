## RSS Downloader
---

一个 RSS 订阅下载工具，配合 Aria2 / qBittorrent 自动追番。


### 1. 安装 🚀

需要 Python 环境。
```bash
# pipx
pipx install rss-downloader
# or uv
uv tool install rss-downloader
# or pip (不推荐)
pip install --user rss-downloader
```

更新
```bash
# pipx
pipx upgrade rss-downloader
# uv
uv tool upgrade rss-downloader
# pip
pip install --user --upgrade rss-downloader
```

### 2. 配置 ⚙️

首次运行时，程序会自动在标准配置目录下创建一个 `config.yaml` 文件（一般在 `~/.config/rss-downloader/`）。您也可以手动创建它。

```yaml
# config.yaml
log:
  level: INFO # 日志级别 (DEBUG, INFO, WARNING, ERROR)

web:
  enabled: true # 是否启用 Web 界面
  host: 127.0.0.1
  port: 8000
  interval_hours: 6 # RSS 自动更新间隔（小时）

# Aria2 配置 (如果不用可以留空或删除)
aria2:
  rpc: http://localhost:6800/jsonrpc
  secret: your_secret
  dir: null # aira2 下载位置

# qBittorrent 配置 (如果不用可以留空或删除)
qbittorrent:
  host: http://localhost:8080
  username: admin
  password: password

# RSS 源配置列表
feeds:
  - name: Mikan
    url: https://mikanime.tv/RSS/MyBangumi?token=
    include:    # 匹配规则
      - chs
      - 简体
    exclude:     # 排除规则
      - 720p
      - \d{2,}\s*[-|~]\s*\d{2,}
    downloader: aria2 # or qbittorrent

  - name: Nyaa
    url: https://nyaa.si/?page=rss&q=
```


### 3. 运行 🎉

配置 `web.enabled` 为 `true`，或者指定运行参数 `-w`，启动 Web 界面和后台服务，浏览器访问 `http://127.0.0.1:8000`。

```bash
> rss-downloader
options:
  -h, --help  显示帮助信息
  -w, --web   启动 Web 界面
```

![下载记录](./assets/下载记录.png)
![配置管理](./assets/配置管理.png)
