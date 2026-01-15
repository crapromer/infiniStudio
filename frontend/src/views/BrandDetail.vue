<template>
  <div>
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 32px">
      <div style="display: flex; align-items: center; gap: 16px">
        <a-button @click="goBack" style="border-radius: 6px">← 返回</a-button>
        <h2 style="margin: 0; font-size: 24px; font-weight: 600; color: #262626; display: flex; align-items: center; gap: 12px">
          <span style="font-size: 28px">🏢</span>
          <span>{{ brandName }}</span>
        </h2>
      </div>
      <a-button type="primary" @click="showModal" size="large" style="height: 40px; padding: 0 24px; font-weight: 500">
        <span style="margin-right: 8px">➕</span>添加加速卡
      </a-button>
    </div>

    <a-card :bordered="false">
      <a-table 
        :columns="columns" 
        :data-source="accelerators" 
        :pagination="false"
        row-key="id"
        :bordered="false"
      >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'action'">
          <a-button type="link" @click="editAccelerator(record)" style="color: #667eea; font-weight: 500; padding: 0 8px">编辑</a-button>
          <a-popconfirm title="确定删除这个加速卡吗？" @confirm="deleteAccelerator(record.id)">
            <a-button type="link" danger style="font-weight: 500; padding: 0 8px">删除</a-button>
          </a-popconfirm>
        </template>
      </template>
    </a-table>
    </a-card>

    <a-modal
      v-model:open="modalVisible"
      :title="editingAccelerator ? '编辑加速卡' : '添加加速卡'"
      width="600px"
      @ok="handleSubmit"
      @cancel="handleCancel"
    >
      <a-form :model="form" :label-col="{ span: 8 }" :wrapper-col="{ span: 16 }">
        <a-form-item label="名称" required>
          <a-input v-model:value="form.name" placeholder="请输入名称" />
        </a-form-item>
        <a-form-item label="型号" required>
          <a-input v-model:value="form.model" placeholder="请输入型号" />
        </a-form-item>
        <a-form-item label="显存">
          <a-input v-model:value="form.memory" placeholder="如：80GB" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script>
import { ref, onMounted, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getBrands, getAccelerators, createAccelerator, updateAccelerator, deleteAccelerator as deleteAcceleratorApi } from '../api'
import { message } from 'ant-design-vue'

export default {
  name: 'BrandDetail',
  setup() {
    const router = useRouter()
    const route = useRoute()
    const brandId = parseInt(route.params.id)
    const brandName = ref('')
    const accelerators = ref([])
    const modalVisible = ref(false)
    const editingAccelerator = ref(null)
    const form = ref({
      name: '',
      model: '',
      memory: ''
    })

    const columns = [
      { title: '名称', dataIndex: 'name', key: 'name' },
      { title: '型号', dataIndex: 'model', key: 'model' },
      { title: '显存', dataIndex: 'memory', key: 'memory' },
      { title: '操作', key: 'action' }
    ]

    const loadData = async () => {
      try {
        const [brandsRes, acceleratorsRes] = await Promise.all([
          getBrands(),
          getAccelerators(brandId)
        ])
        const brand = brandsRes.data.find(b => b.id === brandId)
        if (brand) {
          brandName.value = brand.name
        }
        accelerators.value = acceleratorsRes.data
      } catch (error) {
        message.error('加载数据失败')
      }
    }

    const showModal = () => {
      editingAccelerator.value = null
      form.value = {
        name: '',
        model: '',
        memory: ''
      }
      modalVisible.value = true
    }

    const editAccelerator = (accelerator) => {
      editingAccelerator.value = accelerator
      form.value = { ...accelerator }
      modalVisible.value = true
    }

    const handleSubmit = async () => {
      if (!form.value.name || !form.value.model) {
        message.warning('请输入名称和型号')
        return
      }
      
      try {
        if (editingAccelerator.value) {
          await updateAccelerator(editingAccelerator.value.id, form.value)
          message.success('更新成功')
        } else {
          await createAccelerator(brandId, form.value)
          message.success('创建成功')
        }
        modalVisible.value = false
        loadData()
      } catch (error) {
        message.error('操作失败')
      }
    }

    const handleCancel = () => {
      modalVisible.value = false
    }

    const deleteAccelerator = async (id) => {
      try {
        await deleteAcceleratorApi(id)
        message.success('删除成功')
        loadData()
      } catch (error) {
        message.error('删除失败')
      }
    }

    const goBack = () => {
      router.push({ name: 'brands' })
    }

    onMounted(() => {
      loadData()
    })

    return {
      brandName,
      accelerators,
      columns,
      modalVisible,
      editingAccelerator,
      form,
      showModal,
      editAccelerator,
      handleSubmit,
      handleCancel,
      deleteAccelerator,
      goBack
    }
  }
}
</script>

