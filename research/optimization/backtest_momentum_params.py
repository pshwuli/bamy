#!/usr/bin/env python3
"""全市场动量扫描参数优化回测脚本（优化版）

使用方法：
    前台运行: python3 research/optimization/backtest_momentum_params.py
    后台运行: nohup python3 research/optimization/backtest_momentum_params.py > /tmp/backtest.log 2>&1 &
    查看进度: tail -f /tmp/backtest_progress.txt
    查看结果: cat /tmp/momentum_params_result.json

输出：
    - 终端/日志输出参数排名
    - /tmp/momentum_params_result.json 详细结果
    - /tmp/backtest_progress.txt 实时进度
"""
import numpy as np
from pathlib import Path
from collections import defaultdict
import json
import time
import sys

# 数据目录
DATA_DIR = Path("/root/bamy-data/um_1m")
PROGRESS_FILE = Path("/tmp/backtest_progress.txt")

# 精选参数组合（减少到80组，覆盖关键场景）
def generate_param_sets():
    """生成精选参数组合，控制在合理数量"""
    param_sets = []

    # 核心窗口 + 关键涨幅阈值 + 精选距高点阈值
    core_configs = [
        # (窗口mins, 窗口名, 涨幅列表, 距高点列表, 止损, 启动, 回撤)
        (240, "4h", [12, 15, 18, 20, 22, 25, 30], [-3, -5, -6, -8, -10], 5, 4, 2.5),
        (360, "6h", [15, 18, 20, 25, 30], [-3, -5, -8], 5, 4, 3),
        (720, "12h", [20, 25, 30], [-5, -8], 6, 5, 3),
        (1440, "24h", [25, 30, 35], [-5], 6, 5, 3),
        (120, "2h", [10, 15, 20], [-5, -8], 4, 3, 2),
    ]

    for window_mins, window_name, changes, dists, stop, arm, trail in core_configs:
        for change in changes:
            for dist in dists:
                name = f"{window_name}_涨{change}%_距{dist}%"
                param_sets.append((window_mins, change, dist, stop, arm, trail, name))

    # 添加当前默认参数作为基准
    param_sets.append((240, 20, -5, 4, 4, 3, "4h_涨20%_距-5%_【当前默认】"))

    # 添加一些极端测试
    extreme_tests = [
        (240, 10, -12, 5, 3, 2, "4h_涨10%_距-12%_极激进"),
        (240, 40, -3, 6, 6, 4, "4h_涨40%_距-3%_极保守"),
        (360, 12, -10, 4, 2, 1.5, "6h_涨12%_距-10%_激进"),
        (720, 35, -5, 8, 6, 5, "12h_涨35%_距-5%_保守"),
    ]
    param_sets.extend(extreme_tests)

    return param_sets

PARAM_SETS = generate_param_sets()


def write_progress(message):
    """写进度到文件，支持后台运行时查看"""
    with open(PROGRESS_FILE, 'a') as f:
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        f.write(f"[{timestamp}] {message}\n")
    print(message)
    sys.stdout.flush()


def simulate_trade(prices_after_entry, entry_price, stop_pct, arm_pct, trail_pct):
    """模拟单笔交易的完整生命周期

    Args:
        prices_after_entry: 开仓后的价格序列（最多240根K线，即4小时）
        entry_price: 开仓价
        stop_pct: 止损百分比（正数，如4表示-4%）
        arm_pct: 止盈启动百分比（正数，如4表示+4%）
        trail_pct: 回撤止盈百分比（正数，如3表示回撤3%）

    Returns:
        (exit_price, exit_reason, bars_held)
    """
    stop_price = entry_price * (1 - stop_pct / 100)
    arm_price = entry_price * (1 + arm_pct / 100)

    best_price = entry_price
    armed = False

    for bar_idx, price in enumerate(prices_after_entry):
        # 止损
        if price <= stop_price:
            return price, 'stop_loss', bar_idx + 1

        # 更新最高价
        if price > best_price:
            best_price = price

        # 检查是否启动止盈
        if not armed and price >= arm_price:
            armed = True

        # 止盈跟踪
        if armed:
            trail_trigger = best_price * (1 - trail_pct / 100)
            if price <= trail_trigger:
                return price, 'trailing_stop', bar_idx + 1

    # 到期
    return prices_after_entry[-1], 'max_hold', len(prices_after_entry)


