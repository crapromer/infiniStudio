<template>
  <div style="height: calc(100vh - 112px); display: flex; flex-direction: column">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 20px; border-bottom: 2px solid #f0f0f0">
      <div style="display: flex; align-items: center; gap: 16px">
        <a-button @click="goBack" style="border-radius: 6px">← 返回</a-button>
        <h2 style="margin: 0; font-size: 24px; font-weight: 600; color: #262626; display: flex; align-items: center; gap: 12px">
          <span style="font-size: 28px">💬</span>
          <span>{{ serviceName }}</span>
        </h2>
      </div>
      <a-button @click="clearHistory" style="border-radius: 6px">清空历史</a-button>
    </div>

    <div 
      ref="chatContainer" 
      style="flex: 1; overflow-y: auto; padding: 24px; background: #f5f5f5"
    >
      <div v-for="message in messages" :key="message.id" style="margin-bottom: 16px">
        <div :style="{ 
          textAlign: message.role === 'user' ? 'right' : 'left',
          marginBottom: '8px'
        }">
          <a-card 
            :style="{ 
              display: 'inline-block',
              maxWidth: '70%',
              background: message.role === 'user' ? '#1890ff' : '#fff',
              color: message.role === 'user' ? '#fff' : '#000'
            }"
          >
            <div style="white-space: pre-wrap">{{ message.content }}</div>
            <div :style="{ 
              fontSize: '12px', 
              marginTop: '8px',
              opacity: 0.7 
            }">
              {{ formatTime(message.created_at) }}
            </div>
          </a-card>
        </div>
      </div>
    </div>

    <div style="padding: 16px; background: #fff; border-top: 1px solid #e8e8e8">
      <a-input-search
        v-model:value="inputMessage"
        placeholder="输入消息..."
        enter-button="发送"
        size="large"
        @search="sendMessage"
        :loading="sending"
      />
    </div>
  </div>
</template>

<script>
import { ref, onMounted, nextTick, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getChatHistory, addChatMessage, clearChatHistory, getServices, chatCompletions } from '../api'
import { message } from 'ant-design-vue'

