<script setup lang="ts">
withDefaults(defineProps<{ title?: string; subtitle?: string; padding?: string; flush?: boolean }>(), {
  padding: '16px',
  flush: false,
})
</script>

<template>
  <section class="section-card">
    <header v-if="title || $slots.title || $slots.extra" class="head">
      <div class="titles">
        <slot name="title"><span class="title">{{ title }}</span></slot>
        <span v-if="subtitle" class="subtitle">{{ subtitle }}</span>
      </div>
      <div class="extra"><slot name="extra" /></div>
    </header>
    <div class="body" :style="{ padding: flush ? '0' : padding }"><slot /></div>
  </section>
</template>

<style scoped>
.section-card {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid #f0f2f6;
}
.titles {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.title {
  font-weight: 600;
  font-size: 14px;
  color: var(--c-text);
}
.subtitle {
  font-size: 12px;
  color: var(--c-text-3);
}
.extra {
  display: flex;
  align-items: center;
  gap: 8px;
}
.body {
  flex: 1;
  min-width: 0;
}
</style>
