# Mac 本地测试 CloakBrowser

适用于本项目当前源码、Conda 环境 `bosshunter`、桌面的 Apple ARM64 浏览器 `/Users/mac/Desktop/Chromium.app`。Windows 图形启动器不在 Mac 上运行。CloakBrowser Manager 是单独的浏览器资料管理工具，本项目不依赖它。

先在各自终端用 `Ctrl+C` 停止旧的 BossHunter Web 和 recruiting-worker，避免继续使用旧配置。以下配置使用独立资料目录 `~/boss-cloakbrowser`、浏览器端口 9333、Runtime 端口 3457；Chrome 原资料目录不会迁移或删除。首次需在这次打开的独立浏览器内人工登录。

## 1. 安装新增依赖（一次）

```bash
cd /Users/mac/Desktop/bossAssistant/BossHunter
conda activate bosshunter
```

如果前端 `src/bosshunter/web/frontend/dist/index.html` 已存在且没有前端变更，可以跳过下面两条；全新下载源码时必须先构建前端，再安装 Python 包：

```bash
npm --prefix src/bosshunter/web/frontend ci
npm --prefix src/bosshunter/web/frontend run build
```

```bash
python -m pip install -e '.[recruiting,cloakbrowser]'
```

这一步安装官方 Python 交互层及 Playwright 驱动，不下载另一份浏览器。若系统弹出 Keychain 凭证访问提示，应先核实请求来自当前 Python/Chromium；实际 BOSS Cookie 解密需要登录后验收。

## 2. 配置独立资料和固定种子（一次）

在同一个终端运行。保留现有模型和业务配置；只切换浏览器相关字段。切换浏览器会清除旧的标签页绑定 ID，之后按工作台提示核实目标页。

```bash
python - <<'PY'
from pathlib import Path
import secrets
from bosshunter.config import load_config, save_config

path = Path('config.yaml').resolve()
config = load_config(path)
browser = config.setdefault('browser', {})
profiles = browser.setdefault('desktop_browser_profiles', {})
old_kind = browser.get('recruiting_browser_kind', 'chrome')
if browser.get('recruiting_user_data_dir'):
    profiles[old_kind] = browser['recruiting_user_data_dir']
profile = Path.home() / 'boss-cloakbrowser'
profile.mkdir(parents=True, exist_ok=True)
seed_file = profile / '.cloakbrowser-seed'
if not seed_file.exists():
    with seed_file.open('x', encoding='utf-8') as stream:
        stream.write(str(secrets.randbelow(90000) + 10000))
seed = seed_file.read_text(encoding='utf-8').strip()
if not seed.isdigit() or not 10000 <= int(seed) <= 99999:
    raise SystemExit('种子文件异常，停止；不要删除或自动更换已有身份')
browser.update(recruiting_browser_kind='cloakbrowser', cloakbrowser_humanize=True,
               recruiting_user_data_dir=str(profile), chrome_ports=[9333],
               proxy_port=3457, auto_start_proxy=True)
profiles['cloakbrowser'] = str(profile)
browser.pop('recruiting_target_id', None)
save_config(config, path)
print('CloakBrowser 本地配置已保存，模型凭据未输出。')
PY
```

以后常规重启不需要重新运行配置步骤，直接从第 3 步开始。不要把两种浏览器设置成同一个资料目录。

## 3. 启动浏览器

终端一：

```bash
cd /Users/mac/Desktop/bossAssistant/BossHunter
BOSSHUNTER_CLOAK_SEED=$(cat "$HOME/boss-cloakbrowser/.cloakbrowser-seed")
"/Users/mac/Desktop/Chromium.app/Contents/MacOS/Chromium" \
  --remote-debugging-port=9333 \
  --user-data-dir="$HOME/boss-cloakbrowser" \
  --fingerprint-platform=macos \
  --fingerprint="$BOSSHUNTER_CLOAK_SEED" \
  https://www.zhipin.com/
```

人工登录后进入招聘端沟通页；测试主动招呼时人工打开推荐牛人页。启动命令本身不会点击按钮或开始任务。若 macOS 阻止打开应用，请在系统安全设置中核实来源后处理，项目不会自动修改系统安全设置。

## 4. 启动 Web 和监测进程

终端二：

```bash
cd /Users/mac/Desktop/bossAssistant/BossHunter
conda activate bosshunter
export BOSSHUNTER_CHROME_PORT_STRICT=true
python -m bosshunter.main --config config.yaml web --no-open
```

打开 <http://127.0.0.1:8686/recruiting>。Browser Runtime 按配置自动启动，不需要另开 Node 命令。严格端口模式用于避免发现并连接其他 Chrome。

若要测试 WS/HTTP 消息监测和自动回复，另开终端三：

```bash
cd /Users/mac/Desktop/bossAssistant/BossHunter
conda activate bosshunter
export BOSSHUNTER_CHROME_PORT_STRICT=true
python -m bosshunter.main --config config.yaml recruiting-worker
```

仅测试主动打招呼不需要 worker；任务由工作台按钮启动。worker 按数据库内已有监测/外发开关执行，重启不会自动清空这些开关或待处理任务。测试前在工作台核对开关、已绑定会话及岗位范围。

## 验证范围

桌面这份 145 系列 ARM64 浏览器已使用全新临时资料、CDP 连接和离线模拟页面完成 13 项交互测试，覆盖 iframe 内岗位选择/招呼、联系人滚轮、逐字回复/发送，以及人工改稿、焦点/消息变化、取消和输入未完成禁止发送。测试页面由本地提供，未登录 BOSS、调用模型或向候选人发送消息。真实登录 Cookie 解密、BOSS 页面适配和 Windows 安装包仍需分别验收。

旧版 CloakBrowser 的 `page.set_content()` 有兼容性问题，离线测试改用本地 `data:` 页面创建测试内容；项目生产代码不使用该 API。普通回复在 CloakBrowser 模式逐字输入并拟人点击发送；原有消息核验、简历动作和节流保持不变。Chrome 的输入和发送保持原实现。
