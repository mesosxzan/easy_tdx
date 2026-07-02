import { createRouter, createWebHistory } from 'vue-router'

import BacktestView from './views/BacktestView.vue'
import CompareView from './views/CompareView.vue'
import OptimizeView from './views/OptimizeView.vue'
import PortfolioView from './views/PortfolioView.vue'

// 单标的回测（/）+ 组合回测（/portfolio）+ 参数寻优（/optimize）+ 结果对比（/compare）。
const routes = [
  { path: '/', name: 'backtest', component: BacktestView },
  { path: '/portfolio', name: 'portfolio', component: PortfolioView },
  { path: '/optimize', name: 'optimize', component: OptimizeView },
  { path: '/compare', name: 'compare', component: CompareView },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
