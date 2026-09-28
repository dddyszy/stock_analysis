<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { num, ratioPct } from '@/utils/format'

const props = defineProps<{ modelValue: boolean; code: string; name?: string; signalType?: string; signalDate?: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'done', v: any): void }>()

const loading = ref(false)
const submitting = ref(false)
const plan = ref<any>(null)
const quantity = ref(0)
const price = ref<number | undefined>()
const placeOrder = ref(true)

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const isBj = computed(() => props.code?.startsWith('bj'))
const riskAmount = computed(() => (plan.value ? quantity.value * (plan.value.entry_price - plan.value.stop) : 0))

let seq = 0
async function load() {
  const my = ++seq
  loading.value = true
  plan.value = null
  try {
    const res = await api.entryPreview({ code: props.code, signal_type: props.signalType, signal_date: props.signalDate })
    if (my !== seq) return
    plan.value = res
    quantity.value = res.quantity
    price.value = undefined
  } finally {
    if (my === seq) loading.value = false
  }
}

watch(
  () => [props.modelValue, props.code],
  ([v]) => {
    if (v) load()
  },
  { immediate: true },
)

async function submit() {
  if (!plan.value) return
  if (!quantity.value || quantity.value % 100) {
    ElMessage.warning('数量必须为 100 的整数倍')
    return
  }
  const override = !plan.value.allowed
  if (override) {
    await ElMessageBox.confirm(
      `系统判断不满足开仓条件：${(plan.value.warnings || []).join('；') || '未说明原因'}。确定仍要按 ${quantity.value} 股开仓${placeOrder.value && !isBj.value ? '并提交模拟买单' : ''}吗？`,
      '不满足开仓条件',
      { type: 'warning', confirmButtonText: '仍然开仓' },
    )
  }
  submitting.value = true
  try {
    const res = await api.createPlan({
      code: props.code,
      signal_type: plan.value.signal_type,
      signal_date: plan.value.signal_date,
      quantity: quantity.value,
      entry_price: price.value,
      place_order: placeOrder.value && !isBj.value,
      linked_sim: placeOrder.value && !isBj.value,
      override,
    })
    ElMessage.success(res.order ? `已创建持仓计划并提交模拟买单（${res.order.status}）` : '已创建持仓计划')
    emit('done', res)
    visible.value = false
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" :title="`开仓计划 · ${name || ''} ${code}`" width="560px">
    <div v-loading="loading" style="min-height: 160px">
      <template v-if="plan">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="信号">{{ plan.signal_name }}（{{ plan.signal_date }}）</el-descriptions-item>
          <el-descriptions-item label="参考买入价">{{ num(plan.entry_price) }}</el-descriptions-item>
          <el-descriptions-item label="结构位">{{ num(plan.structural_stop) }}</el-descriptions-item>
          <el-descriptions-item label="初始止损">
            <span class="down">{{ num(plan.stop) }}</span>
            <span class="muted">（{{ ratioPct((plan.entry_price - plan.stop) / plan.entry_price, 1) }}）</span>
          </el-descriptions-item>
          <el-descriptions-item label="1R">{{ num(plan.r_value, 3) }}</el-descriptions-item>
          <el-descriptions-item label="盈亏比">{{ num(plan.reward_risk) }}</el-descriptions-item>
          <el-descriptions-item label="目标一">{{ num(plan.target1) }}</el-descriptions-item>
          <el-descriptions-item label="目标二">{{ num(plan.target2) }}</el-descriptions-item>
          <el-descriptions-item label="建议数量">{{ plan.quantity }} 股</el-descriptions-item>
          <el-descriptions-item label="建议金额">{{ num(plan.position_value) }}</el-descriptions-item>
        </el-descriptions>
        <el-alert
          v-for="w in plan.warnings"
          :key="w"
          :title="w"
          :type="plan.allowed ? 'warning' : 'error'"
          show-icon
          :closable="false"
          style="margin-top: 8px"
        />
        <el-form label-width="96px" style="margin-top: 14px">
          <el-form-item label="买入数量">
            <el-input-number v-model="quantity" :min="100" :step="100" step-strictly />
            <span class="muted" style="margin-left: 10px">本笔风险约 {{ num(riskAmount) }} 元</span>
          </el-form-item>
          <el-form-item label="限价">
            <el-input-number v-model="price" :min="0" :precision="2" :step="0.01" placeholder="留空自动取现价" />
          </el-form-item>
          <el-form-item label="模拟盘下单">
            <el-switch v-model="placeOrder" :disabled="isBj" />
            <span v-if="isBj" class="muted" style="margin-left: 8px">北交所不支持模拟交易，只记录计划</span>
          </el-form-item>
        </el-form>
      </template>
    </div>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="danger" :loading="submitting" :disabled="!plan" @click="submit">
        {{ plan && !plan.allowed ? '仍然开仓' : '确认开仓' }}
      </el-button>
    </template>
  </el-dialog>
</template>
