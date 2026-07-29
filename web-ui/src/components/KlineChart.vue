<script setup lang="ts">
// 专业K线图：主图(蜡烛+MA均线+买卖标注) + 成交量副图。
// 双 grid 布局共享 dataZoom 联动，买卖点标注"买"/"卖"文字。
// 核心：trades 的 datetime 对齐到 K线时间轴--按 datetime 字符串建 index map。

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import echarts, { DOWN_COLOR, UP_COLOR } from '../echarts-setup'
import { fmt2 } from '../format'
import type { Bar, Trade } from '../types'

const props = defineProps<{
  bars: Bar[]
  trades: Trade[]
}>()

const container = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null

// 均线颜色（与 A 股主流行情软件一致）
const MA5_COLOR = '#e6a700'
const MA10_COLOR = '#b06ae3'
const MA20_COLOR = '#4a9eff'

/** 简单移动平均：滑动窗口 O(n)，前 period-1 个为 null。 */
function calcMA(closes: number[], period: number): (number | null)[] {
  const result: (number | null)[] = []
  let sum = 0
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i]
    if (i >= period) sum -= closes[i - period]
    result.push(i >= period - 1 ? sum / period : null)
  }
  return result
}

/** ECharts axis tooltip 回调参数（trigger:'axis' 时为数组）。 */
interface AxisTooltipParam {
  axisValue: string
  seriesName: string
  value: number | number[] | null
  dataIndex: number
}

function render() {
  if (!container.value || props.bars.length === 0) return
  chart ??= echarts.init(container.value, 'dark')
  chart.setOption(buildOption(), true)
}

/** 构建 ECharts 配置。双 grid 布局，trades 对齐到 K 线 index。 */
function buildOption(): echarts.EChartsCoreOption {
  const bars = props.bars
  const keys = bars.map((b) => b.datetime)
  const keyIndex = new Map<string, number>()
  keys.forEach((k, i) => keyIndex.set(k, i))

  // 判断分钟线：datetime 有非零时分秒（日线归一化后是 T00:00:00）
  const isIntraday = keys.some((k) => {
    const time = k.slice(11, 19)
    return time && time !== '00:00:00'
  })
  const dates = keys.map((k) =>
    isIntraday ? k.replace('T', ' ').slice(5, 16) : k.slice(0, 10),
  )

  // K线 OHLC [open, close, low, high]
  const ohlc = bars.map((b) => [b.open, b.close, b.low, b.high])

  // 均线
  const closes = bars.map((b) => b.close)
  const ma5 = calcMA(closes, 5)
  const ma10 = calcMA(closes, 10)
  const ma20 = calcMA(closes, 20)

  // 成交量：颜色随 K 线涨跌（close >= open 红，反之绿）
  const volData = bars.map((b) => ({
    value: b.vol,
    itemStyle: { color: b.close >= b.open ? UP_COLOR : DOWN_COLOR },
  }))

  // 买卖点 markPoint：买入在 K 线 low 下方标"买"，卖出在 high 上方标"卖"
  const markPoints: Array<Record<string, unknown>> = []
  for (const t of props.trades) {
    if (t.rejected) continue
    // 归一化 trade datetime 与 bar key 同格式
    const tKey = t.datetime.slice(0, 19).replace(' ', 'T')
    let idx = keyIndex.get(tKey)
    if (idx === undefined) {
      // 引擎 trade 精度不足（日线回测分钟线场景）：退回按日期首根 bar 匹配
      const dayPrefix = tKey.slice(0, 10)
      idx = keys.findIndex((k) => k.startsWith(dayPrefix))
      if (idx === -1) continue
    }
    const bar = bars[idx]
    const isBuy = t.direction === 'BUY'
    markPoints.push({
      name: isBuy ? '买' : '卖',
      coord: [idx, isBuy ? bar.low : bar.high],
      symbol: 'triangle',
      symbolSize: 12,
      symbolRotate: isBuy ? 0 : 180,
      // 像素偏移让三角形完全在 K 线影线外侧
      symbolOffset: [0, isBuy ? 8 : -8],
      itemStyle: { color: isBuy ? UP_COLOR : DOWN_COLOR },
      label: {
        show: true,
        formatter: isBuy ? '买' : '卖',
        position: isBuy ? 'bottom' : 'top',
        distance: 2,
        color: '#fff',
        fontSize: 11,
        fontWeight: 'bold',
        backgroundColor: isBuy ? UP_COLOR : DOWN_COLOR,
        borderRadius: 3,
        padding: [2, 5],
      },
    })
  }

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'cross',
        link: [{ xAxisIndex: 'all' }],
      },
      formatter: (params: unknown) =>
        formatTooltip(params as AxisTooltipParam[], bars),
    },
    legend: {
      data: ['K线', 'MA5', 'MA10', 'MA20'],
      top: 0,
      textStyle: { fontSize: 11 },
    },
    // 双 grid：主图 55% + 间距 5% + 成交量 18% + 底部 dataZoom
    grid: [
      { left: '8%', right: '3%', top: '8%', height: '55%' },
      { left: '8%', right: '3%', top: '68%', height: '18%' },
    ],
    xAxis: [
      {
        type: 'category',
        data: dates,
        boundaryGap: true,
        axisLine: { onZero: false },
        splitLine: { show: false },
        // 主图 x 轴不显示刻度（由副图统一显示）
        axisLabel: { show: false },
      },
      {
        type: 'category',
        data: dates,
        gridIndex: 1,
        boundaryGap: true,
        axisLine: { onZero: false },
        splitLine: { show: false },
        axisLabel: { formatter: (v: string) => v },
      },
    ],
    yAxis: [
      {
        scale: true,
        gridIndex: 0,
        splitLine: { lineStyle: { color: '#2a2e3a' } },
        axisLabel: { formatter: (v: number) => fmt2(v) },
      },
      {
        gridIndex: 1,
        splitNumber: 2,
        splitLine: { show: false },
        axisLabel: { show: false },
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
      {
        type: 'slider',
        xAxisIndex: [0, 1],
        bottom: 5,
        start: 60,
        end: 100,
        height: 20,
      },
    ],
    series: [
      {
        name: 'K线',
        type: 'candlestick',
        data: ohlc,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: {
          color: UP_COLOR,
          color0: DOWN_COLOR,
          borderColor: UP_COLOR,
          borderColor0: DOWN_COLOR,
        },
        markPoint: {
          data: markPoints,
          animation: false,
        },
      },
      {
        name: 'MA5',
        type: 'line',
        data: ma5,
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: 'none',
        lineStyle: { width: 1, color: MA5_COLOR },
        zlevel: 1,
      },
      {
        name: 'MA10',
        type: 'line',
        data: ma10,
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: 'none',
        lineStyle: { width: 1, color: MA10_COLOR },
        zlevel: 1,
      },
      {
        name: 'MA20',
        type: 'line',
        data: ma20,
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: 'none',
        lineStyle: { width: 1, color: MA20_COLOR },
        zlevel: 1,
      },
      {
        name: '成交量',
        type: 'bar',
        data: volData,
        xAxisIndex: 1,
        yAxisIndex: 1,
      },
    ],
  }
}

