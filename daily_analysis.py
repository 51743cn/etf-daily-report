"""
每日 ETF 分析脚本 — 159140 & 513050
收盘后自动运行，生成分析图表+文字解读，发送 Gmail 报告
"""

import os
import smtplib
import datetime
import io
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib import rcParams
rcParams['font.family'] = 'DejaVu Sans'

# ─── 配置区 ───────────────────────────────────────────────────
GMAIL_SENDER   = os.environ.get("GMAIL_SENDER", "")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")
GMAIL_RECEIVER = os.environ.get("GMAIL_RECEIVER", "")

TICKERS = {
    '159140.SZ': '159140 科创AI ETF',
    '513050.SS': '513050 中概互联ETF',
}
# ─────────────────────────────────────────────────────────────


def fetch_data(ticker, period='6mo'):
    hist = yf.Ticker(ticker).history(period=period)
    hist.index = hist.index.tz_localize(None)
    return hist


def compute_ta(hist):
    df = hist[['Close', 'Volume']].copy()
    df['MA5']  = df['Close'].rolling(5).mean()
    df['MA10'] = df['Close'].rolling(10).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean()
    delta = df['Close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = 100 - 100 / (1 + rs)
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD']   = ema12 - ema26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['Hist']   = df['MACD'] - df['Signal']
    df['BB_mid'] = df['Close'].rolling(20).mean()
    std20 = df['Close'].rolling(20).std()
    df['BB_up']  = df['BB_mid'] + 2 * std20
    df['BB_dn']  = df['BB_mid'] - 2 * std20
    df['BB_pct'] = (df['Close'] - df['BB_dn']) / (df['BB_up'] - df['BB_dn'])
    df['R1'] = df['Close'].rolling(20).max()
    df['S1'] = df['Close'].rolling(20).min()
    # 成交量比（今日量 / 10日均量）
    df['Vol_ratio'] = df['Volume'] / df['Volume'].rolling(10).mean()
    return df


def signal_summary(ta_df, ticker, label):
    last = ta_df.iloc[-1]
    prev = ta_df.iloc[-2]
    rsi = round(last['RSI'], 1)

    if last['MACD'] > last['Signal'] and prev['MACD'] <= prev['Signal']:
        macd_status = '🟢 MACD金叉（买入信号）'
        macd_key = 'golden'
    elif last['MACD'] < last['Signal'] and prev['MACD'] >= prev['Signal']:
        macd_status = '🔴 MACD死叉（卖出信号）'
        macd_key = 'death'
    elif last['MACD'] > last['Signal']:
        macd_status = '🔵 MACD多头排列'
        macd_key = 'bull'
    else:
        macd_status = '⚪ MACD空头排列'
        macd_key = 'bear'

    rsi_status = '⚠️ 超买区间' if rsi > 70 else ('🟢 超卖区间' if rsi < 30 else '🔵 中性区间')
    rsi_key = 'overbought' if rsi > 70 else ('oversold' if rsi < 30 else 'neutral')
    bb_pct = round(last['BB_pct'] * 100, 1)

    if last['MA5'] > last['MA20'] > last['MA60']:
        ma_trend = '多头排列📈'
        ma_key = 'bull'
    elif last['MA5'] < last['MA20'] < last['MA60']:
        ma_trend = '空头排列📉'
        ma_key = 'bear'
    else:
        ma_trend = '混合震荡'
        ma_key = 'mixed'

    close = last['Close']
    change_pct = round((ta_df['Close'].iloc[-1] / ta_df['Close'].iloc[-2] - 1) * 100, 2)
    sign = '+' if change_pct >= 0 else ''

    # 5日涨跌
    ret5 = round((ta_df['Close'].iloc[-1] / ta_df['Close'].iloc[-6] - 1) * 100, 2) if len(ta_df) >= 6 else 0
    sign5 = '+' if ret5 >= 0 else ''

    vol_ratio = round(last['Vol_ratio'], 2) if not np.isnan(last['Vol_ratio']) else 1.0

    return {
        'label': label, 'ticker': ticker,
        'close': round(close, 3),
        'change': f'{sign}{change_pct}%',
        'change_val': change_pct,
        'ret5': f'{sign5}{ret5}%',
        'ret5_val': ret5,
        'rsi': rsi, 'rsi_status': rsi_status, 'rsi_key': rsi_key,
        'macd_status': macd_status, 'macd_key': macd_key,
        'bb_pct': bb_pct, 'ma_trend': ma_trend, 'ma_key': ma_key,
        'support': round(last['S1'], 3), 'resistance': round(last['R1'], 3),
        'ma5': round(last['MA5'], 3), 'ma20': round(last['MA20'], 3), 'ma60': round(last['MA60'], 3),
        'vol_ratio': vol_ratio,
        'hist_narrowing': bool(abs(last['Hist']) < abs(prev['Hist'])),  # MACD柱体收窄
    }


# ─── 自动生成文字分析 ─────────────────────────────────────────

def generate_intraday_analysis(s):
    """当天盘面分析（针对单只基金）"""
    lines = []
    name = s['label'].split(' ')[0]

    # 涨跌描述
    if s['change_val'] > 2:
        lines.append(f"今日大涨 {s['change']}，放量突破，多方占优。")
    elif s['change_val'] > 0.5:
        lines.append(f"今日小幅上涨 {s['change']}，走势平稳。")
    elif s['change_val'] > -0.5:
        lines.append(f"今日基本平收（{s['change']}），盘面分歧明显。")
    elif s['change_val'] > -2:
        lines.append(f"今日小幅下跌 {s['change']}，短期承压。")
    else:
        lines.append(f"今日大跌 {s['change']}，跌幅较大，需关注市场情绪。")

    # 量价配合
    if s['vol_ratio'] > 1.5 and s['change_val'] > 0:
        lines.append(f"成交量是10日均量的 {s['vol_ratio']}倍，放量上涨，资金积极流入，信号较强。")
    elif s['vol_ratio'] > 1.5 and s['change_val'] < 0:
        lines.append(f"成交量是10日均量的 {s['vol_ratio']}倍，放量下跌，主力出货迹象明显，需警惕。")
    elif s['vol_ratio'] < 0.7 and s['change_val'] < 0:
        lines.append(f"缩量下跌（量比 {s['vol_ratio']}），抛压有限，跌势或难持续，可关注企稳信号。")
    elif s['vol_ratio'] < 0.7 and s['change_val'] > 0:
        lines.append(f"缩量上涨（量比 {s['vol_ratio']}），动能不足，需等放量确认才更可信。")
    else:
        lines.append(f"成交量正常（量比 {s['vol_ratio']}），无明显异动。")

    # 5日趋势补充
    if s['ret5_val'] > 3:
        lines.append(f"近5日累涨 {s['ret5']}，短期涨幅较大，需注意短线获利回吐压力。")
    elif s['ret5_val'] < -3:
        lines.append(f"近5日累跌 {s['ret5']}，短期弱势明显。")

    return ' '.join(lines)


def generate_indicator_analysis(s):
    """技术指标解读"""
    parts = []

    # RSI
    if s['rsi_key'] == 'overbought':
        parts.append(f"RSI({s['rsi']})进入超买区（>70），短期存在回调压力，不宜追高。")
    elif s['rsi_key'] == 'oversold':
        parts.append(f"RSI({s['rsi']})处于超卖区（<30），技术上存在反弹可能，但需确认止跌。")
    else:
        if s['rsi'] > 55:
            parts.append(f"RSI({s['rsi']})中性偏强，多方动能尚可。")
        elif s['rsi'] < 45:
            parts.append(f"RSI({s['rsi']})中性偏弱，空方略占优势。")
        else:
            parts.append(f"RSI({s['rsi']})处于中性区间，多空力量均衡。")

    # MACD
    if s['macd_key'] == 'golden':
        parts.append("MACD今日金叉，为阶段性买入信号，可关注。")
    elif s['macd_key'] == 'death':
        parts.append("MACD今日死叉，为阶段性卖出信号，建议谨慎。")
    elif s['macd_key'] == 'bull':
        if s['hist_narrowing']:
            parts.append("MACD多头排列但柱体收窄，上涨动能有所减弱，需关注是否转向。")
        else:
            parts.append("MACD多头排列且柱体扩张，上涨趋势延续中。")
    else:
        if s['hist_narrowing']:
            parts.append("MACD空头排列但柱体收窄，下跌动能减弱，或有止跌迹象。")
        else:
            parts.append("MACD空头排列且柱体持续扩大，下跌趋势尚未结束。")

    # 布林带
    if s['bb_pct'] > 85:
        parts.append(f"布林带位置 {s['bb_pct']}%，价格贴近上轨，短期超买，回调概率较高。")
    elif s['bb_pct'] < 15:
        parts.append(f"布林带位置 {s['bb_pct']}%，价格接近下轨支撑，技术性反弹窗口打开。")
    elif s['bb_pct'] > 60:
        parts.append(f"布林带位置 {s['bb_pct']}%，价格处于中轨上方，偏强区间。")
    else:
        parts.append(f"布林带位置 {s['bb_pct']}%，价格处于中轨下方，偏弱区间。")

    # 均线
    if s['ma_key'] == 'bull':
        parts.append(f"MA5({s['ma5']}) > MA20({s['ma20']}) > MA60({s['ma60']})，均线多头排列，中期趋势向上。")
    elif s['ma_key'] == 'bear':
        parts.append(f"MA5({s['ma5']}) < MA20({s['ma20']}) < MA60({s['ma60']})，均线空头排列，中期趋势向下。")
    else:
        parts.append(f"均线呈混合排列（MA5:{s['ma5']} / MA20:{s['ma20']} / MA60:{s['ma60']}），趋势尚不明朗，震荡格局。")

    return ' '.join(parts)


def generate_operation_advice(s):
    """操作建议"""
    name = s['label'].split(' ')[0]

    # 综合打分：正面信号 vs 负面信号
    bullish = 0
    bearish = 0

    if s['rsi_key'] == 'oversold': bullish += 2
    if s['rsi_key'] == 'overbought': bearish += 2
    if s['macd_key'] == 'golden': bullish += 2
    if s['macd_key'] == 'death': bearish += 2
    if s['macd_key'] == 'bull': bullish += 1
    if s['macd_key'] == 'bear': bearish += 1
    if s['bb_pct'] < 20: bullish += 1
    if s['bb_pct'] > 80: bearish += 1
    if s['ma_key'] == 'bull': bullish += 1
    if s['ma_key'] == 'bear': bearish += 1
    if s['vol_ratio'] > 1.3 and s['change_val'] > 0: bullish += 1
    if s['vol_ratio'] > 1.3 and s['change_val'] < 0: bearish += 1
    if s['vol_ratio'] < 0.8 and s['change_val'] < 0: bullish += 1  # 缩量下跌偏正面

    net = bullish - bearish

    # 支撑压力提示
    sr_text = f"关键支撑：{s['support']}，近期压力：{s['resistance']}。"

    if net >= 3:
        advice = f"综合信号偏多，技术面支持做多。建议：可考虑逢低分批建仓或加仓，{sr_text}若跌破支撑 {s['support']} 则止损观望。"
        color = '#06d6a0'
        tag = '🟢 偏多·可考虑买入'
    elif net >= 1:
        advice = f"信号中性偏多，但力度有限。建议：持仓者可继续持有，空仓者等回调至 {s['support']} 附近再考虑介入。{sr_text}"
        color = '#a8f0c6'
        tag = '🔵 中性偏多·观望为主'
    elif net <= -3:
        advice = f"综合信号偏空，短期风险较高。建议：已持仓者考虑减仓或止损，止损参考 {s['support']}；空仓者不追跌，等稳定信号再入场。{sr_text}"
        color = '#ff6b6b'
        tag = '🔴 偏空·建议减仓/观望'
    elif net <= -1:
        advice = f"信号中性偏空，谨慎为宜。建议：轻仓持有或空仓观望，等待 MACD 或 RSI 企稳后再操作。{sr_text}压力位 {s['resistance']} 是反弹关键考验。"
        color = '#ffb347'
        tag = '🟡 中性偏空·谨慎持仓'
    else:
        advice = f"多空信号均衡，方向不明。建议：短线不追涨不追跌，以 {s['support']} 为止损参考，等待方向明确后再操作。{sr_text}"
        color = '#8b949e'
        tag = '⚪ 中性·等待方向'

    return advice, color, tag


def generate_comparison_text(signals, corr):
    """两基金对比分析"""
    s0, s1 = signals[0], signals[1]
    lines = []

    # 今日表现对比
    if s0['change_val'] > 0 and s1['change_val'] > 0:
        lines.append(f"两只基金今日同步上涨，市场情绪整体偏乐观。")
    elif s0['change_val'] < 0 and s1['change_val'] < 0:
        lines.append(f"两只基金今日同步下跌，市场风险偏好有所收缩。")
    else:
        stronger = s0['label'] if s0['change_val'] > s1['change_val'] else s1['label']
        lines.append(f"两只基金今日走势分化，{stronger.split(' ')[0]} 相对较强。")

    # 相关性
    if corr > 0.85:
        lines.append(f"两基金相关性高达 {corr}，走势高度同步，同时持有分散风险效果有限。")
    elif corr > 0.65:
        lines.append(f"两基金相关性为 {corr}，有一定同步性，但各自受不同板块逻辑驱动。")
    else:
        lines.append(f"两基金相关性仅 {corr}，走势相对独立，组合持有有分散效果。")

    # 强弱对比
    if s0['ma_key'] == 'bull' and s1['ma_key'] != 'bull':
        lines.append(f"{s0['label'].split(' ')[0]} 均线多头，{s1['label'].split(' ')[0]} 趋势较弱，资金分化明显。")
    elif s1['ma_key'] == 'bull' and s0['ma_key'] != 'bull':
        lines.append(f"{s1['label'].split(' ')[0]} 均线多头，{s0['label'].split(' ')[0]} 趋势较弱，资金分化明显。")
    elif s0['rsi'] > s1['rsi'] + 15:
        lines.append(f"{s0['label'].split(' ')[0]} RSI({s0['rsi']}) 远高于 {s1['label'].split(' ')[0]} RSI({s1['rsi']})，前者短期更热，后者相对冷静。")
    elif s1['rsi'] > s0['rsi'] + 15:
        lines.append(f"{s1['label'].split(' ')[0]} RSI({s1['rsi']}) 远高于 {s0['label'].split(' ')[0]} RSI({s0['rsi']})，前者短期更热，后者相对冷静。")

    return ' '.join(lines)


# ─── 图表生成 ─────────────────────────────────────────────────

def make_ta_chart(all_ta):
    colors = ['#00b4d8', '#ff6b6b']
    tickers = list(all_ta.keys())
    fig = plt.figure(figsize=(16, 14), facecolor='#0d1117')
    outer = gridspec.GridSpec(2, 1, figure=fig, hspace=0.5)

    def plot_one(ax_list, df, label, color):
        ax_price, ax_rsi, ax_macd = ax_list
        ax_price.plot(df.index, df['Close'], color=color, linewidth=1.5, label='收盘价')
        ax_price.plot(df.index, df['MA5'],   color='#ffd166', linewidth=0.8, linestyle='--', label='MA5')
        ax_price.plot(df.index, df['MA20'],  color='#06d6a0', linewidth=0.8, linestyle='--', label='MA20')
        ax_price.plot(df.index, df['MA60'],  color='#ef476f', linewidth=0.8, linestyle='--', label='MA60')
        ax_price.fill_between(df.index, df['BB_up'], df['BB_dn'], alpha=0.06, color='white')
        ax_price.plot(df.index, df['BB_up'], color='#aaa', linewidth=0.5, linestyle=':')
        ax_price.plot(df.index, df['BB_dn'], color='#aaa', linewidth=0.5, linestyle=':')
        ax_price.axhline(df['R1'].iloc[-1], color='#ff6b6b', linewidth=0.7, linestyle='-.', alpha=0.7)
        ax_price.axhline(df['S1'].iloc[-1], color='#06d6a0', linewidth=0.7, linestyle='-.', alpha=0.7)
        ax_price.set_title(label, color='white', fontsize=11, pad=6)
        ax_price.set_facecolor('#161b22'); ax_price.tick_params(colors='#aaa', labelsize=7)
        ax_price.spines[:].set_color('#30363d')
        ax_price.legend(fontsize=7, facecolor='#161b22', labelcolor='white', ncol=5)
        ax_rsi.plot(df.index, df['RSI'], color='#a8dadc', linewidth=1.2)
        ax_rsi.axhline(70, color='#ff6b6b', linewidth=0.7, linestyle='--')
        ax_rsi.axhline(30, color='#06d6a0', linewidth=0.7, linestyle='--')
        ax_rsi.fill_between(df.index, df['RSI'], 70, where=df['RSI']>=70, alpha=0.15, color='#ff6b6b')
        ax_rsi.fill_between(df.index, df['RSI'], 30, where=df['RSI']<=30, alpha=0.15, color='#06d6a0')
        ax_rsi.set_ylabel('RSI(14)', color='#aaa', fontsize=8); ax_rsi.set_ylim(0, 100)
        ax_rsi.set_facecolor('#161b22'); ax_rsi.tick_params(colors='#aaa', labelsize=7)
        ax_rsi.spines[:].set_color('#30363d')
        hist_col = ['#06d6a0' if v >= 0 else '#ef476f' for v in df['Hist']]
        ax_macd.bar(df.index, df['Hist'], color=hist_col, alpha=0.6, width=1.5)
        ax_macd.plot(df.index, df['MACD'],   color='#00b4d8', linewidth=1, label='MACD')
        ax_macd.plot(df.index, df['Signal'], color='#ffd166', linewidth=1, label='Signal')
        ax_macd.axhline(0, color='#555', linewidth=0.5)
        ax_macd.set_ylabel('MACD', color='#aaa', fontsize=8)
        ax_macd.set_facecolor('#161b22'); ax_macd.tick_params(colors='#aaa', labelsize=7)
        ax_macd.spines[:].set_color('#30363d')
        ax_macd.legend(fontsize=7, facecolor='#161b22', labelcolor='white')

    for idx, t in enumerate(tickers):
        inner = gridspec.GridSpecFromSubplotSpec(3, 1, subplot_spec=outer[idx],
                                                  height_ratios=[3, 1, 1], hspace=0.05)
        axes = [fig.add_subplot(inner[i]) for i in range(3)]
        plot_one(axes, all_ta[t], TICKERS.get(t, t), colors[idx])

    today = datetime.date.today().strftime('%Y-%m-%d')
    fig.suptitle(f'技术指标分析：159140 vs 513050（{today}）', color='white', fontsize=13, y=0.99)
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='#0d1117')
    plt.close(); buf.seek(0)
    return buf.read()


