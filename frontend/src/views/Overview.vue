<template>
  <div>
    <h2 style="margin: 0 0 32px 0; font-size: 24px; font-weight: 600; color: #262626; display: flex; align-items: center; gap: 12px">
      <span style="font-size: 28px">📊</span>
      <span>总览</span>
    </h2>
    
    <a-row :gutter="[16, 16]" style="margin-bottom: 32px">
      <a-col :xs="24" :sm="12" :md="8">
        <a-card :bordered="false" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 12px">
          <a-statistic 
            title="服务器总数" 
            :value="stats.server_count"
            :value-style="{ color: '#fff', fontWeight: 'bold', fontSize: '32px' }"
            :title-style="{ color: 'rgba(255,255,255,0.85)', fontSize: '14px' }"
          />
        </a-card>
      </a-col>
      <a-col :xs="24" :sm="12" :md="8">
        <a-card :bordered="false" style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); border-radius: 12px">
          <a-statistic 
            title="在线服务器" 
            :value="stats.online_server_count"
            :value-style="{ color: '#fff', fontWeight: 'bold', fontSize: '32px' }"
            :title-style="{ color: 'rgba(255,255,255,0.85)', fontSize: '14px' }"
          />
        </a-card>
      </a-col>
      <a-col :xs="24" :sm="12" :md="8">
        <a-card :bordered="false" style="background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); border-radius: 12px">
          <a-statistic 
            title="服务总数" 
            :value="stats.service_count"
            :value-style="{ color: '#fff', fontWeight: 'bold', fontSize: '32px' }"
            :title-style="{ color: 'rgba(255,255,255,0.85)', fontSize: '14px' }"
          />
        </a-card>
      </a-col>
    </a-row>

    <a-card :bordered="false" style="margin-bottom: 24px">
      <template #title>
        <div style="display: flex; align-items: center; gap: 8px">
          <span style="font-size: 20px">🖥️</span>
          <span style="font-weight: 600; font-size: 18px">服务器列表</span>
        </div>
      </template>
      <a-row :gutter="[16, 16]">
        <a-col :xs="24" :sm="12" :md="8" v-for="server in servers" :key="server.id">
          <a-card 
            :title="server.name" 
            hoverable
            :bordered="false"
            style="border: 1px solid #f0f0f0; transition: all 0.3s"
            @click="openSSHTerminal(server)"
            :style="{ 
              cursor: 'pointer',
              borderRadius: '12px'
            }"
          >
            <p style="margin: 8px 0; color: #595959">
              <span style="font-weight: 500; color: #8c8c8c">品牌型号：</span>
              {{ server.brand_name || '-' }} / {{ server.model_name || '-' }}
            </p>
            <p style="margin: 8px 0; color: #595959">
              <span style="font-weight: 500; color: #8c8c8c">IP地址：</span>
              <span style="font-family: monospace">{{ server.host_ip }}</span>
            </p>
            <p style="margin: 8px 0">
              <span style="font-weight: 500; color: #8c8c8c">状态：</span>
              <a-tag :color="server.status === 'online' ? 'success' : 'error'" style="font-weight: 500; padding: 4px 12px; border-radius: 4px">
                {{ server.status === 'online' ? '在线' : '离线' }}
              </a-tag>
            </p>
            <div v-if="server.status === 'online'" style="margin-top: 12px; padding-top: 12px; border-top: 1px solid #f0f0f0">
              <div style="display: flex; justify-content: space-between; margin-bottom: 8px">
                <span style="font-weight: 500; color: #8c8c8c; font-size: 13px">CPU使用率：</span>
                <a-progress 
                  :percent="server.resources?.cpu_usage ?? 0" 
                  :status="(server.resources?.cpu_usage ?? 0) > 80 ? 'exception' : 'normal'"
                  :stroke-color="getProgressColor(server.resources?.cpu_usage ?? 0)"
                  :show-info="true"
                  :format="(percent) => server.resources?.cpu_usage !== null && server.resources?.cpu_usage !== undefined ? `${percent}%` : '加载中...'"
                  style="flex: 1; margin-left: 8px"
                />
              </div>
              <div style="display: flex; justify-content: space-between; margin-bottom: 8px">
                <span style="font-weight: 500; color: #8c8c8c; font-size: 13px">内存使用率：</span>
                <a-progress 
                  :percent="server.resources?.memory_usage ?? 0" 
                  :status="(server.resources?.memory_usage ?? 0) > 80 ? 'exception' : 'normal'"
                  :stroke-color="getProgressColor(server.resources?.memory_usage ?? 0)"
                  :show-info="true"
                  :format="(percent) => server.resources?.memory_usage !== null && server.resources?.memory_usage !== undefined ? `${percent}%` : '加载中...'"
                  style="flex: 1; margin-left: 8px"
                />
              </div>
              <div style="display: flex; justify-content: space-between">
                <span style="font-weight: 500; color: #8c8c8c; font-size: 13px">磁盘使用率：</span>
                <a-progress 
                  :percent="server.resources?.disk_usage ?? 0" 
                  :status="(server.resources?.disk_usage ?? 0) > 80 ? 'exception' : 'normal'"
                  :stroke-color="getProgressColor(server.resources?.disk_usage ?? 0)"
                  :show-info="true"
                  :format="(percent) => server.resources?.disk_usage !== null && server.resources?.disk_usage !== undefined ? `${percent}%` : '加载中...'"
                  style="flex: 1; margin-left: 8px"
                />
              </div>
            </div>
            <template #actions>
              <a-button type="link" @click.stop="openSSHTerminal(server)" style="color: #667eea; font-weight: 500">
                SSH连接
              </a-button>
            </template>
          </a-card>
        </a-col>
      </a-row>
    </a-card>

    <a-card :bordered="false">
      <template #title>
        <div style="display: flex; align-items: center; gap: 8px">
          <span style="font-size: 20px">🚀</span>
          <span style="font-weight: 600; font-size: 18px">已部署服务</span>
        </div>
      </template>
      <a-row :gutter="[16, 16]">
        <a-col :xs="24" :sm="12" :md="8" v-for="service in services" :key="service.id">
          <a-card 
            hoverable
            :bordered="false"
            style="border: 1px solid #f0f0f0; border-radius: 12px; transition: all 0.3s; cursor: pointer"
            @click="enterService(service)"
          >
            <div style="margin-bottom: 12px">
              <h3 style="margin: 0 0 8px 0; font-size: 18px; font-weight: 600; color: #262626">
                {{ service.name }}
              </h3>
              <p style="margin: 0; color: #8c8c8c; font-size: 14px">
                <span style="font-weight: 500; color: #595959">模型：</span>
                {{ service.model_name || '-' }}
              </p>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 16px; padding-top: 16px; border-top: 1px solid #f0f0f0">
              <a-tag color="blue" style="font-weight: 500; padding: 4px 12px; border-radius: 4px">
                {{ service.server_ids?.length || 0 }} 个服务器
              </a-tag>
              <a-button type="link" @click.stop="enterService(service)" style="color: #667eea; font-weight: 500; padding: 0">
                进入服务 →
              </a-button>
            </div>
          </a-card>
        </a-col>
      </a-row>
      <a-empty v-if="services.length === 0" description="暂无已部署服务" style="padding: 40px 0" />
    </a-card>

    <a-modal
      v-model:open="sshModalVisible"
      :title="`SSH连接 - ${currentServer?.name}`"
      width="1200px"
      :footer="null"
      :maskClosable="false"
      :bodyStyle="{ padding: '0', overflow: 'hidden' }"
      @cancel="handleSSHModalClose"
    >
      <SshTerminal v-if="sshModalVisible" :server-id="currentServer?.id" :key="currentServer?.id" />
    </a-modal>
  </div>
