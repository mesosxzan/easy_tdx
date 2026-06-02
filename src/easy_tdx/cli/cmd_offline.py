"""离线本地数据读取命令（无需网络，读取本地通达信数据文件）。"""

from __future__ import annotations

import click


@click.group()
def offline() -> None:
    """离线本地数据读取（无需网络，读取本地通达信数据文件）。

    需要本地已安装通达信并下载过对应数据。

    示例：

      easy-tdx offline home

      easy-tdx offline daily SZ 000001 --table

      easy-tdx offline min SH 600519 --type lc5 --table

      easy-tdx offline ex-files --table

      easy-tdx offline ex-daily 29#A1801 --table
    """
    pass


@offline.command()
def home() -> None:
    """检测并显示通达信安装目录。"""
    from ..offline import detect_tdx_home

    tdx_home = detect_tdx_home()
    if tdx_home is None:
        click.echo("未检测到通达信安装目录，请设置 TDX_HOME 环境变量")
        raise SystemExit(1)
    click.echo(str(tdx_home))


@offline.command()
@click.argument("market")
@click.argument("code")
@click.option("--count", default=0, type=int, help="返回条数（0=全部）")
@click.option("--table", "use_table", is_flag=True, help="表格输出")
@click.option("--output", "output_fmt", type=click.Choice(["json", "table", "csv"]), default="json")
def daily(
    market: str,
    code: str,
    count: int,
    use_table: bool,
    output_fmt: str,
) -> None:
    """读取 A 股日线数据（.day 文件）。

    MARKET: 市场代码（SZ/SH）
    CODE: 6 位股票代码

    示例：

      easy-tdx offline daily SZ 000001 --table

      easy-tdx offline daily SH 600519 --count 30 --table
    """
    import pandas as pd

    from .output import print_error, print_output
    from .parsers import parse_market

    fmt = "table" if use_table else output_fmt

    try:
        from ..offline import find_daily_bar_file, read_daily_bars

        filepath = find_daily_bar_file(parse_market(market), code)
        bars = read_daily_bars(filepath)
    except Exception as e:
        print_error(str(e))
        raise SystemExit(1)

    if not bars:
        click.echo("未读取到数据，请确认通达信已下载该股票的日线数据")
        raise SystemExit(0)

    rows = [
        {
            "datetime": f"{b.year}-{b.month:02d}-{b.day:02d}",
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "vol": b.vol,
            "amount": b.amount,
        }
        for b in (bars[-count:] if count > 0 else bars)
    ]
    print_output(pd.DataFrame(rows), fmt)


@offline.command()
@click.argument("market")
@click.argument("code")
@click.option(
    "--type",
    "bar_type",
    type=click.Choice(["5min", "lc1", "lc5"]),
    default="5min",
    help="分钟线类型: 5min(.5) / lc1(.lc1) / lc5(.lc5)",
)
@click.option("--count", default=0, type=int, help="返回条数（0=全部）")
@click.option("--table", "use_table", is_flag=True, help="表格输出")
@click.option("--output", "output_fmt", type=click.Choice(["json", "table", "csv"]), default="json")
def min(
    market: str,
    code: str,
    bar_type: str,
    count: int,
    use_table: bool,
    output_fmt: str,
) -> None:
    """读取分钟线数据（.5 / .lc1 / .lc5 文件）。

    MARKET: 市场代码（SZ/SH）
    CODE: 6 位股票代码

    示例：

      easy-tdx offline min SZ 000001 --table

      easy-tdx offline min SH 600519 --type lc1 --count 100 --table
    """
    import pandas as pd

    from .output import print_error, print_output
    from .parsers import parse_market

    fmt = "table" if use_table else output_fmt

    try:
        from ..offline import read_5min_bars, read_lc_min_bars
        from ..offline.finders import find_5min_bar_file, find_lc1_bar_file, find_lc5_bar_file

        mkt = parse_market(market)
        if bar_type == "5min":
            filepath = find_5min_bar_file(mkt, code)
            bars = read_5min_bars(filepath)
        elif bar_type == "lc1":
            filepath = find_lc1_bar_file(mkt, code)
            bars = read_lc_min_bars(filepath)
        else:
            filepath = find_lc5_bar_file(mkt, code)
            bars = read_lc_min_bars(filepath)
    except Exception as e:
        print_error(str(e))
        raise SystemExit(1)

    if not bars:
        click.echo("未读取到数据，请确认通达信已下载该股票的分钟线数据")
        raise SystemExit(0)

    rows = [
        {
            "datetime": f"{b.year}-{b.month:02d}-{b.day:02d} {b.hour:02d}:{b.minute:02d}",
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "vol": b.vol,
            "amount": b.amount,
        }
        for b in (bars[-count:] if count > 0 else bars)
    ]
    print_output(pd.DataFrame(rows), fmt)


