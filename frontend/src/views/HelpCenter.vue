<template>
  <div class="gen-page help">
    <div class="gen-hero">
      <span class="gen-hero-icon">
        <el-icon :size="24"><QuestionFilled /></el-icon>
      </span>
      <div>
        <h1 class="gen-hero-title">帮助中心</h1>
        <p class="gen-hero-desc">
          了解各投放平台的定位与广告投递方式，并快速找到文案 / 图文 / 视频生成与基础设施的 API 配置位置。
        </p>
      </div>
    </div>

    <!-- 使用流程 -->
    <el-card class="gen-card" shadow="never">
      <template #header>整体使用流程</template>
      <div class="flow">
        <div v-for="(s, i) in steps" :key="s.title" class="flow-step">
          <div class="flow-index">{{ i + 1 }}</div>
          <div class="flow-body">
            <div class="flow-title">{{ s.title }}</div>
            <div class="flow-desc">{{ s.desc }}</div>
          </div>
        </div>
      </div>
    </el-card>

    <!-- 投放平台介绍 -->
    <el-card class="gen-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>当前投放平台介绍</span>
          <span class="hint">共 {{ platforms.length }} 个平台</span>
        </div>
      </template>
      <div class="plat-grid">
        <div
          v-for="p in platforms"
          :key="p.value"
          class="plat-card"
          :style="{ '--c': p.theme_color }"
        >
          <div class="plat-head">
            <PlatformLogo :logo-key="p.logo_key" :color="p.theme_color" :label="p.label" :size="46" />
            <div>
              <div class="plat-title">{{ p.label }}</div>
              <div class="plat-belong">{{ p.belong }} · {{ p.category }}</div>
            </div>
          </div>
          <p class="plat-intro">{{ p.intro }}</p>
          <div class="plat-features">
            <span v-for="f in p.features" :key="f" class="chip">{{ f }}</span>
          </div>
          <el-divider content-position="left">广告投递方式</el-divider>
          <ul class="plat-deliver">
            <li v-for="(d, i) in deliverWays(p.value)" :key="i">{{ d }}</li>
          </ul>
        </div>
      </div>
    </el-card>

    <!-- API 配置位置 -->
    <el-card class="gen-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>API 配置位置</span>
          <span class="hint">所有密钥均在项目根目录的 .env 中配置（参考 .env.example）</span>
        </div>
      </template>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="配置文件路径"
        description="backend/.env（实际生效） 与 .env.example（配置示例，含注释说明）。修改 .env 后需重启后端服务生效。"
        style="margin-bottom: 14px"
      />
      <el-table :data="configRows" size="small" style="width: 100%">
        <el-table-column prop="agent" label="模块 / 能力" width="180" />
        <el-table-column label="配置项" min-width="260">
          <template #default="{ row }">
            <span class="mono">{{ row.keys }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="file" label="所在文件" min-width="150">
          <template #default="{ row }"><span class="mono">{{ row.file }}</span></template>
        </el-table-column>
        <el-table-column prop="note" label="说明" min-width="260" />
      </el-table>
      <p class="gen-tip">
        提示：文案生成使用大模型文本 API（LLM_*）；图文生成 Agent 已独立出文生图 API（IMAGE_*），
        并在「充值中心」对应其账户点数；视频生成使用独立的图生视频第三方 API（VIDEO_*）。
      </p>
    </el-card>

    <!-- 常见问题 -->
    <el-card class="gen-card" shadow="never">
      <template #header>常见问题</template>
      <el-collapse>
        <el-collapse-item v-for="q in faqs" :key="q.q" :title="q.q">
          <div class="faq-a">{{ q.a }}</div>
        </el-collapse-item>
      </el-collapse>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'
import PlatformLogo from '@/components/PlatformLogo.vue'
import { listDeliveryPlatforms } from '@/api'

const platforms = ref([])

const steps = [
  { title: '① 生成广告物料', desc: '在「广告生成」选择商品（可自定义）与可选关键词，一键生成爆款文案，再基于文案生成图文 / 视频；也可在「批量生成」中按分叉树一次生成多条。' },
  { title: '② 存到投放界面', desc: '生成完成后点击「存到投放界面」，物料进入广告流水线，并写入历史记录。' },
  { title: '③ 审查中心审批', desc: '物料依次经过违禁词审查、投放预算审查，通过后进入「待审批」，审批通过后方可投放。' },
  { title: '④ 广告投放', desc: '在「广告投放」选择目标平台，勾选已审批广告并填写预算（需在审查区间内），一键投放（Mock 模拟）。' },
  { title: '⑤ 历史广告发售', desc: '投放成功的广告进入历史广告发售，可再次投放到其他平台或进行归档查看。' },
]

const DELIVER_WAYS = {
  qianchuan: [
    '在「巨量千川」后台创建推广计划，选择「短视频带货 / 直播间直投」。',
    '上传图文或视频物料，绑定商品与抖音小店，设置出价与预算。',
    '提交后由平台算法在信息流 / 搜索 / 商城场景分发。',
  ],
  juguang: [
    '在「聚光平台」选择「笔记推广」或「搜索推广」投放类型。',
    '提交笔记式图文 / 视频物料，设置人群定向与兴趣标签。',
    '可通过平台达人合作放大种草效果，转化数据回流聚光后台。',
  ],
  alimama: [
    '在「阿里妈妈」创建计划，选择万相台 / 直通车 / 引力魔方等场景。',
    '上传商品图文 / 视频创意，关联淘宝天猫商品链接。',
    '设置关键词与出价，素材在站内搜索与推荐场景展示。',
  ],
  duoduo: [
    '在「多多进宝 / 多多场景」创建推广，选择多多搜索或场景推广。',
    '上传创意素材并绑定商品，参与百亿补贴等资源位。',
    '结合拼团裂变与社交分享，快速为商品累积初始销量。',
  ],
}
const deliverWays = (key) => DELIVER_WAYS[key] || ['在对应平台广告后台创建推广计划，上传物料并绑定商品后提交投放。']

const configRows = [
  { agent: '文案生成 Agent', keys: 'LLM_API_KEY / LLM_BASE_URL / LLM_MODEL', file: '.env', note: '文案广告所用的大模型文本 API（BaseURL 填 API 域名）' },
  { agent: '图文生成 Agent', keys: 'IMAGE_PROVIDER / IMAGE_API_KEY / IMAGE_API_BASE_URL / IMAGE_API_MODEL', file: '.env', note: '独立文生图 API；PROVIDER=local 时用本地渲染，无需 Key' },
  { agent: '视频生成 Agent', keys: 'VIDEO_PROVIDER / VIDEO_API_KEY / VIDEO_API_BASE_URL / VIDEO_API_MODEL', file: '.env', note: '独立图生视频第三方 API（kling / aliyun）' },
  { agent: '相似案例检索', keys: 'EMBEDDING_API_BASE / EMBEDDING_API_KEY / EMBEDDING_BATCH_SIZE', file: '.env', note: '文案 Agent 的向量化检索（Embedding）' },
  { agent: '知识库 / 队列', keys: 'MILVUS_URI / REDIS_URL', file: '.env', note: 'Milvus 向量库与 Redis 连接地址' },
  { agent: 'MCP / ERP', keys: 'ERP_BASE_URL / ERP_API_KEY / OCEANENGINE_APP_KEY', file: '.env', note: '外部 ERP 与投放平台对接凭证' },
  { agent: '前端后端地址', keys: 'BACKEND_TARGET', file: 'frontend/vite.config.js', note: '前端 /api、/ws、/media 反向代理指向的后端地址' },
  { agent: '跨域白名单', keys: 'ALLOWED_ORIGINS', file: '.env', note: '后端允许的前端来源（开发环境）' },
]

const faqs = [
  { q: '投放是真实投放吗？', a: '当前为演示环境，投放为 Mock 模拟，不会产生真实扣费，也不会上传到真实广告平台后台。' },
  { q: '为什么广告投不出去？', a: '仅「审批通过」的广告可进入广告投放；同时投放预算需落在审查区间内（默认 ¥1,000 ~ ¥100,000）。' },
  { q: '图文生成的 API 在哪里配置？', a: '在项目根目录 .env 中配置 IMAGE_* 项（参考 .env.example 的「图文生成 Agent」段落）。默认 IMAGE_PROVIDER=local 使用本地渲染。' },
  { q: '一键生成最多能同时跑多少个？', a: '批量生成中，单次生成图文 / 视频的数量上限为 5 个，超出会被限制。' },
]

async function loadPlatforms() {
  try {
    platforms.value = (await listDeliveryPlatforms()) || []
  } catch {
    platforms.value = []
  }
}

onMounted(loadPlatforms)
</script>

<style scoped>
.card-head { display: flex; align-items: center; justify-content: space-between; }
.card-head .hint { font-size: 12px; color: #909399; font-weight: 400; }
.mono { font-family: Consolas, Monaco, monospace; font-size: 12px; }

.flow { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
.flow-step { display: flex; gap: 12px; border: 1px solid var(--card-border); border-radius: 12px; padding: 14px 16px; background: #fafbfd; }
.flow-index {
  flex-shrink: 0; width: 26px; height: 26px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: #2e6bff; color: #fff; font-size: 13px; font-weight: 700;
}
.flow-title { font-size: 14px; font-weight: 700; color: #1d2b4f; margin-bottom: 4px; }
.flow-desc { font-size: 12.5px; color: #606266; line-height: 1.7; }

.plat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
.plat-card {
  border: 1px solid var(--card-border); border-top: 4px solid var(--c);
  border-radius: 14px; padding: 18px 20px; background: #fff;
}
.plat-head { display: flex; align-items: center; gap: 12px; }
.plat-title { font-size: 16px; font-weight: 700; color: #1d2b4f; }
.plat-belong { font-size: 12px; color: #909399; margin-top: 2px; }
.plat-intro { font-size: 13px; color: #606266; line-height: 1.7; margin: 12px 0 10px; }
.plat-features { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font-size: 12px; padding: 2px 9px; border-radius: 10px; background: #f4f6fa; color: #5a6b8c; }
.plat-deliver { margin: 0; padding-left: 18px; }
.plat-deliver li { font-size: 12.5px; color: #606266; line-height: 1.9; }

.faq-a { font-size: 13px; color: #606266; line-height: 1.8; }
</style>