<template>
  <div>
    <h2 style="margin: 0 0 32px 0; font-size: 24px; font-weight: 600; color: #262626; display: flex; align-items: center; gap: 12px">
      <span style="font-size: 28px">🧪</span>
      <span>跨平台测试</span>
    </h2>

    <a-card :bordered="false" style="margin-bottom: 24px">
      <template #title>
        <div style="display: flex; align-items: center; gap: 8px">
          <span style="font-size: 18px">📝</span>
          <span style="font-weight: 600">Python脚本</span>
        </div>
      </template>
      <div style="margin-bottom: 16px">
        <a-upload
          :before-upload="handleFileUpload"
          :show-upload-list="false"
          accept=".py"
        >
          <template #default>
            <a-button>
              <span style="margin-right: 8px">📁</span>
              上传Python脚本
            </a-button>
          </template>
        </a-upload>
      </div>
      <a-textarea
        v-model:value="scriptContent"
        :rows="15"
        placeholder="请输入或上传Python脚本..."
        style="font-family: 'Courier New', monospace; font-size: 14px"
      />
      <div style="margin-top: 16px; display: flex; gap: 12px">
        <a-button type="primary" @click="runScript" :loading="running" :disabled="!canRun">
          <span style="margin-right: 8px">▶️</span>
          运行脚本
        </a-button>
        <a-button @click="clearScript">
          <span style="margin-right: 8px">🗑️</span>
          清空脚本
        </a-button>
      </div>
    </a-card>

    <a-card :bordered="false" style="margin-bottom: 24px">
      <template #title>
        <div style="display: flex; align-items: center; gap: 8px">
          <span style="font-size: 18px">🖥️</span>
          <span style="font-weight: 600">选择服务器</span>
        </div>
      </template>
      <a-select
        v-model:value="selectedServerIds"
        mode="multiple"
        placeholder="请选择要运行脚本的服务器"
        style="width: 100%"
        :options="serverOptions"
        :disabled="running"
      />
    </a-card>

    <a-card :bordered="false" v-if="selectedServerIds.length > 0">
      <template #title>
        <div style="display: flex; align-items: center; gap: 8px">
          <span style="font-size: 18px">📊</span>
          <span style="font-weight: 600">运行结果</span>
        </div>
      </template>
      <a-row :gutter="[16, 16]">
        <a-col :xs="24" :sm="12" :md="8" v-for="serverId in selectedServerIds" :key="serverId">
          <a-card
            :title="getServerName(serverId)"
            :bordered="false"
            style="border: 1px solid #f0f0f0; border-radius: 12px"
          >
            <div style="margin-bottom: 12px">
              <a-tag :color="getServerStatusColor(serverId)" style="font-weight: 500; padding: 4px 12px; border-radius: 4px">
                {{ getServerStatus(serverId) }}
              </a-tag>
            </div>
            <div style="margin-bottom: 12px">
              <a-input
                v-model:value="serverArgs[serverId]"
                placeholder="命令行参数（可选，例如：--arg1 value1 --arg2 value2）"
                :disabled="running"
                style="font-family: monospace; font-size: 12px"
              >
                <template #prefix>
                  <span style="color: #8c8c8c; font-size: 12px">参数:</span>
                </template>
              </a-input>
            </div>
            <div
              ref="outputRefs"
              :data-server-id="serverId"
              class="script-output"
            >
              <div v-if="!getServerOutput(serverId)" style="color: #888; text-align: center; padding-top: 120px">
                等待运行...
              </div>
              <div v-else v-html="formatOutput(getServerOutput(serverId))"></div>
            </div>
            <template #actions>
              <a-button type="link" @click="clearServerOutput(serverId)" :disabled="running" style="color: #667eea">
                清空输出
              </a-button>
            </template>
          </a-card>
        </a-col>
      </a-row>
    </a-card>
  </div>
</template>

<script>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { getServers } from '../api'
import { message } from 'ant-design-vue'
import { io } from 'socket.io-client'
import { getSocketURL, getApiBaseURL } from '../utils/config'

