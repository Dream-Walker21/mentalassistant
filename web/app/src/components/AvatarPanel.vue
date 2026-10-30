<script setup lang="ts">
import { computed } from "vue";

import type { AvatarCommand } from "@/api/types";

const props = defineProps<{
  command: AvatarCommand | null;
  thinking: boolean;
}>();

const expression = computed(() => props.command?.expression ?? "neutral");

const face = computed(() => {
  const faces: Record<string, string> = {
    neutral: "🙂",
    gentle_smile: "😊",
    concerned: "😟",
    calm: "😌",
    serious: "😐",
  };
  return faces[expression.value] ?? "🙂";
});

const label = computed(() => {
  if (props.thinking) return "正在思考…";
  if (!props.command) return "待机中";
  return `${props.command.action} · ${props.command.expression}`;
});
</script>

<template>
  <div class="avatar-panel" :class="{ thinking }">
    <div class="avatar-halo">
      <div class="avatar-face">{{ face }}</div>
    </div>
    <p class="avatar-label">{{ label }}</p>
    <p class="avatar-hint">Live2D 立绘待接入</p>
  </div>
</template>

<style scoped>
.avatar-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  min-height: 320px;
  padding: 28px 16px;
  border-radius: 22px;
  background: linear-gradient(180deg, #f4faf8, #eef2ff);
}

.avatar-halo {
  width: min(76%, 300px);
  aspect-ratio: 1;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #fff, #dff0eb 55%, #cfd9ff);
  box-shadow: inset 0 0 0 12px rgba(255, 255, 255, 0.55);
}

.avatar-face {
  font-size: clamp(72px, 8vw, 112px);
  line-height: 1;
  transition: transform 0.25s ease;
}

.avatar-panel.thinking .avatar-face {
  animation: breathe 1.2s ease-in-out infinite;
}

.avatar-label {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.avatar-hint {
  margin: 0;
  color: #94a3b8;
  font-size: 11px;
}

@media (max-width: 900px) {
  .avatar-panel {
    min-height: 200px;
    padding: 18px 14px;
  }

  .avatar-halo {
    width: min(52%, 170px);
  }
}

@keyframes breathe {
  0%,
  100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.08);
  }
}
</style>
