<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { num } from '@/utils/format'

const props = defineProps<{ modelValue: boolean; strategy: string; code: string; name?: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'done', v: any): void }>()

const visible = computed({ get: () => props.modelValue, set: (v) => emit('update:modelValue', v) })
const loading = ref(false)
const submitting = ref(false)
const plan = ref<any>(null)
const quantity = ref(0)
const placeOrder = ref(true)

let seq = 0
async function load() {
  const my = ++seq
  loading.value = true
  plan.value = null
  try {
    const res = await api.dianjinPreview(props.strategy, props.code)
    if (my !== seq) return
    plan.value = res
    quantity.value = res.quantity
  } finally {
    if (my === seq) loading.value = false
  }
}

watch(() => [props.modelValue, props.code, props.strategy], ([v]) => { if (v) load() }, { immediate: true })

async function submit() {
  if (!plan.value) return
  if (!quantity.value || quantity.value % 100) return ElMessage.warning('数量必须为 100 的整数倍')
  const override = !plan.value.allowed
  await ElMessageBox.confirm(
    (override ? `不满足开仓条件：${plan.value.warnings.join('；')}。` : '') +
      `确定按${plan.value.strategy_name}买入 ${props.name || props.code} ${quantity.value} 股（约 ${num(quantity.value * plan.value.price)} 元）${placeOrder.value ? '，并提交模拟盘买单' : ''}吗？点金术不设止损，涨破卖出线才卖出。`,
    override ? '不满足开仓条件' : '确认开仓',
    { type: override ? 'warning' : 'info', confirmButtonText: override ? '仍然开仓' : '确认开仓' },
  )
  submitting.value = true
  try {
    const res = await api.dianjinOpen(props.strategy, { code: props.code, quantity: quantity.value, place_order: placeOrder.value, override })
    ElMessage.success(res.order ? `已创建持仓计划并提交模拟买单（${res.order.status}）` : '已创建持仓计划')
    emit('done', res)
    visible.value = false
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" :title="`开仓 · ${name || ''} ${code}`" width="520px">
    <div v-loading="loading" style="min-height: 140px">
      <template v-if="plan">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="策略">{{ plan.strategy_name }}</el-descriptions-item>
          <el-descriptions-item label="所处区域">{{ plan.zone_name }}</el-descriptions-item>
          <el-descriptions-item label="参考价">{{ num(plan.price) }}</el-descriptions-item>
          <el-descriptions-item label="现价 / MA120">{{ plan.metrics?.ratio ? `${(plan.metrics.ratio * 100).toFixed(0)}%` : '--' }}</el-descriptions-item>
          <el-descriptions-item label="卖出线">{{ num(plan.metrics?.sell_line) }}</el-descriptions-item>
          <el-descriptions-item label="已持有">{{ plan.held }} / {{ plan.max_positions }} 只</el-descriptions-item>
          <el-descriptions-item label="等权数量">{{ plan.quantity }} 股（资产 {{ num(plan.equity) }} ÷ {{ plan.max_positions }}）</el-descriptions-item>
          <el-descriptions-item label="金额">{{ num(plan.amount) }}</el-descriptions-item>
        </el-descriptions>
        <el-alert v-for="w in plan.warnings" :key="w" :title="w" type="error" show-icon :closable="false" style="margin-top: 8px" />
        <el-form label-width="96px" style="margin-top: 14px">
          <el-form-item label="买入数量"><el-input-number v-model="quantity" :min="100" :step="100" step-strictly /></el-form-item>
          <el-form-item label="模拟盘下单"><el-switch v-model="placeOrder" /></el-form-item>
        </el-form>
        <div class="muted small">点金术不设止损，涨破 MA120 的 112% 才卖出；回测结论见「策略」页。</div>
      </template>
    </div>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="danger" :loading="submitting" :disabled="!plan" @click="submit">{{ plan && !plan.allowed ? '仍然开仓' : '确认开仓' }}</el-button>
    </template>
  </el-dialog>
</template>
