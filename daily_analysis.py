"""
每日 ETF 分析脚本 — 159140 & 513050
收盘后自动运行，生成分析图表并发送 Gmail 报告
"""

import os
import smtplib
import datetime
import io
import base64
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

# ─── 配置区（唯一需要修改的地方）─────────────────────────────
GMAIL_SENDER   = os.environ.get("GMAIL_SENDER", "")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")
GMAIL_RECEIVER = os.environ.get("GMAIL_RECEIVER", "")

TICKERS = {
    '159140.SZ': '159140 科创AI ETF',
    '513050.SS': '513050 中概互联ETF',
}
# ──────────────────────────────────────────────────────────────


def fetch_data(ticker, period='6mo'):
    hist = yf.Ticker(ticker).history(period=period)
    hist.index = hist.index.tz_localize(None)
    return hist


def compute_ta(hist):
    df = hist[['Close', 'Volume']].copy()
    df['MA5']  = df['Close'].rolling(5).mean()
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
    return df


def signal_summary(ta_df, ticker, label):
    last = ta_df.iloc[-1]
    prev = ta_df.iloc[-2]
    rsi = round(last['RSI'], 1)
    if last['MACD'] > last['Signal'] and prev['MACD'] <= prev['Signal']:
        macd_status = '🟢 MACD金叉（买入信号）'
    elif last['MACD'] < last['Signal'] and prev['MACD'] >= prev['Signal']:
        macd_status = '🔴 MACD死叉（卖出信号）'
    elif last['MACD'] > last['Signal']:
        macd_status = '🔵 MACD多头排列'
    else:
        macd_status = '⚪ MACD空头排列'
    rsi_status = '⚠️ 超买区间' if rsi > 70 else ('🟢 超卖区间' if rsi < 30 else '🔵 中性区间')
    bb_pct = round(last['BB_pct'] * 100, 1)
    ma_trend = '多头排列📈' if (last['MA5'] > last['MA20'] > last['MA60']) else (
               '空头排列📉' if (last['MA5'] < last['MA20'] < last['MA60']) else '混合震荡')
    close = last['Close']
    change_pct = round((ta_df['Close'].iloc[-1] / ta_df['Close'].iloc[-2] - 1) * 100, 2)
    sign = '+' if change_pct >= 0 else ''
    return {
        'label': label, 'close': round(close, 3),
        'change': f'{sign}{change_pct}%', 'rsi': rsi,
        'rsi_status': rsi_status, 'macd_status': macd_status,
        'bb_pct': bb_pct, 'ma_trend': ma_trend,
        'support': round(last['S1'], 3), 'resistance': round(last['R1'], 3),
        'ma5': round(last['MA5'], 3), 'ma20': round(last['MA20'], 3), 'ma60': round(last['MA60'], 3),
    }


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


