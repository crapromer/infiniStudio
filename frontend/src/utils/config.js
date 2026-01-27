/**
 * 应用配置管理
 * 使用 localStorage 存储后端服务地址和端口配置
 */

const CONFIG_KEY = 'infini_studio_config'
const DEFAULT_CONFIG = {
  backendHost: 'localhost',
  backendPort: 5000,
  useProxy: true  // 默认使用代理模式（通过 nginx）
}

/**
 * 获取配置
 */
export function getConfig() {
  try {
    const stored = localStorage.getItem(CONFIG_KEY)
    if (stored) {
      const config = JSON.parse(stored)
      return {
        ...DEFAULT_CONFIG,
        ...config
      }
    }
  } catch (error) {
    console.error('读取配置失败:', error)
  }
  return { ...DEFAULT_CONFIG }
}

/**
 * 保存配置
 */
export function saveConfig(config) {
  try {
    const currentConfig = getConfig()
    const newConfig = {
      ...currentConfig,
      ...config
    }
    localStorage.setItem(CONFIG_KEY, JSON.stringify(newConfig))
    return true
  } catch (error) {
    console.error('保存配置失败:', error)
    return false
  }
}

/**
 * 获取后端 API 基础 URL
 */
export function getApiBaseURL() {
  const config = getConfig()
  const { backendHost, backendPort, useProxy } = config
  
  // 开发环境或使用代理模式：使用相对路径（通过 nginx 代理）
  if (process.env.NODE_ENV === 'development' || useProxy) {
    return '/api'
  }
  
  // 生产环境且不使用代理：使用完整 URL（直接访问后端）
  return `http://${backendHost}:${backendPort}/api`
}

/**
 * 获取 WebSocket/Socket.IO 连接 URL
 */
export function getSocketURL() {
  const config = getConfig()
  const { backendHost, backendPort, useProxy } = config
  
  // 使用代理模式：使用相对路径（通过 nginx 代理）
  // 注意：Socket.IO 需要通过 window.location 获取当前协议和主机
  if (useProxy || process.env.NODE_ENV === 'development') {
    // 使用当前页面的协议和主机，nginx 会代理到后端
    const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:'
    const host = window.location.host
    return `${protocol}//${host}`
  }
  
  // 不使用代理：直接访问后端服务器
  return `http://${backendHost}:${backendPort}`
}

/**
 * 重置为默认配置
 */
export function resetConfig() {
  try {
    localStorage.setItem(CONFIG_KEY, JSON.stringify(DEFAULT_CONFIG))
    return true
  } catch (error) {
    console.error('重置配置失败:', error)
    return false
  }
}