/** Tooltip 自定义格式化：日期 + OHLC + 涨跌幅 + 成交量 + 均线值。 */
function formatTooltip(params: AxisTooltipParam[], bars: Bar[]): string {
  if (!params || params.length === 0) return ''
  const idx = params[0].dataIndex
  const bar = bars[idx]
  if (!bar) return ''

  const change = bar.open !== 0 ? ((bar.close - bar.open) / bar.open) * 100 : 0
  const changeColor = change >= 0 ? UP_COLOR : DOWN_COLOR
  const changeStr = `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`

  // 从 tooltip params 中提取均线值（MA series 的 value）
  const maColors: Record<string, string> = {
    MA5: MA5_COLOR,
    MA10: MA10_COLOR,
    MA20: MA20_COLOR,
  }
  const maLines: string[] = []
  for (const p of params) {
    if (p.seriesName in maColors && p.value !== null && typeof p.value === 'number') {
      maLines.push(
        `<span style="color:${maColors[p.seriesName]}">${p.seriesName} ${fmt2(p.value)}</span>`,
      )
    }
  }

  return [
    `<div style="font-size:12px;line-height:1.7">`,
    `<div style="color:#8b919e;margin-bottom:2px">${params[0].axisValue}</div>`,
    `<div>开 <b>${fmt2(bar.open)}</b>&nbsp;&nbsp;高 <b style="color:${UP_COLOR}">${fmt2(bar.high)}</b>&nbsp;&nbsp;低 <b style="color:${DOWN_COLOR}">${fmt2(bar.low)}</b>&nbsp;&nbsp;收 <b style="color:${changeColor}">${fmt2(bar.close)}</b></div>`,
    `<div>涨跌 <b style="color:${changeColor}">${changeStr}</b>&nbsp;&nbsp;量 <b>${bar.vol.toLocaleString()}</b></div>`,
    maLines.length ? `<div>${maLines.join('&nbsp;&nbsp;')}</div>` : '',
    `</div>`,
  ].join('')
}

function resize() {
  chart?.resize()
}

onMounted(() => {
  render()
  window.addEventListener('resize', resize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
  chart = null
})
watch(() => [props.bars, props.trades], render)
</script>

<template>
  <div ref="container" class="kline-chart"></div>
</template>

<style scoped>
.kline-chart {
  width: 100%;
  height: 560px;
}
</style>
