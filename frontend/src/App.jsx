import React, { useEffect, useState } from 'react'
import axios from 'axios'
import Upload from './Upload'
import Dashboard from './Dashboard'
import AuditReady from './AuditReady'
import Login from './Login'
import Register from './Register'
import './index.css'

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000/api'
const TOKEN_KEY = 'access_token'
axios.defaults.withCredentials = true

function readAccessToken() {
  try {
    return window.localStorage.getItem(TOKEN_KEY)
  } catch (error) {
    return null
  }
}

function setAccessToken(token) {
  if (token) {
    axios.defaults.headers.common.Authorization = `Bearer ${token}`
    try {
      window.localStorage.setItem(TOKEN_KEY, token)
    } catch (error) {
      console.warn('Unable to store access token', error)
    }
  }
}

function clearClientAuthState() {
  delete axios.defaults.headers.common.Authorization
  delete axios.defaults.headers.common['X-Tenant-ID']

  try {
    window.localStorage.removeItem(TOKEN_KEY)
  } catch (error) {
    console.warn('Unable to clear browser storage', error)
  }
}

axios.interceptors.request.use((config) => {
  const token = readAccessToken()
  config.headers = config.headers || {}
  const isLoginRequest = config.url?.includes('/auth/login/')

  if (token && !isLoginRequest) {
    config.headers.Authorization = `Bearer ${token}`
  } else {
    delete config.headers.Authorization
  }

  return config
})

export default function App() {
  const [page, setPage] = useState('upload')
  const [tenants, setTenants] = useState([])
  const [tenantId, setTenantId] = useState('')
  const [user, setUser] = useState(null)
  const [loadingAuth, setLoadingAuth] = useState(true)

  const resetToLogin = () => {
    clearClientAuthState()
    setUser(null)
    setTenants([])
    setTenantId('')
    setPage('login')
  }

  useEffect(() => {
    const interceptorId = axios.interceptors.response.use(
      (response) => response,
      (error) => {
        const isLoginRequest = error.config?.url?.includes('/auth/login/')
        if (error.response?.status === 401 && !isLoginRequest) {
          resetToLogin()
        }
        return Promise.reject(error)
      }
    )

    return () => axios.interceptors.response.eject(interceptorId)
  }, [])

  useEffect(() => {
    const loadUser = async () => {
      try {
        const token = readAccessToken()
        if (!token) {
          resetToLogin()
          return
        }
        setAccessToken(token)
        const res = await axios.get(`${API}/auth/me/`)
        setUser(res.data)
        setPage('dashboard')
      } catch (error) {
        resetToLogin()
      } finally {
        setLoadingAuth(false)
      }
    }

    loadUser()
  }, [])

  useEffect(() => {
    const recheckAuth = async () => {
      try {
        await axios.get(`${API}/auth/me/`)
      } catch (error) {
        resetToLogin()
      }
    }

    const handlePageShow = (event) => {
      if (event.persisted || document.visibilityState === 'visible') {
        recheckAuth()
      }
    }

    window.addEventListener('pageshow', handlePageShow)
    return () => window.removeEventListener('pageshow', handlePageShow)
  }, [])

  useEffect(() => {
    if (!user) {
      setTenants([])
      setTenantId('')
      delete axios.defaults.headers.common['X-Tenant-ID']
      return
    }

    const loadTenants = async () => {
      try {
        const res = await axios.get(`${API}/tenants/`)
        setTenants(res.data.tenants || [])
        const nextTenantId = res.data.current_tenant_id || res.data.tenants?.[0]?.id || ''
        setTenantId(nextTenantId)
        if (nextTenantId) {
          axios.defaults.headers.common['X-Tenant-ID'] = nextTenantId
        }
      } catch (error) {
        console.error('Unable to load tenants', error)
      }
    }

    loadTenants()
  }, [user])

  useEffect(() => {
    if (tenantId) {
      axios.defaults.headers.common['X-Tenant-ID'] = tenantId
    } else {
      delete axios.defaults.headers.common['X-Tenant-ID']
    }
  }, [tenantId])

  const handleLogin = (userData) => {
    setAccessToken(userData.access_token)
    setUser(userData)
    setPage('dashboard')
  }

  const handleLogout = async () => {
    try {
      await axios.post(`${API}/auth/logout/`)
    } catch (error) {
      console.warn('Logout failed', error)
    }
    resetToLogin()
    window.history.replaceState(null, '', window.location.pathname)
  }

  const currentTenant = tenants.find((tenant) => tenant.id === tenantId)

  if (loadingAuth) {
    return <div className="loading">Checking authentication...</div>
  }

  if (!user) {
    if (page === 'register') {
      return (
        <Register
          apiBase={API}
          onRegisterSuccess={() => setPage('login')}
          onGoToLogin={() => setPage('login')}
        />
      )
    }
    return <Login apiBase={API} onLogin={handleLogin} onGoToRegister={() => setPage('register')} />
  }

  return (
    <div>
      <nav className="navbar">
        <div>
          <h1>BreatheESG</h1>
          <span>{currentTenant?.name || 'Carbon Data Ingestion Platform'}</span>
        </div>
        <div className="nav-links">
          <span className="user-label">Signed in as {user.username}</span>
          <button className="nav-btn logout-btn" onClick={handleLogout}>
            Logout
          </button>
          <select className="tenant-select" value={tenantId} onChange={(event) => setTenantId(event.target.value)}>
            {tenants.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>{tenant.name}</option>
            ))}
          </select>
          <button
            className={`nav-btn ${page === 'upload' ? 'active' : ''}`}
            onClick={() => setPage('upload')}
          >
            Upload Data
          </button>
          <button
            className={`nav-btn ${page === 'dashboard' ? 'active' : ''}`}
            onClick={() => setPage('dashboard')}
          >
            Review Queue
          </button>
          <button
            className={`nav-btn ${page === 'audit' ? 'active' : ''}`}
            onClick={() => setPage('audit')}
          >
            Audit Ready
          </button>
        </div>
      </nav>
      {page === 'upload' && <Upload key={tenantId} />}
      {page === 'dashboard' && <Dashboard key={tenantId} />}
      {page === 'audit' && <AuditReady key={tenantId} />}
    </div>
  )
}
