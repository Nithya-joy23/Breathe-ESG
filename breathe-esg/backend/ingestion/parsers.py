import csv
import io
from datetime import datetime
from decimal import Decimal
from math import radians, sin, cos, sqrt, asin

from .models import RawRecord, NormalizedRecord, AuditAction

# ==================== EMISSION FACTORS (MoEFCC 2023 + CEA 2023 + DEFRA 2024) ====================

EMISSION_FACTORS = {
    'SAP': {
        'DIESEL': Decimal('2.68'),    # kg CO2e per litre — MoEFCC 2023
        'NATGAS': Decimal('2.04'),    # kg CO2e per cubic metre — MoEFCC 2023
        'DEFAULT': Decimal('2.68'),
    },
    'UTILITY': {
        'DEFAULT': Decimal('0.233'),   # kg CO2e per kWh — CEA 2023
    },
    'TRAVEL': {
        'ECONOMY': Decimal('0.255'),  # kg CO2e per km — DEFRA 2024
        'BUSINESS': Decimal('0.739'), # 0.255 * 2.9 multiplier
        'HOTEL': Decimal('20.8'),     # kg CO2e per night — MoEFCC 2023
        'CAR': Decimal('0.171'),      # kg CO2e per km — MoEFCC 2023
    }
}

# ==================== UTILITY FUNCTIONS ====================

