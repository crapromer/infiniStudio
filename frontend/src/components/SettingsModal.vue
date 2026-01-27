<template>
  <a-modal
    v-model:open="visible"
    title="系统设置"
    :width="500"
    :maskClosable="false"
    @ok="handleSave"
    @cancel="handleCancel"
  >
    <a-form
      :model="formData"
      :label-col="{ span: 6 }"
      :wrapper-col="{ span: 18 }"
      style="margin-top: 24px"
    >
      <a-form-item label="后端地址" required>
        <a-input
          v-model:value="formData.backendHost"
          placeholder="请输入后端服务器地址（如：localhost 或 192.168.1.100）"
        />
      </a-form-item>
      <a-form-item label="后端端口" required>
        <a-input-number
          v-model:value="formData.backendPort"
          :min="1"
          :max="65535"
          style="width: 100%"
          placeholder="请输入后端服务器端口（如：5000）"
        />
      </a-form-item>
      <a-form-item label="访问模式">
        <a-radio-group v-model:value="formData.useProxy">
          <a-radio :value="true">代理模式（推荐）</a-radio>
          <a-radio :value="false">直接访问</a-radio>
        </a-radio-group>
        <div style="margin-top: 8px; color: #8c8c8c; font-size: 12px;">
          <span v-if="formData.useProxy">
            通过 nginx 代理访问后端，支持多机器访问
          </span>
          <span v-else>
            直接访问后端服务器，仅限本机或配置的服务器地址
          </span>
        </div>
      </a-form-item>
      <a-alert
        message="提示"
        :description="formData.useProxy ? '代理模式：前端通过 nginx 代理访问后端，支持局域网内其他机器访问。修改配置后需要刷新页面才能生效。' : '直接访问模式：前端直接访问后端服务器地址。仅当不使用 nginx 代理时使用此模式。修改配置后需要刷新页面才能生效。'"
        type="info"
        show-icon
        style="margin-top: 16px"
      />
    </a-form>
    <template #footer>
      <a-button @click="handleCancel">取消</a-button>
      <a-button type="primary" @click="handleSave" :loading="saving">保存</a-button>
    </template>
  </a-modal>
</template>

<script>
import { ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { getConfig, saveConfig } from '../utils/config'
import { refreshApiConfig } from '../api'

export default {
  name: 'SettingsModal',
  props: {
    open: {
      type: Boolean,
      default: false
    }
  },
  emits: ['update:open', 'saved'],
  setup(props, { emit }) {
    const visible = ref(props.open)
    const saving = ref(false)
    const formData = ref({
      backendHost: 'localhost',
      backendPort: 5000,
      useProxy: true
    })

    // 监听 open prop 变化
    watch(() => props.open, (newVal) => {
      visible.value = newVal
      if (newVal) {
        // 打开弹窗时加载当前配置
        loadConfig()
      }
    })

    // 监听 visible 变化，同步到父组件
    watch(visible, (newVal) => {
      emit('update:open', newVal)
    })

    // 加载配置
    const loadConfig = () => {
      const config = getConfig()
      formData.value = {
        backendHost: config.backendHost || 'localhost',
        backendPort: config.backendPort || 5000,
        useProxy: config.useProxy !== undefined ? config.useProxy : true
      }
    }

    // 验证表单
    const validateForm = () => {
      if (!formData.value.backendHost || formData.value.backendHost.trim() === '') {
        message.error('请输入后端地址')
        return false
      }
      if (!formData.value.backendPort || formData.value.backendPort < 1 || formData.value.backendPort > 65535) {
        message.error('请输入有效的端口号（1-65535）')
        return false
      }
      return true
    }

    // 保存配置
    const handleSave = async () => {
      if (!validateForm()) {
        return
      }

      saving.value = true
      try {
        const success = saveConfig({
          backendHost: formData.value.backendHost.trim(),
          backendPort: formData.value.backendPort,
          useProxy: formData.value.useProxy
        })

        if (success) {
          message.success('配置保存成功！请刷新页面使配置生效。')
          // 更新 API 配置（虽然需要刷新页面才能完全生效，但先更新一下）
          refreshApiConfig()
          emit('saved')
          visible.value = false
        } else {
          message.error('配置保存失败，请重试')
        }
      } catch (error) {
        console.error('保存配置失败:', error)
        message.error('保存配置时发生错误：' + error.message)
      } finally {
        saving.value = false
      }
    }

    // 取消
    const handleCancel = () => {
      visible.value = false
    }

    return {
      visible,
      saving,
      formData,
      handleSave,
      handleCancel
    }
  }
}
</script>

<style scoped>
:deep(.ant-form-item-label) {
  font-weight: 500;
}
</style>
