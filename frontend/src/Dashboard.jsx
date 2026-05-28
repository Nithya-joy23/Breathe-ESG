import React, { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { formatActivity, formatCo2eNumber, formatPeriod } from './formatters'

const API = 'http://127.0.0.1:8000/api'

function Modal({ title, children, onClose }) {
  return (
    <div className="modal-backdrop">
      <div className="modal-panel">
        <div className="modal-header">
          <h3>{title}</h3>
          <button className="filter-btn" onClick={onClose}>Close</button>
        </div>
        {children}
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [records, setRecords] = useState([])
  const [failedRows, setFailedRows] = useState([])
  const [summary, setSummary] = useState(null)
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState({ key: 'created_at', direction: 'desc' })
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState(null)
  const [rejecting, setRejecting] = useState(null)
  const [editing, setEditing] = useState(null)
  const [editDraft, setEditDraft] = useState({})
  const [lockOpen, setLockOpen] = useState(false)
  const [expanded, setExpanded] = useState(null)
  const [auditByRecord, setAuditByRecord] = useState({})
  const [selected, setSelected] = useState([])

  const showMessage = (text, type = 'success') => {
    setMessage({ text, type })
    setTimeout(() => setMessage(null), 3000)
  }

  const fetchWithRetry = async (requestFn, retries = 1) => {
    try {
      return await requestFn()
    } catch (error) {
      if (retries > 0) {
        return fetchWithRetry(requestFn, retries - 1)
      }
      throw error
    }
  }

  const fetchData = async () => {
    setLoading(true)
    try {
      const recordsRes = await axios.get(`${API}/records/?include_deleted=true`, { withCredentials: true })
      setRecords(recordsRes.data.results || [])

      const summaryPromise = fetchWithRetry(() => axios.get(`${API}/records/summary/`, { withCredentials: true }))
      const failuresPromise = fetchWithRetry(() => axios.get(`${API}/records/failed/`, { withCredentials: true }))
      const [summaryRes, failuresRes] = await Promise.allSettled([summaryPromise, failuresPromise])

      setSummary(summaryRes.status === 'fulfilled' ? summaryRes.value.data : null)
      setFailedRows(failuresRes.status === 'fulfilled' ? failuresRes.value.data.results || [] : [])

      if (summaryRes.status !== 'fulfilled' || failuresRes.status !== 'fulfilled') {
        console.error('Dashboard refresh failure', {
          summaryError: summaryRes.status === 'rejected' ? summaryRes.reason : null,
          failuresError: failuresRes.status === 'rejected' ? failuresRes.reason : null,
        })
        showMessage('Records loaded, but some dashboard totals could not refresh.', 'error')
      }
    } catch (error) {
      console.error('Dashboard load failed', error)
      showMessage('Could not load review data.', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  const isFlaggedStatus = (status) => status === 'FLAGGED' || status === 'REJECTED'
  const isPendingStatus = (status) => status === 'PENDING' || status === 'EDITED_PENDING'
  const isLockedStatus = (status) => status === 'LOCKED'
  const approvedRecords = records.filter((record) => record.status === 'APPROVED' && !record.is_deleted)
  const approvedCount = summary?.by_status?.APPROVED || 0
  const estimated = summary?.estimated || {}

  const formatCo2e = (record) => (
    record.co2e_kg === null || record.co2e_kg === undefined ? '--' : `${record.co2e_kg} kg`
  )

  const searchable = (record) => JSON.stringify({
    source: record.source_type,
    period: formatPeriod(record),
    co2e: record.co2e_kg,
    status: record.status,
    activity: formatActivity(record),
    factor: record.ef_source,
    raw: record.raw_data,
  }).toLowerCase()

  const visibleRecords = useMemo(() => {
    if (filter === 'failed') return []
    const term = search.trim().toLowerCase()
    const filtered = records.filter((record) => {
      if (filter === 'deleted') return record.is_deleted
      if (record.is_deleted) return false
      if (filter === 'all') return true
      if (filter === 'pending') return record.status === 'PENDING'
      if (filter === 'locked') return isLockedStatus(record.status)
      if (filter === 'approved') return record.status === 'APPROVED'
      if (filter === 'flagged') return isFlaggedStatus(record.status)
      if (filter === 'edited') return record.is_edited || record.status === 'EDITED_PENDING'
      return record.source_type === filter.toUpperCase()
    }).filter((record) => !term || searchable(record).includes(term))

    return [...filtered].sort((a, b) => {
      const getValue = (record) => {
        if (sort.key === 'period') return formatPeriod(record)
        if (sort.key === 'activity') return Number(record.activity_value || 0)
        if (sort.key === 'co2e') return Number(record.co2e_kg || 0)
        if (sort.key === 'factor') return record.ef_source || ''
        if (sort.key === 'scope') return Number(record.scope || 0)
        return record[sort.key] || ''
      }
      const left = getValue(a)
      const right = getValue(b)
      const result = typeof left === 'number' && typeof right === 'number'
        ? left - right
        : String(left).localeCompare(String(right))
      return sort.direction === 'asc' ? result : -result
    })
  }, [records, filter, search, sort])

  const filteredFailedRows = useMemo(() => {
    const term = search.trim().toLowerCase()
    return failedRows.filter((row) => !term || JSON.stringify(row).toLowerCase().includes(term))
  }, [failedRows, search])

  const setSortKey = (key) => {
    setSort((current) => ({
      key,
      direction: current.key === key && current.direction === 'asc' ? 'desc' : 'asc',
    }))
  }

  const runAction = async (action) => {
    try {
      await action()
      setSelected([])
      await fetchData()
    } catch (error) {
      showMessage(error.response?.data?.message || error.response?.data?.error || 'Action failed.', 'error')
    }
  }

  const approve = (record) => runAction(async () => {
    await axios.patch(`${API}/records/${record.id}/approve/`, { comment: 'Approved by analyst' })
    showMessage('Record approved.')
  })

  const submitReject = () => runAction(async () => {
    await axios.patch(`${API}/records/${rejecting.id}/reject/`, { comment: rejecting.comment })
    setRejecting(null)
    showMessage('Record flagged.')
  })

  const openEdit = (record) => {
    setEditing(record)
    setEditDraft({
      activity_value: record.activity_value ?? '',
      activity_unit: record.activity_unit || '',
      emission_factor: record.emission_factor ?? '',
      comment: '',
    })
  }

  const submitEdit = () => runAction(async () => {
    await axios.patch(`${API}/records/${editing.id}/edit/`, editDraft)
    setEditing(null)
    setEditDraft({})
    showMessage('Record edited and marked pending review.')
  })

  const undo = (record) => runAction(async () => {
    await axios.patch(`${API}/records/${record.id}/undo/`, { comment: 'Undo review decision' })
    showMessage('Record returned to pending.')
  })

  const deleteRecord = (record) => runAction(async () => {
    await axios.delete(`${API}/records/${record.id}/delete/`, { data: { comment: 'Soft deleted by analyst' } })
    showMessage('Record soft deleted.')
  })

  const lockApproved = () => runAction(async () => {
    const res = await axios.post(`${API}/records/lock/`)
    setLockOpen(false)
    showMessage(res.data.message)
  })

  const retryFailed = (row) => runAction(async () => {
    await axios.post(`${API}/records/failed/${row.id}/retry/`)
    showMessage('Retry queued for manual reprocessing.')
  })

  const toggleExpand = async (record) => {
    if (expanded === record.id) {
      setExpanded(null)
      return
    }
    setExpanded(record.id)
    if (!auditByRecord[record.id]) {
      const res = await axios.get(`${API}/records/${record.id}/audit/`)
      setAuditByRecord((current) => ({ ...current, [record.id]: res.data.audit_actions || [] }))
    }
  }

  const selectedRecords = records.filter((record) => selected.includes(record.id))
  const toggleSelected = (record) => {
    setSelected((current) => (
      current.includes(record.id)
        ? current.filter((id) => id !== record.id)
        : [...current, record.id]
    ))
  }

  const bulkApprove = () => runAction(async () => {
    const reviewable = selectedRecords.filter((record) => isPendingStatus(record.status) || isFlaggedStatus(record.status))
    await Promise.all(reviewable.map((record) => (
      axios.patch(`${API}/records/${record.id}/approve/`, { comment: 'Bulk approved by analyst' })
    )))
    showMessage(`${reviewable.length} records approved.`)
  })

  const bulkFlag = () => runAction(async () => {
    const reviewable = selectedRecords.filter((record) => isPendingStatus(record.status))
    await Promise.all(reviewable.map((record) => (
      axios.patch(`${API}/records/${record.id}/reject/`, { comment: 'Bulk flagged by analyst' })
    )))
    showMessage(`${reviewable.length} records flagged.`)
  })

  const bulkDelete = () => runAction(async () => {
    const deletable = selectedRecords.filter((record) => !isLockedStatus(record.status))
    await Promise.all(deletable.map((record) => (
      axios.delete(`${API}/records/${record.id}/delete/`, { data: { comment: 'Bulk deleted by analyst' } })
    )))
    showMessage(`${deletable.length} records deleted.`)
  })

  const statusBadge = (record) => {
    if (record.is_deleted) return <span className="badge badge-pending">Deleted</span>
    if (record.status === 'APPROVED') return <span className="badge badge-approved">APPROVED</span>
    if (isFlaggedStatus(record.status)) return <span className="badge badge-flagged">FLAGGED</span>
    if (record.status === 'EDITED_PENDING') return <span className="badge badge-edited">Edited - Pending Review</span>
    if (record.status === 'LOCKED') return <span className="badge badge-approved">LOCKED</span>
    return <span className="badge badge-pending">PENDING</span>
  }

  const rowClass = (record) => {
    if (record.is_deleted) return 'deleted'
    if (isFlaggedStatus(record.status)) return 'flagged'
    if (record.status === 'APPROVED' || isLockedStatus(record.status)) return 'approved'
    return ''
  }

  const actionButton = (children, onClick, className = 'filter-btn', title = '') => (
    <button
      className={className}
      title={title}
      onClick={(event) => {
        event.stopPropagation()
        onClick()
      }}
    >
      {children}
    </button>
  )

  const renderActions = (record) => {
    if (record.is_deleted) return <span className="deleted-text">Deleted</span>
    if (isLockedStatus(record.status)) return <span className="locked-text">Locked {'\u2713'}</span>
    return (
      <div className="action-btns" style={{flexWrap:'wrap'}}>
        {(isPendingStatus(record.status) || isFlaggedStatus(record.status)) && actionButton('\u2713', () => approve(record), 'btn-approve icon-btn', 'Approve')}
        {isFlaggedStatus(record.status) && actionButton('\u21ba', () => undo(record), 'btn-undo icon-btn', 'Undo to pending')}
        {isPendingStatus(record.status) && actionButton('\u2691', () => setRejecting({ ...record, comment: '' }), 'btn-flag icon-btn', 'Flag')}
        {record.status === 'APPROVED' && actionButton('Undo', () => undo(record), 'filter-btn', 'Undo to pending')}
        {actionButton('\u270e', () => openEdit(record), 'filter-btn icon-btn', 'Edit')}
        {actionButton('\u232b', () => deleteRecord(record), 'btn-flag icon-btn', 'Delete')}
      </div>
    )
  }

  const sortableHeader = (key, label) => (
    <th className="sortable" onClick={() => setSortKey(key)}>
      {label} {sort.key === key ? (sort.direction === 'asc' ? '↑' : '↓') : ''}
    </th>
  )

  return (
    <div className="container">
      <div style={{display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:'24px'}}>
        <div>
          <div className="page-title">Review Queue</div>
          <div className="page-sub" style={{marginBottom:0}}>
            Review pending records, resolve anomalies, edit with comments, and lock approved records when ready.
          </div>
        </div>
        <button className="filter-btn active" onClick={() => setLockOpen(true)}>Lock Approved Records</button>
      </div>

      {message && <div className={`alert ${message.type === 'error' ? 'alert-error' : 'alert-success'}`}>{message.text}</div>}

      <div className="stats-grid">
        <div className="stat-card"><div className="number">{formatCo2eNumber(estimated.scope_1_co2e)}</div><div className="unit">kg CO2e</div><div className="label">Scope 1 Estimate</div></div>
        <div className="stat-card"><div className="number">{formatCo2eNumber(estimated.scope_2_co2e)}</div><div className="unit">kg CO2e</div><div className="label">Scope 2 Estimate</div></div>
        <div className="stat-card"><div className="number">{formatCo2eNumber(estimated.scope_3_co2e)}</div><div className="unit">kg CO2e</div><div className="label">Scope 3 Estimate</div></div>
        <button className="stat-card stat-button" onClick={() => setFilter('approved')}><div className="number">{approvedCount}</div><div className="label">Approved to Lock</div></button>
      </div>

      <div className="filter-bar">
        <span style={{fontSize:'12px', color:'#64748b'}}>Filter:</span>
        {['all', 'pending', 'edited', 'flagged', 'approved', 'locked', 'deleted', 'failed', 'sap', 'utility', 'travel'].map((item) => (
          <button key={item} className={`filter-btn ${filter === item ? 'active' : ''}`} onClick={() => { setFilter(item); setSelected([]) }}>
            {item.charAt(0).toUpperCase() + item.slice(1)}
          </button>
        ))}
      </div>

      <div className="table-tools">
        <input
          className="search-input"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search source, period, CO2e, status..."
        />
      </div>

      {!!selected.length && filter !== 'failed' && (
        <div className="bulk-bar">
          <strong>{selected.length} selected</strong>
          <button className="btn-approve" onClick={bulkApprove}>Approve All</button>
          <button className="btn-flag" onClick={bulkFlag}>Flag All</button>
          <button className="filter-btn" onClick={bulkDelete}>Delete All</button>
        </div>
      )}

      <div className="table-wrap">
        {loading ? <div className="loading">Loading records...</div> : filter === 'failed' ? (
          <table>
            <thead><tr><th>Source</th><th>Row</th><th>Error</th><th>Original Raw Row</th><th>Actions</th></tr></thead>
            <tbody>
              {filteredFailedRows.map((failure) => (
                <tr key={failure.id} className="flagged">
                  <td>{failure.source_type}</td>
                  <td>{failure.row_number}</td>
                  <td>{failure.parse_error}</td>
                  <td><pre className="raw-json">{JSON.stringify(failure.raw_data, null, 2)}</pre></td>
                  <td><button className="filter-btn" onClick={() => retryFailed(failure)}>Retry</button></td>
                </tr>
              ))}
              {!filteredFailedRows.length && <tr><td colSpan="5"><div className="empty">No failed rows match this filter.</div></td></tr>}
            </tbody>
          </table>
        ) : (
          <table>
            <thead>
              <tr>
                <th><input type="checkbox" checked={!!visibleRecords.length && selected.length === visibleRecords.length} onChange={() => setSelected(selected.length === visibleRecords.length ? [] : visibleRecords.map((record) => record.id))} /></th>
                {sortableHeader('source_type', 'Source')}
                {sortableHeader('period', 'Period')}
                {sortableHeader('activity', 'Activity')}
                {sortableHeader('co2e', 'CO2e')}
                {sortableHeader('factor', 'Factor')}
                {sortableHeader('scope', 'Scope')}
                {sortableHeader('status', 'Status')}
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {visibleRecords.map((record) => (
                <React.Fragment key={record.id}>
                  <tr className={rowClass(record)} onClick={() => toggleExpand(record)}>
                    <td onClick={(event) => event.stopPropagation()}><input type="checkbox" checked={selected.includes(record.id)} onChange={() => toggleSelected(record)} /></td>
                    <td>
                      <strong>{record.source_type}</strong>
                      {(record.is_anomaly || !!record.anomaly_reasons?.length) && <span className="warning-icon" title={(record.anomaly_reasons || ['Marked by parser as suspicious for analyst review.']).join(' ')}>{'\u26A0'}</span>}
                      {record.is_edited && <div style={{marginTop:'4px'}}><span className="badge badge-edited">Edited</span></div>}
                    </td>
                    <td>{formatPeriod(record)}</td>
                    <td>{formatActivity(record)}</td>
                    <td>{formatCo2e(record)}</td>
                    <td>{record.ef_source}</td>
                    <td><span className={`badge badge-s${record.scope}`}>Scope {record.scope}</span></td>
                    <td>{statusBadge(record)}</td>
                    <td>{renderActions(record)}</td>
                  </tr>
                  {expanded === record.id && (
                    <tr className="detail-row">
                      <td colSpan="9">
                        <div className="detail-grid">
                          <div className="sample-info">
                            <strong>Original raw data</strong>
                            <pre className="raw-json">{JSON.stringify(record.raw_data, null, 2)}</pre>
                          </div>
                          <div className="sample-info">
                            <strong>Normalization steps</strong>
                            {(record.normalization_steps || []).map((step) => <div key={step}>{step}</div>)}
                            <div style={{marginTop:'8px'}}>Source file: {record.source_file_name || '--'}</div>
                            <div>Ingested at: {record.ingested_at || '--'}</div>
                          </div>
                          <div className="sample-info">
                            <strong>Audit trail</strong>
                            {(auditByRecord[record.id] || []).map((action) => (
                              <div key={action.id} className="audit-entry">
                                <strong>{action.action}</strong> by {action.performed_by || 'unknown'} at {action.performed_at}
                                {action.comment && <div>Comment: {action.comment}</div>}
                                {(action.previous_value || action.new_value) && <pre className="raw-json">{JSON.stringify({old: action.previous_value, new: action.new_value}, null, 2)}</pre>}
                              </div>
                            ))}
                            {!auditByRecord[record.id]?.length && <div className="empty">No audit actions yet.</div>}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
              {!visibleRecords.length && <tr><td colSpan="9"><div className="empty">No review records match this filter.</div></td></tr>}
            </tbody>
          </table>
        )}
      </div>

      {rejecting && (
        <Modal title={`Flag Record ${rejecting.id}`} onClose={() => setRejecting(null)}>
          <textarea value={rejecting.comment} onChange={(event) => setRejecting({...rejecting, comment: event.target.value})} placeholder="Flag reason" style={{width:'100%', minHeight:'100px', padding:'10px'}} />
          <button className="btn btn-primary" onClick={submitReject} disabled={!rejecting.comment.trim()} style={{marginTop:'12px'}}>Flag Record</button>
        </Modal>
      )}

      {editing && (
        <Modal title={`Edit Record ${editing.id}`} onClose={() => setEditing(null)}>
          <div className="edit-comparison">
            <div className="sample-info">
              <strong>Original</strong>
              <div>Quantity: {editing.activity_value}</div>
              <div>Unit: {editing.activity_unit}</div>
              <div>Factor: {editing.emission_factor}</div>
              <div>CO2e: {editing.co2e_kg}</div>
            </div>
            <div className="sample-info">
              <strong>New value</strong>
              <label>Quantity<input type="number" step="any" value={editDraft.activity_value} onChange={(event) => setEditDraft({...editDraft, activity_value: event.target.value})} /></label>
              <label>Unit<input value={editDraft.activity_unit} onChange={(event) => setEditDraft({...editDraft, activity_unit: event.target.value})} /></label>
              <label>Emission factor<input type="number" step="any" value={editDraft.emission_factor} onChange={(event) => setEditDraft({...editDraft, emission_factor: event.target.value})} /></label>
            </div>
          </div>
          <textarea value={editDraft.comment} onChange={(event) => setEditDraft({...editDraft, comment: event.target.value})} placeholder="Mandatory edit reason" style={{width:'100%', minHeight:'100px', padding:'10px', marginTop:'12px'}} />
          <button className="btn btn-primary" onClick={submitEdit} disabled={!editDraft.activity_value || !editDraft.activity_unit || !editDraft.emission_factor || !editDraft.comment?.trim()} style={{marginTop:'12px'}}>Save Edit</button>
        </Modal>
      )}

      {lockOpen && (
        <Modal title="Confirm Lock Approved Records" onClose={() => setLockOpen(false)}>
          <p style={{fontSize:'14px', lineHeight:1.5}}>These records will be locked and become audit-ready:</p>
          <div className="lock-list">
            {approvedRecords.map((record) => (
              <div key={record.id}>#{record.id} - {record.source_type} - {formatPeriod(record)} - {formatCo2e(record)}</div>
            ))}
            {!approvedRecords.length && <div className="empty">No approved records to lock.</div>}
          </div>
          <button className="btn btn-primary" onClick={lockApproved} disabled={!approvedRecords.length} style={{marginTop:'16px'}}>
            Lock {approvedRecords.length} Approved Records
          </button>
        </Modal>
      )}
    </div>
  )
}
