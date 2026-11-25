<template>
  <div class="space-y-6">
    <div class="flex items-start justify-between gap-4">
      <div>
        <h3 class="text-base font-bold titillium-web-semibold">Language Profiles</h3>
        <p class="text-xs text-muted-foreground mt-1">
          Ordered language preferences used when requesting book releases. Each row is tried in
          order: "always" rows are searched every time, "only if not found" rows are only used
          when the earlier languages produced nothing.
        </p>
      </div>
      <Button variant="primary" size="sm" :icon="PlusIcon" @click="startNewProfile">
        New Profile
      </Button>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">
      <!-- Profile list -->
      <div class="rounded-xl border border-purple-500/20 bg-black/20 divide-y divide-purple-500/10 overflow-hidden">
        <button
          v-for="profile in profiles"
          :key="profile.id"
          type="button"
          class="w-full text-left px-4 py-3 hover:bg-purple-500/10 transition-colors"
          :class="selectedId === profile.id && !isNew ? 'bg-purple-600/20' : ''"
          @click="selectProfile(profile)"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="text-sm font-semibold text-foreground truncate">{{ profile.name }}</span>
            <Badge v-if="profile.is_default" variant="success">default</Badge>
          </div>
          <p class="text-xs text-muted-foreground mt-0.5">
            {{ kindLabel(profile.kind) }} · {{ profile.rows.map((row) => row.language).join(' → ') }}
          </p>
        </button>
        <p v-if="!profiles.length" class="px-4 py-6 text-sm text-muted-foreground text-center">
          No profiles yet
        </p>
      </div>

      <!-- Editor -->
      <Card variant="default" class="border border-purple-500/20">
        <div v-if="isNew || selectedId !== null" class="space-y-4">
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input v-model="form.name" label="Profile name" placeholder="e.g. French first" />
            <Select
              v-model="form.kind"
              label="Format"
              :options="kindOptions"
              :disabled="!isNew"
            />
          </div>

          <div class="space-y-2">
            <p class="text-xs font-semibold text-foreground uppercase tracking-wide">Languages</p>
            <div
              v-for="(row, index) in form.rows"
              :key="index"
              class="flex flex-col sm:flex-row sm:items-center gap-2 rounded-lg border border-purple-500/15 bg-black/20 p-2"
            >
              <span class="w-6 text-center text-xs text-muted-foreground">{{ index + 1 }}</span>
              <Select
                v-model="row.language"
                class="flex-1"
                :options="languageOptionsFor(index)"
              />
              <Select
                v-model="row.search_criteria"
                class="flex-1"
                :options="criteriaOptions"
              />
              <div class="flex items-center gap-1">
                <Button variant="ghost" size="sm" :disabled="index === 0" @click="moveRow(index, -1)">
                  ↑
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  :disabled="index === form.rows.length - 1"
                  @click="moveRow(index, 1)"
                >
                  ↓
                </Button>
                <Button variant="ghost" size="sm" :icon="TrashIcon" @click="removeRow(index)" />
              </div>
            </div>
            <Button variant="secondary" size="sm" :icon="PlusIcon" @click="addRow">
              Add language
            </Button>
          </div>

          <div class="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-purple-500/10">
            <div class="flex items-center gap-2">
              <Button
                v-if="!isNew && !isSelectedDefault"
                variant="ghost"
                size="sm"
                @click="deleteCurrentProfile"
              >
                Delete
              </Button>
              <Button
                v-if="!isNew && !isSelectedDefault"
                variant="secondary"
                size="sm"
                @click="makeDefault"
              >
                Set as default
              </Button>
            </div>
            <div class="flex items-center gap-2">
              <Button variant="ghost" size="sm" @click="cancelEdit">Cancel</Button>
              <Button variant="primary" size="sm" :loading="saving" @click="save">
                {{ isNew ? 'Create profile' : 'Save changes' }}
              </Button>
            </div>
          </div>
        </div>

        <p v-else class="text-sm text-muted-foreground text-center py-10">
          Select a profile to edit it, or create a new one.
        </p>
      </Card>
    </div>
  </div>
</template>

<script setup lang="ts">
import {
  Plus as PlusIcon,
  Trash2 as TrashIcon,
} from 'lucide-vue-next'
import type { LanguageOption, LanguageProfile, LanguageRow } from '~/types'

const { showSuccess, showError } = useToast()
const api = useApiService()

