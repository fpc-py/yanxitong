<script setup lang="ts">
// 极简账号弹窗：注册 / 登录 / 已登录面板（全局唯一入口：左下角身份卡与配额提示唤起）
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const username = ref('')
const password = ref('')
const passwordConfirm = ref('')

const isRegister = computed(() => auth.authMode === 'register')

const usernameError = computed(() => {
  const v = username.value.trim()
  if (!v) return ''
  if (!/^[\w\u4e00-\u9fa5-]{2,32}$/.test(v)) return '用户名为 2-32 位中文、字母、数字、下划线或连字符'
  return ''
})

const passwordError = computed(() => {
  if (!password.value) return ''
  if (password.value.length < 6) return '密码至少 6 位'
  return ''
})

const confirmError = computed(() => {
  if (!isRegister.value || !passwordConfirm.value) return ''
  return password.value === passwordConfirm.value ? '' : '两次输入的密码不一致'
})

const canSubmit = computed(
  () =>
    !!username.value.trim() &&
    !!password.value &&
    !usernameError.value &&
    !passwordError.value &&
    !confirmError.value &&
    (!isRegister.value || !!passwordConfirm.value),
)

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') auth.closeAuth()
}

watch(
  () => auth.authOpen,
  (open) => {
    if (open) {
      username.value = ''
      password.value = ''
      passwordConfirm.value = ''
      auth.error = null
      window.addEventListener('keydown', onKeydown)
    } else {
      window.removeEventListener('keydown', onKeydown)
    }
  },
)

onUnmounted(() => window.removeEventListener('keydown', onKeydown))

function switchMode(m: 'register' | 'login'): void {
  auth.authMode = m
  auth.error = null
}

async function submit(): Promise<void> {
  if (!canSubmit.value || auth.loading) return
  const name = username.value.trim()
  const ok = isRegister.value
    ? await auth.register(name, password.value)
    : await auth.login(name, password.value)
  if (ok) {
    ElMessage.success(isRegister.value ? `注册成功，欢迎，${auth.displayName}` : `欢迎回来，${auth.displayName}`)
    auth.closeAuth()
  }
}

async function onLogout(): Promise<void> {
  await auth.logout()
  ElMessage.success('已退出登录')
  auth.closeAuth()
}
</script>

<template>
  <Teleport to="body">
    <Transition name="am-fade">
      <div v-if="auth.authOpen" class="am-mask" @click.self="auth.closeAuth()">
        <div class="am-card">
          <button class="am-x" title="关闭" @click="auth.closeAuth()">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>

          <!-- 已登录：账号信息 + 退出 -->
          <template v-if="auth.isLoggedIn">
            <div class="am-user">
              <span class="am-avatar">{{ auth.displayName.slice(0, 1) }}</span>
              <div class="am-meta">
                <div class="am-name">{{ auth.displayName }}</div>
                <div class="am-sub mono">UID · {{ auth.user?.id }}</div>
              </div>
            </div>
            <p class="am-note">研究会话、文献库与知识图谱已绑定当前账号，且与其他用户相互隔离。</p>
            <el-button class="am-submit" :loading="auth.loading" @click="onLogout">退出登录</el-button>
          </template>

          <!-- 未登录：注册 / 登录 -->
          <template v-else>
            <div class="am-tabs">
              <button class="am-tab" :class="{ active: isRegister }" @click="switchMode('register')">注册</button>
              <button class="am-tab" :class="{ active: !isRegister }" @click="switchMode('login')">登录</button>
            </div>

            <div v-if="auth.quotaExhausted" class="am-quota is-out">
              免费体验次数已用完（{{ auth.quota?.limit }} 次），注册 / 登录后可继续提问
            </div>
            <div v-else-if="auth.remaining !== null" class="am-quota">
              未登录访客 · 免费剩余 <b class="mono">{{ auth.remaining }}</b> / {{ auth.quota?.limit }} 次
            </div>

            <el-input
              v-model="username"
              size="large"
              placeholder="用户名（2-32 位中文 / 字母 / 数字）"
              :maxlength="32"
              @keydown.enter="submit"
            />
            <div v-if="usernameError" class="am-err">{{ usernameError }}</div>

            <el-input
              v-model="password"
              type="password"
              size="large"
              show-password
              placeholder="密码（至少 6 位）"
              :maxlength="64"
              @keydown.enter="submit"
            />
            <div v-if="passwordError" class="am-err">{{ passwordError }}</div>

            <template v-if="isRegister">
              <el-input
                v-model="passwordConfirm"
                type="password"
                size="large"
                show-password
                placeholder="确认密码"
                :maxlength="64"
                @keydown.enter="submit"
              />
              <div v-if="confirmError" class="am-err">{{ confirmError }}</div>
            </template>

            <div v-if="auth.error" class="am-alert">{{ auth.error }}</div>

            <el-button
              type="primary"
              class="am-submit"
              :loading="auth.loading"
              :disabled="!canSubmit || auth.loading"
              @click="submit"
            >
              {{ isRegister ? '注册' : '登录' }}
            </el-button>
          </template>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.am-mask {
  position: fixed;
  inset: 0;
  z-index: 2000;
  padding: 24px;
  display: grid;
  place-items: center;
  background: rgba(28, 26, 22, 0.38);
  backdrop-filter: blur(3px);
}

.am-card {
  position: relative;
  width: 340px;
  max-width: 100%;
  padding: 26px 24px 22px;
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.am-x {
  position: absolute;
  top: 10px;
  right: 10px;
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--ink-3);
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
}

.am-x:hover {
  background: var(--soft);
  color: var(--ink);
}

.am-x svg {
  width: 13px;
  height: 13px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
}

.am-tabs {
  display: flex;
  gap: 16px;
  margin: 2px 0 6px;
}

.am-tab {
  padding: 2px 1px 6px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--ink-3);
  font-family: var(--font-body);
  font-size: 14px;
  cursor: pointer;
  transition: color 0.15s var(--ease), border-color 0.15s var(--ease);
}

.am-tab:hover {
  color: var(--ink);
}

.am-tab.active {
  color: var(--ink);
  border-bottom-color: var(--accent);
  font-weight: 500;
}

.am-quota {
  font-size: 12px;
  color: var(--ink-3);
}

.am-quota.is-out {
  color: var(--danger);
}

.am-err {
  margin: -4px 0 0;
  font-size: 11.5px;
  color: var(--danger);
}

.am-alert {
  padding: 8px 10px;
  border: 1px solid var(--danger-line);
  border-radius: var(--radius-sm);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 12px;
  line-height: 1.6;
}

.am-submit {
  width: 100%;
  margin-top: 2px;
}

.am-user {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 4px;
}

.am-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--ink);
  color: var(--bg);
  display: grid;
  place-items: center;
  font-family: var(--font-display);
  font-style: italic;
  font-size: 18px;
  flex: 0 0 auto;
}

.am-meta {
  min-width: 0;
  line-height: 1.5;
}

.am-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.am-sub {
  font-size: 11px;
  color: var(--ink-3);
}

.am-note {
  margin: 4px 0 2px;
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.6;
}

.am-fade-enter-active,
.am-fade-leave-active {
  transition: opacity 0.16s var(--ease);
}

.am-fade-enter-from,
.am-fade-leave-to {
  opacity: 0;
}
</style>