export default {
  name: 'ServiceChat',
  setup() {
    const router = useRouter()
    const route = useRoute()
    const serviceId = parseInt(route.params.id)
    const serviceName = ref('')
    const messages = ref([])
    const inputMessage = ref('')
    const sending = ref(false)
    const chatContainer = ref(null)

    const loadService = async () => {
      try {
        const res = await getServices()
        const service = res.data.find(s => s.id === serviceId)
        if (service) {
          serviceName.value = service.name
        }
      } catch (error) {
        message.error('加载服务信息失败')
      }
    }

    const loadMessages = async () => {
      try {
        const res = await getChatHistory(serviceId)
        messages.value = res.data
        scrollToBottom()
      } catch (error) {
        message.error('加载聊天记录失败')
      }
    }

    const sendMessage = async () => {
      if (!inputMessage.value.trim() || sending.value) {
        return
      }

      const userMessage = inputMessage.value.trim()
      inputMessage.value = ''
      sending.value = true

      try {
        // 立即添加用户消息到前端显示
        const userMsg = {
          id: 'user-' + Date.now(),
          role: 'user',
          content: userMessage,
          created_at: new Date().toISOString()
        }
        messages.value.push(userMsg)
        scrollToBottom()

        // 添加用户消息到数据库
        await addChatMessage(serviceId, {
          role: 'user',
          content: userMessage
        })

        // 准备消息历史（转换为API格式）
        const apiMessages = messages.value.map(msg => ({
          role: msg.role,
          content: msg.content
        }))

        // 调用大模型API（流式响应）
        const requestData = {
          model: 'jiuge',
          messages: apiMessages,
          temperature: 1.0,
          top_k: 50,
          top_p: 0.8,
          max_tokens: 512,
          stream: true
        }

        // 创建临时的助手消息用于显示流式响应
        const tempAssistantMessage = {
          id: 'temp-' + Date.now(),
          role: 'assistant',
          content: '',
          created_at: new Date().toISOString()
        }
        messages.value.push(tempAssistantMessage)
        scrollToBottom()

        // 处理流式响应
        let fullResponse = ''
        let buffer = ''  // 用于处理不完整的行
        try {
          // 流式请求：开发环境下尽量绕过 devServer(8080) proxy，避免 SSE 被缓冲成“一次性输出”
          const apiBase =
            (process.env.VUE_APP_API_BASE && process.env.VUE_APP_API_BASE.trim()) ||
            (process.env.NODE_ENV === 'development'
              ? `http://${window.location.hostname}:5000`
              : '')

          const response = await fetch(`${apiBase}/api/services/${serviceId}/chat/completions`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Accept': 'text/event-stream',
              'Cache-Control': 'no-cache'
            },
            body: JSON.stringify(requestData)
          })

          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`)
          }

          const reader = response.body.getReader()
          const decoder = new TextDecoder()

          while (true) {
            const { done, value } = await reader.read()
            if (done) break

            // 解码数据块
            const chunk = decoder.decode(value, { stream: true })
            buffer += chunk

            // 处理完整的行
            const lines = buffer.split('\n')
            buffer = lines.pop() || ''  // 保留最后不完整的行

            for (const line of lines) {
              if (!line.trim()) continue  // 跳过空行
              
              if (line.startsWith('data: ')) {
                const data = line.slice(6).trim()
                if (data === '[DONE]' || data === '') {
                  continue
                }
                try {
                  const json = JSON.parse(data)
                  console.log('收到SSE数据:', json)  // 调试日志
                  
                  if (json.choices && json.choices.length > 0) {
                    const choice = json.choices[0]
                    const delta = choice.delta

                    // 统一增量累加：优先 delta.content，其次 choice.text（有些实现会把 token 放在 text 字段）
                    const piece = (delta && delta.content) ? delta.content : (choice.text || '')
                    if (piece) {
                      fullResponse += piece
                      // 实时更新临时消息内容
                      const tempMsg = messages.value.find(m => m.id === tempAssistantMessage.id)
                      if (tempMsg) {
                        tempMsg.content = fullResponse
                        scrollToBottom()
                      }
                    }
                  }
                } catch (e) {
                  console.error('解析SSE数据失败:', e, '原始数据:', data)
                }
              }
            }
          }

          // 处理剩余的数据
          if (buffer.trim()) {
            const line = buffer.trim()
            if (line.startsWith('data: ')) {
              const data = line.slice(6).trim()
              if (data && data !== '[DONE]') {
                try {
                  const json = JSON.parse(data)
                  if (json.choices && json.choices.length > 0) {
                    const choice = json.choices[0]
                    const delta = choice.delta
                    const piece = (delta && delta.content) ? delta.content : (choice.text || '')
                    if (piece) {
                      fullResponse += piece
                      const tempMsg = messages.value.find(m => m.id === tempAssistantMessage.id)
                      if (tempMsg) {
                        tempMsg.content = fullResponse
                      }
                    }
                  }
                } catch (e) {
                  console.error('解析剩余SSE数据失败:', e, data)
                }
              }
            }
          }

          // 流式响应完成：不移除临时消息，直接“转正”为最终assistant消息，避免闪烁
          const tempMsg = messages.value.find(m => m.id === tempAssistantMessage.id)
          if (tempMsg) {
            tempMsg.content = fullResponse || tempMsg.content || ''
          }

          // 保存到数据库（成功后可用返回的id/created_at更新临时消息，保持一致性）
          if (fullResponse) {
            const saved = await addChatMessage(serviceId, {
              role: 'assistant',
              content: fullResponse
            })
            if (saved && saved.data && tempMsg) {
              // 兼容后端可能返回 {id, created_at, ...}
              if (saved.data.id) tempMsg.id = saved.data.id
              if (saved.data.created_at) tempMsg.created_at = saved.data.created_at
            }
          }
        } catch (error) {
          // 保留临时消息并标记错误，避免“闪一下就没了”
          const tempMsg = messages.value.find(m => m.id === tempAssistantMessage.id)
          if (tempMsg && !tempMsg.content) {
            tempMsg.content = '[错误] 流式响应失败，请重试'
          }
          throw error
        }
      } catch (error) {
        console.error('发送消息失败:', error)
        message.error('发送消息失败: ' + (error.response?.data?.error || error.message))
      } finally {
        sending.value = false
      }
    }

    const clearHistory = async () => {
      try {
        await clearChatHistory(serviceId)
        messages.value = []
        message.success('历史记录已清空')
      } catch (error) {
        message.error('清空历史记录失败')
      }
    }

    const formatTime = (timeStr) => {
      if (!timeStr) return ''
      const date = new Date(timeStr)
      return date.toLocaleString('zh-CN')
    }

    const scrollToBottom = () => {
      nextTick(() => {
        if (chatContainer.value) {
          chatContainer.value.scrollTop = chatContainer.value.scrollHeight
        }
      })
    }

    watch(messages, () => {
      scrollToBottom()
    }, { deep: true })

    const goBack = () => {
      router.push({ name: 'services' })
    }

    onMounted(() => {
      loadService()
      loadMessages()
    })

    return {
      serviceName,
      messages,
      inputMessage,
      sending,
      chatContainer,
      sendMessage,
      clearHistory,
      formatTime,
      goBack
    }
  }
}
</script>

