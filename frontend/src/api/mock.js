// ============================================
// 前端内置 Mock 层
// 作用：成员B/C 的 FastAPI 与 WebSocket 未就绪前，前端可独立联调演示。
// 后端就绪后将 .env.development 的 VITE_USE_MOCK 置为 false 即可无缝切换。
//
// 包含三部分：
// 1. bus：模拟服务端事件总线（驱动 MockWebSocket）
// 2. mockAdapter：axios 适配器，实现与后端一致的 REST 契约
// 3. MockSocket：模拟 WebSocket（任务状态 / 熔断审批事件实时推送）
// ============================================

export const USE_MOCK = false

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const nowIso = () => new Date().toISOString()
let seq = 1
const nextId = (prefix) => `${prefix}-${Date.now().toString(36).slice(-6)}${seq++}`

// ---------- 极简事件总线 ----------
class EventBus {
  constructor() {
    this.listeners = new Map()
  }
  on(event, handler) {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set())
    this.listeners.get(event).add(handler)
    return () => this.listeners.get(event)?.delete(handler)
  }
  emit(event, data) {
    this.listeners.get(event)?.forEach((fn) => fn(data))
  }
}
export const bus = new EventBus()

// ---------- 内存数据 ----------
const tasks = new Map()
const approvals = new Map()
// approvalId -> resolve 函数，模拟 Handoff 节点等待人工决策
const approvalResolvers = new Map()
const CURRENT_OPERATOR = '当前登录运营'

const log = (node, level, msg) => {
  node.logs.push({ ts: nowIso(), level, msg })
}

function patchNode(task, nodeId, patch) {
  const node = task.nodes.find((n) => n.id === nodeId)
  Object.assign(node, patch)
  bus.emit('node:update', { task_id: task.id, node_id: nodeId, patch })
  bus.emit('task:update', { task_id: task.id, status: task.status })
  return node
}

