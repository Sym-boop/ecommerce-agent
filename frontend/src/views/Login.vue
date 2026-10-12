<template>
  <div class="login-page">
    <div class="login-brand">
      <el-icon :size="30"><Shop /></el-icon>
      <div>
        <h1>电商多智能体运营驾驶舱</h1>
        <p>文案 · 图文 · 视频 · 投放 · 审查 全链路智能协同</p>
      </div>
    </div>

    <el-card class="login-card" shadow="always">
      <div class="login-tabs">
        <button
          class="login-tab"
          :class="{ 'is-active': mode === 'login' }"
          @click="switchMode('login')"
        >
          登录
        </button>
        <button
          class="login-tab"
          :class="{ 'is-active': mode === 'register' }"
          @click="switchMode('register')"
        >
          注册
        </button>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @submit.prevent
      >
        <el-form-item label="用户名" prop="username">
          <el-input
            v-model="form.username"
            :prefix-icon="User"
            placeholder="请输入用户名"
            clearable
            @keyup.enter="handleSubmit"
          />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            :prefix-icon="Lock"
            placeholder="请输入密码（至少 6 位）"
            show-password
            @keyup.enter="handleSubmit"
          />
        </el-form-item>

        <el-form-item v-if="mode === 'register'" label="昵称（选填）" prop="display_name">
          <el-input v-model="form.display_name" placeholder="用于展示的昵称" />
        </el-form-item>

        <el-alert
          v-if="hint"
          :type="hintType"
          :closable="false"
          show-icon
          class="login-hint"
        >
          <template #title>{{ hint }}</template>
          <template v-if="hintAction" #default>
            <el-button link type="primary" @click="hintAction.handler">
              {{ hintAction.text }}
            </el-button>
          </template>
        </el-alert>

        <el-button
          type="primary"
          size="large"
          class="login-submit"
          :loading="authStore.loading"
          @click="handleSubmit"
        >
          {{ mode === 'login' ? '登 录' : '注 册 并 登 录' }}
        </el-button>
      </el-form>

      <p class="login-foot">
        {{ mode === 'login' ? '还没有账号？' : '已有账号？' }}
        <el-button link type="primary" @click="switchMode(mode === 'login' ? 'register' : 'login')">
          {{ mode === 'login' ? '立即注册' : '返回登录' }}
        </el-button>
      </p>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Shop, User, Lock } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const formRef = ref(null)
const mode = ref('login')
const hint = ref('')
const hintType = ref('info')
const hintAction = ref(null)

const form = reactive({ username: '', password: '', display_name: '' })

const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, message: '用户名至少 3 个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
}

function switchMode(next) {
  mode.value = next
  hint.value = ''
  hintAction.value = null
  formRef.value?.clearValidate()
}

async function handleSubmit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  hint.value = ''
  hintAction.value = null

  const payload = { username: form.username.trim(), password: form.password }
  try {
    if (mode.value === 'login') {
      await authStore.login(payload)
      ElMessage.success('登录成功')
    } else {
      await authStore.register({ ...payload, display_name: form.display_name.trim() })
      ElMessage.success('注册成功，已自动登录')
    }
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/copy'
    router.replace(redirect)
  } catch (e) {
    const msg = e?.response?.data?.msg || e?.message || '操作失败，请稍后重试'
    hintType.value = 'error'
    if (mode.value === 'login' && msg.includes('账号不存在')) {
      // 用户不存在：明确提示并引导注册
      hint.value = msg
      hintAction.value = {
        text: '去注册',
        handler: () => {
          switchMode('register')
        },
      }
      ElMessage.warning(msg)
    } else {
      hint.value = msg
      ElMessage.error(msg)
    }
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 28px;
  padding: 40px 20px;
  background: radial-gradient(circle at 20% 20%, #2e4c86 0%, #1d2b4f 45%, #131d36 100%);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 16px;
  color: #fff;
}
.login-brand h1 {
  margin: 0;
  font-size: 22px;
  letter-spacing: 0.5px;
}
.login-brand p {
  margin: 6px 0 0;
  font-size: 13px;
  color: #a9b8d4;
}

.login-card {
  width: 420px;
  max-width: 100%;
  border-radius: 16px;
  border: none;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
}

.login-tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 18px;
  border-bottom: 1px solid var(--card-border);
}
.login-tab {
  flex: 1;
  padding: 10px 0;
  border: none;
  background: transparent;
  font-size: 15px;
  font-weight: 600;
  color: #909399;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: color 0.2s ease, border-color 0.2s ease;
}
.login-tab.is-active {
  color: #2e4c86;
  border-bottom-color: #2e4c86;
}

.login-hint {
  margin-bottom: 14px;
}
.login-submit {
  width: 100%;
  margin-top: 6px;
}
.login-foot {
  margin: 16px 0 0;
  text-align: center;
  font-size: 13px;
  color: #909399;
}
</style>