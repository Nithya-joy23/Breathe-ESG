import React, { useState } from 'react'
import axios from 'axios'

export default function Login({ apiBase, onLogin, onGoToRegister }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (event) => {
    event.preventDefault()
    setLoading(true)
    setError(null)

    try {
      const response = await axios.post(`${apiBase}/auth/login/`, {
        username,
        password,
      })
      onLogin(response.data)
    } catch (error) {
      setError(error.response?.data?.error || 'Unable to sign in')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <div className="login-mark" aria-hidden="true">
            B
          </div>
          <h1>BreatheESG</h1>
          <p>Emissions Data Management Platform</p>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <label className="login-field">
            <span>Username</span>
            <div className="login-input-wrap">
              <svg className="login-input-icon" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4Zm0 2c-3.3 0-6 1.8-6 4v1h12v-1c0-2.2-2.7-4-6-4Z" />
              </svg>
              <input
                type="text"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                placeholder="Enter your username"
                required
              />
            </div>
          </label>

          <label className="login-field">
            <span>Password</span>
            <div className="login-input-wrap">
              <svg className="login-input-icon" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M17 9h-1V7a4 4 0 0 0-8 0v2H7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2Zm-7-2a2 2 0 0 1 4 0v2h-4Zm3 8.7V17h-2v-1.3a2 2 0 1 1 2 0Z" />
              </svg>
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                placeholder="Enter your password"
                required
              />
              <button
                className="password-toggle"
                type="button"
                onClick={() => setShowPassword((visible) => !visible)}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? (
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="m3.7 2.3 18 18-1.4 1.4-3.1-3.1A10.6 10.6 0 0 1 12 20C5.6 20 2 13 2 13a18.4 18.4 0 0 1 4.1-5.1L2.3 3.7Zm6.1 7.5a3 3 0 0 0 4.4 4.4Zm2.2-5.8c6.4 0 10 7 10 7a18 18 0 0 1-2.5 3.6L17 12.1V12a5 5 0 0 0-5-5h-.1L9.8 4.9A10.4 10.4 0 0 1 12 4Z" />
                  </svg>
                ) : (
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M12 5C5.6 5 2 12 2 12s3.6 7 10 7 10-7 10-7-3.6-7-10-7Zm0 11a4 4 0 1 1 4-4 4 4 0 0 1-4 4Zm0-2a2 2 0 1 0-2-2 2 2 0 0 0 2 2Z" />
                  </svg>
                )}
              </button>
            </div>
          </label>

          <button className="login-submit" type="submit" disabled={loading}>
            {loading ? 'Signing in...' : 'Sign In'}
          </button>

          {error && (
            <div className="error-message" role="alert">
              {error}
            </div>
          )}
        </form>

        <p className="login-footer">
          Don't have an account? <a href="#" onClick={(e) => { e.preventDefault(); onGoToRegister(); }}>Register here</a>
        </p>
      </div>
    </div>
  )
}