// ---------- 预置历史数据 ----------
function seedHistory() {
  const oldTask = {
    id: 'TASK-20260922-001',
    goal: '为新款降噪耳机做亚马逊Listing推广并跟进竞品调价',
    status: 'done',
    budget: 30000,
    created_at: '2026-09-22T09:30:00.000Z',
    nodes: [
      { id: 'n-sup', type: 'supervisor', name: 'Supervisor 主控', status: 'success', started_at: '2026-09-22T09:30:01.000Z', finished_at: '2026-09-22T09:30:03.000Z', input: { goal: '为新款降噪耳机做亚马逊Listing推广并跟进竞品调价' }, output: { sub_tasks: ['copywriter', 'pricing'] }, error: null, logs: [
        { ts: '2026-09-22T09:30:01.000Z', level: 'info', msg: '接收运营目标，开始任务拆解' },
        { ts: '2026-09-22T09:30:03.000Z', level: 'success', msg: '拆解完成：文案生成、动态定价 2 个子任务' },
      ] },
      { id: 'n-copy', type: 'copywriter', name: '爆款文案 Agent', status: 'success', started_at: '2026-09-22T09:30:04.000Z', finished_at: '2026-09-22T09:30:11.000Z', input: { platform: 'amazon', language: 'en-US' }, output: { platforms: 3, title: 'Active Noise Cancelling Wireless Earbuds Pro 3' }, error: null, logs: [
        { ts: '2026-09-22T09:30:04.000Z', level: 'info', msg: 'Milvus 向量检索 Top10 高转化 Listing 参考文案（128ms）' },
        { ts: '2026-09-22T09:30:09.000Z', level: 'info', msg: '违禁词扫描完成，命中 0 处' },
        { ts: '2026-09-22T09:30:11.000Z', level: 'success', msg: '亚马逊英文 Listing 草稿生成完成' },
      ] },
      { id: 'n-price', type: 'pricing', name: '动态定价 Agent', status: 'success', started_at: '2026-09-22T09:30:04.000Z', finished_at: '2026-09-22T09:30:13.000Z', input: { sku: 'ER-BUDS-PRO3' }, output: { action: '维持', confidence: 0.72 }, error: null, logs: [
        { ts: '2026-09-22T09:30:04.000Z', level: 'info', msg: '通过 ERP MCP 拉取 5 家竞品价格' },
        { ts: '2026-09-22T09:30:13.000Z', level: 'success', msg: '波动在 ±3% 阈值内，建议维持现价 299 元' },
      ] },
      { id: 'n-handoff', type: 'handoff', name: '人工审批 Handoff', status: 'success', started_at: '2026-09-22T09:30:14.000Z', finished_at: '2026-09-22T09:45:02.000Z', input: null, output: { resolved: 1 }, error: null, logs: [
        { ts: '2026-09-22T09:30:14.000Z', level: 'warn', msg: '文案为重大发布，阻断执行并推送人工审批' },
        { ts: '2026-09-22T09:45:02.000Z', level: 'success', msg: '运营主管审批通过，流程继续' },
      ] },
      { id: 'n-end', type: 'end', name: '执行完成', status: 'success', started_at: '2026-09-22T09:45:03.000Z', finished_at: '2026-09-22T09:45:03.000Z', input: null, output: null, error: null, logs: [
        { ts: '2026-09-22T09:45:03.000Z', level: 'success', msg: '任务全部完成' },
      ] },
    ],
    edges: [
      { source: 'n-sup', target: 'n-copy', label: '文案任务', kind: 'normal' },
      { source: 'n-sup', target: 'n-price', label: '定价任务', kind: 'normal' },
      { source: 'n-copy', target: 'n-handoff', label: '重大发布', kind: 'condition' },
      { source: 'n-price', target: 'n-handoff', label: '超阈值幅度', kind: 'condition' },
      { source: 'n-handoff', target: 'n-end', label: '', kind: 'normal' },
    ],
  }
  tasks.set(oldTask.id, oldTask)

  const history = [
    {
      id: 'APV-20260922-001', type: 'copy', status: 'approved', task_id: oldTask.id, created_at: '2026-09-22T09:30:14.000Z', handled_at: '2026-09-22T09:45:02.000Z', handler: '王运营', comment: '标题再精炼一点，同意发布',
      sku: 'ER-BUDS-PRO3', product_name: '降噪蓝牙耳机 Pro3', platform: 'amazon', language: 'en-US',
      title: 'Active Noise Cancelling Wireless Earbuds Pro 3',
      content: 'Experience immersive sound with hybrid ANC, 40h battery and IPX5 waterproof. #Earbuds #Tech',
      violations: [], versions: [{ version: 1, content: 'Experience immersive sound with hybrid ANC, 40h battery and IPX5 waterproof. #Earbuds #Tech', editor: 'copywriter-agent', created_at: '2026-09-22T09:30:14.000Z' }],
    },
    {
      id: 'APV-20260921-002', type: 'ads', status: 'rejected', task_id: 'TASK-20260921-008', created_at: '2026-09-21T16:10:00.000Z', handled_at: '2026-09-21T16:22:00.000Z', handler: '李总监', comment: '总预算超出大促预算池，驳回请重新分配渠道比例',
      campaign_name: '99大促-千川信息流加投', platform: '巨量千川', bid: 4.2, bid_threshold: 2.0, daily_budget: 30000, total_budget: 90000, budget_threshold: 50000, threshold_exceeded: true,
      allocation: [], roi_forecast: 2.6, expected_clicks: '6.2万', reason: '竞价上行，申请提高出价',
    },
    {
      id: 'APV-20260920-003', type: 'pricing', status: 'approved', task_id: 'TASK-20260920-005', created_at: '2026-09-20T11:05:00.000Z', handled_at: '2026-09-20T11:18:00.000Z', handler: '王运营', comment: '同意跟价',
      sku: 'SK-PROTECT-SPF50', product_name: '轻透防晒隔离霜 SPF50+', current_price: 129, suggested_price: 119, change_percent: -7.75, action: '降价', confidence: 0.81, threshold_percent: 10, threshold_exceeded: false,
      competitors: [{ name: '竞品X 自营店', price: 119, shipping: 0 }], reason: '竞品促销降价至 119 元', expected_revenue: '预计周销量 +12%', risk_level: 'low', risk_note: '毛利率 41%，处于安全区间',
    },
  ]
  history.forEach((item) => approvals.set(item.id, item))
}
seedHistory()

