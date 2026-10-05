<script setup>
// Why의 한 장: 장 이름 → 한 문장의 발견 → 근거 그림(slot) → 작은 글씨의 질문·방법·판단
defineProps({ side: { type: String, required: true }, finding: { type: String, required: true }, notes: { type: Object, required: true } })
const TERMS = [['question', '질문'], ['method', '방법'], ['judgment', '판단']]
</script>

<template>
  <article class="chapter">
    <p class="side">{{ side }}</p>
    <div class="body">
      <h3 class="h3">{{ finding }}</h3>
      <slot />
      <dl class="qmj">
        <div v-for="[key, term] in TERMS" v-show="notes[key]" :key="key"><dt>{{ term }}</dt><dd>{{ notes[key] }}</dd></div>
      </dl>
    </div>
  </article>
</template>
