import axios from 'axios'
import { getApiBaseURL } from '../utils/config'

// 创建 axios 实例，baseURL 会根据环境自动调整
const api = axios.create({
  baseURL: getApiBaseURL(),
  timeout: 30000
})

// 监听配置变化，动态更新 baseURL
let currentBaseURL = getApiBaseURL()
const updateApiBaseURL = () => {
  const newBaseURL = getApiBaseURL()
  if (newBaseURL !== currentBaseURL) {
    currentBaseURL = newBaseURL
    api.defaults.baseURL = newBaseURL
  }
}

// 导出更新函数，供设置组件调用
export const refreshApiConfig = () => {
  updateApiBaseURL()
}

// 初始化时更新一次
updateApiBaseURL()

// 文件上传
export const uploadFile = (file) => {
  const formData = new FormData()
  formData.append('file', file)
  return api.post('/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
}

// 品牌管理
export const getBrands = () => api.get('/brands')
export const createBrand = (data, file) => {
  const formData = new FormData()
  formData.append('name', data.name)
  if (file) {
    formData.append('logo_file', file)
  } else if (data.logo) {
    formData.append('logo', data.logo)
  }
  return api.post('/brands', formData, {
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
}
export const updateBrand = (id, data, file) => {
  const formData = new FormData()
  formData.append('name', data.name)
  if (file) {
    formData.append('logo_file', file)
  } else if (data.logo) {
    formData.append('logo', data.logo)
  }
  return api.put(`/brands/${id}`, formData, {
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
}
export const deleteBrand = (id) => api.delete(`/brands/${id}`)
export const reorderBrands = (orders) => api.post('/brands/reorder', { orders })

// 加速卡管理
export const getAccelerators = (brandId) => api.get(`/brands/${brandId}/accelerators`)
export const createAccelerator = (brandId, data) => api.post(`/brands/${brandId}/accelerators`, data)
export const updateAccelerator = (id, data) => api.put(`/accelerators/${id}`, data)
export const deleteAccelerator = (id) => api.delete(`/accelerators/${id}`)

// 模型管理
export const getModels = () => api.get('/models')
export const createModel = (data, file) => {
  const formData = new FormData()
  formData.append('name', data.name)
  if (data.parameters) {
    formData.append('parameters', data.parameters)
  }
  if (file) {
    formData.append('logo_file', file)
  } else if (data.logo) {
    formData.append('logo', data.logo)
  }
  return api.post('/models', formData, {
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
}
export const updateModel = (id, data, file) => {
  const formData = new FormData()
  formData.append('name', data.name)
  if (data.parameters) {
    formData.append('parameters', data.parameters)
  }
  if (file) {
    formData.append('logo_file', file)
  } else if (data.logo) {
    formData.append('logo', data.logo)
  }
  return api.put(`/models/${id}`, formData, {
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
}
export const deleteModel = (id) => api.delete(`/models/${id}`)

// 服务器管理
export const getServers = () => api.get('/servers')
export const createServer = (data) => api.post('/servers', data)
export const updateServer = (id, data) => api.put(`/servers/${id}`, data)
export const deleteServer = (id) => api.delete(`/servers/${id}`)
export const checkServer = (id) => api.post(`/servers/${id}/check`)
export const checkAllServers = () => api.post('/servers/check-all')
export const getServerResources = (id) => api.get(`/servers/${id}/resources`)
export const getAllServersResources = () => api.post('/servers/resources')

// 服务管理
export const getServices = () => api.get('/services')
export const refreshServicesStatus = () => api.post('/services/refresh-status')
export const createService = (data) => api.post('/services', data)
export const updateService = (id, data) => api.put(`/services/${id}`, data)
export const deleteService = (id) => api.delete(`/services/${id}`)
export const restartService = (id) => api.post(`/services/${id}/restart`)
export const stopService = (id) => api.post(`/services/${id}/stop`)
export const getDeployLog = (id) => api.get(`/services/${id}/deploy-log`)

// 聊天记录
export const getChatHistory = (serviceId) => api.get(`/services/${serviceId}/chat`)
export const addChatMessage = (serviceId, data) => api.post(`/services/${serviceId}/chat`, data)
export const clearChatHistory = (serviceId) => api.delete(`/services/${serviceId}/chat`)
export const chatCompletions = (serviceId, data) => {
  const config = {}
  if (data && data.stream) {
    config.responseType = 'text'
  }
  return api.post(`/services/${serviceId}/chat/completions`, data, config)
}

// 计划任务
export const getTasks = () => api.get('/tasks')
export const createTask = (data) => api.post('/tasks', data)
export const updateTask = (id, data) => api.put(`/tasks/${id}`, data)
export const deleteTask = (id) => api.delete(`/tasks/${id}`)
export const executeTask = (id) => api.post(`/tasks/${id}/execute`)
export const getTaskResult = (id) => api.get(`/tasks/${id}/result`)

// 统计信息
export const getStats = () => api.get('/stats')

// 跨平台测试
export const runCrossPlatformTest = (data) => api.post('/cross-platform-test/run', data)