export default {
  name: 'CrossPlatformTest',
  setup() {
    const scriptContent = ref('')
    const selectedServerIds = ref([])
    const servers = ref([])
    const running = ref(false)
    const serverOutputs = ref({})
    const serverStatuses = ref({})
    const serverArgs = ref({})  // 存储每个服务器的命令行参数
    const socket = ref(null)
    const outputRefs = ref([])

    const serverOptions = ref([])

    const loadServers = async () => {
      try {
        const res = await getServers()
        servers.value = res.data
        serverOptions.value = res.data.map(server => ({
          label: `${server.name} (${server.host_ip})`,
          value: server.id
        }))
      } catch (error) {
        message.error('加载服务器列表失败')
      }
    }

    const getServerName = (serverId) => {
      const server = servers.value.find(s => s.id === serverId)
      return server ? server.name : `服务器 ${serverId}`
    }

    const getServerOutput = (serverId) => {
      return serverOutputs.value[serverId] || ''
    }

    const getServerStatus = (serverId) => {
      const status = serverStatuses.value[serverId]
      if (status === 'running') return '运行中'
      if (status === 'completed') return '已完成'
      if (status === 'error') return '执行错误'
      return '等待中'
    }

    const getServerStatusColor = (serverId) => {
      const status = serverStatuses.value[serverId]
      if (status === 'running') return 'processing'
      if (status === 'completed') return 'success'
      if (status === 'error') return 'error'
      return 'default'
    }

    const formatOutput = (output) => {
      if (!output) return ''
      // 转义HTML并保留换行
      return output
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\n/g, '<br>')
    }

    const clearServerOutput = (serverId) => {
      serverOutputs.value[serverId] = ''
      serverStatuses.value[serverId] = 'waiting'
      // 不清空参数，保留用户输入
    }

    const handleFileUpload = (file) => {
      const reader = new FileReader()
      reader.onload = (e) => {
        scriptContent.value = e.target.result
        message.success('脚本上传成功')
      }
      reader.onerror = () => {
        message.error('文件读取失败')
      }
      reader.readAsText(file)
      return false // 阻止自动上传
    }

    const clearScript = () => {
      scriptContent.value = ''
    }

    const connectSocket = () => {
      const socketURL = getSocketURL()
      socket.value = io(socketURL, {
        transports: ['websocket', 'polling']
      })

      socket.value.on('connect', () => {
        console.log('Socket connected')
      })

      socket.value.on('script_output', (data) => {
        const { server_id, output, is_error } = data
        if (serverOutputs.value[server_id] === undefined) {
          serverOutputs.value[server_id] = ''
        }
        serverOutputs.value[server_id] += output
        // 自动滚动到底部
        nextTick(() => {
          scrollToBottom(server_id)
        })
      })

      socket.value.on('script_status', (data) => {
        const { server_id, status } = data
        serverStatuses.value[server_id] = status
        if (status === 'completed' || status === 'error') {
          // 检查是否所有服务器都完成了
          const allCompleted = selectedServerIds.value.every(id => 
            serverStatuses.value[id] === 'completed' || serverStatuses.value[id] === 'error'
          )
          if (allCompleted) {
            running.value = false
            message.success('所有服务器执行完成')
          }
        }
      })

      socket.value.on('script_error', (data) => {
        const { server_id, error } = data
        if (serverOutputs.value[server_id] === undefined) {
          serverOutputs.value[server_id] = ''
        }
        serverOutputs.value[server_id] += `\n[错误] ${error}\n`
        serverStatuses.value[server_id] = 'error'
        nextTick(() => {
          scrollToBottom(server_id)
        })
      })

      socket.value.on('script_started', (data) => {
        message.success(data.message || '脚本已开始执行')
      })
    }

    const scrollToBottom = (serverId) => {
      const outputElement = document.querySelector(`[data-server-id="${serverId}"]`)
      if (outputElement) {
        outputElement.scrollTop = outputElement.scrollHeight
      }
    }

    const runScript = async () => {
      if (!scriptContent.value.trim()) {
        message.warning('请输入Python脚本')
        return
      }
      if (selectedServerIds.value.length === 0) {
        message.warning('请至少选择一个服务器')
        return
      }

      // 初始化状态
      running.value = true
      selectedServerIds.value.forEach(id => {
        serverOutputs.value[id] = ''
        serverStatuses.value[id] = 'running'
        // 确保每个服务器都有参数字段
        if (!serverArgs.value[id]) {
          serverArgs.value[id] = ''
        }
      })

      // 连接Socket（如果未连接）
      if (!socket.value || !socket.value.connected) {
        connectSocket()
        // 等待连接建立
        await new Promise((resolve, reject) => {
          if (socket.value.connected) {
            resolve()
          } else {
            const timeout = setTimeout(() => {
              reject(new Error('连接超时'))
            }, 5000)
            socket.value.once('connect', () => {
              clearTimeout(timeout)
              resolve()
            })
          }
        })
      }

      // 通过SocketIO发送运行请求，包含每个服务器的参数
      try {
        // 构建服务器参数映射
        const server_args_map = {}
        selectedServerIds.value.forEach(id => {
          server_args_map[id] = serverArgs.value[id] || ''
        })
        
        socket.value.emit('run_script', {
          script: scriptContent.value,
          server_ids: selectedServerIds.value,
          server_args: server_args_map
        })
      } catch (error) {
        message.error('执行失败: ' + error.message)
        running.value = false
        selectedServerIds.value.forEach(id => {
          serverStatuses.value[id] = 'error'
        })
      }
    }

    const canRun = computed(() => {
      return scriptContent.value.trim().length > 0 && 
             selectedServerIds.value.length > 0 && 
             !running.value
    })

    onMounted(() => {
      loadServers()
      connectSocket()
    })

    onUnmounted(() => {
      if (socket.value) {
        socket.value.disconnect()
      }
    })

    return {
      scriptContent,
      selectedServerIds,
      servers,
      running,
      serverOutputs,
      serverStatuses,
      serverOptions,
      outputRefs,
      serverArgs,
      getServerName,
      getServerOutput,
      getServerStatus,
      getServerStatusColor,
      formatOutput,
      clearServerOutput,
      handleFileUpload,
      clearScript,
      runScript,
      canRun
    }
  }
}
</script>

<style scoped>
.script-output {
  height: 300px;
  overflow: auto;
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 12px;
  border-radius: 8px;
  font-family: Monaco, "Courier New", monospace;
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  scrollbar-width: thin;
  scrollbar-color: #555 #1e1e1e;
}

.script-output::-webkit-scrollbar {
  width: 8px;
}

.script-output::-webkit-scrollbar-track {
  background: #1e1e1e;
}

.script-output::-webkit-scrollbar-thumb {
  background: #555;
  border-radius: 4px;
}

.script-output::-webkit-scrollbar-thumb:hover {
  background: #777;
}
</style>