</template>

<script>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { getStats, getServers, getServices, checkAllServers, getAllServersResources } from '../api'
import SshTerminal from '../components/SshTerminal.vue'

export default {
  name: 'Overview',
  components: {
    SshTerminal
  },
  setup() {
    const router = useRouter()
    const stats = ref({ server_count: 0, service_count: 0, online_server_count: 0 })
    const servers = ref([])
    const services = ref([])
    const sshModalVisible = ref(false)
    const currentServer = ref(null)
    let statusCheckInterval = null

    const loadData = async () => {
      try {
        const [statsRes, serversRes, servicesRes] = await Promise.all([
          getStats(),
          getServers(),
          getServices()
        ])
        stats.value = statsRes.data
        // 为每个服务器添加默认的resources属性
        servers.value = serversRes.data.map(server => ({
          ...server,
          resources: server.resources || {
            cpu_usage: null,
            memory_usage: null,
            disk_usage: null
          }
        }))
        services.value = servicesRes.data
      } catch (error) {
        console.error('加载数据失败:', error)
      }
    }

    const getProgressColor = (percent) => {
      if (percent >= 80) return '#ff4d4f'
      if (percent >= 60) return '#faad14'
      return '#52c41a'
    }

    const checkServerStatus = async () => {
      try {
        const [statusRes, resourcesRes] = await Promise.all([
          checkAllServers(),
          getAllServersResources()
        ])
        
        // 更新服务器状态
        const statusMap = {}
        statusRes.data.results.forEach(result => {
          statusMap[result.id] = result.status
        })
        
        // 更新资源使用情况
        const resourcesMap = {}
        resourcesRes.data.forEach(result => {
          resourcesMap[result.id] = {
            cpu_usage: result.cpu_usage,
            memory_usage: result.memory_usage,
            disk_usage: result.disk_usage
          }
        })
        
        servers.value = servers.value.map(server => ({
          ...server,
          status: statusMap[server.id] || server.status,
          resources: resourcesMap[server.id] || server.resources || {
            cpu_usage: null,
            memory_usage: null,
            disk_usage: null
          }
        }))
        
        // 重新加载统计数据
        const statsRes = await getStats()
        stats.value = statsRes.data
      } catch (error) {
        console.error('检查服务器状态失败:', error)
      }
    }

    const openSSHTerminal = (server) => {
      currentServer.value = server
      sshModalVisible.value = true
    }

    const handleSSHModalClose = () => {
      // 模态框关闭时会触发组件卸载，组件会自己处理连接清理
      sshModalVisible.value = false
      currentServer.value = null
    }

    const enterService = (service) => {
      router.push({ name: 'serviceChat', params: { id: service.id } })
    }

    onMounted(() => {
      loadData()
      // 每秒检查一次服务器状态
      statusCheckInterval = setInterval(checkServerStatus, 1000)
      // 立即检查一次
      checkServerStatus()
    })

    onUnmounted(() => {
      if (statusCheckInterval) {
        clearInterval(statusCheckInterval)
      }
    })

    return {
      stats,
      servers,
      services,
      sshModalVisible,
      currentServer,
      openSSHTerminal,
      enterService,
      handleSSHModalClose,
      getProgressColor
    }
  }
}
</script>

