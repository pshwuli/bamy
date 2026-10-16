# 策略研究回测

数据：币安现货 1 小时 K 线（https://data.binance.vision），BTC/ETH/BNB/SOL，2020-01 至 2026-09，重采样为 4h / 1d。
成本：手续费 0.1%/边 + 滑点 0.05%/边。信号在 K 线收盘计算、下一根开盘成交（无前视）。
切分：2020–2023 样本内只用于选参数；2024-01 起样本外只用于验证。

复现：

```
mkdir -p /tmp/bt/data && cd /tmp/bt/data   # 下载 K 线 zip 到这里（见 bt.load 的文件名格式）
python run_all.py 4h        # 第一轮：旧策略基线 + 4 个候选
python search_e.py 4h e     # 趋势回调分批：样本内网格搜索，打印样本外验证
python robust.py            # 参数邻域、风险定仓、滚动前进
python regime.py            # 日线趋势 + 波动率定仓、多币轮动
python parity.py            # 上线内核（extensions/.../helpers/signals.py）与研究版一致性
python parity_tv.py
python final_live.py        # 用上线内核出最终数字（含资金规模对成交笔数的影响）
python depth.py             # 实测盘口深度（大资金滑点）
```
