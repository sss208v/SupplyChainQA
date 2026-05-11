<template>
  <MainLayout />
</template>

<script setup>
import MainLayout from '@/components/Layout/MainLayout.vue'
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}
html, body, #app {
  height: 100%;
  font-family: var(--font-body);
}
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-thumb { background: var(--color-text-meta); border-radius: 3px; }
::-webkit-scrollbar-track { background: transparent; }
</style>