@offline.command("ex-files")
@click.option("--table", "use_table", is_flag=True, help="表格输出")
def ex_files(use_table: bool) -> None:
    """列出扩展市场可用的日线数据文件。

    文件位于 vipdoc/ds/lday/ 目录下。
    """
    import pandas as pd

    from ..offline.paths import detect_tdx_home
    from .output import print_error, print_output

    fmt = "table" if use_table else "json"

    tdx_home = detect_tdx_home()
    if tdx_home is None:
        print_error("未检测到通达信安装目录，请设置 TDX_HOME 环境变量")
        raise SystemExit(1)

    lday_dir = tdx_home / "vipdoc" / "ds" / "lday"
    if not lday_dir.is_dir():
        print_error(f"扩展市场目录不存在: {lday_dir}")
        raise SystemExit(1)

    files = sorted(lday_dir.glob("*.day"))
    if not files:
        click.echo("扩展市场目录为空，请在通达信中下载扩展市场数据")
        raise SystemExit(0)

    rows = [{"filename": f.name, "size_kb": round(f.stat().st_size / 1024, 1)} for f in files]
    print_output(pd.DataFrame(rows), fmt)


@offline.command("ex-daily")
@click.argument("filename")
@click.option("--count", default=0, type=int, help="返回条数（0=全部）")
@click.option("--table", "use_table", is_flag=True, help="表格输出")
@click.option("--output", "output_fmt", type=click.Choice(["json", "table", "csv"]), default="json")
def ex_daily(
    filename: str,
    count: int,
    use_table: bool,
    output_fmt: str,
) -> None:
    """读取扩展市场日线数据（期货/港股/外盘）。

    FILENAME: 文件名（如 29#A1801）或完整路径

    示例：

      easy-tdx offline ex-daily 29#A1801 --table

      easy-tdx offline ex-daily 12#A_IXIC --count 30 --table
    """
    from pathlib import Path

    import pandas as pd

    from ..offline import read_ex_daily_bars
    from ..offline.paths import detect_tdx_home
    from .output import print_error, print_output

    fmt = "table" if use_table else output_fmt

    filepath = Path(filename)
    if not filepath.is_file():
        tdx_home = detect_tdx_home()
        if tdx_home is not None:
            filepath = tdx_home / "vipdoc" / "ds" / "lday" / f"{filename}.day"

    try:
        bars = read_ex_daily_bars(filepath)
    except Exception as e:
        print_error(str(e))
        raise SystemExit(1)

    if not bars:
        click.echo("未读取到数据，请确认文件存在且已下载对应数据")
        raise SystemExit(0)

    rows = [
        {
            "datetime": f"{b.year}-{b.month:02d}-{b.day:02d}",
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "vol": b.vol,
            "settlement": b.settlement,
        }
        for b in (bars[-count:] if count > 0 else bars)
    ]
    print_output(pd.DataFrame(rows), fmt)