def build_html_email(signals, corr):
    today = datetime.date.today().strftime('%Y年%m月%d日')
    wd = ['周一','周二','周三','周四','周五','周六','周日'][datetime.date.today().weekday()]

    signal_rows = ''
    for s in signals:
        bg = '#0d4f2e' if '+' in s['change'] else ('#4f0d0d' if s['change'].startswith('-') else '#1a1a2e')
        c = '#06d6a0' if '+' in s['change'] else '#ff6b6b'
        signal_rows += f"""<tr style="background:{bg};">
          <td style="padding:10px;font-weight:bold;color:#fff;">{s['label']}</td>
          <td style="padding:10px;text-align:center;color:#ffd166;font-size:16px;font-weight:bold;">{s['close']}</td>
          <td style="padding:10px;text-align:center;color:{c};font-weight:bold;">{s['change']}</td>
          <td style="padding:10px;text-align:center;color:#a8dadc;">{s['rsi']} {s['rsi_status']}</td>
          <td style="padding:10px;color:#ccc;">{s['macd_status']}</td>
          <td style="padding:10px;text-align:center;color:#ccc;">{s['bb_pct']}%</td>
          <td style="padding:10px;color:#ccc;">{s['ma_trend']}</td>
          <td style="padding:10px;text-align:center;color:#06d6a0;">{s['support']}</td>
          <td style="padding:10px;text-align:center;color:#ff6b6b;">{s['resistance']}</td>
        </tr>"""

    corr_desc = ('相关性极高（同涨同跌），分散效果有限' if corr > 0.85 else
                 ('相关性较高，仍有一定分散价值' if corr > 0.65 else '相关性中等，组合持有有分散效果'))
    ma_detail = ''.join([f"""<div style="margin-bottom:12px;">
        <p style="margin:0;font-weight:bold;color:#fff;">{s['label']}</p>
        <p style="margin:4px 0;color:#ccc;font-size:13px;">
          MA5: <span style="color:#ffd166;">{s['ma5']}</span> &nbsp;|&nbsp;
          MA20: <span style="color:#06d6a0;">{s['ma20']}</span> &nbsp;|&nbsp;
          MA60: <span style="color:#ef476f;">{s['ma60']}</span> &nbsp;|&nbsp;
          趋势: <span style="color:#a8dadc;">{s['ma_trend']}</span>
        </p></div>""" for s in signals])

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="background:#0d1117;color:#e6edf3;font-family:Arial,sans-serif;margin:0;padding:20px;">
<div style="max-width:900px;margin:0 auto;">
  <div style="background:linear-gradient(135deg,#1f2937,#111827);border-radius:12px;padding:24px;margin-bottom:20px;border:1px solid #30363d;">
    <h1 style="margin:0;font-size:22px;color:#00b4d8;">📊 ETF 每日收盘分析报告</h1>
    <p style="margin:8px 0 0;color:#8b949e;font-size:14px;">{today} {wd} · 收盘后自动生成</p>
    <p style="margin:4px 0 0;color:#8b949e;font-size:13px;">标的：159140 科创AI ETF · 513050 中概互联ETF</p>
  </div>
  <div style="background:#161b22;border-radius:10px;padding:16px;margin-bottom:20px;border:1px solid #30363d;">
    <h2 style="margin:0 0 12px;font-size:16px;color:#e6edf3;">📋 今日技术信号汇总</h2>
    <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="background:#21262d;color:#8b949e;">
        <th style="padding:10px;text-align:left;">基金</th><th style="padding:10px;">收盘价</th>
        <th style="padding:10px;">涨跌</th><th style="padding:10px;">RSI(14)</th>
        <th style="padding:10px;">MACD</th><th style="padding:10px;">布林带%</th>
        <th style="padding:10px;">均线趋势</th><th style="padding:10px;">支撑</th><th style="padding:10px;">压力</th>
      </tr></thead>
      <tbody>{signal_rows}</tbody>
    </table></div>
  </div>
  <div style="background:#161b22;border-radius:10px;padding:16px;margin-bottom:20px;border:1px solid #30363d;">
    <h2 style="margin:0 0 8px;font-size:16px;color:#e6edf3;">🔗 两基金相关性</h2>
    <p style="margin:0;color:#a8dadc;font-size:18px;font-weight:bold;">r = {corr}</p>
    <p style="margin:6px 0 0;color:#8b949e;font-size:13px;">{corr_desc}</p>
  </div>
  <div style="background:#161b22;border-radius:10px;padding:16px;margin-bottom:20px;border:1px solid #30363d;">
    <h2 style="margin:0 0 12px;font-size:16px;color:#e6edf3;">📉 均线详情</h2>{ma_detail}
  </div>
  <div style="background:#161b22;border-radius:10px;padding:16px;margin-bottom:20px;border:1px solid #30363d;">
    <h2 style="margin:0 0 8px;font-size:16px;color:#e6edf3;">📈 图表（见附件）</h2>
    <p style="margin:0;color:#8b949e;font-size:13px;">
      · <strong>technical_analysis.png</strong> — K线 + RSI + MACD（近6个月）<br>
      · <strong>etf_compare.png</strong> — 归一化走势对比 + 相关性散点
    </p>
  </div>
  <div style="border-top:1px solid #30363d;margin-top:20px;padding-top:12px;">
    <p style="margin:0;color:#484f58;font-size:11px;">⚠️ 本报告由自动化程序生成，仅供参考，不构成投资建议。<br>
    数据来源：Yahoo Finance · 生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} CST</p>
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
    print("📝 生成信号摘要...")
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
