<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from "vue";

import type { RiskLevel } from "@/api/types";

const props = defineProps<{
  open: boolean;
  level: RiskLevel;
}>();

const emit = defineEmits<{ close: [] }>();

const info = computed(() => {
  if (props.level === "critical") {
    return {
      label: "危机风险",
      summary:
        "我们注意到你现在的状态可能需要更多支持。你不需要一个人扛着，请尽快联系可信任的人或专业帮助。",
    };
  }
  return {
    label: "高风险",
    summary:
      "我们注意到你最近承受了不少压力。这不是诊断，但找一个人聊聊，可能会让你轻一点。",
  };
});

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape" && props.open) emit("close");
}

onMounted(() => window.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown));
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="risk-overlay" @click.self="emit('close')">
      <div class="risk-card" role="dialog" aria-modal="true" aria-labelledby="risk-title">
        <div class="risk-ring" :class="level">
          <strong>{{ info.label }}</strong>
        </div>

        <h3 id="risk-title">风险评估</h3>
        <p class="risk-summary">{{ info.summary }}</p>

        <ul class="risk-actions">
          <li>联系你信任的朋友、家人或辅导员</li>
          <li>
            学校心理中心 / 心理援助热线
            <a href="tel:02558255200">025-58255200</a>
          </li>
          <li>如有立即危险，请拨打 <a href="tel:120">120</a></li>
        </ul>

        <p class="risk-note">这不是医学诊断，评估结果仅作为支持参考。</p>

        <button type="button" class="risk-confirm" @click="emit('close')">我知道了</button>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.risk-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(31, 41, 51, 0.32);
  backdrop-filter: blur(4px);
}

.risk-card {
  width: min(460px, 100%);
  padding: 28px 26px 24px;
  border-radius: 24px;
  background: #fff;
  box-shadow: 0 28px 70px rgba(31, 41, 51, 0.24);
  text-align: center;
  animation: rise 0.18s ease-out;
}

.risk-ring {
  width: 112px;
  height: 112px;
  margin: 0 auto 16px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  border: 10px solid #ffedd5;
  background: #fff7ed;
  color: #c2410c;
}

.risk-ring.critical {
  border-color: #fee4e2;
  background: #fff5f4;
  color: #b42318;
}

.risk-ring strong {
  font-size: 15px;
}

.risk-card h3 {
  margin: 0 0 10px;
  font-size: 19px;
}

.risk-summary {
  margin: 0 0 16px;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.7;
}

.risk-actions {
  margin: 0 0 14px;
  padding: 14px 18px;
  border-radius: 14px;
  background: #f8fafc;
  color: var(--ink);
  font-size: 13px;
  line-height: 1.9;
  text-align: left;
  list-style: none;
}

.risk-actions li::before {
  content: "·";
  margin-right: 8px;
  color: var(--primary);
  font-weight: 700;
}

.risk-actions a {
  color: var(--primary-strong);
  font-weight: 600;
}

.risk-note {
  margin: 0 0 18px;
  color: #94a3b8;
  font-size: 11px;
}

.risk-confirm {
  width: 100%;
  padding: 12px;
  border: 0;
  border-radius: 14px;
  color: #fff;
  font-weight: 700;
  background: linear-gradient(135deg, var(--primary), var(--primary-strong));
}

@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
