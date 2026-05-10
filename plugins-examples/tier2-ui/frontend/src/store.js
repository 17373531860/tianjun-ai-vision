import { defineStore } from 'pinia'
import { ref } from 'vue'

export const usePluginDashboardStore = defineStore('plugin-internal-demo-dashboard', () => {
  const shiftTarget = ref(1200)
  const defectThreshold = ref(8)

  function updateConfig(next) {
    if (typeof next?.shiftTarget === 'number') shiftTarget.value = next.shiftTarget
    if (typeof next?.defectThreshold === 'number') defectThreshold.value = next.defectThreshold
  }

  return { shiftTarget, defectThreshold, updateConfig }
})
