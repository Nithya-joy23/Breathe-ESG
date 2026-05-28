import React, { useEffect, useState } from 'react'
import axios from 'axios'
import { formatPeriod, formatCo2eNumber } from './formatters'

const API = 'http://127.0.0.1:8000/api'

export default function AuditReady() {
  const [records, setRecords] = useState([])
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      setError(null)

      try {
        const [recordsRes, summaryRes] = await Promise.all([
          axios.get(`${API}/records/?status=LOCKED`),
          axios.get(`${API}/records/summary/`),
        ])
        setRecords(recordsRes.data.results || [])
        setSummary(summaryRes.data)
      } catch (err) {
        setError('Could not load audit-ready records.')
      } finally {
        setLoading(false)
      }
    }

    load()
  }, [])

  return (
    <div className="container">
      <div className="page-title">Audit Ready</div>
      <div className="page-sub">
        View records that are locked and ready for audit, with scope totals and review status.
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <div className="stats-grid" style={{ marginBottom: '24px' }}>
        <div className="stat-card">
          <div className="number">{formatCo2eNumber(summary?.scope_1_co2e)}</div>
          <div className="unit">kg CO2e</div>
          <div className="label">Scope 1 Locked</div>
        </div>
        <div className="stat-card">
          <div className="number">{formatCo2eNumber(summary?.scope_2_co2e)}</div>
          <div className="unit">kg CO2e</div>
          <div className="label">Scope 2 Locked</div>
        </div>
        <div className="stat-card">
          <div className="number">{formatCo2eNumber(summary?.scope_3_co2e)}</div>
          <div className="unit">kg CO2e</div>
          <div className="label">Scope 3 Locked</div>
        </div>
      </div>

      {loading ? (
        <div className="loading">Loading locked records...</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Source</th>
                <th>Period</th>
                <th>CO2e</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {records.length ? (
                records.map((record) => (
                  <tr key={record.id}>
                    <td>{record.id}</td>
                    <td>{record.source_type}</td>
                    <td>{formatPeriod(record)}</td>
                    <td>{formatCo2eNumber(record.co2e_kg)}</td>
                    <td>{record.status}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="5">
                    <div className="empty">No locked audit-ready records found.</div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
