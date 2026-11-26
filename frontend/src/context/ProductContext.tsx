import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import { clearProductConfig, loadProductConfig, productApi, saveProductConfig } from '../services/productApi'
import type { ProductConfig, Site } from '../types/product'

interface SetupInput {
  siteName: string
  domain: string
  country: string
  language: string
  timezone: string
  ownerName: string
  ownerEmail: string
  executorName: string
  executorEmail: string
}

interface ProductContextValue {
  config: ProductConfig | null
  site: Site | null
  loading: boolean
  error: string | null
  initialize: (input: SetupInput) => Promise<void>
  refreshSite: () => Promise<void>
  reset: () => void
}

const ProductContext = createContext<ProductContextValue | null>(null)

export function ProductProvider({ children, loadExistingSite = true }: { children: ReactNode; loadExistingSite?: boolean }) {
  const [config, setConfig] = useState<ProductConfig | null>(loadProductConfig)
  const [site, setSite] = useState<Site | null>(null)
  const [loading, setLoading] = useState(loadExistingSite && Boolean(config))
  const [error, setError] = useState<string | null>(null)

  const refreshSite = async () => {
    if (!config) return
    setLoading(true)
    setError(null)
    try {
      setSite(await productApi.getSite(config.siteId))
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!loadExistingSite || !config) return
    const timer = window.setTimeout(() => void refreshSite(), 0)
    return () => window.clearTimeout(timer)
    // Product identity changes only when siteId changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config?.siteId, loadExistingSite])

  const initialize = async (input: SetupInput) => {
    setLoading(true)
    setError(null)
    try {
      const registered = await productApi.registerSite({
        name: input.siteName.trim(),
        domain: input.domain.trim(),
        default_country: input.country,
        default_language: input.language,
        timezone: input.timezone,
        owner_name: input.ownerName.trim(),
        owner_email: input.ownerEmail.trim(),
        executor_name: input.executorName.trim(),
        executor_email: input.executorEmail.trim(),
      })
      const next: ProductConfig = {
        siteId: registered.site.id,
        ownerActorId: registered.owner_actor_id,
        agentActorId: registered.agent_actor_id,
        executorActorId: registered.executor_actor_id,
      }
      saveProductConfig(next)
      setConfig(next)
      setSite(registered.site)
    } catch (cause) {
      setError((cause as Error).message)
      throw cause
    } finally {
      setLoading(false)
    }
  }

  const reset = () => {
    clearProductConfig()
    setConfig(null)
    setSite(null)
    setError(null)
  }

  const value: ProductContextValue = {
    config,
    site,
    loading,
    error,
    initialize,
    refreshSite,
    reset,
  }
  return <ProductContext.Provider value={value}>{children}</ProductContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useProduct() {
  const value = useContext(ProductContext)
  if (!value) throw new Error('ProductProvider is missing')
  return value
}
