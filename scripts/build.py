#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按骨架 m3u 重建直播清单：只换源不删台。

规则：
1) base.m3u 里每一条都保留（分组、顺序、台名一律不动）。
2) 深度体检：播放列表 -> 变体 -> 拉一个真实分片。能出分片才算活，
   “返回 200 但正文是报错文字”的一律判死（这是央视 ysp 转播失效的典型表现）。
3) 打不开的，按同名台找可播线路：优先用清单内其它分组的同名源，再到上游清单找。
4) 不新增台、不删除台。
"""
import argparse
import concurrent.futures
import os
import re
import sys
import time
import urllib.request
from urllib.parse import urljoin

UA = "okhttp/3.12.0"
# 换源候选清单：先这两家（实测可播最多），再原来的上游
DEFAULT_UPSTREAMS = [
    "https://live.hacks.tools/tv/iptv4.m3u",
    "https://raw.githubusercontent.com/kilvn/iptv/master/iptv.m3u",
    "https://tvm3u.zdgxy888.top/u/EgQ-amsMEhRXY3Z5KX42y2qK/m3u",
    "https://raw.githubusercontent.com/vbskycn/iptv/master/tv/iptv4.m3u",
]
TIMEOUT = 9
WORKERS = 40
TRIES = 2
ERR_HINTS = ("链接请求失败", "暂停向官方请求", "暫停向官方請求", "不可用", "請稍後")

HIDE_GROUPS = [
    "体育-昨天",
    "体育-明天",
    "体育赛事直播",
    "体育-今天",
    "中国景观直播",
    "教育",
    "文旅",
]

# 央视在咪咕通道的编号（实测能出分片，优先于 ysp 转播）
MGU_PREFIX = "https://tvm3u.zdgxy888.top/u/EgQ-amsMEhRXY3Z5KX42y2qK/"
CCTV_MAP = {
    "CCTV1综合": "608807420",
    "CCTV2财经": "631780532",
    "CCTV3综艺": "624878271",
    "CCTV4中文国际": "631780421",
    "CCTV4美洲": "608807416",
    "CCTV5体育": "641886683",
    "CCTV5+体育赛事": "641886773",
    "CCTV6电影": "624878396",
    "CCTV7国防军事": "673168121",
    "CCTV8电视剧": "624878356",
    "CCTV9纪录": "673168140",
    "CCTV10科教": "624878405",
    "CCTV11戏曲": "667987558",
    "CCTV12社会与法": "673168185",
    "CCTV13新闻": "608807423",
    "CCTV14少儿": "624878440",
    "CCTV15音乐": "673168223",
    "CCTV17农业农村": "673168256",
}


def fetch(url, limit=200_000, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit)


def is_media(data):
    return bool(data) and len(data) >= 1500 and (
        data[:1] == b"\x47" or b"ftyp" in data[:64] or data[:3] == b"FLV")


def seg_ok(url):
    try:
        return len(fetch(url, 30_000)) >= 2000
    except Exception:
        return False


def resolve(base, seg):
    """相对地址解析：既试标准拼接，也试“把 base 当目录”的拼接（咪咕/部分代理要后者）。"""
    if seg.startswith("http"):
        return [seg]
    out = [urljoin(base, seg)]
    if not base.endswith("/"):
        out.append(urljoin(base + "/", seg))
    return out


def probe(url, timeout=TIMEOUT):
    """返回 (ok, 原因)"""
    try:
        data = fetch(url, timeout=timeout)
    except Exception as e:
        return False, "拉取失败:%s" % type(e).__name__
    if not data:
        return False, "空响应"
    head = data[:400].decode("utf-8", "ignore")
    if b"#EXTM3U" not in data and any(h in head for h in ERR_HINTS):
        return False, "报错文本"
    if is_media(data):
        return True, "直媒体"
    if b"#EXTM3U" not in data:
        return False, "非播放列表(%dB)" % len(data)

    text = data.decode("utf-8", "ignore")
    lines = [x.strip() for x in text.splitlines()
             if x.strip() and not x.strip().startswith("#")]

    if "#EXT-X-STREAM-INF" in text:
        for i, sub in enumerate(lines[:3]):
            for variant in resolve(url, sub):
                try:
                    subtext = fetch(variant, 60_000).decode("utf-8", "ignore")
                except Exception:
                    continue
                segs = [x.strip() for x in subtext.splitlines()
                        if x.strip() and not x.strip().startswith("#")]
                if not segs:
                    continue
                if any(seg_ok(s) for s in resolve(variant, segs[0])):
                    return True, "变体%d" % i
        return False, "变体分片全失败"

    if not lines:
        return False, "无分片"
    if any(seg_ok(s) for s in resolve(url, lines[0])):
        return True, "分片ok"
    return False, "分片失败"


def alive(url, tries=TRIES):
    for i in range(tries):
        ok, why = probe(url)
        if ok:
            return True
        if i + 1 < tries:
            time.sleep(1.0)
    return False


def parse_m3u(text):
    out = []
    cur = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#EXTINF"):
            g = re.search(r'group-title="([^"]*)"', s)
            out.append([g.group(1) if g else "", s.split(",")[-1].strip(), None])
            cur = out[-1]
        elif s.startswith("#"):
            continue
        elif cur is not None and cur[2] is None:
            cur[2] = s
            cur = None
    return out


def norm(name):
    n = name.strip()
    for t in ("高清", "HD", "1080P", "1080p", "720P", "4K", "频道", "（", "）", "(", ")"):
        n = n.replace(t, "")
    n = re.sub(r"\s+", "", n)
    return n.upper()


def clean_url(u):
    u = u.split(";")[0].split("#")[0].strip()
    return u


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="base.m3u")
    ap.add_argument("--out", default="live.m3u")
    ap.add_argument("--local-upstreams", nargs="*", default=None)
    ap.add_argument("--no-probe", action="store_true")
    ap.add_argument("--only", default=None, help="只体检指定分组（逗号分隔），其余直接沿用")
    a = ap.parse_args()

    raw = open(a.base, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    base_lines = raw.split(nl)
    base_entries = parse_m3u(raw)
    print("骨架条目 %d" % len(base_entries), flush=True)

    # 候选池：先用清单内其它分组的同名源，再用上游清单的同名源
    pool = {}
    for e in base_entries:
        if e[2]:
            pool.setdefault(norm(e[1]), []).append(clean_url(e[2]))

    texts = []
    if a.local_upstreams:
        for p in a.local_upstreams:
            texts.append(open(p, encoding="utf-8", errors="ignore").read())
    else:
        for u in DEFAULT_UPSTREAMS:
            try:
                texts.append(fetch(u).decode("utf-8", "ignore"))
                print("上游 %s 取回 %d 字符" % (u[:50], len(texts[-1])), flush=True)
            except Exception as e:
                print("上游失败 %s %s" % (u[:50], e), flush=True)

    for t in texts:
        for g, name, url in parse_m3u(t):
            if url:
                pool.setdefault(norm(name), []).append(clean_url(url))
    print("候选台名 %d 个" % len(pool), flush=True)

    only = set(a.only.split(",")) if a.only else None
    targets = [e for e in base_entries
               if e[2] and e[0] not in HIDE_GROUPS and (only is None or e[0] in only)]
    skipped = [e for e in base_entries
               if e[2] and e[0] not in HIDE_GROUPS and only is not None and e[0] not in only]
    print("本次体检 %d 条（沿用 %d 条）" % (len(targets), len(skipped)), flush=True)

    dead = []
    if not a.no_probe:
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(alive, e[2]): e for e in targets}
            for f in concurrent.futures.as_completed(futs):
                done += 1
                if not f.result():
                    dead.append(futs[f])
                if done % 200 == 0 or done == len(futs):
                    print("体检进度 %d/%d，失效 %d" % (done, len(futs), len(dead)), flush=True)

    # 已知不稳的转播入口：即使这次体检偶然通过也要换到更稳的线路
    FLAKY = ("/relay/ysp-",)
    flaky = [e for e in targets
             if e[2] and any(f in e[2] for f in FLAKY) and e not in dead]
    tofix = dead + flaky

    replaced = 0

    def fix(entry):
        key = norm(entry[1])
        tried = 0
        # 央视：咪咕编号线路优先（实测稳定，ysp 转播常被央视 CDN 403）
        mapped = CCTV_MAP.get(entry[1].strip())
        if mapped:
            mgu = MGU_PREFIX + mapped
            if mgu != entry[2]:
                if alive(mgu, tries=2):
                    return mgu
                if any(f in entry[2] for f in FLAKY):
                    return mgu  # 两边都不稳时，仍选用户实测能放的咪咕通道
        for cand in pool.get(key, []):
            cand = clean_url(cand)
            if not cand.startswith("http") or " " in cand or cand == entry[2]:
                continue
            if cand.split("://", 1)[-1].startswith("["):
                continue  # IPv6 直连，电视用不了
            if any(f in cand for f in FLAKY):
                continue
            tried += 1
            if tried > 8:
                break
            if alive(cand, tries=2):
                return cand
        return None

    if tofix:
        # 换源阶段请求少一些，避免把同一台站打到限流
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for entry, newurl in zip(tofix, ex.map(fix, tofix)):
                if newurl:
                    entry[2] = newurl
                    replaced += 1
                    print("换源 %-16s -> %s" % (entry[1][:16], newurl[:80]), flush=True)
    print("失效 %d 条，成功换源 %d 条，仍无可用源 %d 条"
          % (len(dead), replaced, len(dead) - min(replaced, len(dead))), flush=True)

    out = []
    idx = 0
    i = 0
    n_keep = 0
    while i < len(base_lines):
        line = base_lines[i]
        if not line.startswith("#EXTINF"):
            out.append(line)
            i += 1
            continue
        entry = base_entries[idx] if idx < len(base_entries) else None
        idx += 1
        keep = bool(entry) and entry[0] not in HIDE_GROUPS
        if keep:
            out.append(line)
            n_keep += 1
        has_url = (i + 1 < len(base_lines)) and not base_lines[i + 1].startswith("#")
        if has_url:
            url = entry[2] if entry else base_lines[i + 1]
            if url and ";" in url:
                url = url.split(";")[0].strip()
            if url and "#http" in url:
                url = url.split("#")[0].strip()
            if keep:
                out.append(url or base_lines[i + 1])
            i += 2
        else:
            i += 1

    groups = {}
    for e in base_entries:
        if e[0] in HIDE_GROUPS:
            continue
        groups[e[0]] = groups.get(e[0], 0) + 1
    open(a.out, "w", encoding="utf-8", newline="").write(nl.join(out))
    print("输出 %s：%d 条（骨架 %d，隐藏 %d），换源 %d 条"
          % (a.out, n_keep, len(base_entries), len(base_entries) - n_keep, replaced), flush=True)
    print("隐藏分组: " + ("、".join(HIDE_GROUPS) if HIDE_GROUPS else "无"), flush=True)
    print("分组: " + " / ".join("%s=%d" % (k, v) for k, v in groups.items()), flush=True)


if __name__ == "__main__":
    main()