def backtest_single_coin(data, window_mins, min_chg, max_dist, stop, arm, trail):
    """对单个币回测一组参数"""
    o = data['o']
    h = data['h']
    l = data['l']
    c = data['c']

    if len(c) < window_mins + 240:  # 需要window + 最多持仓240根K线
        return None

    trades = []

    # 扫描所有可能的开仓点
    for i in range(window_mins, len(c) - 240):
        w_open = o[i - window_mins]
        w_high = h[i - window_mins:i].max()
        w_close = c[i]

        chg = (w_close / w_open - 1) * 100
        dist = (w_close / w_high - 1) * 100

        # 触发开仓条件
        if chg >= min_chg and dist >= max_dist:
            entry_price = w_close

            # 获取后续最多240根K线的价格（使用收盘价模拟）
            prices_after = c[i+1:i+241]

            if len(prices_after) == 0:
                continue

            # 模拟交易
            exit_price, exit_reason, bars_held = simulate_trade(
                prices_after, entry_price, stop, arm, trail
            )

            pnl_pct = (exit_price / entry_price - 1) * 100

            trades.append({
                'entry_idx': i,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'pnl_pct': pnl_pct,
                'reason': exit_reason,
                'bars_held': bars_held,
                'triggered_change': chg,
                'triggered_dist': dist,
            })

    if not trades:
        return None

    pnls = [t['pnl_pct'] for t in trades]

    return {
        'trade_count': len(trades),
        'win_rate': sum(1 for p in pnls if p > 0) / len(pnls) * 100,
        'avg_pnl': np.mean(pnls),
        'median_pnl': np.median(pnls),
        'max_pnl': max(pnls),
        'min_pnl': min(pnls),
        'std_pnl': np.std(pnls),
        'avg_bars_held': np.mean([t['bars_held'] for t in trades]),
        'stop_loss_count': sum(1 for t in trades if t['reason'] == 'stop_loss'),
        'trailing_stop_count': sum(1 for t in trades if t['reason'] == 'trailing_stop'),
        'max_hold_count': sum(1 for t in trades if t['reason'] == 'max_hold'),
    }


