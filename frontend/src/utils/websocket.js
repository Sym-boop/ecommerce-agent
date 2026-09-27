// ============================================
// WebSocket 客户端封装
// - 心跳保活（25s ping，超时自动重连）
// - 指数退避重连（1s → 2s → 4s … 上限 15s）
// - 业务事件订阅：socket.on('node:update', handler)
//
// 服务端帧协议（与后端对齐）：
//   下行 { event: 'node:update' | 'task:update' | 'approval:created' | 'approval:updated' | 'pong', data }
//   上行 { action: 'ping' }
// ============================================
import { USE_MOCK, MockSocket } from '@/api/mock'

const HEARTBEAT_INTERVAL = 25000
const HEARTBEAT_TIMEOUT = 8000
const MAX_RETRY_DELAY = 15000

function resolveWsUrl() {
  const configured = String(import.meta.env.VITE_WS_URL || '/ws/dashboard')
  if (/^wss?:\/\//.test(configured)) return configured
  // 相对地址（开发环境走 Vite proxy）：补全为当前页面的 ws(s) 地址
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${window.location.host}${configured}`
}

export class DashboardSocket {
  constructor() {
    this.handlers = new Map()
    this.ws = null
    this.retryCount = 0
    this.manualClosed = false
    this.connected = false
    this._heartbeatTimer = null
    this._timeoutTimer = null
    this._retryTimer = null
    this.onStatusChange = null
  }

  on(event, handler) {
    if (!this.handlers.has(event)) this.handlers.set(event, new Set())
    this.handlers.get(event).add(handler)
    return () => this.handlers.get(event)?.delete(handler)
  }

  emit(event, data) {
    this.handlers.get(event)?.forEach((fn) => fn(data))
  }

  setStatus(connected) {
    this.connected = connected
    this.onStatusChange?.(connected)
  }

  connect() {
    this.manualClosed = false
    const SocketImpl = USE_MOCK ? MockSocket : window.WebSocket
    this.ws = new SocketImpl(resolveWsUrl())

    this.ws.onopen = () => {
      this.retryCount = 0
      this.setStatus(true)
      this.startHeartbeat()
    }

    this.ws.onmessage = (evt) => {
      this.scheduleHeartbeat() // 任意下行帧都视为连接存活
      let frame
      try {
        frame = JSON.parse(evt.data)
      } catch {
        return
      }
      if (frame.event && frame.event !== 'pong') {
        this.emit(frame.event, frame.data)
      }
    }

    this.ws.onclose = () => {
      this.setStatus(false)
      this.clearTimers()
      if (!this.manualClosed) this.scheduleReconnect()
    }

    this.ws.onerror = () => {
      // close 事件会紧随其后触发，统一在 onclose 重连
      this.setStatus(false)
    }
  }

  startHeartbeat() {
    this.scheduleHeartbeat()
  }

  scheduleHeartbeat() {
    clearTimeout(this._timeoutTimer)
    clearInterval(this._heartbeatTimer)
    this._heartbeatTimer = setInterval(() => {
      if (this.ws?.readyState === 1) {
        this.ws.send(JSON.stringify({ action: 'ping' }))
        this._timeoutTimer = setTimeout(() => {
          // 心跳超时：主动断开后重连
          try {
            this.ws?.close()
          } catch {
            /* noop */
          }
        }, HEARTBEAT_TIMEOUT)
      }
    }, HEARTBEAT_INTERVAL)
  }

  scheduleReconnect() {
    clearTimeout(this._retryTimer)
    const delay = Math.min(1000 * 2 ** this.retryCount, MAX_RETRY_DELAY)
    this.retryCount += 1
    this._retryTimer = setTimeout(() => this.connect(), delay)
  }

  clearTimers() {
    clearInterval(this._heartbeatTimer)
    clearTimeout(this._timeoutTimer)
  }

  close() {
    this.manualClosed = true
    clearTimeout(this._retryTimer)
    this.clearTimers()
    this.ws?.close()
  }
}

// 单例
export const dashboardSocket = new DashboardSocket()