def make_compare_chart(data_1y):
    tickers = list(data_1y.keys())
    colors = ['#00b4d8', '#ff6b6b']
    price_df = pd.DataFrame({TICKERS.get(t, t): data_1y[t]['Close'] for t in tickers}).dropna()
    normalized = price_df / price_df.iloc[0] * 100
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), facecolor='#0d1117')
    ax1 = axes[0]
    for i, col in enumerate(normalized.columns):
        ax1.plot(normalized.index, normalized[col], color=colors[i], linewidth=1.5, label=col)
    ax1.axhline(100, color='#555', linestyle='--', linewidth=0.8)
    ax1.fill_between(normalized.index, normalized.iloc[:, 0], normalized.iloc[:, 1], alpha=0.08, color='white')
    ax1.set_facecolor('#161b22'); ax1.set_title('近1年归一化价格走势 (基准=100)', color='white', fontsize=11)
    ax1.tick_params(colors='#aaa', labelsize=8); ax1.spines[:].set_color('#30363d')
    ax1.legend(fontsize=9, facecolor='#161b22', labelcolor='white')
    ax2 = axes[1]
    r0 = price_df.iloc[:, 0].pct_change().dropna()
    r1 = price_df.iloc[:, 1].pct_change().dropna()
    common = r0.index.intersection(r1.index)
    corr = r0[common].corr(r1[common])
    ax2.scatter(r0[common]*100, r1[common]*100, alpha=0.3, s=8, color='#a8dadc')
    m, b = np.polyfit(r0[common], r1[common], 1)
    x_line = np.linspace(r0[common].min(), r0[common].max(), 100)
    ax2.plot(x_line*100, (m*x_line+b)*100, color='#ff6b6b', linewidth=1.2, linestyle='--')
    ax2.set_facecolor('#161b22'); ax2.set_title(f'日收益率相关性 (r={corr:.3f})', color='white', fontsize=11)
    ax2.tick_params(colors='#aaa', labelsize=8); ax2.spines[:].set_color('#30363d')
    fig.suptitle('ETF走势对比（近1年）', color='white', fontsize=13)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='#0d1117')
    plt.close(); buf.seek(0)
    return buf.read(), round(corr, 4)