// ---------- 审批单构造 ----------
function makeCopyApproval(task) {
  const content =
    '姐妹们！今年春天的第一条碎花裙我先冲了🙈\n' +
    '法式 V 领 + 雪纺面料，上身巨显瘦，微胖女孩也能轻松驾驭～\n' +
    '腰线剪裁很绝，160cm 穿 M 码长度刚好到小腿肚，搭针织开衫或小西装都好看。\n' +
    '现在新品限时 8 折，只要 239，喜欢的姐妹评论区扣 1 发链接！'
  return {
    id: nextId('APV-COPY'),
    type: 'copy',
    status: 'pending',
    task_id: task.id,
    priority: 'high',
    created_at: nowIso(),
    handled_at: null,
    handler: null,
    comment: '',
    sku: 'FX-DRESS-2026-007',
    product_name: '法式碎花雪纺连衣裙',
    platform: 'xiaohongshu',
    language: 'zh-CN',
    title: '春天的第一条碎花裙｜温柔到犯规🌸',
    content,
    violations: [
      { word: '巨显瘦', type: '绝对化效果承诺', suggestion: '视觉上很显身材' },
      { word: '只要', type: '诱导性低价用语', suggestion: '到手价' },
    ],
    versions: [{ version: 1, content, editor: 'copywriter-agent', created_at: nowIso() }],
  }
}

function makePricingApproval(task) {
  return {
    id: nextId('APV-PRICE'),
    type: 'pricing',
    status: 'pending',
    task_id: task.id,
    priority: 'high',
    created_at: nowIso(),
    handled_at: null,
    handler: null,
    comment: '',
    sku: 'ER-BUDS-PRO3',
    product_name: '降噪蓝牙耳机 Pro3',
    current_price: 299,
    suggested_price: 265,
    change_percent: -11.37,
    action: '降价',
    confidence: 0.87,
    threshold_percent: 10,
    threshold_exceeded: true,
    competitors: [
      { name: '竞品A 京东自营', price: 269, shipping: 0 },
      { name: '竞品B 天猫旗舰', price: 279, shipping: 0 },
      { name: '竞品C 抖音商城', price: 285, shipping: 8 },
    ],
    reason:
      '竞品A 在 72 小时内连续两次下调至 269 元，低于本品当前价 10.0%；同时近 7 日本品转化率下滑 12%。建议跟进降价至 265 元，利用价格保护期内的跟价策略稳住搜索排名。',
    expected_revenue: '预计周销量 +18%，周 GMV 增加约 4.2 万元',
    risk_level: 'medium',
    risk_note: '调价后毛利率由 38% 降至 34%，仍高于 30% 毛利保护线',
  }
}

function makeAdsApproval(task) {
  return {
    id: nextId('APV-ADS'),
    type: 'ads',
    status: 'pending',
    task_id: task.id,
    priority: 'critical',
    created_at: nowIso(),
    handled_at: null,
    handler: null,
    comment: '',
    campaign_name: '黑五新品冲刺-千川信息流',
    platform: '巨量千川',
    bid: 3.5,
    bid_threshold: 2.0,
    daily_budget: 20000,
    total_budget: task.budget || 80000,
    budget_threshold: 50000,
    threshold_exceeded: true,
    allocation: [
      { channel: '千川信息流', percent: 60, amount: 48000 },
      { channel: '达人种草内容加热', percent: 25, amount: 20000 },
      { channel: '品牌搜索广告', percent: 15, amount: 12000 },
    ],
    roi_forecast: 3.2,
    expected_clicks: '5.6 万',
    reason:
      '大促期间竞价流量成本上涨约 40%，系统测算出价 3.5 元可保障目标曝光量；总预算超过 5 万元风控阈值，触发熔断，需人工确认后下发广告计划。',
  }
}

// ---------- 任务构造与生命周期模拟 ----------
const COPY_KEYWORDS = ['文案', '推广', '小红书', 'TikTok', '亚马逊', '种草', '图文', 'Listing']
const PRICE_KEYWORDS = ['调价', '定价', '竞品', '价格', '降价', '涨价', '跟价']
const ADS_KEYWORDS = ['广告', '投放', '出价', '千川', '预算', '巨量', 'Meta', '加投']

const makeNode = (id, type, name) => ({
  id, type, name,
  status: 'pending',
  started_at: null, finished_at: null,
  input: null, output: null, error: null,
  logs: [],
})

