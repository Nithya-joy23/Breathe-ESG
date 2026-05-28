export function formatPeriod(record) {
  if (!record) return '--'
  if (record.period_display) return record.period_display
  if (record.source_type === 'TRAVEL') {
    return record.travel_date || record.period_display || `${record.period_start || '--'} to ${record.period_end || '--'}`
  }

  if (record.period_start && record.period_end) {
    return record.period_start === record.period_end
      ? record.period_start
      : `${record.period_start} to ${record.period_end}`
  }

  return record.period_start || record.period_end || '--'
}

export function formatActivity(record) {
  if (!record) return '--'
  const value = record.activity_value
  const unit = record.activity_unit || ''
  if (value === null || value === undefined || value === '') return '--'
  const formatted = Number(value).toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })
  return unit ? `${formatted} ${unit}` : formatted
}

export function formatCo2eNumber(value) {
  if (value === null || value === undefined || value === '') return '--'
  return Number(value).toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })
}