const profiles = ref<LanguageProfile[]>([])
const languages = ref<LanguageOption[]>([])
const criteriaOptions = ref<Array<{ value: string; label: string }>>([])
const selectedId = ref<number | null>(null)
const isNew = ref(false)
const saving = ref(false)

const form = reactive({
  name: '',
  kind: 'ebook' as 'ebook' | 'audiobook',
  rows: [] as LanguageRow[],
})

const kindOptions = [
  { label: 'Ebook', value: 'ebook' },
  { label: 'Audiobook', value: 'audiobook' },
]

const isSelectedDefault = computed(() => {
  const profile = profiles.value.find((item) => item.id === selectedId.value)
  return Boolean(profile?.is_default)
})

const kindLabel = (kind: string) => (kind === 'ebook' ? 'Ebook' : 'Audiobook')

const loadAll = async () => {
  try {
    const [languageResponse, profileResponse] = await Promise.all([
      api.getLanguages(),
      api.getLanguageProfiles(),
    ])
    languages.value = languageResponse.languages || []
    criteriaOptions.value = languageResponse.search_criteria || []
    profiles.value = profileResponse.profiles || []
  } catch (error: any) {
    showError('Failed to load language profiles', error.message)
  }
}

const languageOptionsFor = (index: number) => {
  const usedElsewhere = new Set(
    form.rows
      .filter((_, rowIndex) => rowIndex !== index)
      .map((row) => row.language)
  )
  return languages.value.filter((language) => !usedElsewhere.has(language.value))
}

const resetForm = () => {
  form.name = ''
  form.kind = 'ebook'
  form.rows = []
}

const startNewProfile = () => {
  isNew.value = true
  selectedId.value = null
  resetForm()
  form.rows = [{ language: 'fr', search_criteria: 'always' }]
}

const selectProfile = (profile: LanguageProfile) => {
  isNew.value = false
  selectedId.value = profile.id
  form.name = profile.name
  form.kind = profile.kind
  form.rows = profile.rows.map((row) => ({
    language: row.language,
    search_criteria: row.search_criteria,
  }))
}

const cancelEdit = () => {
  isNew.value = false
  selectedId.value = null
  resetForm()
}

const addRow = () => {
  const used = new Set(form.rows.map((row) => row.language))
  const next = languages.value.find((language) => !used.has(language.value))
  if (!next) return
  form.rows.push({
    language: next.value,
    search_criteria: form.rows.length === 0 ? 'always' : 'if_not_found',
  })
}

const removeRow = (index: number) => {
  form.rows.splice(index, 1)
}

const moveRow = (index: number, delta: number) => {
  const target = index + delta
  if (target < 0 || target >= form.rows.length) return
  const [row] = form.rows.splice(index, 1)
  form.rows.splice(target, 0, row)
}

const save = async () => {
  if (!form.name.trim()) {
    showError('Profile name is required')
    return
  }
  if (!form.rows.length) {
    showError('Add at least one language')
    return
  }
  saving.value = true
  try {
    const rows = form.rows.map((row) => ({
      language: row.language,
      search_criteria: row.search_criteria,
    }))
    if (isNew.value) {
      const response = await api.createLanguageProfile({
        name: form.name.trim(),
        kind: form.kind,
        rows,
      })
      showSuccess('Language profile created')
      await loadAll()
      if (response.profile) selectProfile(response.profile)
    } else if (selectedId.value !== null) {
      const response = await api.updateLanguageProfile(selectedId.value, {
        name: form.name.trim(),
        rows,
      })
      showSuccess('Language profile saved')
      await loadAll()
      if (response.profile) selectProfile(response.profile)
    }
  } catch (error: any) {
    showError('Save failed', error.message)
  } finally {
    saving.value = false
  }
}

const makeDefault = async () => {
  if (selectedId.value === null) return
  try {
    await api.updateLanguageProfile(selectedId.value, { is_default: true })
    showSuccess('Default profile updated')
    await loadAll()
  } catch (error: any) {
    showError('Failed to set default', error.message)
  }
}

const deleteCurrentProfile = async () => {
  if (selectedId.value === null) return
  try {
    await api.deleteLanguageProfile(selectedId.value)
    showSuccess('Language profile deleted')
    cancelEdit()
    await loadAll()
  } catch (error: any) {
    showError('Delete failed', error.message)
  }
}

onMounted(loadAll)
</script>