function buildTask(payload) {
  const goal = payload.goal || ''
  const needs = {
    copy: COPY_KEYWORDS.some((k) => goal.includes(k)),
    pricing: PRICE_KEYWORDS.some((k) => goal.includes(k)),
    ads: ADS_KEYWORDS.some((k) => goal.includes(k)) || (Number(payload.budget) || 0) >= 50000,
  }
  // 无识别结果时默认跑文案，保证演示可用
  if (!needs.copy && !needs.pricing && !needs.ads) needs.copy = true

  const nodes = [makeNode('n-sup', 'supervisor', 'Supervisor 主控')]
  const edges = []
  if (needs.copy) {
    nodes.push(makeNode('n-copy', 'copywriter', '爆款文案 Agent'))
    edges.push({ source: 'n-sup', target: 'n-copy', label: '文案任务', kind: 'normal' })
  }
  if (needs.pricing) {
    nodes.push(makeNode('n-price', 'pricing', '动态定价 Agent'))
    edges.push({ source: 'n-sup', target: 'n-price', label: '定价任务', kind: 'normal' })
  }
  if (needs.ads) {
    nodes.push(makeNode('n-ads', 'ads', '广告风控网关'))
    edges.push({ source: 'n-sup', target: 'n-ads', label: '广告投放', kind: 'normal' })
  }
  nodes.push(makeNode('n-handoff', 'handoff', '人工审批 Handoff'))
  nodes.push(makeNode('n-end', 'end', '执行完成'))

  if (needs.copy) {
    edges.push({ source: 'n-copy', target: 'n-handoff', label: '重大发布', kind: 'condition' })
    edges.push({ source: 'n-copy', target: 'n-end', label: '常规通过', kind: 'normal' })
  }
  if (needs.pricing) {
    edges.push({ source: 'n-price', target: 'n-handoff', label: '超阈值幅度', kind: 'condition' })
    edges.push({ source: 'n-price', target: 'n-end', label: '阈值内', kind: 'normal' })
  }
  if (needs.ads) {
    edges.push({ source: 'n-ads', target: 'n-handoff', label: '预算熔断', kind: 'condition' })
  }
  edges.push({ source: 'n-handoff', target: 'n-end', label: '', kind: 'normal' })

  return {
    id: nextId('TASK'),
    goal,
    status: 'running',
    budget: Number(payload.budget) || null,
    created_at: nowIso(),
    nodes,
    edges,
  }
}

function waitApproval(id) {
  return new Promise((resolve) => approvalResolvers.set(id, resolve))
}

async function runCopyNode(task) {
  const node = patchNode(task, 'n-copy', {
    status: 'running', started_at: nowIso(),
    input: { platforms: ['xiaohongshu', 'tiktok', 'amazon'], languages: ['zh-CN'] },
  })
  log(node, 'info', '接收子任务：为法式碎花雪纺连衣裙生成多平台营销文案')
  await sleep(700)
  log(node, 'info', 'Milvus 向量检索 Top10 历史高转化文案（耗时 128ms，相似度 0.87）')
  await sleep(650)
  log(node, 'info', '已生成 小红书 / TikTok / 亚马逊 三平台文案草稿')
  await sleep(500)
  log(node, 'warn', '违禁词扫描命中 2 处疑似风险词，已标注替换建议，需人工复核')
  await sleep(400)

  const approval = makeCopyApproval(task)
  approvals.set(approval.id, approval)
  patchNode(task, 'n-copy', {
    status: 'success', finished_at: nowIso(),
    output: { platforms: 3, languages: 1, violations_found: 2, approval_id: approval.id },
  })
  bus.emit('approval:created', approval)
  return [approval.id]
}

async function runPricingNode(task) {
  const node = patchNode(task, 'n-price', {
    status: 'running', started_at: nowIso(),
    input: { sku: 'ER-BUDS-PRO3', monitor_window: '72h' },
  })
  log(node, 'info', '通过 ERP MCP 适配器拉取 5 家竞品实时价格')
  await sleep(800)
  log(node, 'info', '抓取成功 3/5，2 家限流，已加入 Celery 15 分钟重试队列')
  await sleep(700)
  log(node, 'info', '价格波动分析：竞品A 72h 内累计降价 10.0%，超过 8% 异常波动线')
  await sleep(500)
  log(node, 'warn', '建议降价 11.37%（299 → 265），超过 10% 调价审批阈值，触发熔断')
  await sleep(400)

  const approval = makePricingApproval(task)
  approvals.set(approval.id, approval)
  patchNode(task, 'n-price', {
    status: 'success', finished_at: nowIso(),
    output: { action: '降价', confidence: 0.87, change_percent: -11.37, approval_id: approval.id },
  })
  bus.emit('approval:created', approval)
  return [approval.id]
}

