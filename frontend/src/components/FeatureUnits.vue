<script setup>
import { computed } from 'vue'
import { FEATURE_GROUP_LABELS } from '../research.js'

// 만든 피처 묶음(research-data.json)과 분포 모델이 실제 쓰는 피처(모델 metadata)를 한 칸에 하나씩 보여 준다.
// 모델을 바꾸면 metadata가 바뀌므로 이 그림도 저절로 바뀐다.
const props = defineProps({ groups: { type: Object, required: true }, metadata: { type: Object, default: null } })
const used = computed(() => new Set(props.metadata?.distribution?.horizons?.[20]?.features ?? []))
const columns = computed(() => {
  const grouped = new Set(Object.values(props.groups).flat())
  const separate = [...used.value].filter(name => !grouped.has(name))  // 폭의 HAR 입력
  return [
    ...Object.entries(props.groups).map(([key, names]) => ({ key, label: FEATURE_GROUP_LABELS[key] ?? key, names })),
    ...(separate.length ? [{ key: 'separate', label: '변동성(별도)', names: separate, extra: true }] : []),
  ]
})
const rows = computed(() => [
  { key: 'all', name: '만든 피처', set: null },
  { key: 'distribution', name: '분포 모델', set: used.value },
].map(row => ({ ...row, count: row.set ? row.set.size : Object.values(props.groups).flat().length })))
</script>

<template>
  <div class="table-wrap units">
    <table>
      <thead><tr><th />
        <th v-for="c in columns" :key="c.key">{{ c.label }} {{ c.names.length }}</th>
      </tr></thead>
      <tbody>
        <tr v-for="row in rows" :key="row.key">
          <th scope="row">{{ row.name }}<small>{{ row.count }}개</small></th>
          <td v-for="c in columns" :key="c.key">
            <span v-if="!(row.key === 'all' && c.extra)" class="sq">
              <i v-for="name in c.names" :key="name" :class="row.set ? (row.set.has(name) ? 'on' : '') : 'all'" :title="name" />
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