@offline.command()
@click.argument("filepath")
@click.option("--count", default=0, type=int, help="返回条数（0=全部）")
@click.option("--table", "use_table", is_flag=True, help="表格输出")
@click.option("--output", "output_fmt", type=click.Choice(["json", "table", "csv"]), default="json")
def gbbq(
    filepath: str,
    count: int,
    use_table: bool,
    output_fmt: str,
) -> None:
    """读取股本变迁数据（gbbq 文件）。

    FILEPATH: gbbq 文件路径

    示例：

      easy-tdx offline gbbq C:\\new_jyplug\\T0002\\hq_cache\\gbbq --table
    """
    import pandas as pd

    from ..offline import read_gbbq
    from .output import print_error, print_output

    fmt = "table" if use_table else output_fmt

    try:
        records = read_gbbq(filepath)
    except Exception as e:
        print_error(str(e))
        raise SystemExit(1)

    if not records:
        click.echo("未读取到数据")
        raise SystemExit(0)

    rows = [
        {
            "market": r.market,
            "code": r.code,
            "datetime": r.datetime,
            "category": r.category,
            "hongli_panqianliutong": r.hongli_panqianliutong,
            "peigujia_qianzongguben": r.peigujia_qianzongguben,
            "songgu_qianzongguben": r.songgu_qianzongguben,
            "peigu_houzongguben": r.peigu_houzongguben,
        }
        for r in (records[-count:] if count > 0 else records)
    ]
    print_output(pd.DataFrame(rows), fmt)


@offline.command()
@click.argument("filepath")
@click.option("--count", default=0, type=int, help="返回条数（0=全部）")
@click.option("--table", "use_table", is_flag=True, help="表格输出")
@click.option("--output", "output_fmt", type=click.Choice(["json", "table", "csv"]), default="json")
def financial(
    filepath: str,
    count: int,
    use_table: bool,
    output_fmt: str,
) -> None:
    """读取历史财务数据（gpcw*.dat / gpcw*.zip）。

    FILEPATH: 财务数据文件路径

    示例：

      easy-tdx offline financial C:\\new_jyplug\\vipdoc\\sz\\gpcw.zip --count 5 --table
    """
    import pandas as pd

    from ..offline import read_history_financial
    from .output import print_error, print_output

    fmt = "table" if use_table else output_fmt

    try:
        records = read_history_financial(filepath)
    except Exception as e:
        print_error(str(e))
        raise SystemExit(1)

    if not records:
        click.echo("未读取到数据")
        raise SystemExit(0)

    display = records[-count:] if count > 0 else records
    rows = [
        {
            "code": r.code,
            "market": r.market.value,
            "report_date": r.report_date,
        }
        for r in display
    ]
    print_output(pd.DataFrame(rows), fmt)


@offline.command()
@click.argument("block_dir")
@click.option("--table", "use_table", is_flag=True, help="表格输出")
@click.option("--output", "output_fmt", type=click.Choice(["json", "table", "csv"]), default="json")
def blocks(
    block_dir: str,
    use_table: bool,
    output_fmt: str,
) -> None:
    """读取自定义板块数据。

    BLOCK_DIR: 自定义板块目录路径（如 C:\\new_jyplug\\T0002\\blocknew）

    示例：

      easy-tdx offline blocks C:\\new_jyplug\\T0002\\blocknew --table
    """
    import pandas as pd

    from ..offline import read_customer_blocks
    from .output import print_error, print_output

    fmt = "table" if use_table else output_fmt

    try:
        result = read_customer_blocks(block_dir)
    except Exception as e:
        print_error(str(e))
        raise SystemExit(1)

    if not result:
        click.echo("未读取到板块数据")
        raise SystemExit(0)

    rows = [
        {
            "blockname": b.blockname,
            "type": b.block_type,
            "stock_count": len(b.codes),
            "codes": ",".join(b.codes[:10]) + ("..." if len(b.codes) > 10 else ""),
        }
        for b in result
    ]
    print_output(pd.DataFrame(rows), fmt)
