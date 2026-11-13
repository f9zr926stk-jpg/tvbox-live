# tvbox-live：让电视直播源自己更新

这套东西的作用：把你自己整理好的直播清单挂在 GitHub 上，每天自动体检一遍、把打不开的台换成同名台的新线路，电视只认一个网址，从此不用再手动推送文件。

核心原则与你现在的要求一致：**只换源、不删台、不改分组、港澳台组不被上游污染**。

## 目录结构

```
tvbox-live/
├── base.m3u                     # 骨架清单（2382 条，你的分组/顺序/台名全在这里）
├── live.m3u                     # 自动产物（脚本生成，不用手动改）
├── scripts/build.py             # 重建脚本：体检 + 同名换源
├── .github/workflows/update.yml # 每天自动跑一次
└── README.md
```

## 一次性的建立步骤

### 1. 建仓库
1. 打开 https://github.com/new
2. Repository name 填 `tvbox-live`
3. 选 **Public**（必须，私有仓库的 raw 地址要带 token，电视填不了）
4. 不要勾 Add a README / .gitignore / license（避免和本地文件冲突）
5. 点 Create repository

### 2. 上传 4 个文件
用网页上传，注意两个子目录要手动建：

1. 仓库首页 → **Add file → Upload files** → 上传 `base.m3u`、`README.md`，提交
2. **Add file → Create new file**，文件名输入 `scripts/build.py`（斜杠会自动建目录），把 `scripts/build.py` 的内容粘进去，提交
3. 再 **Create new file**，文件名输入 `.github/workflows/update.yml`，粘内容，提交

### 3. 开权限
Settings → Actions → General → 拉到 Workflow permissions → 选 **Read and write permissions** → Save

### 4. 手动跑一次
Actions 标签 → 左侧 `update-live` → 右侧 **Run workflow** → 等状态变绿（首次约 10～40 分钟，因为要体检 2382 条）

### 5. 确认产物
仓库分支下拉里会多出一个 `live` 分支，里面只有 `live.m3u`。
点开它右上角 **Raw**，地址形如：

```
https://raw.githubusercontent.com/<你的用户名>/tvbox-live/live/live.m3u
```

### 6. 电视端填地址
国内直连 `raw.githubusercontent.com` 经常不通，三个前缀任选，先用电脑验一遍再填：

```powershell
curl.exe -s -o NUL -w "%{http_code} %{size_download}`n" "https://g.blfrp.cn/https://raw.githubusercontent.com/<用户名>/tvbox-live/live/live.m3u"
```

- 首选：`https://g.blfrp.cn/https://raw.githubusercontent.com/<用户名>/tvbox-live/live/live.m3u`（你这台机器已验证可用）
- 备选一：`https://cdn.jsdelivr.net/gh/<用户名>/tvbox-live@live/live.m3u`
- 备选二：`https://ghproxy.net/https://raw.githubusercontent.com/<用户名>/tvbox-live/live/live.m3u`

要求：返回 `200` 且 size 约 70 万字节。哪个满足就用哪个，填进影视仓的 `liveApi`。

> 点播配置（`api`）不用托管：`https://8815.kstore.vip/tvbox/wmz` 本身就是线上接口，提供方更新后你自动生效。

## 日常怎么维护

- **什么都不用做**：每天北京时间 06:10 自动体检 + 换源
- **想加台 / 改分组**：直接编辑 `base.m3u` 再提交，第二天（或手动 Run 一次）产物就跟着变
- **想看某次结果**：Actions 里点开那次运行，日志会打印「骨架条目 / 上游台名 / 换源 xx 条」，以及每条换源明细

## 已知限制

1. GitHub 的定时任务在整点高峰期会延迟，可能晚 10～40 分钟才跑，不保证准点
2. 体检只验证「能拉到数据」，不验证画面内容；上游同名台如果换了台标，脚本识别不出来
3. 上游只有两个：zdgxy 全汇总 + vbskycn/iptv 的 iptv4。两个都挂了的那天，只能体检、换不了源
4. 港澳台在 base.m3u 里共 43 条，脚本只会给它们换同名线路（实际上游没有港澳台，所以基本原样保留）
5. 每天只保留一个提交（用 orphan 分支 + force push），仓库体积恒在 0.7MB 左右，不会越滚越大

## 回退

电视 `liveApi` 改回 `clan://localhost/live_100.m3u` 即可，本地文件一直都在。
