"""把一个币的月度 1m K 线 zip（币安 data.binance.vision 格式）合并成一个 npz。

用法：python zip2npz.py <zip 目录> <输出.npz>
字段：t(int32 Unix 秒，开盘时间) o h l c qv(float32)，压缩保存。按时间排序去重。
币安 2025 年起部分文件的时间戳是微秒，这里统一换成秒；有的文件带表头，自动跳过。
"""
import io
import sys
import zipfile
from pathlib import Path

import numpy as np

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
parts = []
for z in sorted(src.glob("*.zip")):
    try:
        with zipfile.ZipFile(z) as zf:
            raw = zf.read(zf.namelist()[0])
    except zipfile.BadZipFile:
        continue
    lines = raw.split(b"\n")
    if lines and not lines[0][:1].isdigit():
        lines = lines[1:]
    if not lines:
        continue
    a = np.genfromtxt(io.BytesIO(b"\n".join(lines)), delimiter=",", usecols=(0, 1, 2, 3, 4, 5, 7), dtype=np.float64)
    if a.ndim == 1:
        a = a[None, :]
    parts.append(a[~np.isnan(a).any(axis=1)])
if not parts:
    sys.exit(1)
a = np.concatenate(parts)
t = a[:, 0].astype(np.int64)
t = np.where(t > 10**14, t // 1_000_000, np.where(t > 10**11, t // 1000, t))   # 微秒/毫秒 → 秒
order = np.argsort(t, kind="stable")
t, a = t[order], a[order]
keep = np.concatenate([[True], np.diff(t) > 0])
t, a = t[keep], a[keep]
tmp = dst.with_name(dst.name + ".tmp.npz")   # 先写临时文件再改名：中途被杀不会留下半个文件
np.savez_compressed(tmp, t=t.astype(np.int32), o=a[:, 1].astype(np.float32), h=a[:, 2].astype(np.float32),
                    l=a[:, 3].astype(np.float32), c=a[:, 4].astype(np.float32), qv=a[:, 6].astype(np.float32))
tmp.rename(dst)