async function runAdsNode(task) {
  const node = patchNode(task, 'n-ads', {
    status: 'running', started_at: nowIso(),
    input: { campaign: '黑五新品冲刺-千川信息流' },
  })
  log(node, 'info', '接收广告计划下发请求，执行风控前置校验')
  await sleep(700)
  log(node, 'info', `出价 3.5 元（阈值 2.0 元），总预算 ${task.budget || 80000} 元（阈值 50000 元）`)
  await sleep(500)
  log(node, 'warn', '出价与总预算双双超过风控阈值，已阻断广告下发 API 调用')

  const approval = makeAdsApproval(task)
  approvals.set(approval.id, approval)
  patchNode(task, 'n-ads', {
    status: 'success', finished_at: nowIso(),
    output: { blocked: true, reason: '超预算阈值', approval_id: approval.id },
  })
  bus.emit('approval:created', approval)
  return [approval.id]
}

async function settleHandoff(task, approvalIds) {
  const handoff = task.nodes.find((n) => n.type === 'handoff')
  patchNode(task, handoff.id, { status: 'running', started_at: handoff.started_at || nowIso() })
  log(handoff, 'warn', `${approvalIds.length} 项关键操作触发风控规则，执行链路已阻断，推送人工审批`)
  patchNode(task, handoff.id, { status: 'waiting' })

  const results = await Promise.all(approvalIds.map(waitApproval))
  if (task.status === 'failed') return // 已被取消/其它流程终止

  const rejectedCount = results.filter((r) => r !== 'approved').length
  if (rejectedCount > 0) {
    log(handoff, 'error', `${rejectedCount} 项审批被驳回，熔断终止，子任务结果不予下发`)
    patchNode(task, handoff.id, {
      status: 'failed', finished_at: nowIso(), error: '人工审批驳回',
      output: { approved: approvalIds.length - rejectedCount, rejected: rejectedCount },
    })
    task.status = 'failed'
    bus.emit('task:update', { task_id: task.id, status: task.status })
    return
  }

  log(handoff, 'success', `${approvalIds.length} 项审批全部通过，Handoff 放行，继续执行下游`)
  patchNode(task, handoff.id, {
    status: 'success', finished_at: nowIso(),
    output: { resolved: approvalIds.length },
  })
  await finishTask(task)
}

async function finishTask(task) {
  await sleep(300)
  patchNode(task, 'n-end', { status: 'running', started_at: nowIso() })
  const end = task.nodes.find((n) => n.type === 'end')
  log(end, 'success', '所有子任务与审批环节完成，结果已回传 ERP / 广告平台')
  await sleep(500)
  task.status = 'done'
  patchNode(task, 'n-end', { status: 'success', finished_at: nowIso() })
}

async function runLifecycle(task) {
  const sup = task.nodes.find((n) => n.type === 'supervisor')
  patchNode(task, 'n-sup', { status: 'running', started_at: nowIso(), input: { goal: task.goal } })
  log(sup, 'info', '接收运营目标，调用 LLM 进行意图识别与任务拆解')
  await sleep(900)

  const subTasks = []
  if (task.nodes.some((n) => n.type === 'copywriter')) subTasks.push('copywriter')
  if (task.nodes.some((n) => n.type === 'pricing')) subTasks.push('pricing')
  if (task.nodes.some((n) => n.type === 'ads')) subTasks.push('ads_gateway')
  log(sup, 'success', `拆解完成，分发 ${subTasks.length} 个子任务：${subTasks.join('、')}`)
  patchNode(task, 'n-sup', {
    status: 'success', finished_at: nowIso(),
    output: { sub_tasks: subTasks },
  })

  const children = []
  if (subTasks.includes('copywriter')) children.push(runCopyNode(task))
  if (subTasks.includes('pricing')) children.push(runPricingNode(task))
  if (subTasks.includes('ads_gateway')) children.push(runAdsNode(task))
  const approvalGroups = await Promise.all(children)
  const approvalIds = approvalGroups.flat()

  if (approvalIds.length > 0) {
    await settleHandoff(task, approvalIds)
  } else {
    await finishTask(task)
  }
}

// ---------- REST 路由 ----------
function parsePath(config) {
  const base = String(import.meta.env.VITE_API_BASE_URL || '/api/v1')
  // 注意：自定义 adapter 拿到的 config.url 尚未拼接 params（params 由内置 adapter 序列化），
  // 因此需要同时合并 config.params，否则查询条件会丢失。
  const url = new URL(config.url || '', 'http://mock.local')
  const params = config.params || {}
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, value)
    }
  })
  let pathname = url.pathname
  if (pathname.startsWith(base)) pathname = pathname.slice(base.length)
  return { path: pathname.replace(/\/+$/, '') || '/', query: url.searchParams }
}

