import { defineStore } from 'pinia'
import { ref } from 'vue'
import { listTasks, getTask, createGoal, retryTask, cancelTask } from '@/api'

// 任务拓扑状态：任务列表、当前选中任务、Socket 增量补丁合并
export const useTaskStore = defineStore('task', () => {
  const tasks = ref([])
  const currentId = ref('')
  const current = ref(null)
  const loading = ref(false)
  const submitting = ref(false)

  async function fetchTasks(selectLatest = false) {
    loading.value = true
    try {
      tasks.value = await listTasks()
      if (selectLatest && tasks.value.length > 0) {
        await selectTask(tasks.value[0].id)
      } else if (currentId.value) {
        // 保持列表中对象引用为最新
        const latest = tasks.value.find((t) => t.id === currentId.value)
        if (latest) current.value = latest
      }
    } finally {
      loading.value = false
    }
  }

  async function selectTask(taskId) {
    if (!taskId) {
      currentId.value = ''
      current.value = null
      return
    }
    currentId.value = taskId
    loading.value = true
    try {
      current.value = await getTask(taskId)
      const idx = tasks.value.findIndex((t) => t.id === taskId)
      if (idx >= 0) tasks.value[idx] = current.value
      else tasks.value.unshift(current.value)
    } finally {
      loading.value = false
    }
  }

  async function submitGoal(payload) {
    submitting.value = true
    try {
      const { task_id } = await createGoal(payload)
      await fetchTasks()
      await selectTask(task_id)
      return task_id
    } finally {
      submitting.value = false
    }
  }

  async function retry(taskId, nodeId) {
    await retryTask(taskId, nodeId)
    await selectTask(taskId)
    await fetchTasks()
  }

  async function cancel(taskId) {
    await cancelTask(taskId)
    await selectTask(taskId)
    await fetchTasks()
  }

  // 合并 WebSocket 推送的节点补丁
  function applyNodeUpdate({ task_id, node_id, patch }) {
    const target =
      current.value?.id === task_id
        ? current.value
        : tasks.value.find((t) => t.id === task_id)
    if (!target) return
    const idx = target.nodes.findIndex((n) => n.id === node_id)
    // 用 splice 整体替换节点，保证 SVG foreignObject 场景下视图可靠刷新
    if (idx > -1) target.nodes.splice(idx, 1, { ...target.nodes[idx], ...patch })
  }

  function applyTaskUpdate({ task_id, status }) {
    const target =
      current.value?.id === task_id
        ? current.value
        : tasks.value.find((t) => t.id === task_id)
    if (target) target.status = status
  }

  return {
    tasks,
    currentId,
    current,
    loading,
    submitting,
    fetchTasks,
    selectTask,
    submitGoal,
    retry,
    cancel,
    applyNodeUpdate,
    applyTaskUpdate,
  }
})