def parse_date(date_str, formats=None):
    """Parse date from multiple formats."""
    if formats is None:
        formats = ['%d.%m.%Y', '%Y-%m-%d', '%Y/%m/%d', '%d/%m/%Y']
    
    if not date_str or not isinstance(date_str, str):
        return None
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate great-circle distance between two points (in km)."""
    R = 6371  # Earth radius in km
    phi1 = radians(lat1)
    phi2 = radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    
    a = sin(dphi/2)**2 + cos(phi1)*cos(phi2)*sin(dlambda/2)**2
    c = 2 * asin(sqrt(a))
    return R * c


# IATA airport coordinates (limited set for prototype)
IATA_LOOKUP = {
    'BOM': {'lat': 28.5561, 'lon': 77.0991, 'name': 'Bombay/Mumbai'},
    'LHR': {'lat': 51.4700, 'lon': -0.4543, 'name': 'London Heathrow'},
    'DEL': {'lat': 28.5663, 'lon': 77.1200, 'name': 'Delhi'},
    'BLR': {'lat': 13.1939, 'lon': 77.7064, 'name': 'Bangalore'},
    'SIN': {'lat': 1.3644, 'lon': 103.9915, 'name': 'Singapore'},
    'DXB': {'lat': 25.2532, 'lon': 55.3657, 'name': 'Dubai'},
}

# ==================== SAP PARSER ====================

COLUMN_MAP_SAP = {
    'Buchungsdatum': 'posting_date',
    'BLDAT': 'posting_date',
    'Werk': 'plant_code',
    'WERKS': 'plant_code',
    'Material': 'material',
    'MATNR': 'material',
    'Menge': 'quantity',
    'MENGE': 'quantity',
    'Meins': 'unit',
    'MEINS': 'unit',
    'Bewegungsart': 'movement_type',
    'BWART': 'movement_type',
    'Kostenstelle': 'cost_center',
    'KOSTL': 'cost_center',
}

UNIT_CONVERSION_TO_LITRES = {
    'L': Decimal('1.0'),
    'LTR': Decimal('1.0'),
    'GAL': Decimal('3.78541'),
    'KL': Decimal('1000.0'),
    'M3': Decimal('1000.0'),
}


def get_emission_factor_for_material(material_code):
    """Get emission factor (kg CO2e/L) for a material code."""
    if not material_code:
        return 'DEFAULT', EMISSION_FACTORS['SAP']['DEFAULT']
    
    material_upper = str(material_code).upper()
    
    for key in ['DIESEL', 'NATGAS']:
        if key in material_upper:
            return key, EMISSION_FACTORS['SAP'][key]
    
    return 'DEFAULT', EMISSION_FACTORS['SAP']['DEFAULT']


def parse_sap(file, ingestion_run, tenant):
    """Parse SAP fuel/procurement data."""
    try:
        decoded = file.read().decode('utf-8')
        reader = csv.DictReader(io.StringIO(decoded))
        
        if not reader.fieldnames:
            ingestion_run.total_rows = 0
            return
        
        # Normalize column headers
        normalized_headers = {}
        for header in reader.fieldnames:
            normalized_header = COLUMN_MAP_SAP.get(header, header)
            normalized_headers[header] = normalized_header
        
        for row_num, row in enumerate(reader, start=1):
            ingestion_run.total_rows += 1
            
            # Normalize row keys
            normalized_row = {normalized_headers[k]: v for k, v in row.items()}
            
            # Always create RawRecord first
            raw = RawRecord.objects.create(
                tenant=tenant,
                ingestion_run=ingestion_run,
                source_type='SAP',
                raw_data=dict(row),
                row_number=row_num,
            )
            
            try:
                # Check movement type when present (201 = consumed, 101 = received).
                # Some MB51-style exports omit BWART; treat those rows as consumption
                # rather than skipping the entire upload.
                movement_type = str(normalized_row.get('movement_type', '')).strip()
                if movement_type and movement_type != '201':
                    raw.parse_error = f'movement_type_skipped:{movement_type}'
                    raw.save()
                    ingestion_run.skipped_rows += 1
                    ingestion_run.save()
                    continue
                
                # Parse quantity
                quantity_str = str(normalized_row.get('quantity', '')).strip()
                if not quantity_str:
                    raise ValueError('missing_quantity')
                
                quantity = Decimal(quantity_str)
                if quantity <= 0:
                    raise ValueError('non_positive_quantity')
                
                # Parse unit and convert to litres
                unit = str(normalized_row.get('unit', '')).strip().upper()
                if unit not in UNIT_CONVERSION_TO_LITRES:
                    raise ValueError(f'unrecognized_unit:{unit}')
                
                activity_value = quantity * UNIT_CONVERSION_TO_LITRES[unit]
                
                # Get emission factor
                material = normalized_row.get('material', '')
                fuel_type, ef = get_emission_factor_for_material(material)
                
                # Calculate CO2e
                co2e = activity_value * ef
                
                # Parse date
                posting_date_str = normalized_row.get('posting_date', '')
                posting_date = parse_date(posting_date_str, formats=['%d.%m.%Y', '%Y-%m-%d', '%Y/%m/%d'])
                if not posting_date:
                    raise ValueError('invalid_date_format')
                
                # Create NormalizedRecord
                NormalizedRecord.objects.create(
                    tenant=tenant,
                    raw_record=raw,
                    source_type='SAP',
                    scope=1,  # SAP is always Scope 1
                    activity_value=activity_value,
                    activity_unit='L',
                    co2e_kg=co2e,
                    emission_factor=ef,
                    ef_source='MoEFCC 2023',
                    period_start=posting_date,
                    period_end=posting_date,
                    status='PENDING',
                )
                
                ingestion_run.parsed_rows += 1
                ingestion_run.save()
                
            except Exception as e:
                raw.parse_error = str(e)
                raw.save()
                ingestion_run.failed_rows += 1
                ingestion_run.save()
    
    except Exception as e:
        ingestion_run.status = 'FAILED'
        ingestion_run.save()
        raise


# ==================== UTILITY PARSER ====================

def parse_utility(file, ingestion_run, tenant):
    """Parse utility electricity data."""
    try:
        decoded = file.read().decode('utf-8')
        reader = csv.DictReader(io.StringIO(decoded))
        
        if not reader.fieldnames:
            ingestion_run.total_rows = 0
            return
        
        for row_num, row in enumerate(reader, start=1):
            ingestion_run.total_rows += 1
            
            # Always create RawRecord
            raw = RawRecord.objects.create(
                tenant=tenant,
                ingestion_run=ingestion_run,
                source_type='UTILITY',
                raw_data=dict(row),
                row_number=row_num,
            )
            
            try:
                # Parse period dates
                period_start_str = row.get('period_start', '')
                period_end_str = row.get('period_end', '')
                
                period_start = parse_date(period_start_str)
                period_end = parse_date(period_end_str)
                
                if not period_start or not period_end:
                    raise ValueError('invalid_period_dates')
                
                # Parse kWh
                kwh_str = str(row.get('kwh_consumed', '')).strip()
                if not kwh_str:
                    raise ValueError('missing_kwh_consumed')
                
                kwh = Decimal(kwh_str)
                # Allow negative (net metering) but flag as anomaly
                
                # Calculate CO2e
                ef = EMISSION_FACTORS['UTILITY']['DEFAULT']
                co2e = kwh * ef
                
                # Create NormalizedRecord
                NormalizedRecord.objects.create(
                    tenant=tenant,
                    raw_record=raw,
                    source_type='UTILITY',
                    scope=2,  # Utility is always Scope 2
                    activity_value=kwh,
                    activity_unit='kWh',
                    co2e_kg=co2e,
                    emission_factor=ef,
                    ef_source='CEA 2023',
                    period_start=period_start,
                    period_end=period_end,
                    is_anomaly=kwh < 0,  # Flag negative consumption
                    status='PENDING',
                )
                
                ingestion_run.parsed_rows += 1
                ingestion_run.save()
                
            except Exception as e:
                raw.parse_error = str(e)
                raw.save()
                ingestion_run.failed_rows += 1
                ingestion_run.save()
    
    except Exception as e:
        ingestion_run.status = 'FAILED'
        ingestion_run.save()
        raise


# ==================== TRAVEL PARSER ====================

def create_flagged_travel_record(tenant, raw, travel_date, reason, activity_unit='km', actor=None):
    record = NormalizedRecord.objects.create(
        tenant=tenant,
        raw_record=raw,
        source_type='TRAVEL',
        scope=3,
        activity_value=None,
        activity_unit=activity_unit,
        co2e_kg=None,
        emission_factor=None,
        ef_source='UNAVAILABLE',
        period_start=travel_date,
        period_end=travel_date,
        status='FLAGGED',
        is_anomaly=True,
    )

    if actor:
        AuditAction.objects.create(
            tenant=tenant,
            normalized_record=record,
            action='FLAGGED',
            performed_by=actor,
            comment=reason,
            previous_value=None,
            new_value={
                'status': 'FLAGGED',
                'activity_value': None,
                'activity_unit': activity_unit,
                'co2e_kg': None,
                'reason': reason,
            },
        )

    return record


def parse_travel(file, ingestion_run, tenant, actor=None):
    """Parse corporate travel data."""
    try:
        decoded = file.read().decode('utf-8')
        reader = csv.DictReader(io.StringIO(decoded))
        
        if not reader.fieldnames:
            ingestion_run.total_rows = 0
            return
        
        for row_num, row in enumerate(reader, start=1):
            ingestion_run.total_rows += 1
            
            # Always create RawRecord
            raw = RawRecord.objects.create(
                tenant=tenant,
                ingestion_run=ingestion_run,
                source_type='TRAVEL',
                raw_data=dict(row),
                row_number=row_num,
            )
            
            try:
                travel_date_str = row.get('travel_date', '')
                travel_date = parse_date(travel_date_str)
                if not travel_date:
                    raise ValueError('invalid_travel_date')
                
                mode = str(row.get('mode', '')).strip().upper()
                
                if mode == 'FLIGHT':
                    # Parse origin and destination
                    origin_code = str(row.get('origin', '')).strip().upper()
                    dest_code = str(row.get('destination', '')).strip().upper()
                    
                    if origin_code not in IATA_LOOKUP:
                        reason = f'Unknown airport code: {origin_code}'
                        create_flagged_travel_record(tenant, raw, travel_date, reason, actor=actor)
                        ingestion_run.parsed_rows += 1
                        ingestion_run.save()
                        continue
                    if dest_code not in IATA_LOOKUP:
                        reason = f'Unknown airport code: {dest_code}'
                        create_flagged_travel_record(tenant, raw, travel_date, reason, actor=actor)
                        ingestion_run.parsed_rows += 1
                        ingestion_run.save()
                        continue
                    
                    origin = IATA_LOOKUP[origin_code]
                    dest = IATA_LOOKUP[dest_code]
                    
                    # Calculate distance
                    distance_km = haversine_distance(
                        origin['lat'], origin['lon'],
                        dest['lat'], dest['lon']
                    )
                    
                    # Parse cabin class
                    cabin_class = str(row.get('cabin_class', 'ECONOMY')).strip().upper()
                    if cabin_class not in EMISSION_FACTORS['TRAVEL']:
                        cabin_class = 'ECONOMY'
                    
                    ef = EMISSION_FACTORS['TRAVEL'][cabin_class]
                    co2e = Decimal(str(distance_km)) * ef
                    
                    NormalizedRecord.objects.create(
                        tenant=tenant,
                        raw_record=raw,
                        source_type='TRAVEL',
                        scope=3,
                        activity_value=Decimal(str(distance_km)),
                        activity_unit='km',
                        co2e_kg=co2e,
                        emission_factor=ef,
                        ef_source='DEFRA 2024',
                        period_start=travel_date,
                        period_end=travel_date,
                        status='PENDING',
                    )
                
                elif mode == 'HOTEL':
                    # Parse hotel nights
                    nights_str = str(row.get('hotel_nights', '')).strip()
                    if not nights_str:
                        raise ValueError('missing_hotel_nights')
                    
                    nights = Decimal(nights_str)
                    if nights <= 0:
                        raise ValueError('non_positive_hotel_nights')
                    
                    ef = EMISSION_FACTORS['TRAVEL']['HOTEL']
                    co2e = nights * ef
                    
                    NormalizedRecord.objects.create(
                        tenant=tenant,
                        raw_record=raw,
                        source_type='TRAVEL',
                        scope=3,
                        activity_value=nights,
                        activity_unit='nights',
                        co2e_kg=co2e,
                        emission_factor=ef,
                        ef_source='MoEFCC 2023',
                        period_start=travel_date,
                        period_end=travel_date,
                        status='PENDING',
                    )
                elif mode == 'CAR':
                    distance_str = str(row.get('distance_km', '')).strip()
                    if not distance_str:
                        raise ValueError('missing_distance_km')
                    
                    distance = Decimal(distance_str)
                    if distance <= 0:
                        raise ValueError('non_positive_distance_km')
                    
                    ef = EMISSION_FACTORS['TRAVEL']['CAR']
                    co2e = distance * ef
                    
                    NormalizedRecord.objects.create(
                        tenant=tenant,
                        raw_record=raw,
                        source_type='TRAVEL',
                        scope=3,
                        activity_value=distance,
                        activity_unit='km',
                        co2e_kg=co2e,
                        emission_factor=ef,
                        ef_source='MoEFCC 2023',
                        period_start=travel_date,
                        period_end=travel_date,
                        status='PENDING',
                    )
                else:
                    reason = f'Unsupported travel mode: {mode}'
                    create_flagged_travel_record(
                        tenant,
                        raw,
                        travel_date,
                        reason,
                        activity_unit='',
                        actor=actor,
                    )
                    ingestion_run.parsed_rows += 1
                    ingestion_run.save()
                    continue
                
                ingestion_run.parsed_rows += 1
                ingestion_run.save()
                
            except Exception as e:
                raw.parse_error = str(e)
                raw.save()
                ingestion_run.failed_rows += 1
                ingestion_run.save()
    
    except Exception as e:
        ingestion_run.status = 'FAILED'
        ingestion_run.save()
        raise
