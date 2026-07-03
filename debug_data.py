# -*- coding: utf-8 -*-
"""真机数据自检：直接测 Tushare 各接口 + DSA 兜底函数，定位为何报告缺数据。"""
import os
import sys
import datetime

# 清代理（与 main.py 一致）
for _v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_v, None)

# 加载 .env
try:
    from src.config import setup_env
    setup_env()
except Exception as e:
    print("setup_env 失败:", e)

CODE = sys.argv[1] if len(sys.argv) > 1 else "601899"  # 默认紫金矿业
TS = {"601899": "601899.SH", "600425": "600425.SH"}.get(CODE, CODE + ".SH")

print("=" * 50)
print("自检股票:", CODE, "->", TS)
print("=" * 50)

from data_provider.tushare_fetcher import TushareFetcher

f = TushareFetcher()
print("[0] Tushare API 初始化:", "OK" if f._api is not None else "失败(检查 TUSHARE_TOKEN)")
if f._api is None:
    sys.exit(1)

end = datetime.datetime.now().strftime("%Y%m%d")
start = (datetime.datetime.now() - datetime.timedelta(days=500)).strftime("%Y%m%d")

print("\n[1] 原始 Tushare 接口直连测试")
for name, kw in [
    ("daily_basic", dict(ts_code=TS, start_date=start, end_date=end,
                         fields="trade_date,volume_ratio,turnover_rate,pe,pe_ttm,pb,total_mv")),
    ("fina_indicator", dict(ts_code=TS, start_date=start, end_date=end,
                            fields="end_date,roe,netprofit_yoy,or_yoy")),
    ("income", dict(ts_code=TS, start_date=start, end_date=end,
                    fields="end_date,revenue,total_revenue,n_income_attr_p")),
    ("moneyflow", dict(ts_code=TS, start_date=start, end_date=end)),
]:
    try:
        df = getattr(f._api, name)(**kw)
        n = 0 if df is None else len(df)
        print(f"  {name:15s}: {n} 行", "" if n else "  <-- 空!")
        if n:
            print("     最新:", df.iloc[0].to_dict())
    except Exception as e:
        print(f"  {name:15s}: 异常 {repr(e)[:160]}")

print("\n[2] DSA 兜底函数测试")
try:
    q = f.get_realtime_quote(CODE)
    if q:
        print(f"  实时报价: 价={q.price} 量比={q.volume_ratio} 换手率={q.turnover_rate} "
              f"PE={q.pe_ratio} PB={q.pb_ratio} 市值={q.total_mv}")
    else:
        print("  实时报价: None")
except Exception as e:
    print("  实时报价 异常:", repr(e)[:160])

try:
    print("  资金流  :", f.get_stock_money_flow(CODE))
except Exception as e:
    print("  资金流 异常:", repr(e)[:160])

try:
    print("  基本面  :", f.get_financial_fundamentals(CODE))
except Exception as e:
    print("  基本面 异常:", repr(e)[:160])

print("\n[3] 管线实际取数路径（报告里的 MA/价格/量比 来源）")
try:
    from src.services.alphasift_service import get_dsa_daily_history, get_dsa_realtime_quote
    df, src = get_dsa_daily_history(CODE, lookback_days=120)
    print(f"  日线历史: {0 if df is None else len(df)} 行, 来源={src}",
          "  <-- 空! MA/价格/支撑压力会全缺" if (df is None or len(df) == 0) else "")
    rq = get_dsa_realtime_quote(CODE) or {}
    print(f"  管理器实时报价: 价={rq.get('price')} 量比={rq.get('volume_ratio')} "
          f"换手率={rq.get('turnover_rate')} PE={rq.get('pe_ratio')} 来源={rq.get('source')}")
    if not rq:
        print("  <-- 管理器实时报价为空!")
except Exception as e:
    import traceback
    print("  [3] 异常:", repr(e)[:200])
    traceback.print_exc()

print("\n自检完成。把以上输出全部发回。")