# ─── 邮件HTML构建 ─────────────────────────────────────────────

def build_html_email(signals, corr):
    today = datetime.date.today().strftime('%Y年%m月%d日')
    wd = ['周一','周二','周三','周四','周五','周六','周日'][datetime.date.today().weekday()]

    # 生成文字分析
    analyses = []
    for s in signals:
        intraday = generate_intraday_analysis(s)
        indicators = generate_indicator_analysis(s)
        advice, advice_color, advice_tag = generate_operation_advice(s)
        analyses.append({
            'signal': s,
            'intraday': intraday,
            'indicators': indicators,
            'advice': advice,
            'advice_color': advice_color,
            'advice_tag': advice_tag,
        })

    comparison_text = generate_comparison_text(signals, corr)

    # 信号汇总表格行
    signal_rows = ''
    for s in signals:
        bg = '#0d4f2e' if '+' in s['change'] else ('#4f0d0d' if s['change'].startswith('-') else '#1a1a2e')
        c = '#06d6a0' if '+' in s['change'] else '#ff6b6b'
        signal_rows += f"""<tr style="background:{bg};">
          <td style="padding:10px;font-weight:bold;color:#fff;">{s['label']}</td>
          <td style="padding:10px;text-align:center;color:#ffd166;font-size:16px;font-weight:bold;">{s['close']}</td>
          <td style="padding:10px;text-align:center;color:{c};font-weight:bold;">{s['change']}</td>
          <td style="padding:10px;text-align:center;color:#8b949e;">{s['ret5']}</td>
          <td style="padding:10px;text-align:center;color:#a8dadc;">{s['rsi']} {s['rsi_status']}</td>
          <td style="padding:10px;color:#ccc;">{s['macd_status']}</td>
          <td style="padding:10px;text-align:center;color:#ccc;">{s['bb_pct']}%</td>
          <td style="padding:10px;color:#ccc;">{s['ma_trend']}</td>
          <td style="padding:10px;text-align:center;color:#06d6a0;">{s['support']}</td>
          <td style="padding:10px;text-align:center;color:#ff6b6b;">{s['resistance']}</td>
        </tr>"""

    # 各基金详细分析块
    analysis_blocks = ''
    for a in analyses:
        s = a['signal']
        chg_color = '#06d6a0' if '+' in s['change'] else '#ff6b6b'
        analysis_blocks += f"""
  <div style="background:#161b22;border-radius:10px;padding:20px;margin-bottom:20px;border:1px solid #30363d;">
    <!-- 基金标题行 -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px;">
      <h2 style="margin:0;font-size:17px;color:#e6edf3;">{s['label']}</h2>
      <div style="display:flex;gap:12px;align-items:center;">
        <span style="font-size:20px;font-weight:bold;color:#ffd166;">{s['close']}</span>
        <span style="font-size:15px;font-weight:bold;color:{chg_color};">{s['change']}</span>
        <span style="font-size:12px;color:#8b949e;">5日 {s['ret5']}</span>
      </div>
    </div>

    <!-- 快速指标行 -->
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;">
      <span style="background:#21262d;border-radius:6px;padding:5px 10px;font-size:12px;color:#a8dadc;">RSI {s['rsi']} · {s['rsi_status'].split('️')[1].strip() if '️' in s['rsi_status'] else s['rsi_status']}</span>
      <span style="background:#21262d;border-radius:6px;padding:5px 10px;font-size:12px;color:#ccc;">{s['macd_status']}</span>
      <span style="background:#21262d;border-radius:6px;padding:5px 10px;font-size:12px;color:#ccc;">布林带 {s['bb_pct']}%</span>
      <span style="background:#21262d;border-radius:6px;padding:5px 10px;font-size:12px;color:#ccc;">{s['ma_trend']}</span>
      <span style="background:#21262d;border-radius:6px;padding:5px 10px;font-size:12px;color:#8b949e;">量比 {s['vol_ratio']}</span>
    </div>

    <!-- 当天盘面分析 -->
    <div style="margin-bottom:14px;">
      <p style="margin:0 0 6px;font-size:13px;font-weight:bold;color:#00b4d8;">📊 当天盘面分析</p>
      <p style="margin:0;font-size:13px;color:#ccc;line-height:1.7;">{a['intraday']}</p>
    </div>

    <!-- 技术指标解读 -->
    <div style="margin-bottom:14px;">
      <p style="margin:0 0 6px;font-size:13px;font-weight:bold;color:#00b4d8;">📉 技术指标解读</p>
      <p style="margin:0;font-size:13px;color:#ccc;line-height:1.7;">{a['indicators']}</p>
    </div>

    <!-- 支撑压力 -->
    <div style="display:flex;gap:16px;margin-bottom:14px;flex-wrap:wrap;">
      <div style="background:#0d4f2e;border-radius:6px;padding:8px 14px;">
        <p style="margin:0;font-size:11px;color:#8b949e;">支撑位</p>
        <p style="margin:2px 0 0;font-size:16px;font-weight:bold;color:#06d6a0;">{s['support']}</p>
      </div>
      <div style="background:#4f0d0d;border-radius:6px;padding:8px 14px;">
        <p style="margin:0;font-size:11px;color:#8b949e;">压力位</p>
        <p style="margin:2px 0 0;font-size:16px;font-weight:bold;color:#ff6b6b;">{s['resistance']}</p>
      </div>
      <div style="background:#21262d;border-radius:6px;padding:8px 14px;">
        <p style="margin:0;font-size:11px;color:#8b949e;">MA5 / MA20 / MA60</p>
        <p style="margin:2px 0 0;font-size:13px;color:#ccc;">{s['ma5']} · {s['ma20']} · {s['ma60']}</p>
      </div>
    </div>

    <!-- 操作建议 -->
    <div style="background:{a['advice_color']}22;border-left:3px solid {a['advice_color']};border-radius:0 6px 6px 0;padding:12px 14px;">
      <p style="margin:0 0 4px;font-size:13px;font-weight:bold;color:{a['advice_color']};">{a['advice_tag']}</p>
      <p style="margin:0;font-size:13px;color:#ccc;line-height:1.7;">{a['advice']}</p>
    </div>
  </div>"""

    corr_desc = ('相关性极高（同涨同跌），分散效果有限' if corr > 0.85 else
                 ('相关性较高，仍有一定分散价值' if corr > 0.65 else '相关性中等，组合持有有分散效果'))

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>* {{box-sizing:border-box;}} @media(max-width:600px){{body{{padding:10px!important;}}}}</style>
</head>
<body style="background:#0d1117;color:#e6edf3;font-family:Arial,sans-serif;margin:0;padding:20px;">
<div style="max-width:900px;margin:0 auto;">

  <!-- 标题栏 -->
  <div style="background:linear-gradient(135deg,#1f2937,#111827);border-radius:12px;padding:24px;margin-bottom:20px;border:1px solid #30363d;">
    <h1 style="margin:0;font-size:22px;color:#00b4d8;">📊 ETF 每日收盘分析报告</h1>
    <p style="margin:8px 0 0;color:#8b949e;font-size:14px;">{today} {wd} · 收盘后自动生成</p>
    <p style="margin:4px 0 0;color:#8b949e;font-size:13px;">标的：159140 科创AI ETF · 513050 中概互联ETF</p>
  </div>

  <!-- 信号汇总表 -->
  <div style="background:#161b22;border-radius:10px;padding:16px;margin-bottom:20px;border:1px solid #30363d;">
    <h2 style="margin:0 0 12px;font-size:16px;color:#e6edf3;">📋 今日信号速览</h2>
    <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;font-size:12px;min-width:700px;">
      <thead><tr style="background:#21262d;color:#8b949e;">
        <th style="padding:8px;text-align:left;">基金</th>
        <th style="padding:8px;">收盘价</th>
        <th style="padding:8px;">今日</th>
        <th style="padding:8px;">5日</th>
        <th style="padding:8px;">RSI</th>
        <th style="padding:8px;">MACD</th>
        <th style="padding:8px;">布林带</th>
        <th style="padding:8px;">均线趋势</th>
        <th style="padding:8px;">支撑</th>
        <th style="padding:8px;">压力</th>
      </tr></thead>
      <tbody>{signal_rows}</tbody>
    </table></div>
  </div>

  <!-- 各基金详细分析 -->
  {analysis_blocks}

  <!-- 对比分析 -->
  <div style="background:#161b22;border-radius:10px;padding:20px;margin-bottom:20px;border:1px solid #30363d;">
    <h2 style="margin:0 0 12px;font-size:16px;color:#e6edf3;">🔗 两基金对比分析</h2>
    <p style="margin:0 0 10px;font-size:13px;color:#ccc;line-height:1.7;">{comparison_text}</p>
    <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;">
      <span style="font-size:13px;color:#8b949e;">相关系数：</span>
      <span style="font-size:20px;font-weight:bold;color:#a8dadc;">r = {corr}</span>
      <span style="font-size:13px;color:#8b949e;">— {corr_desc}</span>
    </div>
  </div>

  <!-- 图表附件说明 -->
  <div style="background:#161b22;border-radius:10px;padding:16px;margin-bottom:20px;border:1px solid #30363d;">
    <h2 style="margin:0 0 8px;font-size:16px;color:#e6edf3;">📈 附件图表说明</h2>
    <p style="margin:0;color:#8b949e;font-size:13px;line-height:1.8;">
      · <strong style="color:#ccc;">technical_analysis.png</strong> — 近6个月 K线价格 + MA均线 + 布林带 + RSI(14) + MACD<br>
      · <strong style="color:#ccc;">etf_compare.png</strong> — 近1年归一化走势对比 + 日收益率相关性散点图
    </p>
  </div>

  <!-- 免责声明 -->
  <div style="border-top:1px solid #30363d;margin-top:20px;padding-top:12px;">
    <p style="margin:0;color:#484f58;font-size:11px;line-height:1.6;">
      ⚠️ 本报告由自动化程序依据技术指标生成，仅供参考，不构成投资建议。市场有风险，投资需谨慎。<br>
      数据来源：Yahoo Finance · 生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} CST
    </p>
  </div>

