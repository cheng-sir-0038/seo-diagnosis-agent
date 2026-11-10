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

export function ProductProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<ProductConfig | null>(loadProductConfig)
  const [site, setSite] = useState<Site | null>(null)
  const [loading, setLoading] = useState(Boolean(config))
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
    const timer = window.setTimeout(() => {
      if (config) void refreshSite()
      else setLoading(false)
    }, 0)
    return () => window.clearTimeout(timer)
    // Product identity changes only when siteId changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config?.siteId])

  const initialize = async (input: SetupInput) => {
    setLoading(true)
    setError(null)
    try {
      const createdSite = await productApi.createSite({
        name: input.siteName,
        domain: input.domain,
        default_country: input.country,
        default_language: input.language,
        timezone: input.timezone,
      })
      const [owner, agent, executor] = await Promise.all([
        productApi.createActor({ actor_type: 'USER', name: input.ownerName, email: input.ownerEmail }),
        productApi.createActor({ actor_type: 'SEO_AGENT', name: 'SEO 优化专员' }),
        productApi.createActor({
          actor_type: 'HUMAN_OPERATOR',
          name: input.executorName,
          email: input.executorEmail,
        }),
      ])
      await productApi.createSubscription({
        site_id: createdSite.id,
        subscriber_actor_id: owner.id,
        recipient_email: input.ownerEmail,
        recipient_role: 'APPROVER',
        is_default_approver: true,
      })
      const next: ProductConfig = {
        siteId: createdSite.id,
        ownerActorId: owner.id,
        agentActorId: agent.id,
        executorActorId: executor.id,
      }
      saveProductConfig(next)
      setConfig(next)
      setSite(createdSite)
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
