import { createContext, useContext, useState, ReactNode, useCallback } from 'react'
import { ConversationDrawer } from './ConversationDrawer'

/* Единый drawer лида: любой вью (inbox, канбан, канвас) зовёт openConversation(id)
   и получает одинаковую карточку с перепиской и действиями. */

interface DrawerCtx {
  openConversation: (id: string) => void
  closeConversation: () => void
}

const Ctx = createContext<DrawerCtx>({ openConversation: () => {}, closeConversation: () => {} })

export function useDrawer() {
  return useContext(Ctx)
}

export function DrawerProvider({ children, onChanged }: { children: ReactNode; onChanged?: () => void }) {
  const [convId, setConvId] = useState<string | null>(null)

  const openConversation = useCallback((id: string) => setConvId(id), [])
  const closeConversation = useCallback(() => {
    setConvId(null)
    onChanged?.()
  }, [onChanged])

  return (
    <Ctx.Provider value={{ openConversation, closeConversation }}>
      {children}
      {convId && <ConversationDrawer convId={convId} onClose={closeConversation} />}
    </Ctx.Provider>
  )
}
