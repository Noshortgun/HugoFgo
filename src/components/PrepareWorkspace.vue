<script setup lang="ts">
import { computed } from 'vue';
import DevicePanel from './DevicePanel.vue';
import ResourceCenter from './ResourceCenter.vue';
import ChannelSettingsPanel from './ChannelSettingsPanel.vue';
import AppIcon from './AppIcon.vue';
import { runSettings } from '../runSettings';
import { resourceRegion, RESOURCE_REGIONS } from '../resources';
import type { Region } from '../resources';

const props = defineProps<{ selectedSerial: string }>();
const emit = defineEmits<{
  'update:selectedSerial': [value: string];
  device: [value: { serial: string; label: string; online: boolean }];
  updated: [region: Region];
}>();

const regionLabel = computed(() => RESOURCE_REGIONS.find((region) => region.id === resourceRegion.value)?.label ?? resourceRegion.value);
const regionRunnable = computed(() => resourceRegion.value === 'CN');
const channelLabel = computed(() => runSettings.value.device_channel === 'scrcpy' ? 'scrcpy（默认）' : 'ADB');
</script>

<template>
  <section class="prepare-workspace">
    <header class="workspace-page-heading">
      <div><span>READY ROOM</span><h2>准备</h2><p>先确认设备、区服、资源和运行参数，再进入作业与运行。</p></div>
      <div class="prepare-status-strip">
        <span :class="{ good: regionRunnable }"><AppIcon name="shield" :size="14" />{{ regionLabel }}{{ regionRunnable ? '可执行' : '仅资源' }}</span>
        <span><AppIcon name="device" :size="14" />{{ props.selectedSerial || '未选择设备' }}</span>
        <span><AppIcon name="bolt" :size="14" />{{ channelLabel }}</span>
      </div>
    </header>
    <p v-if="!regionRunnable" class="workspace-blocker" role="alert"><AppIcon name="help" :size="15" />当前资源区服是 {{ regionLabel }}，执行器只支持国服。可以继续管理素材，但启动会被阻止。</p>
    <div class="prepare-grid">
      <DevicePanel :model-value="props.selectedSerial" @update:model-value="emit('update:selectedSerial', $event)" @device="emit('device', $event)" />
      <ChannelSettingsPanel />
      <ResourceCenter class="prepare-resources" @updated="emit('updated', $event)" />
    </div>
  </section>
</template>
