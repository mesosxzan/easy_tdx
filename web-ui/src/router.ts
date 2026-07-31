import { createRouter, createWebHistory } from 'vue-router'

import BacktestView from './views/BacktestView.vue'
import CompareView from './views/CompareView.vue'
import OptimizeView from './views/OptimizeView.vue'
import PortfolioView from './views/PortfolioView.vue'
import ServerSettingsView from './views/ServerSettingsView.vue'
import StrategiesView from './views/StrategiesView.vue'
import WencaiBacktestView from './views/WencaiBacktestView.vue'

// 单标的回测（/）+ 问财批量回测（/wencai-backtest）+ 组合回测（/portfolio）+ 参数寻优（/optimize）+ 结果对比（/compare）+ 策略库（/strategies）+ 服务器设置（/settings）。
const routes = [
  { path: '/', name: 'backtest', component: BacktestView },
  { path: '/wencai-backtest', name: 'wencai-backtest', component: WencaiBacktestView },
  { path: '/portfolio', name: 'portfolio', component: PortfolioView },
  { path: '/optimize', name: 'optimize', component: OptimizeView },
  { path: '/compare', name: 'compare', component: CompareView },
  { path: '/strategies', name: 'strategies', component: StrategiesView },
  { path: '/settings', name: 'settings', component: ServerSettingsView },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
