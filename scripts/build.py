#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按骨架 m3u 重建直播清单：只换源不删台。

规则：
1) base.m3u 里每一条都保留（分组、顺序、台名一律不动）。
2) 逐条体检：能播的保持原样。
3) 打不开的，到上游清单里找同名台的可播线路，换地址；找不到就原样留着。
4) 不新增台、不删除台，港澳台组不会被上游内容污染。
"""
import argparse
import concurrent.futures
import os
import re
import sys
import urllib.request

UA = "okhttp/3.12.0"
DEFAULT_UPSTREAMS = [
    "https://tvm3u.zdgxy888.top/u/EgQ-amsMEhRXY3Z5KX42y2qK/m3u",
    "https://raw.githubusercontent.com/vbskycn/iptv/master/tv/iptv4.m3u",
]
TIMEOUT = 7
WORKERS = 40


def fetch(url, limit=900_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read(limit)


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


def looks_playable(data):
    if not data:
        return False
    if b"#EXTM3U" in data or b"#EXTINF" in data:
        return True
    if len(data) >= 8000 and (b"\x00\x00\x01" in data[:4096] or data[:3] == b"FLV"):
        return True
    return False


def alive(url):
    try:
        data = fetch(url, limit=120_000)
    except Exception:
        return False
    if not looks_playable(data):
        return False
    if b"#EXTM3U" in data:
        lines = [x.strip() for x in data.decode("utf-8", "ignore").splitlines()
                 if x.strip() and not x.strip().startswith("#")]
        if lines:
            sub = lines[0]
            if not sub.startswith("http"):
                sub = url.rsplit("/", 1)[0] + "/" + sub
            try:
                return looks_playable(fetch(sub, limit=120_000))
            except Exception:
                return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="base.m3u")
    ap.add_argument("--out", default="live.m3u")
    ap.add_argument("--local-upstreams", nargs="*", default=None,
                    help="本地上游清单文件（离线调试用）")
    ap.add_argument("--no-probe", action="store_true")
    a = ap.parse_args()

    raw = open(a.base, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    base_lines = raw.split(nl)
    base_entries = parse_m3u(raw)
    print("骨架条目 %d" % len(base_entries), flush=True)

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

    pool = {}
    for t in texts:
        for g, name, url in parse_m3u(t):
            if url:
                pool.setdefault(norm(name), []).append(url)
    print("上游台名 %d 个" % len(pool), flush=True)

    def probe(entry):
        return alive(entry[2])

    dead = []
    if not a.no_probe:
        total = len(base_entries)
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(probe, e): e for e in base_entries if e[2]}
            for f in concurrent.futures.as_completed(futs):
                done += 1
                if not f.result():
                    dead.append(futs[f])
                if done % 200 == 0 or done == len(futs):
                    print("体检进度 %d/%d，已判失效 %d" % (done, len(futs), len(dead)), flush=True)
    else:
        print("--no-probe：跳过体检，只做结构检查", flush=True)

    replaced = 0
    if dead:
        def fix(entry):
            key = norm(entry[1])
            for cand in pool.get(key, [])[:4]:
                if cand != entry[2] and alive(cand):
                    return cand
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for entry, newurl in zip(dead, ex.map(fix, dead)):
                if newurl:
                    entry[2] = newurl
                    replaced += 1
                    print("换源 %-14s -> %s" % (entry[1][:14], newurl[:70]), flush=True)

    urlmap = {id(e): e[2] for e in base_entries}
    idx = 0
    for i, line in enumerate(base_lines):
        if line.startswith("#EXTINF"):
            if idx < len(base_entries):
                src = base_entries[idx][2]
                if src and i + 1 < len(base_lines):
                    base_lines[i + 1] = src
            idx += 1

    open(a.out, "w", encoding="utf-8", newline="").write(nl.join(base_lines))
    print("输出 %s：%d 条，换源 %d 条" % (a.out, len(base_entries), replaced), flush=True)


if __name__ == "__main__":
    main()