def main():
    npz_files = sorted(list(DATA_DIR.glob("*.npz")))

    # 清空进度文件
    if PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()

    write_progress(f"="*80)
    write_progress(f"全市场动量扫描参数优化回测（精选版）")
    write_progress(f"="*80)
    write_progress(f"数据目录: {DATA_DIR}")
    write_progress(f"币种数量: {len(npz_files)}")
    write_progress(f"参数组合: {len(PARAM_SETS)} 组（精选）")
    write_progress(f"预计总测试: {len(npz_files) * len(PARAM_SETS):,} 次")

    # 预估时间
    est_time_min = len(npz_files) * len(PARAM_SETS) * 0.05 / 60
    est_time_max = len(npz_files) * len(PARAM_SETS) * 0.15 / 60
    write_progress(f"预计耗时: {est_time_min:.0f}-{est_time_max:.0f} 分钟")
    write_progress(f"开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    write_progress("")

    start_time = time.time()

    # 汇总结果
    results = defaultdict(list)
    total_tests = 0
    failed_tests = 0

    for idx, npz_file in enumerate(npz_files):
        try:
            symbol = npz_file.stem

            # 先输出开始处理的币
            if (idx + 1) % 1 == 0:  # 每个币都输出
                elapsed = time.time() - start_time
                progress_pct = (idx + 1) / len(npz_files) * 100
                write_progress(f"[{idx + 1}/{len(npz_files)} {progress_pct:.1f}%] 正在处理: {symbol}")

            data = np.load(npz_file, allow_pickle=False)

            for window_mins, min_chg, max_dist, stop, arm, trail, name in PARAM_SETS:
                total_tests += 1

                try:
                    coin_result = backtest_single_coin(
                        data, window_mins, min_chg, max_dist, stop, arm, trail
                    )

                    if coin_result:
                        coin_result['symbol'] = symbol
                        results[name].append(coin_result)
                except Exception as e:
                    failed_tests += 1

            # 完成后输出总结
            elapsed = time.time() - start_time
            progress_pct = (idx + 1) / len(npz_files) * 100
            eta = (elapsed / (idx + 1)) * (len(npz_files) - idx - 1)

            write_progress(
                f"[{idx + 1}/{len(npz_files)} {progress_pct:.1f}%] {symbol:12s} | "
                f"耗时 {elapsed/60:.0f}分 | 剩余 {eta/60:.0f}分 | 失败 {failed_tests}"
            )

        except Exception as e:
            write_progress(f"  处理 {npz_file.name} 失败: {e}")
            failed_tests += 1

    write_progress("")
    write_progress(f"="*80)
    write_progress(f"回测完成！总耗时: {(time.time() - start_time)/60:.1f} 分钟")
    write_progress(f"总测试次数: {total_tests:,}, 失败: {failed_tests}")
    write_progress(f"="*80)
    write_progress("")

    # 汇总并排名
    write_progress("="*120)
    write_progress("回测结果汇总（按平均盈亏排序）")
    write_progress("="*120)

    summary = []

    for name in sorted(results.keys()):
        coin_results = results[name]

        if len(coin_results) < 20:  # 至少20个币有信号才算有效
            continue

        avg_trade_count = np.mean([r['trade_count'] for r in coin_results])
        avg_win_rate = np.mean([r['win_rate'] for r in coin_results])
        avg_pnl = np.mean([r['avg_pnl'] for r in coin_results])
        median_pnl = np.median([r['avg_pnl'] for r in coin_results])
        avg_bars = np.mean([r['avg_bars_held'] for r in coin_results])
        avg_stop_loss_rate = np.mean([r['stop_loss_count'] / r['trade_count'] for r in coin_results]) * 100
        avg_trailing_rate = np.mean([r['trailing_stop_count'] / r['trade_count'] for r in coin_results]) * 100

        # 计算夏普比率的近似值
        all_pnls = [r['avg_pnl'] for r in coin_results]
        sharpe = np.mean(all_pnls) / np.std(all_pnls) if np.std(all_pnls) > 0 else 0

        summary.append({
            'name': name,
            'coin_count': len(coin_results),
            'avg_trades_per_coin': avg_trade_count,
            'avg_win_rate': avg_win_rate,
            'avg_pnl': avg_pnl,
            'median_pnl': median_pnl,
            'avg_bars_held': avg_bars,
            'avg_stop_loss_rate': avg_stop_loss_rate,
            'avg_trailing_rate': avg_trailing_rate,
            'sharpe': sharpe,
        })

    # 多种排序方式
    by_pnl = sorted(summary, key=lambda x: x['avg_pnl'], reverse=True)
    by_winrate = sorted(summary, key=lambda x: x['avg_win_rate'], reverse=True)
    by_sharpe = sorted(summary, key=lambda x: x['sharpe'], reverse=True)

    # 输出 TOP 20 按盈亏排序
    write_progress(f"\n【按平均盈亏排序 - TOP 20】\n")
    write_progress(f"{'排名':<4} {'参数组合':<40} {'币数':<6} {'信号':<8} {'胜率%':<8} {'盈亏%':<8} {'持仓':<6} {'夏普':<6}")
    write_progress("-"*110)

    for rank, item in enumerate(by_pnl[:20], 1):
        marker = " ← 当前" if "当前默认" in item['name'] else ""
        write_progress(
            f"{rank:<4} {item['name']:<40} {item['coin_count']:<6} {item['avg_trades_per_coin']:<8.0f} "
            f"{item['avg_win_rate']:<8.1f} {item['avg_pnl']:<8.2f} {item['avg_bars_held']:<6.0f} {item['sharpe']:<6.2f}{marker}"
        )

    # 输出 TOP 10 按胜率排序
    write_progress(f"\n{'='*110}")
    write_progress(f"\n【按胜率排序 - TOP 10】\n")
    write_progress(f"{'排名':<4} {'参数组合':<40} {'币数':<6} {'胜率%':<8} {'盈亏%':<8} {'夏普':<6}")
    write_progress("-"*90)

    for rank, item in enumerate(by_winrate[:10], 1):
        write_progress(
            f"{rank:<4} {item['name']:<40} {item['coin_count']:<6} "
            f"{item['avg_win_rate']:<8.1f} {item['avg_pnl']:<8.2f} {item['sharpe']:<6.2f}"
        )

    # 输出 TOP 10 按夏普比率排序
    write_progress(f"\n{'='*110}")
    write_progress(f"\n【按夏普比率排序 - TOP 10】\n")
    write_progress(f"{'排名':<4} {'参数组合':<40} {'币数':<6} {'夏普':<6} {'盈亏%':<8} {'胜率%':<8}")
    write_progress("-"*90)

    for rank, item in enumerate(by_sharpe[:10], 1):
        write_progress(
            f"{rank:<4} {item['name']:<40} {item['coin_count']:<6} "
            f"{item['sharpe']:<6.2f} {item['avg_pnl']:<8.2f} {item['avg_win_rate']:<8.1f}"
        )

    write_progress(f"\n{'='*110}")
    write_progress(f"\n✓ TOP 5 综合推荐参数组合:\n")

    for rank, item in enumerate(by_pnl[:5], 1):
        write_progress(f"{rank}. {item['name']}")
        write_progress(f"   - 覆盖 {item['coin_count']} 个币，平均每币 {item['avg_trades_per_coin']:.0f} 个信号")
        write_progress(f"   - 胜率 {item['avg_win_rate']:.1f}%，平均每笔 {item['avg_pnl']:+.2f}%")
        write_progress(f"   - 平均持仓 {item['avg_bars_held']:.0f} 根K线（约 {item['avg_bars_held']/60:.1f} 小时）")
        write_progress(f"   - 止损率 {item['avg_stop_loss_rate']:.1f}%，移动止盈率 {item['avg_trailing_rate']:.1f}%")
        write_progress(f"   - 夏普比率 {item['sharpe']:.2f}")

        if rank == 1:
            current_idx = next((i for i, x in enumerate(by_pnl) if "当前默认" in x['name']), None)
            if current_idx is not None:
                current = by_pnl[current_idx]
                improvement = item['avg_pnl'] - current['avg_pnl']
                write_progress(f"   - vs 当前默认: {improvement:+.2f}% (当前排名第{current_idx+1})")
        write_progress("")

    # 保存详细结果
    output = {
        'summary_by_pnl': by_pnl,
        'summary_by_winrate': by_winrate,
        'summary_by_sharpe': by_sharpe,
        'param_count': len(PARAM_SETS),
        'coin_count': len(npz_files),
        'total_tests': total_tests,
        'failed_tests': failed_tests,
        'run_time_minutes': (time.time() - start_time) / 60,
    }

    output_file = Path("/tmp/momentum_params_result.json")
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2, default=float)

    write_progress(f"✓ 详细结果已保存到: {output_file}")
    write_progress(f"✓ 进度文件: {PROGRESS_FILE}")
    write_progress(f"✓ 总耗时: {(time.time() - start_time)/60:.1f} 分钟")
    write_progress(f"✓ 完成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    main()
