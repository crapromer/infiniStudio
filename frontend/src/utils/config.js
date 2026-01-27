/**
 * 应用配置管理
 * 使用 localStorage 存储后端服务地址和端口配置
 */

const CONFIG_KEY = 'infini_studio_config'
const DEFAULT_CONFIG = {
  backendHost: 'localhost',
  backendPort: 5000
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
  const { backendHost, backendPort } = config
  
  // 如果是开发环境，使用代理（相对路径）
  // 开发环境的代理配置在 vue.config.js 中
  if (process.env.NODE_ENV === 'development') {
    return '/api'
  }
  
  // 生产环境使用完整 URL（从配置中读取）
  return `http://${backendHost}:${backendPort}/api`
}

/**
 * 获取 WebSocket/Socket.IO 连接 URL
 */
export function getSocketURL() {
  const config = getConfig()
  const { backendHost, backendPort } = config
  
  // 开发环境和生产环境都使用完整 URL
  // 因为 Socket.IO 需要直接连接到后端服务器
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
