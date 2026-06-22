import { api } from './client'
import { emitLeadsChanged } from '../lib'

/* Быстрое «убрать лид»: стадия «Не сложилось» (причина hard_stop = мусор/отказ) +
   сразу в архив одним вызовом бэка. Обратимо — см. restoreLead. */
export async function dismissLead(id: string): Promise<void> {
  await api.post(`/conversations/${id}/stage`, {
    to_stage: 'lost', lost_reason: 'hard_stop', also_archive: true,
  })
  emitLeadsChanged()
}

/* «Вернуть» из архива; если известна прежняя стадия (и она не lost) — восстановить её. */
export async function restoreLead(id: string, toStage?: string): Promise<void> {
  await api.post(`/conversations/${id}/archive`, { archived: false })
  if (toStage && toStage !== 'lost') {
    await api.post(`/conversations/${id}/stage`, { to_stage: toStage })
  }
  emitLeadsChanged()
}
