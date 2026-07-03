# tushare skill（本地化安装说明）

来源：[waditu-tushare/skills](https://github.com/waditu-tushare/skills)（`tushare` 目录，version 1.1.12，author tushare.pro）

本目录是把上游 Tushare 数据研究技能安装到本项目，并按**本项目核心域**筛选了接口索引。

## 目录结构

| 文件 | 说明 |
| :--- | :--- |
| `SKILL.md` | 技能定义（上游原样保留，便于后续同步升级） |
| `references/数据接口.md` | **已筛选**的接口索引，每条带 Tushare 官方文档链接 |
| `scripts/stock_data_demo.py` | 股票数据获取示例（`stock_basic` / `daily` / `fina_indicator`） |
| `scripts/fund_data_demo.py` | 基金数据获取示例（`fund_basic` / `fund_nav` / `fund_manager`） |

## 接口筛选范围

只保留与本股票分析项目直接相关的接口（共 **150** 个），筛选规则：分类以
`股票数据`、`指数专题`、`ETF专题`、`港股数据`、`美股数据` 开头的全部保留。

| 分类 | 接口数 | 覆盖能力 |
| :--- | ---: | :--- |
| 股票数据 | 103 | 行情/基础/财务/估值(daily_basic)/资金流(moneyflow*)/筹码(cyq_chips)/龙虎榜/板块/两融/复权因子 |
| 指数专题 | 19 | 指数行情、成分权重、申万/中信行业、国际指数 |
| ETF专题 | 8 | ETF 行情、基准指数、份额规模、复权因子 |
| 港股数据 | 11 | 港股行情/复权/财务三表/基础信息 |
| 美股数据 | 9 | 美股行情/复权/财务三表/基础信息 |

**已排除**（非本项目核心域）：债券专题、公募基金(OTC 净值/持仓)、外汇数据、
期权数据、期货数据、现货(黄金)、宏观经济(CPI/PMI/利率)、大模型语料(新闻/公告/研报)、
行业经济(电影/TMT)、财富管理(基金销售)。

如需完整 220+ 接口，参见上游仓库 `references/数据接口.md`。

## Token 配置

本项目已在 `.env` 中配置 `TUSHARE_TOKEN`（实测积分 ≥ 2000，`daily` / `hk_daily` /
`daily_basic` 等关键接口均可用）。技能会复用同一环境变量，无需额外配置。

> 注：SKILL.md 为上游完整定义，其正文 taxonomy 仍提及少量已排除的接口（如宏观/新闻）；
> 以本目录 `references/数据接口.md` 为本地实际可用接口索引的准绳。