</div></body></html>"""


def send_email(html_content, ta_img_bytes, compare_img_bytes):
    today = datetime.date.today().strftime('%Y-%m-%d')
    msg = MIMEMultipart('related')
    msg['Subject'] = f'📊 ETF每日分析 | 159140 & 513050 | {today}'
    msg['From']    = GMAIL_SENDER
    msg['To']      = GMAIL_RECEIVER
    alt = MIMEMultipart('alternative')
    msg.attach(alt)
    alt.attach(MIMEText(html_content, 'html', 'utf-8'))
    img1 = MIMEImage(ta_img_bytes, name='technical_analysis.png')
    img1.add_header('Content-Disposition', 'attachment', filename='technical_analysis.png')
    msg.attach(img1)
    img2 = MIMEImage(compare_img_bytes, name='etf_compare.png')
    img2.add_header('Content-Disposition', 'attachment', filename='etf_compare.png')
    msg.attach(img2)
    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(GMAIL_SENDER, GMAIL_APP_PASS)
        server.sendmail(GMAIL_SENDER, [GMAIL_RECEIVER], msg.as_string())
    print(f"✅ 邮件已发送至 {GMAIL_RECEIVER}")


def main():
    print("🔄 开始获取数据...")
    tickers = list(TICKERS.keys())
    data_6mo = {t: fetch_data(t, '6mo') for t in tickers}
    data_1y  = {t: fetch_data(t, '1y')  for t in tickers}
    print("📊 计算技术指标...")
    all_ta = {t: compute_ta(data_6mo[t]) for t in tickers}
    print("📝 生成信号和文字分析...")
    signals = [signal_summary(all_ta[t], t, TICKERS[t]) for t in tickers]
    for s in signals:
        print(f"  {s['label']}: {s['close']} ({s['change']}) | RSI={s['rsi']} | {s['macd_status']}")
    print("🎨 生成图表...")
    ta_img_bytes = make_ta_chart(all_ta)
    compare_img_bytes, corr = make_compare_chart(data_1y)
    print("📧 构建邮件并发送...")
    html = build_html_email(signals, corr)
    send_email(html, ta_img_bytes, compare_img_bytes)
    print("✅ 完成！")


if __name__ == '__main__':
    main()
