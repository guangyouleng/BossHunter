# 招聘端浏览器交互后端

版本仍为 2.4.2。本次拆分覆盖招聘端驱动与页面交互；消息同步、账号/岗位核验、节流、草稿记账、Cookie 读取和 Windows 进程管理继续使用公共业务代码。

## 模块边界

| 文件 | 职责 |
| --- | --- |
| `src/bosshunter/recruiting/browser_backends/__init__.py` | 浏览器注册表、按配置加载对应后端、向启动器提供可选项 |
| `chrome.py` | Chrome 的 Patchright 驱动；使用原有 DOM 输入/点击行为 |
| `cloakbrowser.py` | CloakBrowser 的 Playwright 驱动、官方鼠标/滚轮交互、逐字回复及发送点击 |
| `common.py` | 公共定位/会话守卫，以及原有标准 DOM 动作；显式关闭拟人交互时也使用这些标准动作 |
| `reply_state.py` | CloakBrowser 逐字输入的临时状态句柄、人工编辑/焦点保护及最终发送保护 |

招聘适配器和推荐页通过 `inputs` 后端对象调用驱动、招呼、切岗、滚动及回复，不再按浏览器名称分支。注册表按需导入后端；Chrome 不导入 CloakBrowser 交互模块或其可选依赖，两种驱动对象不会混用。

`browser.recruiting_browser_kind` 选择后端，修改后重启。未知、未注册或无法导入的后端报错停止，不自动改用另一浏览器。`browser.cloakbrowser_humanize: false` 是用户明确关闭拟人交互的配置，此时 CloakBrowser 使用标准 DOM 动作；浏览器类型、登录资料及目标页不会改变。

## 回复行为

Chrome 保留原来的整段写入、发送前核验和 DOM 点击。CloakBrowser 逐字通过浏览器文本输入插入内容，字间使用官方键盘时序的短暂停顿；中文、emoji、换行不通过系统键盘或剪贴板。不会模拟错字、回删、清空或覆盖已有人工草稿，换行不按 Enter。

逐字输入临时句柄记录本次已输入内容，并检查会话 ID、岗位、消息、编辑器身份、焦点和末尾光标；每次插入前后均核验。人工修改、焦点或消息变化时停止，保留现有内容。临时输入/点击保护还覆盖核验和实际输入之间的页面变化，输入未完成不能点击发送。

输入完成后移动至唯一可用发送按钮，重新核验任务和页面，再点击一次。点击期间身份或消息变化会阻止提交。进入点击后取消不能当作确定未发送，仍保留待核实；发送成功继续以原流程核对平台页面出现对应本人消息和编辑器清空。不会因超时改用 JS 或再次点击。所有临时监听器随操作清理，不向 window/DOM 写状态。

## 依赖和打包

- 普通招聘依赖：`.[recruiting]`。
- CloakBrowser 额外依赖：`.[cloakbrowser]`；源码可一次安装 `.[recruiting,cloakbrowser]`。
- Windows 默认双浏览器构建安装 `.[recruiting,cloakbrowser,ocr,pdf]`，收集 Playwright、CloakBrowser Python 层及驱动，自检对应辅助脚本；浏览器本体不随包分发。

## 以后移除一种浏览器

先从注册表 `PROVIDERS` 删除对应条目，启动器选择同步减少；使用已移除类型的配置会明确停止，需由用户选择保留的浏览器并重启。再删除对应后端模块并更新文档/测试。Chrome 和 CloakBrowser 的专属交互模块互不导入，因此可分别移除。

如果要连安装包中的依赖一起裁剪，还需同步调整 `pyproject.toml` 可选依赖、`packaging/windows/build.ps1` 安装 extras、`BossHunter.spec` 收集项，以及 `launcher.py` 的对应离线自检。注册表不会自动卸载依赖或删除用户资料。Patchright 同时被保留的求职模块使用，不能仅因为移除招聘 Chrome 后端就盲目删除这个公共依赖。

登录资料、数据库和历史待核实记录不由后端移除操作自动删除。