const ok = (data) => ({
  data: { code: 0, msg: 'ok', data },
  status: 200,
  statusText: 'OK',
  headers: {},
  config: {},
})

export async function mockAdapter(config) {
  await sleep(180 + Math.random() * 220)
  const method = (config.method || 'get').toLowerCase()
  const body = config.data ? JSON.parse(config.data) : {}
  const { path, query } = parsePath(config)

  // POST /goals —— 提交运营目标
  if (method === 'post' && path === '/goals') {
    if (!body.goal || !String(body.goal).trim()) {
      const err = new Error('goal 不能为空')
      err.response = { status: 400, data: { code: 400, msg: 'goal 不能为空' } }
      throw err
    }
    const task = buildTask(body)
    tasks.set(task.id, task)
    bus.emit('task:update', { task_id: task.id, status: task.status })
    runLifecycle(task)
    return ok({ task_id: task.id })
  }

  // GET /tasks
  if (method === 'get' && path === '/tasks') {
    return ok(Array.from(tasks.values()).sort((a, b) => b.created_at.localeCompare(a.created_at)))
  }

  const taskMatch = path.match(/^\/tasks\/([^/]+)(\/(retry|cancel))?$/)
  if (taskMatch) {
    const [, taskId, , action] = taskMatch
    const task = tasks.get(taskId)
    if (!task) {
      const err = new Error('任务不存在')
      err.response = { status: 404, data: { code: 404, msg: '任务不存在' } }
      throw err
    }
    if (method === 'get' && !action) return ok(task)

    if (method === 'post' && action === 'cancel') {
      task.status = 'failed'
      const handoff = task.nodes.find((n) => n.type === 'handoff')
      if (['running', 'waiting'].includes(handoff.status)) {
        log(handoff, 'error', '运营手动终止任务，未决审批自动驳回')
        patchNode(task, handoff.id, { status: 'failed', finished_at: nowIso(), error: '手动终止' })
      }
      Array.from(approvalResolvers.keys()).forEach((apvId) => {
        const apv = approvals.get(apvId)
        if (apv && apv.task_id === taskId && apv.status === 'pending') {
          apv.status = 'rejected'
          apv.handled_at = nowIso()
          apv.handler = CURRENT_OPERATOR
          apv.comment = '任务被手动终止，系统自动驳回'
          approvalResolvers.get(apvId)?.('rejected')
          approvalResolvers.delete(apvId)
          bus.emit('approval:updated', apv)
        }
      })
      bus.emit('task:update', { task_id: task.id, status: task.status })
      return ok({ task_id: taskId, status: task.status })
    }

    if (method === 'post' && action === 'retry') {
      // 将该任务下被驳回的审批单重新生成待审批单，Handoff 回到等待态
      const oldRejected = Array.from(approvals.values()).filter(
        (a) => a.task_id === taskId && a.status === 'rejected',
      )
      if (oldRejected.length === 0) {
        const err = new Error('当前任务没有可重试的驳回项')
        err.response = { status: 400, data: { code: 400, msg: '当前任务没有可重试的驳回项' } }
        throw err
      }
      task.status = 'running'
      const handoff = task.nodes.find((n) => n.type === 'handoff')
      const end = task.nodes.find((n) => n.type === 'end')
      handoff.status = 'pending'; handoff.error = null; handoff.output = null
      end.status = 'pending'
      bus.emit('node:update', { task_id: taskId, node_id: handoff.id, patch: { status: 'pending' } })
      bus.emit('node:update', { task_id: taskId, node_id: end.id, patch: { status: 'pending' } })

      const newIds = oldRejected.map((old) => {
        const fresh = { ...old, id: nextId(`APV-${old.type.toUpperCase()}`), status: 'pending', created_at: nowIso(), handled_at: null, handler: null, comment: '' }
        approvals.set(fresh.id, fresh)
        bus.emit('approval:created', fresh)
        return fresh.id
      })
      bus.emit('task:update', { task_id: task.id, status: task.status })
      settleHandoff(task, newIds)
      return ok({ task_id: taskId, status: task.status, recreated: newIds })
    }
  }

  // GET /approvals
  if (method === 'get' && path === '/approvals') {
    let list = Array.from(approvals.values())
    const status = query.get('status')
    if (status === 'pending') list = list.filter((a) => a.status === 'pending')
    if (status === 'processed') list = list.filter((a) => a.status !== 'pending')
    const type = query.get('type')
    if (type) list = list.filter((a) => a.type === type)
    const start = query.get('start_time')
    const end = query.get('end_time')
    if (start) list = list.filter((a) => a.created_at >= start)
    if (end) list = list.filter((a) => a.created_at <= end)
    list.sort((a, b) => b.created_at.localeCompare(a.created_at))
    return ok(list)
  }

  const apvMatch = path.match(/^\/approvals\/([^/]+)(\/(approve|reject))?$/)
  if (apvMatch) {
    const [, apvId, , action] = apvMatch
    const apv = approvals.get(apvId)
    if (!apv) {
      const err = new Error('审批单不存在')
      err.response = { status: 404, data: { code: 404, msg: '审批单不存在' } }
      throw err
    }
    if (method === 'get' && !action) return ok(apv)

    if (method === 'post') {
      if (apv.status !== 'pending') {
        const err = new Error('该审批单已处理，请勿重复操作')
        err.response = { status: 409, data: { code: 409, msg: '该审批单已处理，请勿重复操作' } }
        throw err
      }
      if (action === 'reject' && !body.comment?.trim()) {
        const err = new Error('驳回必须填写原因')
        err.response = { status: 400, data: { code: 400, msg: '驳回必须填写原因' } }
        throw err
      }
      apv.status = action === 'approve' ? 'approved' : 'rejected'
      apv.handled_at = nowIso()
      apv.handler = CURRENT_OPERATOR
      apv.comment = body.comment || (action === 'approve' ? '同意' : '')
      if (action === 'approve' && body.edited_content) {
        apv.content = body.edited_content
        apv.versions.push({
          version: apv.versions.length + 1,
          content: body.edited_content,
          editor: CURRENT_OPERATOR,
          created_at: nowIso(),
        })
      }
      approvalResolvers.get(apvId)?.(apv.status)
      approvalResolvers.delete(apvId)
      bus.emit('approval:updated', apv)
      return ok(apv)
    }
  }

  // PUT /copies/:id —— 文案微调保存为新版本
  const copyMatch = path.match(/^\/copies\/([^/]+)$/)
  if (method === 'put' && copyMatch) {
    const apv = approvals.get(copyMatch[1])
    if (!apv || apv.type !== 'copy') {
      const err = new Error('文案审批单不存在')
      err.response = { status: 404, data: { code: 404, msg: '文案审批单不存在' } }
      throw err
    }
    apv.content = body.content
    apv.versions.push({
      version: apv.versions.length + 1,
      content: body.content,
      editor: CURRENT_OPERATOR,
      created_at: nowIso(),
    })
    bus.emit('approval:updated', apv)
    return ok({ version: apv.versions.length })
  }

  const err = new Error(`Mock 未实现的接口：${method.toUpperCase()} ${path}`)
  err.response = { status: 404, data: { code: 404, msg: err.message } }
  throw err
}

// ---------- MockWebSocket ----------
export class MockSocket {
  constructor(url) {
    this.url = url
    this.readyState = 0
    this.onopen = null
    this.onmessage = null
    this.onclose = null
    this.onerror = null
    this.offFns = []
    setTimeout(() => {
      this.readyState = 1
      this.onopen?.()
      this.offFns.push(
        bus.on('node:update', (data) => this.dispatch('node:update', data)),
        bus.on('task:update', (data) => this.dispatch('task:update', data)),
        bus.on('approval:created', (data) => this.dispatch('approval:created', data)),
        bus.on('approval:updated', (data) => this.dispatch('approval:updated', data)),
      )
    }, 150)
  }
  dispatch(event, data) {
    this.onmessage?.({ data: JSON.stringify({ event, data }) })
  }
  send(raw) {
    try {
      const msg = JSON.parse(raw)
      if (msg.action === 'ping') this.dispatch('pong', { ts: nowIso() })
    } catch {
      /* ignore malformed frame */
    }
  }
  close() {
    if (this.readyState === 3) return
    this.readyState = 3
    this.offFns.forEach((fn) => fn())
    this.offFns = []
    this.onclose?.({ code: 1000, reason: 'mock closed' })
  }
}
