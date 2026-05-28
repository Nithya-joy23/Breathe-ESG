import React, { useState } from 'react'
import axios from 'axios'

const API = 'http://127.0.0.1:8000/api'

function FailureList({ failures }) {
  if (!failures?.length) return null

  return (
    <div className="table-wrap" style={{marginTop:'16px'}}>
      <table>
        <thead>
          <tr>
            <th>Row</th>
            <th>Error</th>
            <th>Original Data</th>
          </tr>
        </thead>
        <tbody>
          {failures.map((failure) => (
            <tr key={`${failure.row_number}-${failure.parse_error}`} className="flagged">
              <td>{failure.row_number}</td>
              <td>{failure.parse_error}</td>
              <td>
                <pre style={{whiteSpace:'pre-wrap', fontSize:'11px'}}>
                  {JSON.stringify(failure.raw_data, null, 2)}
                </pre>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function UploadCard({ title, scope, scopeClass, description, endpoint, sampleInfo }) {
  const [file, setFile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const handleFile = (selectedFile) => {
    setError(null)
    setResult(null)
    if (selectedFile && !selectedFile.name.toLowerCase().endsWith('.csv')) {
      setFile(null)
      setError('Only CSV files are supported.')
      return
    }
    setFile(selectedFile)
  }

  const handleUpload = async () => {
    if (!file) return
    setLoading(true)
    setResult(null)
    setError(null)

    const form = new FormData()
    form.append('file', file)

    try {
      const res = await axios.post(`${API}/${endpoint}/`, form)
      setResult(res.data)
      setFile(null)
    } catch (uploadError) {
      setError(uploadError.response?.data?.message || uploadError.response?.data?.error || 'Upload failed. Check the file format.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="upload-card">
      <h3>{title}</h3>
      <span className={`scope-tag ${scopeClass}`}>{scope}</span>
      <p>{description}</p>
      {error && <div className="alert alert-error">{error}</div>}
      {result && (
        <div className="alert alert-success">
          <strong>{result.file_name}</strong>: {result.parsed_rows} parsed, {result.failed_rows} failed, {result.skipped_rows} skipped.
        </div>
      )}
      <label className="upload-area">
        <input type="file" accept=".csv" onChange={(event) => handleFile(event.target.files[0])} />
        <div className="icon">CSV</div>
        <div className="text">Click to select CSV file</div>
        {file && <div className="filename">Selected: {file.name}</div>}
      </label>
      <button className="btn btn-primary" onClick={handleUpload} disabled={!file || loading}>
        {loading ? 'Processing...' : 'Upload and Process'}
      </button>
      <div className="sample-info">{sampleInfo}</div>
      <FailureList failures={result?.failures} />
    </div>
  )
}

export default function Upload() {
  return (
    <div className="container">
      <div className="page-title">Upload Center</div>
      <div className="page-sub">
        Upload CSV exports from SAP, utility portals, or travel systems. Each run returns parsed, failed, and skipped row counts immediately.
      </div>
      <div className="upload-grid">
        <UploadCard
          title="SAP Fuel"
          scope="Scope 1"
          scopeClass="scope-1"
          endpoint="ingest/sap"
          description="SAP MB51 flat-file export with fuel movement records, German headers, and unit conversion."
          sampleInfo="Sample file: sample_data/sap_export.csv"
        />
        <UploadCard
          title="Utility Electricity"
          scope="Scope 2"
          scopeClass="scope-2"
          endpoint="ingest/utility"
          description="Utility portal CSV with billing periods and electricity consumption in kWh."
          sampleInfo="Sample file: sample_data/utility_export.csv"
        />
        <UploadCard
          title="Business Travel"
          scope="Scope 3"
          scopeClass="scope-3"
          endpoint="ingest/travel"
          description="Concur/Navan-style travel export with flights and hotel stays."
          sampleInfo="Sample file: sample_data/travel_export.csv"
        />
      </div>
    </div>
  )
}
