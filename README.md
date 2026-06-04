# 台股技術選股器

每個交易日自動掃描台股全市場，篩出符合多條件技術型態的標的。

> ⚠️ 本工具僅為**技術面資訊整理，非投資建議**。型態為量化近似，務必人工看圖 + 查基本面再決定。

## 篩選條件
1. 股價 10–100 元
2. 型態：頭肩底 / 下降楔形（量化代理分數，人工確認）
3. MACD 柱狀圖近 N 日內翻紅（預設 3 日）
4. 日線：三黑量縮 → 紅K量微增
5. 60 分線：同上（只對日線決選股檢查）

## 資料來源
- 全市場代號 + 最新收盤：證交所 STOCK_DAY_ALL + 櫃買 OpenAPI（官方）
- 日線歷史：yfinance，並以官方收盤交叉驗證（誤差 >2% 標警告）

## 每日自動執行（雲端）
GitHub Actions 每天 **台北 15:00（週一~五）** 自動跑，當日報告 commit 到 `reports/YYYY-MM-DD.txt`。
也可在 Actions 頁面手動 **Run workflow**。

## 手動執行
```bash
pip install -r requirements.txt
python run_daily.py          # 完整每日流程（含新鮮度檢查）
python screener2.py          # 直接跑全市場掃描
```

## 檔案
| 檔 | 用途 |
|---|---|
| `screener2.py` | 全市場掃描主程式 |
| `screener.py`  | 指標/型態函式（被 screener2 匯入） |
| `run_daily.py` | 跨平台每日執行器 |
| `universe.py`  | 小範圍驗證股票池 |
| `reports/`     | 每日掃描報告 |
