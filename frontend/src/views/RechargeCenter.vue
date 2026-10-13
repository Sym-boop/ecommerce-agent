<template>
  <div class="gen-page recharge">
    <div class="gen-hero">
      <span class="gen-hero-icon">
        <el-icon :size="24"><Wallet /></el-icon>
      </span>
      <div>
        <h1 class="gen-hero-title">充值中心</h1>
        <p class="gen-hero-desc">
          用于「图文生成 Agent」独立 API 的账户额度充值与账单查询。当前为演示环境，充值为模拟记账，不产生真实扣费。
        </p>
      </div>
    </div>

    <!-- 账户概览 -->
    <el-card class="gen-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>账户概览</span>
          <span class="account-id mono">{{ account.account_id }}</span>
        </div>
      </template>
      <div class="account-grid">
        <div class="account-item">
          <span class="account-label">账户余额</span>
          <div class="account-value">¥{{ money(account.balance) }}</div>
        </div>
        <div class="account-item">
          <span class="account-label">可用点数</span>
          <div class="account-value accent">{{ num(account.credits) }}</div>
        </div>
        <div class="account-item">
          <span class="account-label">累计充值</span>
          <div class="account-value">¥{{ money(account.total_recharged) }}</div>
        </div>
        <div class="account-item">
          <span class="account-label">更新时间</span>
          <div class="account-value small">{{ formatTime(account.updated_at) }}</div>
        </div>
      </div>
      <el-divider content-position="left">计费口径（每次生成消耗点数）</el-divider>
      <div class="unit-cost">
        <div class="unit-item">
          <el-icon><Picture /></el-icon>
          <span>图文生成</span>
          <b>{{ account.unit_cost?.image ?? '-' }} 点 / 张</b>
        </div>
        <div class="unit-item">
          <el-icon><VideoCamera /></el-icon>
          <span>视频生成</span>
          <b>{{ account.unit_cost?.video ?? '-' }} 点 / 条</b>
        </div>
        <div class="unit-item">
          <el-icon><EditPen /></el-icon>
          <span>文案生成</span>
          <b>{{ account.unit_cost?.copy ?? '-' }} 点 / 条</b>
        </div>
      </div>
    </el-card>

    <!-- 充值套餐 -->
    <el-card class="gen-card" shadow="never">
      <template #header>充值套餐</template>
      <div class="pkg-grid">
        <div
          v-for="p in packages"
          :key="p.id"
          class="pkg-card"
          :class="{ 'is-loading': paying === p.id }"
        >
          <div class="pkg-top">
            <span class="pkg-label">{{ p.label }}</span>
            <el-tag v-if="p.tag" size="small" type="danger" effect="dark">{{ p.tag }}</el-tag>
          </div>
          <div class="pkg-amount">¥{{ num(p.amount) }}</div>
          <div class="pkg-credits">
            <b>{{ num(p.credits) }}</b> 点数
            <span v-if="p.bonus_rate" class="pkg-bonus">赠 {{ p.bonus_rate }}%</span>
          </div>
          <el-button
            type="primary"
            size="large"
            :icon="Wallet"
            :loading="paying === p.id"
            @click="handleRecharge(p)"
          >
            立即充值
          </el-button>
        </div>
      </div>
    </el-card>

    <!-- 账单流水 -->
    <el-card class="gen-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>账单流水（{{ invoices.length }}）</span>
          <el-button size="small" :icon="Refresh" @click="loadAll">刷新</el-button>
        </div>
      </template>
      <el-empty v-if="invoices.length === 0" description="暂无充值 / 消费流水" :image-size="80" />
      <el-table v-else :data="invoices" size="small" style="width: 100%">
        <el-table-column prop="id" label="流水号" min-width="130">
          <template #default="{ row }"><span class="mono">{{ row.id }}</span></template>
        </el-table-column>
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.type === 'recharge' ? 'success' : 'info'" effect="plain">
              {{ row.type === 'recharge' ? '充值' : '消费' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="说明" min-width="160" />
        <el-table-column label="金额" width="110" align="right">
          <template #default="{ row }">
            {{ row.amount ? '¥' + num(row.amount) : '—' }}
          </template>
        </el-table-column>
        <el-table-column label="点数" width="120" align="right">
          <template #default="{ row }">
            <span :class="row.credits >= 0 ? 'plus' : 'minus'">
              {{ row.credits >= 0 ? '+' : '' }}{{ num(row.credits) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column label="时间" min-width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Wallet, Refresh, Picture, VideoCamera, EditPen } from '@element-plus/icons-vue'
import { getBillingAccount, listBillingPackages, listBillingInvoices, rechargeAccount } from '@/api'
import { formatTime } from '@/utils/format'

const account = reactive({
  account_id: '-',
  balance: 0,
  credits: 0,
  total_recharged: 0,
  updated_at: '',
  unit_cost: {},
})
const packages = ref([])
const invoices = ref([])
const paying = ref('')

const num = (v) => Number(v || 0).toLocaleString()
const money = (v) => Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })

async function loadAccount() {
  try {
    const data = await getBillingAccount()
    Object.assign(account, data || {})
  } catch {
    /* 保持默认值 */
  }
}

async function loadPackages() {
  try {
    packages.value = (await listBillingPackages()) || []
  } catch {
    packages.value = []
  }
}

async function loadInvoices() {
  try {
    invoices.value = (await listBillingInvoices()) || []
  } catch {
    invoices.value = []
  }
}

async function loadAll() {
  await Promise.all([loadAccount(), loadPackages(), loadInvoices()])
}

async function handleRecharge(pkg) {
  const credits = Math.round(pkg.credits * (1 + (pkg.bonus_rate || 0) / 100))
  try {
    await ElMessageBox.confirm(
      `确认充值「${pkg.label}」¥${num(pkg.amount)}，到账 ${num(credits)} 点数？`,
      '充值确认',
      { type: 'info', confirmButtonText: '确认充值', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  paying.value = pkg.id
  try {
    const res = await rechargeAccount({ package_id: pkg.id })
    if (res?.account) Object.assign(account, res.account)
    ElMessage.success(res?.message || '充值成功')
    await loadInvoices()
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '充值失败，请稍后重试')
  } finally {
    paying.value = ''
  }
}

onMounted(loadAll)
</script>

<style scoped>
.card-head { display: flex; align-items: center; justify-content: space-between; }
.account-id { font-size: 12px; color: #909399; }

.account-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
}
.account-item {
  border: 1px solid var(--card-border);
  border-radius: 12px;
  padding: 16px 18px;
  background: linear-gradient(160deg, #ffffff, #f6f9ff);
}
.account-label { font-size: 12px; color: #909399; }
.account-value { font-size: 26px; font-weight: 700; color: #1d2b4f; margin-top: 6px; }
.account-value.accent { color: #2e6bff; }
.account-value.small { font-size: 15px; font-weight: 600; color: #4b5563; }

.unit-cost { display: flex; gap: 12px; flex-wrap: wrap; }
.unit-item {
  display: inline-flex; align-items: center; gap: 8px;
  border: 1px solid var(--card-border); border-radius: 10px;
  padding: 10px 16px; font-size: 13px; color: #4b5563; background: #fafbfd;
}
.unit-item b { color: #1d2b4f; }

.pkg-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 16px;
}
.pkg-card {
  border: 1px solid var(--card-border);
  border-radius: 14px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: #fff;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.pkg-card:hover { transform: translateY(-3px); box-shadow: 0 12px 26px rgba(46, 107, 255, 0.14); }
.pkg-top { display: flex; align-items: center; justify-content: space-between; }
.pkg-label { font-size: 15px; font-weight: 700; color: #1d2b4f; }
.pkg-amount { font-size: 26px; font-weight: 700; color: #2e6bff; }
.pkg-credits { font-size: 13px; color: #606266; }
.pkg-credits b { font-size: 16px; color: #1d2b4f; }
.pkg-bonus {
  margin-left: 8px; font-size: 12px; color: #f56c6c;
  border: 1px solid #f56c6c55; border-radius: 8px; padding: 1px 7px;
}

.plus { color: #67c23a; font-weight: 600; }
.minus { color: #f56c6c; font-weight: 600; }
.mono { font-family: Consolas, Monaco, monospace; font-size: 12px; }
</style>