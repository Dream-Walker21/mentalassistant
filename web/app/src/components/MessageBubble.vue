<script setup lang="ts">
defineProps<{
  role: "user" | "assistant";
  content: string;
}>();
</script>

<template>
  <div class="message" :class="role">
    <div class="bubble">{{ content }}</div>
  </div>
</template>
