<script setup lang="ts">
import { computed, onMounted, reactive, ref, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { instancesApi, type InstanceInput } from '@/api/instances'
import { strategiesApi, itemsOf } from '@/api/extensions'
import { accountsApi } from '@/api/accounts'
import { authApi } from '@/api/auth'
import { fieldErrors } from '@/api/http'
import type { Account, Strategy } from '@/api/types'
import { handleError } from '@/composables/useErrors'
import { mergeValues } from '@/utils/schema'
import { req } from '@/utils/rules'
import PageHeader from '@/components/PageHeader.vue'
import ErrorState from '@/components/ErrorState.vue'
import SchemaForm from '@/components/SchemaForm.vue'
import CapabilityBadges from '@/components/CapabilityBadges.vue'
import StrategyFit from '@/components/StrategyFit.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const loading = ref(true)
const loadError = ref<unknown>(null)
const strategies = shallowRef<Strategy[]>([])
const accounts = shallowRef<Account[]>([])
const strategy = shallowRef<Strategy | null>(null)
const strategyLoading = ref(false)

const formRef = ref<FormInstance>()
const paramsRef = ref<InstanceType<typeof SchemaForm>>()
const form = reactive({
  strategy_id: '',
  name: '',
  symbol: '',
  paper: true,
  account_id: null as number | null,
  paper_balance: 10000 as number | null,
  fee_rate: 0.001,
  slippage: 0.0005,
  stop_action: 'keep',
  market_type: 'spot' as 'spot' | 'futures',
  leverage: 1,
  live_capital: null as number | null,
})
const params = ref<Record<string, unknown>>({})
const errs = ref<Record<string, string>>({})
const saving = ref(false)
// 服务器没开实盘（ENABLE_LIVE_TRADING=false）时只能建模拟盘；不知道时先按开着处理，后端会再拦
const liveEnabled = ref(true)

const symbolOptions = computed(() => strategy.value?.runtime?.symbols ?? [])
// 扫描类策略：不用选交易对，一个实例扫全部 USDT 交易对
const isScan = computed(() => !!strategy.value?.runtime?.scan)
const marketTypes = computed(() => strategy.value?.runtime?.market_type ?? ['spot'])
const onlineAccounts = computed(() =>
  accounts.value.filter((a) => (a.status === 'online' || a.status === 'active') && (a.market_type ?? 'spot') === form.market_type),
)
const loadedStrategies = computed(() => strategies.value.filter((s) => s.loaded !== false))

const rules = computed<FormRules>(() => ({
  strategy_id: [req()],
  name: [req(), { min: 1, max: 64, trigger: ['blur', 'change'], message: t('schema.maxLength', { n: 64 }) }],
  symbol: isScan.value ? [] : [req()],
  account_id: form.paper ? [] : [req()],
  paper_balance: form.paper ? [req()] : [],
}))

async function init() {
  loading.value = true
  loadError.value = null
  try {
    const [s, a, st] = await Promise.all([
      strategiesApi.list({ page_size: 100 }),
      accountsApi.list({ page: 1, page_size: 100 }),
      authApi.setupStatus().catch(() => null),
    ])
    if (st && st.live_trading === false) {
      liveEnabled.value = false
      form.paper = true
    }
    strategies.value = itemsOf(s)
    accounts.value = a.items ?? []
    const q = typeof route.query.strategy === 'string' ? route.query.strategy : ''
    if (q && strategies.value.some((x) => x.id === q)) {
      form.strategy_id = q
      await pickStrategy(q)
    }
  } catch (e) {
    loadError.value = e
  } finally {
    loading.value = false
  }
}

async function pickStrategy(id: string) {
  strategy.value = null
  params.value = {}
  if (!id) return
  strategyLoading.value = true
  try {
    const s = await strategiesApi.get(id)
    strategy.value = s
    params.value = mergeValues(s.params_schema ?? [], {})
    const syms = s.runtime?.symbols ?? []
    if (syms.length && !syms.includes(form.symbol)) form.symbol = syms[0]
    const types = s.runtime?.market_type ?? ['spot']
    if (!types.includes(form.market_type)) form.market_type = (types[0] as 'spot' | 'futures') ?? 'spot'
    // 参数里有杠杆时与实例杠杆保持一致
    const lev = (s.params_schema ?? []).find((f) => f.key === 'leverage')
    if (lev) form.leverage = Number(params.value.leverage ?? 1)
    if (!form.name) form.name = `${s.name}-${Date.now().toString(36).slice(-4)}`
  } catch {
    form.strategy_id = ''
  } finally {
    strategyLoading.value = false
  }
}

function withLeverage(v: Record<string, unknown>) {
  if (form.market_type === 'futures' && 'leverage' in params.value) return { ...v, leverage: form.leverage }
  return v
}

function errOf(k: string) {
  return errs.value[k] || ''
}

async function submit() {
  const okBase = await formRef.value?.validate().catch(() => false)
  const okParams = paramsRef.value ? paramsRef.value.validate() : true
  if (!okBase || !okParams) return
  saving.value = true
  errs.value = {}
  const body: InstanceInput = {
    name: form.name.trim(),
    strategy_id: form.strategy_id,
    symbol: isScan.value ? '' : form.symbol.trim().toUpperCase(),
    market_type: form.market_type,
    paper: form.paper,
    account_id: form.paper ? null : form.account_id,
    paper_balance: form.paper ? form.paper_balance : null,
    paper_config: form.paper
      ? { fee_rate: form.fee_rate, slippage: form.slippage, ...(form.market_type === 'futures' ? { leverage: form.leverage } : {}) }
      : {
          ...(form.live_capital ? { live_capital: form.live_capital } : {}),
          ...(form.market_type === 'futures' ? { leverage: form.leverage } : {}),
        },
    params: withLeverage(paramsRef.value ? paramsRef.value.values() : {}),
    stop_action: form.stop_action,
  }
  try {
    const inst = await instancesApi.create(body)
    ElMessage.success(t('instance.created'))
    void router.replace(`/instances/${inst.id}`)
  } catch (e) {
    errs.value = fieldErrors(e)
    handleError(e)
  } finally {
    saving.value = false
  }
}

onMounted(init)
</script>

<template>
  <div>
    <PageHeader :title="t('instance.create')" back />
    <ErrorState v-if="loadError" :error="loadError" @retry="init" />
    <div v-else v-loading="loading" class="create-wrap">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="130px" class="create-form" @submit.prevent>
        <el-card shadow="never" class="mb">
          <template #header>{{ t('instx.basic') }}</template>
          <el-form-item :label="t('instance.strategy')" prop="strategy_id" :error="errOf('strategy_id')">
            <el-select v-model="form.strategy_id" :placeholder="t('instx.selectStrategy')" filterable style="width: 100%" @change="pickStrategy">
              <el-option v-for="s in loadedStrategies" :key="s.id" :value="s.id" :label="`${s.name} (v${s.version})`" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="strategy" :label="t('instx.strategyCaps')">
            <div>
              <p v-if="strategy.description" class="desc">{{ strategy.description }}</p>
              <StrategyFit :strategy="strategy" compact :capital="form.paper ? form.paper_balance : null" />
              <CapabilityBadges :caps="strategy.capabilities" />
            </div>
          </el-form-item>
          <el-form-item :label="t('instance.name')" prop="name" :error="errOf('name')">
            <el-input v-model="form.name" maxlength="64" show-word-limit />
          </el-form-item>
          <el-form-item v-if="strategy" :label="t('mkt.type')" :error="errOf('market_type')">
            <el-radio-group v-model="form.market_type">
              <el-radio-button value="spot" :disabled="!marketTypes.includes('spot')">{{ t('mkt.spot') }}</el-radio-button>
              <el-radio-button value="futures" :disabled="!marketTypes.includes('futures')">{{ t('mkt.futures') }}</el-radio-button>
            </el-radio-group>
            <div v-if="!marketTypes.includes('futures')" class="hint">{{ t('mkt.spotOnly') }}</div>
          </el-form-item>
          <el-form-item v-if="isScan" :label="t('mkt.scope')">
            <div>
              <b>{{ form.market_type === 'futures' ? t('mkt.scopeFutures') : t('mkt.scopeSpot') }}</b>
              <div class="hint">{{ t('mkt.scopeHint') }}</div>
            </div>
          </el-form-item>
          <el-form-item v-else :label="t('instance.symbol')" prop="symbol" :error="errOf('symbol')">
            <el-select v-if="symbolOptions.length" v-model="form.symbol" filterable style="width: 100%">
              <el-option v-for="s in symbolOptions" :key="s" :value="s" :label="s" />
            </el-select>
            <el-input v-else v-model="form.symbol" :placeholder="t('instx.symbolPh')" />
            <div v-if="form.market_type === 'futures'" class="hint">{{ t('mkt.futSymbolHint') }}</div>
          </el-form-item>
          <el-form-item v-if="form.market_type === 'futures'" :label="t('mkt.leverage')" :error="errOf('paper_config.leverage')">
            <el-input-number v-model="form.leverage" :min="1" :max="20" :step="1" controls-position="right" />
            <span class="suffix">x · {{ t('mkt.isolated') }}</span>
            <div class="hint">{{ t('mkt.leverageHint') }}</div>
          </el-form-item>
          <el-form-item :label="t('instance.mode')">
            <el-radio-group v-model="form.paper">
              <el-radio-button :value="true">{{ t('common.paper') }}</el-radio-button>
              <el-radio-button :value="false" :disabled="!liveEnabled">{{ t('common.live') }}</el-radio-button>
            </el-radio-group>
            <div v-if="!liveEnabled" class="hint">{{ t('mkt.liveOff') }}</div>
          </el-form-item>
          <template v-if="!form.paper">
            <el-form-item :label="t('instance.account')" prop="account_id" :error="errOf('account_id')">
              <el-select v-model="form.account_id" :placeholder="t('instance.selectAccount')" style="width: 100%">
                <el-option v-for="a in onlineAccounts" :key="a.id" :value="a.id" :label="a.name" :disabled="a.readonly">
                  <span>{{ a.name }}</span>
                  <span class="opt-sub">{{ a.env }}{{ a.readonly ? ` · ${t('common.readonly')}` : '' }}</span>
                </el-option>
              </el-select>
              <div v-if="!onlineAccounts.length" class="hint">
                {{ t('instx.noOnlineAccount') }} <router-link to="/accounts">{{ t('nav.accounts') }}</router-link>
              </div>
            </el-form-item>
            <el-form-item :label="t('livex.capital')" :error="errOf('paper_config.live_capital')">
              <el-input-number v-model="form.live_capital" :min="1" :step="100" controls-position="right" :placeholder="t('livex.optional')" />
              <span class="suffix">USDT</span>
              <div class="hint">{{ t('livex.capitalHelp') }}</div>
            </el-form-item>
          </template>
          <template v-else>
            <el-form-item :label="t('instance.paperBalance')" prop="paper_balance" :error="errOf('paper_balance')">
              <el-input-number v-model="form.paper_balance" :min="1" :step="1000" controls-position="right" />
              <span class="suffix">USDT</span>
            </el-form-item>
            <el-form-item :label="t('instance.paperFee')" :error="errOf('paper_config.fee_rate')">
              <el-input-number v-model="form.fee_rate" :min="0" :max="0.1" :step="0.0001" :precision="4" controls-position="right" />
            </el-form-item>
            <el-form-item :label="t('instance.paperSlippage')" :error="errOf('paper_config.slippage')">
              <el-input-number v-model="form.slippage" :min="0" :max="0.1" :step="0.0001" :precision="4" controls-position="right" />
            </el-form-item>
          </template>
          <el-form-item :label="t('instance.stopAction')" :error="errOf('stop_action')">
            <el-radio-group v-model="form.stop_action">
              <el-radio value="keep">{{ t('instance.stopKeep') }}</el-radio>
              <el-radio value="close">{{ t('instance.stopClose') }}</el-radio>
            </el-radio-group>
          </el-form-item>
        </el-card>
      </el-form>

      <el-card shadow="never" class="mb">
        <template #header>{{ t('instance.params') }}</template>
        <div v-loading="strategyLoading">
          <el-empty v-if="!strategy" :description="t('instance.strategyFirst')" :image-size="48" />
          <SchemaForm
            v-else-if="strategy.params_schema?.length"
            ref="paramsRef"
            v-model="params"
            :schema="strategy.params_schema"
            :symbols="symbolOptions"
            :external-errors="errs"
            error-prefix="params."
            label-width="130px"
          />
          <el-empty v-else :description="t('instx.noParams')" :image-size="48" />
        </div>
      </el-card>

      <p class="note">{{ t('instance.noPluginNote') }}</p>
      <div class="actions">
        <el-button @click="router.back()">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" :disabled="!strategy" @click="submit">{{ t('instx.createSubmit') }}</el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.create-wrap { max-width: 860px; }
.mb { margin-bottom: 16px; }
.desc { margin: 0 0 6px; color: var(--el-text-color-secondary); font-size: 13px; }
.hint { color: var(--el-text-color-secondary); font-size: 12px; margin-top: 4px; }
.suffix { margin-left: 8px; color: var(--el-text-color-secondary); }
.opt-sub { float: right; color: var(--el-text-color-secondary); font-size: 12px; margin-left: 12px; }
.note { color: var(--el-text-color-secondary); font-size: 13px; }
.actions { display: flex; gap: 8px; justify-content: flex-end; }
@media (max-width: 767px) {
  .create-form :deep(.el-form-item) { display: block; }
  .create-form :deep(.el-form-item__label) { justify-content: flex-start; width: auto !important; }
}
</style>
